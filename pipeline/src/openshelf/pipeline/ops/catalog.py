"""Build catalog.json from root book manifests on R2 and upload it."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from typing import Sequence

from botocore.exceptions import ClientError

from openshelf.config import R2_BUCKET, R2_DEFAULT_RENDITION, R2_PREFIX_BOOKS
from openshelf.pipeline import r2_keys
from openshelf.pipeline.r2 import make_client


def list_manifests(client, bucket: str) -> list[str]:
    """List root book manifest.json keys under books/."""
    keys = []
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=f"{R2_PREFIX_BOOKS}/"):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if parse_manifest_key(key):
                keys.append(obj["Key"])
    return keys


def parse_manifest_key(key: str) -> dict | None:
    """Extract author_slug and title_slug from a root book manifest key."""
    parts = key.split("/")
    if len(parts) != 4 or parts[0] != R2_PREFIX_BOOKS or parts[3] != "manifest.json":
        return None
    return {
        "author_slug": parts[1],
        "title_slug": parts[2],
    }


def choose_catalog_rendition(renditions: dict) -> str | None:
    """Pick the rendition represented in catalog rows."""
    if not renditions:
        return None
    if R2_DEFAULT_RENDITION in renditions:
        return R2_DEFAULT_RENDITION
    return sorted(renditions.keys())[0]


def _read_json_object(client, bucket: str, key: str) -> dict | None:
    try:
        obj = client.get_object(Bucket=bucket, Key=key)
    except ClientError as e:
        if e.response["Error"]["Code"] in ("NoSuchKey", "404"):
            return None
        raise
    return json.loads(obj["Body"].read())


def _key_exists(client, bucket: str, key: str) -> bool:
    try:
        client.head_object(Bucket=bucket, Key=key)
        return True
    except ClientError as e:
        if e.response["Error"]["Code"] in ("NoSuchKey", "404"):
            return False
        raise


def build_catalog(client, bucket: str) -> dict:
    """Scan R2 and build the catalog dict."""
    manifest_keys = list_manifests(client, bucket)
    print(f"Found {len(manifest_keys)} manifest(s) on R2.\n")

    books = []
    for key in sorted(manifest_keys):
        meta = parse_manifest_key(key)
        if not meta:
            print(f"  [SKIP] unexpected key format: {key}")
            continue

        manifest = _read_json_object(client, bucket, key)
        if not manifest:
            print(f"  [SKIP] missing manifest: {key}")
            continue

        renditions = manifest.get("renditions", {})
        rendition = choose_catalog_rendition(renditions)
        if not rendition:
            print(f"  [SKIP] no renditions: {key}")
            continue

        current_build = renditions.get(rendition, {}).get("current_build")
        if not current_build:
            print(f"  [SKIP] no current_build for {rendition}: {key}")
            continue

        rendition_manifest_key = r2_keys.rendition_manifest_key(
            meta["author_slug"],
            meta["title_slug"],
            rendition,
            current_build,
        )
        rendition_manifest = _read_json_object(client, bucket, rendition_manifest_key)
        if not rendition_manifest:
            print(f"  [SKIP] missing rendition manifest: {rendition_manifest_key}")
            continue

        has_cover = (
            _key_exists(
                client,
                bucket,
                r2_keys.cover_key(meta["author_slug"], meta["title_slug"], "image/jpeg"),
            )
            or _key_exists(
                client,
                bucket,
                r2_keys.cover_key(meta["author_slug"], meta["title_slug"], "image/png"),
            )
        )
        chapters = rendition_manifest.get("chapters", [])

        book = {
            "author": manifest.get("author", meta["author_slug"]),
            "author_slug": meta["author_slug"],
            "title": manifest.get("title", meta["title_slug"]),
            "title_slug": meta["title_slug"],
            "source": manifest.get("source", "unknown"),
            "rendition": rendition,
            "total_duration_seconds": rendition_manifest.get("total_duration_seconds", 0),
            "chapter_count": len(chapters),
            "has_cover": has_cover,
        }
        books.append(book)
        mins = book["total_duration_seconds"] / 60
        print(f"  {book['author']} - {book['title']} ({book['chapter_count']} ch, {mins:.1f} min)")

    return {
        "version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "books": books,
    }


def build_and_upload_catalog(*, dry_run: bool = False, client=None, bucket: str = R2_BUCKET) -> dict:
    """Build catalog.json and upload it unless ``dry_run`` is set."""
    if client is None:
        client = make_client()
    catalog = build_catalog(client, bucket)

    print(f"\nCatalog: {len(catalog['books'])} book(s)")

    if dry_run:
        print("\n" + json.dumps(catalog, indent=2))
        print("\n[DRY RUN] Not uploaded.")
        return catalog

    catalog_json = json.dumps(catalog, indent=2, ensure_ascii=False)
    client.put_object(
        Bucket=bucket,
        Key="catalog.json",
        Body=catalog_json.encode("utf-8"),
        ContentType="application/json",
        CacheControl="public, max-age=60",
    )
    print("Uploaded catalog.json to R2.")
    return catalog


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="openshelf-pipeline ops catalog",
        description="Build and upload catalog.json from root R2 manifests.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print catalog without uploading")
    args = parser.parse_args(argv)
    build_and_upload_catalog(dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
