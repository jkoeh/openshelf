"""R2 uploaders for the build-versioned layout.

See pipeline/docs/step6-r2.md for the layout. Every key construction is
delegated to `r2_keys`; this module only owns upload behaviour — headers,
the single-gate idempotency check, and the per-uploader force semantics.

Key invariants enforced here:

- Per-build artifacts (audio, chapter_data, rendition-manifest) all live
  under the same `audio/{rendition}/builds/{build}/` prefix and are written
  in a single call to `upload_rendition_build`. The rendition-manifest is
  always uploaded last so its presence on R2 signals the build is complete.
- The book manifest is the only mutable per-book artifact; it is always
  overwritten and takes no `force` parameter.
"""

from __future__ import annotations

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


# ---------------------------------------------------------------------------
# Per-build upload (audio + chapter_data + rendition-manifest)
# ---------------------------------------------------------------------------


def upload_rendition_build(
    client,
    bucket: str,
    author_slug: str,
    title_slug: str,
    rendition: str,
    build_id: str,
    audio_dir: str,
    chapter_data_path: str,
    rendition_manifest_path: str,
    force: bool = False,
) -> list[str]:
    """Upload every per-build artifact for one (rendition, build).

    Order: all m4a files, then chapter_data.json, then rendition-manifest.json
    last. The rendition-manifest is the single-gate idempotency signal: if
    it already exists on R2, the whole build is skipped (one HEAD request,
    not O(N) per file). `force=True` skips the check and re-uploads.

    Returns the list of R2 keys written (empty list when skipped).
    """
    manifest_key = r2_keys.rendition_manifest_key(author_slug, title_slug, rendition, build_id)

    if not force and key_exists(client, bucket, manifest_key):
        logger.info("Build already uploaded, skipping: %s", manifest_key)
        return []

    uploaded: list[str] = []

    # 1. Audio files, sorted to ensure stable ordering across runs.
    for filename in sorted(f for f in os.listdir(audio_dir) if f.endswith(".m4a")):
        # filename format: chapter-NN.m4a
        chapter_number = int(filename.removeprefix("chapter-").removesuffix(".m4a"))
        key = r2_keys.audio_key(author_slug, title_slug, rendition, build_id, chapter_number)
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

    # 2. chapter_data.json.
    cd_key = r2_keys.chapter_data_key(author_slug, title_slug, rendition, build_id)
    client.upload_file(
        chapter_data_path,
        bucket,
        cd_key,
        ExtraArgs={
            "ContentType": "application/json",
            "CacheControl": R2_CACHE_CONTROL_IMMUTABLE,
        },
    )
    logger.info("Uploaded: %s", cd_key)
    uploaded.append(cd_key)

    # 3. rendition-manifest.json LAST — completion signal.
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
