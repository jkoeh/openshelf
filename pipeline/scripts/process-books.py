#!/usr/bin/env python3
"""Download and convert books to audio end-to-end.

Usage:
    python3 pipeline/scripts/process-books.py --author "Kafka"
    python3 pipeline/scripts/process-books.py --book "Romeo and Juliet"
    python3 pipeline/scripts/process-books.py --author "Shakespeare" --book "Romeo"
    python3 pipeline/scripts/process-books.py --author "Kafka" --upload
    python3 pipeline/scripts/process-books.py --author "Kafka" --dry-run
    python3 pipeline/scripts/process-books.py --epub path/to/book.epub --upload
"""

import argparse
import os
import subprocess
import sys

# Allow running from the scripts/ directory without pip install
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from openshelf.scrapers.gutenberg import gutenberg_search
from openshelf.scrapers.http import download_book, sanitize
from openshelf.scrapers.standard_ebooks import se_search


def search_and_download(args):
    """Search sources and download matching EPUBs. Returns list of (epub_path, source_name)."""
    downloaded = []
    query = args.author or args.book

    if args.source in ("gutenberg", "all"):
        source_name = "gutenberg"
        source_dir = os.path.join(args.download_dir, source_name)
        print("=== Project Gutenberg ===")
        for author_name, title, epub_url in gutenberg_search(author=query, delay=args.delay):
            if args.book and args.book.lower() not in title.lower():
                continue
            if args.author and args.author.lower() not in author_name.lower():
                continue
            print(f"  {author_name} — {title}")
            ok = download_book(author_name, title, epub_url, source_dir, delay=args.delay)
            if ok:
                author_slug = sanitize(author_name)
                title_slug = sanitize(title) or "untitled"
                epub_path = os.path.join(source_dir, author_slug, title_slug + ".epub")
                if os.path.isfile(epub_path):
                    downloaded.append((epub_path, source_name))

    if args.source in ("standard-ebooks", "all"):
        source_name = "standard-ebooks"
        source_dir = os.path.join(args.download_dir, source_name)
        print("\n=== Standard Ebooks ===")
        for author_name, title, epub_url in se_search(author=query, delay=args.delay):
            if args.book and args.book.lower() not in title.lower():
                continue
            if args.author and args.author.lower() not in author_name.lower():
                continue
            print(f"  {author_name} — {title}")
            ok = download_book(author_name, title, epub_url, source_dir, delay=args.delay)
            if ok:
                author_slug = sanitize(author_name)
                title_slug = sanitize(title) or "untitled"
                epub_path = os.path.join(source_dir, author_slug, title_slug + ".epub")
                if os.path.isfile(epub_path):
                    downloaded.append((epub_path, source_name))

    return downloaded


def convert_book(epub_path, source_name, args):
    """Run convert-book.py on a single EPUB."""
    script = os.path.join(os.path.dirname(__file__), "convert-book.py")
    cmd = [sys.executable, script, epub_path, "--source", source_name, "--output", args.output]

    if args.voice:
        cmd += ["--voice", args.voice]
    if args.device:
        cmd += ["--device", args.device]
    if args.keep_wav:
        cmd.append("--keep-wav")
    if args.upload:
        cmd.append("--upload")
    if args.dry_run:
        cmd.append("--dry-run")
    if args.force:
        cmd.append("--force")

    return subprocess.run(cmd).returncode


def main():
    parser = argparse.ArgumentParser(description="Download and convert books to audio")
    parser.add_argument("--author", help="Filter by author name")
    parser.add_argument("--book", help="Filter by book title")
    parser.add_argument("--epub", help="Path to a local EPUB file (skips download)")
    parser.add_argument("--source", default="all", choices=["gutenberg", "standard-ebooks", "all"])
    parser.add_argument("--output", "-o", default="audio", help="Audio output directory (default: audio/)")
    parser.add_argument("--voice", default=None, help="Kokoro voice ID")
    parser.add_argument("--device", default=None, help="Device: cuda, mps, cpu (default: auto)")
    parser.add_argument("--dry-run", action="store_true", help="Download + parse only, no audio")
    parser.add_argument("--keep-wav", action="store_true", help="Keep WAV files after encoding")
    parser.add_argument("--upload", action="store_true", help="Upload to R2 after conversion")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Regenerate audio + chapter_data under a fresh build prefix",
    )
    parser.add_argument("--delay", type=float, default=2, help="Seconds between HTTP requests (default: 2)")
    parser.add_argument("--download-dir", default="download/books", help="Download directory (default: download/books)")
    args = parser.parse_args()

    if not args.author and not args.book and not args.epub:
        parser.error("at least one of --author, --book, or --epub is required")

    # If --epub is given, skip search/download entirely
    if args.epub:
        epub_path = os.path.abspath(args.epub)
        if not os.path.isfile(epub_path):
            print(f"Error: file not found: {epub_path}")
            sys.exit(1)
        # Infer source from path, default to "local"
        source_name = "local"
        for src in ("gutenberg", "standard-ebooks"):
            if src in epub_path:
                source_name = src
                break
        downloaded = [(epub_path, source_name)]
        print(f"Using local EPUB: {epub_path}\n")
    else:
        # Phase 1: Search and download
        print("Phase 1: Downloading books ...\n")
        downloaded = search_and_download(args)

        if not downloaded:
            print("\nNo books downloaded.")
            return

        print(f"\n{len(downloaded)} book(s) downloaded.\n")

    # Phase 2: Convert each book
    print("Phase 2: Converting books ...\n")
    succeeded = 0
    failed = 0

    for i, (epub_path, source_name) in enumerate(downloaded, 1):
        print(f"\n{'=' * 60}")
        print(f"[{i}/{len(downloaded)}] {epub_path}")
        print(f"{'=' * 60}\n")

        rc = convert_book(epub_path, source_name, args)
        if rc == 0:
            succeeded += 1
        else:
            failed += 1
            print(f"\nError: convert-book.py exited with code {rc}")

    print(f"\n{'=' * 60}")
    print(f"Done. {succeeded} succeeded, {failed} failed out of {len(downloaded)} book(s).")


if __name__ == "__main__":
    main()
