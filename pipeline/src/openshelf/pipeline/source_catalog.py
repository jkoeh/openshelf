"""Bounded import of rights-verified Gutenberg and Standard Ebooks editions."""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import os
import re
import tarfile
import tempfile
import urllib.error
from pathlib import Path
from datetime import datetime, timezone
from xml.etree import ElementTree
from openshelf.pipeline.language import normalize_language

from openshelf.pipeline.job_consumer import (JobAPI, MAX_RIGHTS_BYTES,
                                             RightsNotVerified, public_domain_epub_url)

MAX_BOOKS_PER_RUN = 1_000
REQUIRED_COLUMNS = {"Text#", "Type", "Title", "Language", "Authors"}
MAX_RIGHTS_ARCHIVE_BYTES = 256 * 1024 * 1024
MAX_RDF_MEMBERS = 100_000
MAX_EXPANDED_RDF_BYTES = 3 * 1024 * 1024 * 1024


def catalog_books(path: Path, *, after_id: int = 0, max_books: int = 500, language: str = "all"):
    """Yield supported-language text records, never trusting a catalog URL."""
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
                languages = {normalize_language(part.strip()) for part in (row["Language"] or "").split(";")}
                selected = ("zh" if "zh" in languages else "en") if language == "all" else language
                if selected not in languages or selected not in {"en", "zh"}:
                    continue
                title = re.sub(r"\s+", " ", row["Title"] or "").strip()
                author = re.sub(r"\s+", " ", row["Authors"] or "Unknown").strip()
                if not title or len(title) > 300 or not author or len(author) > 200:
                    continue
                yield {"source_id": f"gutenberg:{number}", "title": title,
                       "author": author, "language": selected}
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
                data = source.read(MAX_RIGHTS_BYTES + 1)
                url = public_domain_epub_url(data, source_id)
                language_nodes = ElementTree.fromstring(data).findall(".//{http://purl.org/dc/terms/}language//{http://www.w3.org/1999/02/22-rdf-syntax-ns#}value")
                languages = {normalize_language(node.text or "") for node in language_nodes}
                if wanted[source_id].get("language") and wanted[source_id]["language"] not in languages:
                    continue
                from openshelf.pipeline.gutenberg_source import rights_url
                approved[source_id] = {"epub_url": url, "provenance": {
                    "source_url": url, "rights_url": rights_url(source_id),
                    "rights_statement": "Public domain in the USA.", "rights_sha256": hashlib.sha256(data).hexdigest(),
                    "verified_at": datetime.now(timezone.utc).isoformat(), "rights_archive": archive_path.name}}
            except RightsNotVerified:
                pass
            if len(seen) == len(wanted):
                break
    for book in books:
        evidence = approved.get(book["source_id"])
        if evidence:
            yield {**book, **evidence}


def validate_reading_copy(path: Path, book: dict) -> None:
    """Trust boundary: validate before either indexing or uploading reading bytes."""
    import zipfile
    from openshelf.pipeline.job_consumer import validate_local_epub, check_epub_rights, check_spoken_text
    from openshelf.pipeline.epub_parser import read_book_metadata
    if path.stat().st_size > 32 * 1024 * 1024:
        raise ValueError("EPUB exceeds reader size limit")
    validate_local_epub(path)
    with zipfile.ZipFile(path) as archive:
        if sum(entry.file_size for entry in archive.infolist()) > 64 * 1024 * 1024:
            raise ValueError("EPUB expands beyond reader size limit")
        if archive.read("mimetype") != b"application/epub+zip":
            raise ValueError("invalid EPUB media type")
    check_epub_rights(path, book["source_id"])
    language = normalize_language(read_book_metadata(str(path)).get("language", "en"))
    if language not in {"en", "zh"} or language != book.get("language", "en"):
        raise ValueError("EPUB language does not match source metadata")
    check_spoken_text(path)


def cache_book(api: JobAPI, book: dict, path: Path | None = None) -> bool:
    from openshelf.pipeline.job_consumer import fetch_epub
    if api.source_epub_cached(book["source_id"]):
        return False
    with tempfile.TemporaryDirectory() as directory:
        epub = path or Path(directory) / "book.epub"
        if path is None:
            fetch_epub(book["epub_url"], book["source_id"], epub)
        validate_reading_copy(epub, book)
        api.post("/internal/source-books/sync", {"books": [book]})
        api.upload_source_epub(book["source_id"], epub)
    return True


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="openshelf-pipeline books sync-catalog")
    parser.add_argument("--source", choices=["gutenberg", "standard-ebooks", "wikisource"], default="gutenberg")
    parser.add_argument("--language", choices=["en", "zh", "all"], default="all")
    parser.add_argument("--editions", type=Path)
    parser.add_argument("--catalog", type=Path)
    parser.add_argument("--rights-archive", type=Path)
    parser.add_argument("--after-page", type=int, default=0)
    parser.add_argument("--max-pages", type=int, default=20)
    parser.add_argument("--api-base", required=True)
    parser.add_argument("--after-id", type=int, default=0)
    parser.add_argument("--max-books", type=int, default=500)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if args.after_id < 0 or not 1 <= args.max_books <= MAX_BOOKS_PER_RUN:
        parser.error("--after-id must be nonnegative and --max-books must be 1..1000")
    if args.after_page < 0 or not 1 <= args.max_pages <= 50:
        parser.error("--after-page must be nonnegative and --max-pages must be 1..50")
    if args.source == "gutenberg" and (not args.catalog or not args.rights_archive):
        parser.error("Gutenberg requires --catalog and --rights-archive")
    if args.source == "wikisource" and not args.editions:
        parser.error("Wikisource requires --editions")
    if args.source == "standard-ebooks":
        from openshelf.pipeline.standard_ebooks_source import catalog_books as se_books
        candidates = se_books(max_books=args.max_books, after_page=args.after_page, max_pages=args.max_pages)
    elif args.source == "wikisource":
        from openshelf.pipeline.wikisource_source import read_editions
        candidates = read_editions(args.editions, args.max_books)
    else:
        candidates = verified_books(catalog_books(args.catalog, after_id=args.after_id,
                                                 max_books=args.max_books, language=args.language), args.rights_archive)
    api = None if args.dry_run else JobAPI(args.api_base, os.environ.get("OPENSHELF_PC_TOKEN", ""))
    total = 0
    failures = 0
    for book in candidates:
        try:
            with tempfile.TemporaryDirectory() as directory:
                path = None
                if args.source == "wikisource":
                    if args.language != "all" and book.get("language") != args.language:
                        continue
                    from openshelf.pipeline.wikisource_source import build_edition
                    path = None if args.dry_run else Path(directory) / "book.epub"
                    book = build_edition(book, path)
                if args.dry_run:
                    verb = "Would index"
                else:
                    verb = "Indexed and cached" if cache_book(api, book, path) else "Already cached"
                total += 1
                print(f"{verb} {total}; source ID {book['source_id']}")
        except urllib.error.HTTPError as exc:
            if exc.code in (403, 429):
                print(f"Stopped after {total} cached editions: HTTP {exc.code}. Retry the import later.")
                return 1
            failures += 1
            print(f"Skipped edition: HTTP {exc.code}")
        except (ValueError, OSError, KeyError) as exc:
            failures += 1
            print(f"Skipped edition: {type(exc).__name__}: {exc}")
            if "maxlag" in str(exc):
                return 1
    if not total:
        print("No rights-verified EPUBs in the selected catalog range")
    return int(failures > 0)
