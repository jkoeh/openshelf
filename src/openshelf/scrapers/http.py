"""Shared HTTP utilities and helpers for scrapers."""

import re
import time
import urllib.error
import urllib.request

from openshelf.config import USER_AGENT


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


def download_book(author_name, title, epub_url, source_dir, delay=2, dry_run=False):
    """Download a single EPUB into source_dir/author/title.epub."""
    import os

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
