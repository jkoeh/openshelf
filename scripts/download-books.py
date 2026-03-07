#!/usr/bin/env python3
"""Download books from Project Gutenberg and Standard Ebooks.

Thin CLI entry point — all logic lives in openshelf.scrapers.
"""

import argparse
import os
import sys

# Allow running from the scripts/ directory without pip install
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from openshelf.scrapers.gutenberg import gutenberg_search
from openshelf.scrapers.http import download_book, sanitize
from openshelf.scrapers.standard_ebooks import se_search


def run_gutenberg(args):
    """Download from Project Gutenberg."""
    source_dir = os.path.join(args.output, "gutenberg")
    total = 0
    downloaded = 0
    failed = 0

    for author_name, title, epub_url in gutenberg_search(
        author=args.author,
        language=args.language,
        subject=args.subject,
        delay=args.delay,
    ):
        total += 1
        ok = download_book(author_name, title, epub_url, source_dir,
                           delay=args.delay, dry_run=args.dry_run)
        if args.dry_run:
            pass
        elif ok:
            author_slug = sanitize(author_name)
            title_slug = sanitize(title) or "untitled"
            filepath = os.path.join(source_dir, author_slug, title_slug + ".epub")
            if os.path.exists(filepath):
                downloaded += 1
        else:
            failed += 1

    print(f"\n  Gutenberg: {total} books found", end="")
    if not args.dry_run:
        print(f", {failed} failed", end="")
    print()


def run_standard_ebooks(args):
    """Download from Standard Ebooks."""
    source_dir = os.path.join(args.output, "standard-ebooks")
    total = 0
    failed = 0

    for author_name, title, epub_url in se_search(
        author=args.author,
        language=args.language,
        delay=args.delay,
    ):
        total += 1
        ok = download_book(author_name, title, epub_url, source_dir,
                           delay=args.delay, dry_run=args.dry_run)
        if not ok and not args.dry_run:
            failed += 1

    print(f"\n  Standard Ebooks: {total} books found", end="")
    if not args.dry_run:
        print(f", {failed} failed", end="")
    print()


def main():
    parser = argparse.ArgumentParser(
        description="Download EPUB books from Project Gutenberg and Standard Ebooks."
    )
    parser.add_argument(
        "--source", default="all",
        choices=["gutenberg", "standard-ebooks", "all"],
        help="Which source to download from (default: all)",
    )
    parser.add_argument(
        "--author", default=None,
        help="Filter by author name (case-insensitive substring match)",
    )
    parser.add_argument(
        "--language", default=None,
        help="Filter by language code, e.g. en, fr, de",
    )
    parser.add_argument(
        "--subject", default=None,
        help="Filter by subject/topic (Gutenberg only)",
    )
    parser.add_argument(
        "--output", default="raw-download/books",
        help="Base output directory (default: raw-download/books)",
    )
    parser.add_argument(
        "--delay", type=float, default=2,
        help="Seconds between HTTP requests (default: 2)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="List matching books without downloading",
    )
    args = parser.parse_args()

    if args.source in ("gutenberg", "all"):
        print("=== Project Gutenberg ===")
        run_gutenberg(args)

    if args.source in ("standard-ebooks", "all"):
        print("\n=== Standard Ebooks ===")
        run_standard_ebooks(args)

    print("\nDone.")


if __name__ == "__main__":
    main()
