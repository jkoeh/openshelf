#!/usr/bin/env python3
"""Build catalog.json from all manifests on R2 and upload it.

Scans R2 for books/<author>/<title>/audio/<rendition>/manifest.json,
aggregates them into a single catalog.json, and uploads it to the bucket root.

Usage:
    python3 pipeline/scripts/build-catalog.py
    python3 pipeline/scripts/build-catalog.py --dry-run   # print catalog, don't upload
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from openshelf.config import R2_BUCKET, R2_PREFIX_BOOKS
from openshelf.pipeline.r2 import make_client


def list_manifests(client, bucket: str) -> list[str]:
    """List all manifest.json keys under books/."""
    keys = []
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=f"{R2_PREFIX_BOOKS}/"):
        for obj in page.get("Contents", []):
            if obj["Key"].endswith("/manifest.json"):
                keys.append(obj["Key"])
    return keys


def parse_manifest_key(key: str) -> dict | None:
    """Extract author_slug, title_slug, rendition from a manifest key.

    Expected: books/<author>/<title>/audio/<rendition>/manifest.json
    """
    parts = key.split("/")
    if len(parts) != 6:
        return None
    return {
        "author_slug": parts[1],
        "title_slug": parts[2],
        "rendition": parts[4],
    }


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

        obj = client.get_object(Bucket=bucket, Key=key)
        manifest = json.loads(obj["Body"].read())

        book = {
            "author": manifest.get("author", meta["author_slug"]),
            "author_slug": meta["author_slug"],
            "title": manifest.get("title", meta["title_slug"]),
            "title_slug": meta["title_slug"],
            "source": manifest.get("source", "unknown"),
            "rendition": meta["rendition"],
            "total_duration_seconds": manifest.get("total_duration_seconds", 0),
            "chapter_count": len(manifest.get("chapters", [])),
        }
        books.append(book)
        mins = book["total_duration_seconds"] / 60
        print(f"  {book['author']} — {book['title']} ({book['chapter_count']} ch, {mins:.1f} min)")

    return {
        "version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "books": books,
    }


def main():
    parser = argparse.ArgumentParser(description="Build and upload catalog.json from R2 manifests")
    parser.add_argument("--dry-run", action="store_true", help="Print catalog without uploading")
    args = parser.parse_args()

    client = make_client()
    catalog = build_catalog(client, R2_BUCKET)

    print(f"\nCatalog: {len(catalog['books'])} book(s)")

    if args.dry_run:
        print("\n" + json.dumps(catalog, indent=2))
        print("\n[DRY RUN] Not uploaded.")
        return

    catalog_json = json.dumps(catalog, indent=2, ensure_ascii=False)
    client.put_object(
        Bucket=R2_BUCKET,
        Key="catalog.json",
        Body=catalog_json.encode("utf-8"),
        ContentType="application/json",
        CacheControl="public, max-age=60",
    )
    print("Uploaded catalog.json to R2.")


if __name__ == "__main__":
    main()
