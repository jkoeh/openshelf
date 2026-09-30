"""Bounded import of Project Gutenberg's official CSV and rights-bearing RDF."""

from __future__ import annotations

import argparse
import csv
import gzip
import os
import re
import tarfile
from pathlib import Path

from openshelf.pipeline.job_consumer import (JobAPI, MAX_RIGHTS_BYTES,
                                             RightsNotVerified, public_domain_epub_url)

MAX_BOOKS_PER_RUN = 1_000
BATCH_SIZE = 50
REQUIRED_COLUMNS = {"Text#", "Type", "Title", "Language", "Authors"}
MAX_RIGHTS_ARCHIVE_BYTES = 256 * 1024 * 1024
MAX_RDF_MEMBERS = 100_000
MAX_EXPANDED_RDF_BYTES = 3 * 1024 * 1024 * 1024


def catalog_books(path: Path, *, after_id: int = 0, max_books: int = 500):
    """Yield validated English text records, never trusting a catalog URL."""
    if path.stat().st_size > (16 if path.suffix == ".gz" else 64) * 1024 * 1024:
        raise ValueError("catalog file exceeds size limit")
    opener = gzip.open if path.suffix == ".gz" else open
    old_limit = csv.field_size_limit()
    csv.field_size_limit(8_192)
    try:
        with opener(path, "rt", encoding="utf-8-sig", newline="") as source:
            rows = csv.DictReader(source)
            if not rows.fieldnames or not REQUIRED_COLUMNS.issubset(rows.fieldnames):
                raise ValueError("not a Gutenberg CSV catalog")
            emitted = 0
            for row in rows:
                raw_id = row["Text#"] or ""
                if not raw_id.isascii() or not raw_id.isdecimal():
                    continue
                number = int(raw_id)
                if number <= after_id or number < 1 or row["Type"] != "Text":
                    continue
                if "en" not in {part.strip() for part in (row["Language"] or "").split(";")}:
                    continue
                title = re.sub(r"\s+", " ", row["Title"] or "").strip()
                author = re.sub(r"\s+", " ", row["Authors"] or "Unknown").strip()
                if not title or len(title) > 300 or not author or len(author) > 200:
                    continue
                yield {"source_id": f"gutenberg:{number}", "title": title,
                       "author": author}
                emitted += 1
                if emitted >= max_books:
                    break
    finally:
        csv.field_size_limit(old_limit)


def verified_books(candidates, archive_path: Path):
    """Join bounded CSV candidates to official RDF without extracting the tar."""
    books = list(candidates)
    if not books:
        return
    if archive_path.stat().st_size > MAX_RIGHTS_ARCHIVE_BYTES:
        raise ValueError("rights archive exceeds size limit")
    wanted = {book["source_id"]: book for book in books}
    approved = {}
    seen = set()
    expanded = 0
    with tarfile.open(archive_path, "r:bz2") as archive:
        for count, member in enumerate(archive, 1):
            if count > MAX_RDF_MEMBERS:
                raise ValueError("rights archive has too many members")
            expanded += member.size
            if expanded > MAX_EXPANDED_RDF_BYTES:
                raise ValueError("rights archive expands beyond limit")
            match = re.fullmatch(r"cache/epub/([1-9][0-9]*)/pg\1\.rdf", member.name)
            if not match:
                continue
            source_id = f"gutenberg:{match.group(1)}"
            if source_id not in wanted:
                continue
            seen.add(source_id)
            if not member.isfile() or member.size > MAX_RIGHTS_BYTES:
                continue
            source = archive.extractfile(member)
            if source is None:
                continue
            try:
                approved[source_id] = public_domain_epub_url(source.read(MAX_RIGHTS_BYTES + 1), source_id)
            except RightsNotVerified:
                pass
            if len(seen) == len(wanted):
                break
    for book in books:
        epub_url = approved.get(book["source_id"])
        if epub_url:
            yield {**book, "epub_url": epub_url}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="openshelf-pipeline books sync-catalog")
    parser.add_argument("--catalog", required=True, type=Path)
    parser.add_argument("--rights-archive", required=True, type=Path)
    parser.add_argument("--api-base", required=True)
    parser.add_argument("--after-id", type=int, default=0)
    parser.add_argument("--max-books", type=int, default=500)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if args.after_id < 0 or not 1 <= args.max_books <= MAX_BOOKS_PER_RUN:
        parser.error("--after-id must be nonnegative and --max-books must be 1..1000")
    api = None if args.dry_run else JobAPI(args.api_base, os.environ.get("OPENSHELF_PC_TOKEN", ""))
    batch = []
    total = 0
    for book in verified_books(catalog_books(args.catalog, after_id=args.after_id,
                                             max_books=args.max_books), args.rights_archive):
        batch.append(book)
        if len(batch) == BATCH_SIZE:
            if api:
                api.post("/internal/source-books/sync", {"books": batch})
            total += len(batch)
            verb = "Would index" if args.dry_run else "Indexed"
            print(f"{verb} {total}; last Gutenberg ID {batch[-1]['source_id'].split(':')[1]}")
            batch = []
    if batch:
        if api:
            api.post("/internal/source-books/sync", {"books": batch})
        total += len(batch)
        verb = "Would index" if args.dry_run else "Indexed"
        print(f"{verb} {total}; last Gutenberg ID {batch[-1]['source_id'].split(':')[1]}")
    if not total:
        print("No rights-verified EPUBs in the selected catalog range")
    return 0
