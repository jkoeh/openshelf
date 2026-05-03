#!/usr/bin/env python3
"""Reprocess an already-downloaded book end-to-end with force-overwrite.

Locates the local EPUB under download/books/, then runs convert-book.py with
`--upload --force` so all local artifacts (audio, manifest, chapter_data) and
their R2 counterparts are regenerated. Does NOT redownload the EPUB.

Usage:
    python3 pipeline/scripts/reprocess-book.py Kafka Metamorphosis
"""

from __future__ import annotations

import argparse
import glob
import os
import subprocess
import sys

# Allow running from the scripts/ directory without pip install
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from openshelf.scrapers.http import sanitize


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DEFAULT_DOWNLOAD_DIR = os.path.join(REPO_ROOT, "download", "books")


def find_local_epub(author: str, title: str, download_dir: str) -> str | None:
    """Return the local EPUB path for (author, title) or None if not found.

    Expected layout: download/books/<source>/<author-slug>/<title-slug>.epub
    Author and title are matched as substrings of the directory/filename slugs
    because sources differ on naming (Gutenberg yields "kafka-franz", Standard
    Ebooks yields "franz-kafka") and the user typically passes a partial name.
    """
    author_slug = sanitize(author)
    title_slug = sanitize(title)

    all_epubs = glob.glob(os.path.join(download_dir, "*", "*", "*.epub"))
    matches = [
        p for p in all_epubs
        if author_slug in os.path.basename(os.path.dirname(p)).lower()
        and title_slug in os.path.basename(p).lower()
    ]
    return matches[0] if matches else None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Reprocess a previously-downloaded book end-to-end (overwrites local + R2)."
    )
    parser.add_argument("author", help="Author name, e.g. Kafka")
    parser.add_argument("title", help="Book title, e.g. Metamorphosis")
    parser.add_argument(
        "--download-dir",
        default=DEFAULT_DOWNLOAD_DIR,
        help=f"Where to look for the local EPUB (default: {DEFAULT_DOWNLOAD_DIR})",
    )
    args = parser.parse_args()

    epub_path = find_local_epub(args.author, args.title, args.download_dir)
    if not epub_path:
        print(
            f"Error: no local EPUB found for {args.author} / {args.title} under {args.download_dir}",
            file=sys.stderr,
        )
        print(
            "Hint: run process-books.py to download it first.",
            file=sys.stderr,
        )
        return 1

    print(f"Reprocessing local EPUB: {epub_path}\n")

    cmd = [
        "uv",
        "run",
        "--python",
        "3.11",
        "--with-requirements",
        "pipeline/requirements.txt",
        "--with",
        "python-dotenv",
        "python",
        "pipeline/scripts/process-books.py",
        "--epub",
        epub_path,
        "--upload",
        "--force",
    ]

    return subprocess.run(cmd).returncode


if __name__ == "__main__":
    raise SystemExit(main())
