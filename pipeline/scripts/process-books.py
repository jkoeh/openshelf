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
import logging
import os
import subprocess
import sys

# Allow running from the scripts/ directory without pip install
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from openshelf.pipeline.logging_utils import (
    Heartbeat,
    configure_console_output,
    configure_pipeline_logging,
)
from openshelf.scrapers.gutenberg import gutenberg_search
from openshelf.scrapers.http import download_book, matches_filter, sanitize
from openshelf.scrapers.standard_ebooks import se_search


logger = logging.getLogger("openshelf.process_books")


def search_and_download(args):
    """Search sources and download matching EPUBs. Returns list of (epub_path, source_name)."""
    downloaded = []
    query = args.author or args.book
    logger.info("Starting search source=%s query=%r", args.source, query)

    if args.source in ("gutenberg", "all"):
        source_name = "gutenberg"
        source_dir = os.path.join(args.download_dir, source_name)
        print("=== Project Gutenberg ===")
        with Heartbeat(logger, "Searching Project Gutenberg"):
            for author_name, title, epub_url in gutenberg_search(author=query, delay=args.delay):
                if not matches_filter(args.book, title):
                    logger.debug("Skipping Gutenberg title filter: %s", title)
                    continue
                if not matches_filter(args.author, author_name):
                    logger.debug("Skipping Gutenberg author filter: %s", author_name)
                    continue
                logger.info("Matched Gutenberg book: %s - %s", author_name, title)
                print(f"  {author_name} - {title}")
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
        with Heartbeat(logger, "Searching Standard Ebooks"):
            for author_name, title, epub_url in se_search(author=query, delay=args.delay):
                if not matches_filter(args.book, title):
                    logger.debug("Skipping Standard Ebooks title filter: %s", title)
                    continue
                if not matches_filter(args.author, author_name):
                    logger.debug("Skipping Standard Ebooks author filter: %s", author_name)
                    continue
                logger.info("Matched Standard Ebooks book: %s - %s", author_name, title)
                print(f"  {author_name} - {title}")
                ok = download_book(author_name, title, epub_url, source_dir, delay=args.delay)
                if ok:
                    author_slug = sanitize(author_name)
                    title_slug = sanitize(title) or "untitled"
                    epub_path = os.path.join(source_dir, author_slug, title_slug + ".epub")
                    if os.path.isfile(epub_path):
                        downloaded.append((epub_path, source_name))

    logger.info("Search/download complete: %d book(s)", len(downloaded))
    return downloaded


def convert_book(epub_path, source_name, args):
    """Run the DAG full-book runner on a single EPUB."""
    src_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
    cmd = [
        sys.executable,
        "-m",
        "openshelf.pipeline.dag_cli",
        "run",
        "--epub",
        epub_path,
        "--source",
        source_name,
        "--output",
        args.output,
    ]

    if args.engine:
        cmd += ["--engine", args.engine]
    if args.voice:
        cmd += ["--voice", args.voice]
    if args.rendition:
        cmd += ["--rendition", args.rendition]
    if args.cast_mode:
        cmd += ["--cast-mode", args.cast_mode]
    if args.performance_direction:
        cmd += ["--performance-direction", args.performance_direction]
    if args.device:
        cmd += ["--device", args.device]
    if getattr(args, "chapters", None):
        cmd += ["--chapters", args.chapters]
    if args.keep_wav:
        cmd.append("--keep-wav")
    if args.upload:
        cmd.append("--upload")
    if args.dry_run:
        cmd.append("--dry-run")
    if getattr(args, "build_id", None):
        cmd += ["--build-id", args.build_id]
    if getattr(args, "resume", False):
        cmd.append("--resume")
    if args.force:
        cmd.append("--force")
    cmd += ["--log-dir", args.log_dir]

    env = os.environ.copy()
    prior_pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        src_dir if not prior_pythonpath else src_dir + os.pathsep + prior_pythonpath
    )
    logger.info("Starting DAG runner subprocess: %s", " ".join(cmd))
    return subprocess.run(cmd, env=env).returncode


def refresh_catalog(args):
    """Run build-catalog.py after successful uploads."""
    script = os.path.join(os.path.dirname(__file__), "build-catalog.py")
    cmd = [sys.executable, script]
    logger.info("Starting build-catalog subprocess: %s", " ".join(cmd))
    return subprocess.run(cmd).returncode


def main():
    configure_console_output()

    parser = argparse.ArgumentParser(description="Download and convert books to audio")
    parser.add_argument("--author", help="Filter by author name")
    parser.add_argument("--book", help="Filter by book title")
    parser.add_argument("--epub", help="Path to a local EPUB file (skips download)")
    parser.add_argument("--source", default="all", choices=["gutenberg", "standard-ebooks", "all"])
    parser.add_argument("--output", "-o", default="audio", help="Audio output directory (default: audio/)")
    parser.add_argument("--engine", default=None, help="TTS engine (default: TTS_ENGINE)")
    parser.add_argument("--voice", default=None, help="Narrator voice override")
    parser.add_argument("--rendition", default=None, help="Rendition slug override")
    parser.add_argument("--cast-mode", default=None, choices=["solo", "multicast"], help="Voice casting mode")
    parser.add_argument(
        "--performance-direction",
        default=None,
        choices=["batched", "chunk", "off"],
        help="Performance/emotion direction mode for supporting engines",
    )
    parser.add_argument("--device", default=None, help="Device: cuda, mps, cpu (default: auto)")
    parser.add_argument(
        "--chapters",
        default=None,
        help="Local sample filter: chapter number/ranges to generate, e.g. 2 or 2,4-5",
    )
    parser.add_argument("--dry-run", action="store_true", help="Download + parse only, no audio")
    parser.add_argument("--keep-wav", action="store_true", help="Keep WAV files after encoding")
    parser.add_argument("--upload", action="store_true", help="Upload to R2 after conversion")
    parser.add_argument("--build-id", default=None, help="Target 16-hex build ID for resume/force runs")
    parser.add_argument("--resume", action="store_true", help="Resume an existing build only if run.json matches")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Regenerate audio + chapter_data under a fresh build prefix",
    )
    parser.add_argument("--delay", type=float, default=2, help="Seconds between HTTP requests (default: 2)")
    parser.add_argument("--download-dir", default="download/books", help="Download directory (default: download/books)")
    parser.add_argument("--log-dir", default="logs", help="Local log directory (default: logs/)")
    args = parser.parse_args()

    run_name = "process-books"
    if args.author:
        run_name += f"-{sanitize(args.author)}"
    if args.book:
        run_name += f"-{sanitize(args.book)}"
    log_path = configure_pipeline_logging(run_name, args.log_dir)
    print(f"Log file: {log_path}")
    logger.info("process-books started")

    if not args.author and not args.book and not args.epub:
        parser.error("at least one of --author, --book, or --epub is required")

    # If --epub is given, skip search/download entirely
    if args.epub:
        epub_path = os.path.abspath(args.epub)
        if not os.path.isfile(epub_path):
            print(f"Error: file not found: {epub_path}")
            logger.error("Local EPUB not found: %s", epub_path)
            sys.exit(1)
        source_name = "local"
        for src in ("gutenberg", "standard-ebooks"):
            if src in epub_path:
                source_name = src
                break
        downloaded = [(epub_path, source_name)]
        print(f"Using local EPUB: {epub_path}\n")
        logger.info("Using local EPUB: %s source=%s", epub_path, source_name)
    else:
        print("Phase 1: Downloading books ...\n")
        downloaded = search_and_download(args)

        if not downloaded:
            print("\nNo books downloaded.")
            logger.warning("No books downloaded")
            sys.exit(1)

        print(f"\n{len(downloaded)} book(s) downloaded.\n")

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
            logger.info("DAG run succeeded: %s", epub_path)
        else:
            failed += 1
            logger.error("DAG run failed rc=%s: %s", rc, epub_path)
            print(f"\nError: DAG runner exited with code {rc}")

    print(f"\n{'=' * 60}")
    print(f"Done. {succeeded} succeeded, {failed} failed out of {len(downloaded)} book(s).")
    logger.info("process-books complete: %d succeeded, %d failed", succeeded, failed)
    if failed:
        sys.exit(1)

    if args.upload and succeeded:
        print("\nRefreshing catalog.json ...")
        rc = refresh_catalog(args)
        if rc != 0:
            logger.error("build-catalog.py failed rc=%s", rc)
            print(f"Error: build-catalog.py exited with code {rc}")
            sys.exit(rc)
        logger.info("catalog refresh succeeded")
        print("Catalog refreshed.")


if __name__ == "__main__":
    main()
