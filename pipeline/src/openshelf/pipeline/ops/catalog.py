"""Build catalog.json from root book manifests on R2 and upload it."""

from __future__ import annotations

import argparse
import json
import os
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
        if rendition_manifest.get("version") != 2:
            print(f"  [SKIP] incompatible rendition manifest: {rendition_manifest_key}")
            continue
        sections = rendition_manifest.get("sections", [])

        book = {
            "author": manifest.get("author", meta["author_slug"]),
            "author_slug": meta["author_slug"],
            "title": manifest.get("title", meta["title_slug"]),
            "title_slug": meta["title_slug"],
            "source": manifest.get("source", "unknown"),
            "rendition": rendition,
            "current_build": current_build,
            "total_duration_seconds": rendition_manifest.get("total_duration_seconds", 0),
            "section_count": len(sections),
            "has_cover": has_cover,
        }
        books.append(book)
        mins = book["total_duration_seconds"] / 60
        print(f"  {book['author']} - {book['title']} ({book['section_count']} sections, {mins:.1f} min)")

    return {
        "version": 2,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "books": books,
    }


def build_and_upload_catalog(*, dry_run: bool = False, client=None, bucket: str = R2_BUCKET,
                             api_base: str | None = None) -> dict:
    """Upload the R2 audit artifact and synchronize current D1 publication rows."""
    api = None
    if not dry_run:
        from openshelf.pipeline.job_consumer import JobAPI
        base = api_base or os.environ.get("OPENSHELF_API_BASE", "")
        if not base:
            raise ValueError("Catalog publication requires --api-base or OPENSHELF_API_BASE")
        api = JobAPI(base, os.environ.get("OPENSHELF_PC_TOKEN", ""))
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
    keys = [{"author_slug": book["author_slug"], "title_slug": book["title_slug"]}
            for book in catalog["books"]]
    for offset in range(0, len(keys), 10):
        batch = keys[offset:offset + 10]
        result = api.post("/internal/catalog/sync", {"books": batch})
        if result.get("synced") != len(batch):
            raise RuntimeError("Catalog sync did not index every publication; retry after resolving concurrent deletion or missing manifests")
    print(f"Synchronized {len(keys)} publication(s) to D1.")
    after = None
    while True:
        result = api.post("/internal/catalog/reconcile", {"after": after} if after else {})
        next_after = result.get("after")
        if not next_after:
            break
        if next_after == after:
            raise RuntimeError("Catalog reconciliation did not advance its cursor")
        after = next_after
    print("Reconciled the D1 publication catalog with R2.")
    return catalog


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="openshelf-pipeline ops catalog",
        description="Build and upload catalog.json from root R2 manifests.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print catalog without uploading")
    parser.add_argument("--api-base", help="Worker /api/v1 URL (or OPENSHELF_API_BASE)")
    args = parser.parse_args(argv)
    build_and_upload_catalog(dry_run=args.dry_run, api_base=args.api_base)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
