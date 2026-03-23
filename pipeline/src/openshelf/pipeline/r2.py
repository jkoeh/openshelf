"""Step 6: Upload audio files, chunks, EPUBs, and manifests to Cloudflare R2."""

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
    R2_PREFIX_BOOKS,
    R2_SECRET_KEY,
)

logger = logging.getLogger(__name__)


def make_client():
    """Create a boto3 S3 client configured for Cloudflare R2."""
    return boto3.client(
        "s3",
        endpoint_url=f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
        aws_access_key_id=R2_ACCESS_KEY,
        aws_secret_access_key=R2_SECRET_KEY,
        region_name="auto",
    )


def _book_prefix(author_slug: str, title_slug: str) -> str:
    return f"{R2_PREFIX_BOOKS}/{author_slug}/{title_slug}"


def key_exists(client, bucket: str, key: str) -> bool:
    """Return True if the key already exists in the bucket, False on 404."""
    try:
        client.head_object(Bucket=bucket, Key=key)
        return True
    except ClientError as e:
        if e.response["Error"]["Code"] == "404":
            return False
        raise


def upload_rendition(
    client,
    bucket: str,
    author_slug: str,
    title_slug: str,
    rendition: str,
    audio_dir: str,
    manifest_path: str,
) -> list[str]:
    """Upload all Opus files and manifest.json for a rendition to R2. Returns uploaded keys.

    Key pattern: books/{author}/{title}/audio/{rendition}/chapter-NN.m4a
    Idempotency: manifest.json is always uploaded last. Its presence on R2 signals
    that the rendition is complete. One HEAD request at the top — not one per file.
    """
    prefix = f"{_book_prefix(author_slug, title_slug)}/audio/{rendition}"
    manifest_key = f"{prefix}/manifest.json"

    if key_exists(client, bucket, manifest_key):
        logger.info("Rendition already uploaded, skipping: %s", manifest_key)
        return []

    uploaded: list[str] = []

    opus_files = sorted(f for f in os.listdir(audio_dir) if f.endswith(".m4a"))
    for filename in opus_files:
        key = f"{prefix}/{filename}"
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

    client.upload_file(
        manifest_path,
        bucket,
        manifest_key,
        ExtraArgs={
            "ContentType": "application/json",
            "CacheControl": R2_CACHE_CONTROL_MANIFEST,
        },
    )
    logger.info("Uploaded: %s", manifest_key)
    uploaded.append(manifest_key)

    return uploaded


def upload_epub(
    client,
    bucket: str,
    author_slug: str,
    title_slug: str,
    epub_path: str,
) -> str | None:
    """Upload an EPUB file to R2. Returns the key, or None if already exists.

    Key pattern: books/{author}/{title}/book.epub
    Idempotent: skips upload if the key already exists.
    """
    key = f"{_book_prefix(author_slug, title_slug)}/book.epub"

    if key_exists(client, bucket, key):
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


def upload_word_alignment(
    client,
    bucket: str,
    author_slug: str,
    title_slug: str,
    rendition: str,
    word_alignment_path: str,
) -> str | None:
    """Upload word_alignment.json to R2. Returns the key, or None if already exists.

    Key pattern: books/{author}/{title}/audio/{rendition}/word_alignment.json
    Idempotent: skips upload if the key already exists.
    """
    prefix = f"{_book_prefix(author_slug, title_slug)}/audio/{rendition}"
    key = f"{prefix}/word_alignment.json"

    if key_exists(client, bucket, key):
        logger.info("Word alignment already uploaded, skipping: %s", key)
        return None

    client.upload_file(
        word_alignment_path,
        bucket,
        key,
        ExtraArgs={
            "ContentType": "application/json",
            "CacheControl": R2_CACHE_CONTROL_IMMUTABLE,
        },
    )
    logger.info("Uploaded: %s", key)
    return key


def upload_chunks(
    client,
    bucket: str,
    author_slug: str,
    title_slug: str,
    chunks_path: str,
) -> str | None:
    """Upload chunks.json to R2. Returns the key, or None if already exists.

    Key pattern: books/{author}/{title}/chunks.json
    Idempotent: skips upload if the key already exists.
    """
    key = f"{_book_prefix(author_slug, title_slug)}/chunks.json"

    if key_exists(client, bucket, key):
        logger.info("Chunks already uploaded, skipping: %s", key)
        return None

    client.upload_file(
        chunks_path,
        bucket,
        key,
        ExtraArgs={
            "ContentType": "application/json",
            "CacheControl": R2_CACHE_CONTROL_IMMUTABLE,
        },
    )
    logger.info("Uploaded: %s", key)
    return key
