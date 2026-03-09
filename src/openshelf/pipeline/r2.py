"""Step 6: Upload audio files and manifest to Cloudflare R2."""

import logging
import os

import boto3
from botocore.exceptions import ClientError

from openshelf.config import (
    R2_ACCESS_KEY,
    R2_ACCOUNT_ID,
    R2_CACHE_CONTROL_AUDIO,
    R2_CACHE_CONTROL_MANIFEST,
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


def key_exists(client, bucket: str, key: str) -> bool:
    """Return True if the key already exists in the bucket, False on 404."""
    try:
        client.head_object(Bucket=bucket, Key=key)
        return True
    except ClientError as e:
        if e.response["Error"]["Code"] == "404":
            return False
        raise


def upload_book(
    client,
    bucket: str,
    author_slug: str,
    title_slug: str,
    audio_dir: str,
    manifest_path: str,
) -> list[str]:
    """Upload all MP3s and manifest.json to R2. Returns uploaded keys.

    Idempotency: manifest.json is always uploaded last. Its presence on R2 signals
    that the entire book is complete. One HEAD request at the top — not one per file.
    """
    prefix = f"{author_slug}/{title_slug}"
    manifest_key = f"{prefix}/manifest.json"

    if key_exists(client, bucket, manifest_key):
        logger.info("Book already uploaded, skipping: %s", manifest_key)
        return []

    uploaded: list[str] = []

    mp3_files = sorted(f for f in os.listdir(audio_dir) if f.endswith(".mp3"))
    for filename in mp3_files:
        key = f"{prefix}/{filename}"
        client.upload_file(
            os.path.join(audio_dir, filename),
            bucket,
            key,
            ExtraArgs={
                "ContentType": "audio/mpeg",
                "CacheControl": R2_CACHE_CONTROL_AUDIO,
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
