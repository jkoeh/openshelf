"""R2 uploaders for the build-versioned layout.

See pipeline/docs/step6-r2.md for the layout. Every key construction is
delegated to `r2_keys`; this module only owns upload behaviour — headers,
the single-gate idempotency check, and the per-uploader force semantics.

Key invariants enforced here:

- Per-build artifacts (audio, synthesis unit audits, section_data,
  character_registry, voice_direction, rendition-manifest) all live
  under the same `audio/{rendition}/builds/{build}/` prefix and are written
  in a single call to `upload_rendition_build`. The rendition-manifest is
  always uploaded last so its presence on R2 signals the build is complete.
- The book manifest is the only mutable per-book artifact; it is always
  overwritten and takes no `force` parameter.
"""

from __future__ import annotations

import json
import logging
import os

import boto3
from botocore.exceptions import ClientError

from openshelf.config import (
    R2_ACCESS_KEY,
    R2_ACCOUNT_ID,
    R2_CACHE_CONTROL_IMMUTABLE,
    R2_CACHE_CONTROL_MANIFEST,
    R2_SECRET_KEY,
)
from openshelf.pipeline import r2_keys

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Client + helpers
# ---------------------------------------------------------------------------


def make_client():
    """Create a boto3 S3 client configured for Cloudflare R2."""
    return boto3.client(
        "s3",
        endpoint_url=f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
        aws_access_key_id=R2_ACCESS_KEY,
        aws_secret_access_key=R2_SECRET_KEY,
        region_name="auto",
    )


def key_exists(client, bucket: str, key: str) -> bool:
    """Return True if the key exists in the bucket, False on 404."""
    try:
        client.head_object(Bucket=bucket, Key=key)
        return True
    except ClientError as e:
        if e.response["Error"]["Code"] == "404":
            return False
        raise


def fetch_prior_book_manifest(client, bucket: str, author_slug: str, title_slug: str) -> dict:
    """Return the prior book manifest on R2 (parsed), or {} if it does not exist."""
    key = r2_keys.book_manifest_key(author_slug, title_slug)
    try:
        obj = client.get_object(Bucket=bucket, Key=key)
    except ClientError as e:
        if e.response["Error"]["Code"] in ("NoSuchKey", "404"):
            return {}
        raise
    return json.loads(obj["Body"].read().decode("utf-8"))


# ---------------------------------------------------------------------------
# Per-build upload (audio + section_data + rendition-manifest)
# ---------------------------------------------------------------------------


def upload_rendition_build(
    client,
    bucket: str,
    author_slug: str,
    title_slug: str,
    rendition: str,
    build_id: str,
    audio_dir: str,
    section_data_path: str,
    rendition_manifest_path: str,
    character_registry_path: str | None = None,
    voice_direction_path: str | None = None,
    run_context_path: str | None = None,
    force: bool = False,
) -> list[str]:
    """Upload every per-build artifact for one (rendition, build).

    Order: all m4a files, then JSON build artifacts, then
    rendition-manifest.json last. The rendition-manifest is the single-gate
    idempotency signal: if it already exists on R2, the whole build is
    skipped (one HEAD request, not O(N) per file). `force=True` skips the
    check and re-uploads.

    Returns the list of R2 keys written (empty list when skipped).
    """
    manifest_key = r2_keys.rendition_manifest_key(author_slug, title_slug, rendition, build_id)

    if not force and key_exists(client, bucket, manifest_key):
        logger.info("Build already uploaded, skipping: %s", manifest_key)
        return []

    uploaded: list[str] = []

    # 1. Audio files, sorted to ensure stable ordering across runs.
    for filename in sorted(
        f for f in os.listdir(audio_dir)
        if f.startswith("section-") and f.endswith(".m4a")
    ):
        sequence = int(filename.removeprefix("section-").removesuffix(".m4a"))
        key = r2_keys.audio_key(author_slug, title_slug, rendition, build_id, sequence)
        client.upload_file(
            os.path.join(audio_dir, filename),
            bucket,
            key,
            ExtraArgs={
                "ContentType": "audio/mp4",
                "CacheControl": R2_CACHE_CONTROL_IMMUTABLE,
                "ContentDisposition": "inline",
            },
        )
        logger.info("Uploaded: %s", key)
        uploaded.append(key)

    # 2. Private synthesis-unit seam audits, when present.
    for filename in sorted(
        f for f in os.listdir(audio_dir)
        if f.startswith("section-") and f.endswith(".synthesis_units.json")
    ):
        sequence = int(
            filename.removeprefix("section-").removesuffix(".synthesis_units.json")
        )
        key = r2_keys.synthesis_units_key(
            author_slug,
            title_slug,
            rendition,
            build_id,
            sequence,
        )
        client.upload_file(
            os.path.join(audio_dir, filename),
            bucket,
            key,
            ExtraArgs={
                "ContentType": "application/json",
                "CacheControl": R2_CACHE_CONTROL_IMMUTABLE,
            },
        )
        logger.info("Uploaded: %s", key)
        uploaded.append(key)

    # 3. section_data.json.
    cd_key = r2_keys.section_data_key(author_slug, title_slug, rendition, build_id)
    client.upload_file(
        section_data_path,
        bucket,
        cd_key,
        ExtraArgs={
            "ContentType": "application/json",
            "CacheControl": R2_CACHE_CONTROL_IMMUTABLE,
        },
    )
    logger.info("Uploaded: %s", cd_key)
    uploaded.append(cd_key)

    # 4. Optional artifacts are uploaded before the manifest completion signal.
    if character_registry_path and os.path.exists(character_registry_path):
        registry_key = r2_keys.character_registry_key(author_slug, title_slug, rendition, build_id)
        client.upload_file(
            character_registry_path,
            bucket,
            registry_key,
            ExtraArgs={
                "ContentType": "application/json",
                "CacheControl": R2_CACHE_CONTROL_IMMUTABLE,
            },
        )
        logger.info("Uploaded: %s", registry_key)
        uploaded.append(registry_key)

    if voice_direction_path and os.path.exists(voice_direction_path):
        direction_key = r2_keys.voice_direction_key(author_slug, title_slug, rendition, build_id)
        client.upload_file(
            voice_direction_path,
            bucket,
            direction_key,
            ExtraArgs={
                "ContentType": "application/json",
                "CacheControl": R2_CACHE_CONTROL_IMMUTABLE,
            },
        )
        logger.info("Uploaded: %s", direction_key)
        uploaded.append(direction_key)

    if run_context_path and os.path.exists(run_context_path):
        run_key = r2_keys.run_context_key(author_slug, title_slug, rendition, build_id)
        client.upload_file(
            run_context_path,
            bucket,
            run_key,
            ExtraArgs={
                "ContentType": "application/json",
                "CacheControl": R2_CACHE_CONTROL_IMMUTABLE,
            },
        )
        logger.info("Uploaded: %s", run_key)
        uploaded.append(run_key)

    # 5. rendition-manifest.json LAST: completion signal.
    client.upload_file(
        rendition_manifest_path,
        bucket,
        manifest_key,
        ExtraArgs={
            "ContentType": "application/json",
            "CacheControl": R2_CACHE_CONTROL_IMMUTABLE,
        },
    )
    logger.info("Uploaded: %s", manifest_key)
    uploaded.append(manifest_key)

    return uploaded


def delete_build_prefixes(
    client,
    bucket: str,
    author_slug: str,
    title_slug: str,
    rendition: str,
    build_ids: list[str],
) -> list[str]:
    """Delete complete superseded build prefixes after publishing version 2."""
    deleted: list[str] = []
    for build_id in dict.fromkeys(build_ids):
        prefix = r2_keys.build_prefix(
            author_slug,
            title_slug,
            rendition,
            build_id,
        ) + "/"
        paginator = client.get_paginator("list_objects_v2")
        keys: list[str] = []
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            keys.extend(item["Key"] for item in page.get("Contents", []))
        for index in range(0, len(keys), 1000):
            batch = keys[index:index + 1000]
            client.delete_objects(
                Bucket=bucket,
                Delete={"Objects": [{"Key": key} for key in batch], "Quiet": True},
            )
            deleted.extend(batch)
        logger.info("Deleted superseded build prefix: %s", prefix)
    return deleted


# ---------------------------------------------------------------------------
# Book manifest (the only mutable per-book artifact — always overwrite)
# ---------------------------------------------------------------------------


def upload_book_manifest(
    client,
    bucket: str,
    author_slug: str,
    title_slug: str,
    manifest_path: str,
) -> str:
    """Upload the book-level manifest.json. Always overwrites; no `force` flag.

    This is the single mutable pointer named by every short-cached client
    fetch. Returns the R2 key.
    """
    key = r2_keys.book_manifest_key(author_slug, title_slug)
    client.upload_file(
        manifest_path,
        bucket,
        key,
        ExtraArgs={
            "ContentType": "application/json",
            "CacheControl": R2_CACHE_CONTROL_MANIFEST,
        },
    )
    logger.info("Uploaded book manifest: %s", key)
    return key


# ---------------------------------------------------------------------------
# Root-level immutable artifacts (cover, epub)
# ---------------------------------------------------------------------------


def upload_cover(
    client,
    bucket: str,
    author_slug: str,
    title_slug: str,
    cover_path: str,
    content_type: str = "image/jpeg",
    force: bool = False,
) -> str | None:
    """Upload the cover image. Skips if the key already exists unless `force=True`."""
    key = r2_keys.cover_key(author_slug, title_slug, content_type=content_type)

    if not force and key_exists(client, bucket, key):
        logger.info("Cover already uploaded, skipping: %s", key)
        return None

    client.upload_file(
        cover_path,
        bucket,
        key,
        ExtraArgs={
            "ContentType": content_type,
            "CacheControl": R2_CACHE_CONTROL_IMMUTABLE,
        },
    )
    logger.info("Uploaded: %s", key)
    return key


def upload_epub(
    client,
    bucket: str,
    author_slug: str,
    title_slug: str,
    epub_path: str,
    force: bool = False,
) -> str | None:
    """Upload the annotated EPUB. Skips if the key already exists unless `force=True`."""
    key = r2_keys.book_epub_key(author_slug, title_slug)

    if not force and key_exists(client, bucket, key):
        logger.info("EPUB already uploaded, skipping: %s", key)
        return None

    client.upload_file(
        epub_path,
        bucket,
        key,
        ExtraArgs={
            "ContentType": "application/epub+zip",
            "CacheControl": R2_CACHE_CONTROL_IMMUTABLE,
        },
    )
    logger.info("Uploaded: %s", key)
    return key
