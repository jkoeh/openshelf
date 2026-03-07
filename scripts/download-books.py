#!/usr/bin/env python3
"""Download books from Project Gutenberg and Standard Ebooks.

Fetches EPUB files organized by author into a configurable output directory.
Uses only Python stdlib — no pip dependencies required.
"""

import argparse
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request


USER_AGENT = "BookScraper/1.0 (educational project)"
GUTENDEX_API = "https://gutendex.com/books"
SE_BASE = "https://standardebooks.org"


def sanitize(name):
    """Lowercase, replace non-alphanumeric runs with hyphens, strip edges."""
    s = name.lower().strip()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


def make_request(url, delay=0, accept=None):
    """Fetch a URL and return the response body as bytes."""
    headers = {"User-Agent": USER_AGENT}
    if accept:
        headers["Accept"] = accept
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read()
        if delay > 0:
            time.sleep(delay)
        return data
    except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
        print(f"  ERROR fetching {url}: {exc}")
        return None


# ---------------------------------------------------------------------------
# Project Gutenberg via Gutendex
# ---------------------------------------------------------------------------

def gutenberg_search(author=None, language=None, subject=None, delay=2):
    """Yield (author_name, title, epub_url) from the Gutendex API."""
    params = {}
    if author:
        params["search"] = author
    if language:
        params["languages"] = language
    if subject:
        params["topic"] = subject

    url = GUTENDEX_API
    if params:
        url += "?" + urllib.parse.urlencode(params)

    page = 1
    while url:
        print(f"  Gutenberg: fetching page {page} …")
        data = make_request(url, delay=delay)
        if data is None:
            break

        body = json.loads(data)
        for book in body.get("results", []):
            epub_url = book.get("formats", {}).get("application/epub+zip")
            if not epub_url:
                continue
            authors = book.get("authors", [])
            author_name = authors[0]["name"] if authors else "unknown"
            title = book.get("title", "untitled")
            yield author_name, title, epub_url

        url = body.get("next")
        page += 1


# ---------------------------------------------------------------------------
# Standard Ebooks via OPDS
# ---------------------------------------------------------------------------


def se_search(author=None, language=None, delay=2):
    """Yield (author_name, title, epub_url) from Standard Ebooks.

    Scrapes the public website listing pages. The OPDS feeds require
    authentication (patron access), so we use the HTML catalog instead.
    """
    # Build the listing URL with optional query filter
    base_url = "https://standardebooks.org/ebooks"
    params = {}
    if author:
        params["query"] = author
    page_num = 1

    while True:
        if page_num > 1:
            params["page"] = str(page_num)
        url = base_url
        if params:
            url += "?" + urllib.parse.urlencode(params)

        print(f"  Standard Ebooks: fetching page {page_num} ({url}) …")
        data = make_request(url, delay=delay)
        if data is None:
            break

        text = data.decode("utf-8", errors="replace")

        # Check for "No ebooks matched"
        if "No ebooks matched" in text:
            if page_num == 1:
                print("  Standard Ebooks: no results found.")
            break

        # Find ebook page links: /ebooks/author-slug/title-slug/translator-slug
        # These appear as about="/ebooks/..." on <li> elements with schema:Book
        ebook_paths = re.findall(
            r'about="(/ebooks/[a-z0-9][a-z0-9/_-]*)"', text
        )
        if not ebook_paths:
            # Also try href patterns for book links
            ebook_paths = re.findall(
                r'href="(/ebooks/[a-z][a-z0-9-]*/[a-z][a-z0-9-]*(?:/[a-z][a-z0-9-]*)*)"',
                text,
            )

        if not ebook_paths:
            break

        seen = set()
        found_any = False
        for path in ebook_paths:
            parts = path.strip("/").split("/")
            if len(parts) < 3 or parts[0] != "ebooks":
                continue
            if path in seen:
                continue
            seen.add(path)
            found_any = True

            # Extract author and title from the path slug
            # Format: /ebooks/author-slug/title-slug[/translator-slug]
            author_slug = parts[1]
            title_slug = parts[2]

            # Build the epub download URL directly (predictable pattern)
            # e.g. /ebooks/fyodor-dostoevsky/crime-and-punishment/constance-garnett/downloads/
            #       fyodor-dostoevsky_crime-and-punishment_constance-garnett.epub
            filename_parts = parts[1:]  # author, title, [translator...]
            filename = "_".join(filename_parts) + ".epub"
            epub_url = f"https://standardebooks.org{path}/downloads/{filename}"

            # Convert slugs to readable names
            author_name = author_slug.replace("-", " ").title()
            title_name = title_slug.replace("-", " ").title()

            # Apply author filter (may be needed when browsing full catalog)
            if author and author.lower() not in author_name.lower():
                continue

            yield author_name, title_name, epub_url

        # Check if there's a next page link
        if not found_any or f'page={page_num + 1}' not in text:
            break
        page_num += 1


# ---------------------------------------------------------------------------
# Download logic
# ---------------------------------------------------------------------------

def download_book(author_name, title, epub_url, source_dir, delay=2, dry_run=False):
    """Download a single EPUB into source_dir/author/title.epub."""
    author_slug = sanitize(author_name)
    title_slug = sanitize(title)
    if not title_slug:
        title_slug = "untitled"

    author_dir = os.path.join(source_dir, author_slug)
    filepath = os.path.join(author_dir, title_slug + ".epub")

    if dry_run:
        print(f"  [DRY RUN] {author_name} — {title}")
        print(f"            -> {filepath}")
        return True

    if os.path.exists(filepath):
        print(f"  [SKIP] {filepath} (already exists)")
        return True

    os.makedirs(author_dir, exist_ok=True)
    print(f"  [DOWNLOAD] {author_name} — {title}")
    print(f"             {epub_url}")

    data = make_request(epub_url, delay=delay)
    if data is None:
        print(f"  [FAIL] Could not download {epub_url}")
        return False

    with open(filepath, "wb") as f:
        f.write(data)
    print(f"  [OK] {filepath} ({len(data)} bytes)")
    return True


def run_gutenberg(args):
    """Download from Project Gutenberg."""
    source_dir = os.path.join(args.output, "gutenberg")
    total = 0
    downloaded = 0
    skipped = 0
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
            # Check if it was a skip or new download
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
