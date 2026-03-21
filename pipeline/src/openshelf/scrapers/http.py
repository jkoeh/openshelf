"""Shared HTTP utilities and helpers for scrapers."""

import http.cookiejar
import re
import time
import urllib.error
import urllib.request

from openshelf.config import USER_AGENT

# Shared cookie jar so session cookies persist across requests
_cookie_jar = http.cookiejar.CookieJar()
_opener = urllib.request.build_opener(
    urllib.request.HTTPCookieProcessor(_cookie_jar)
)


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
        with _opener.open(req, timeout=30) as resp:
            data = resp.read()
        if delay > 0:
            time.sleep(delay)
        return data
    except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
        print(f"  ERROR fetching {url}: {exc}")
        return None


def _is_epub(data: bytes) -> bool:
    """Check if data starts with ZIP/EPUB magic bytes."""
    return data[:4] == b"PK\x03\x04"


def _force_se_cookie() -> None:
    """Force the Standard Ebooks download-count cookie high enough to get files.

    SE requires download-count >= 5 before serving actual EPUB files.
    We clear any existing download-count cookie and set ours.
    """
    # Remove any existing download-count cookies
    to_remove = [c for c in _cookie_jar if c.name == "download-count"]
    for c in to_remove:
        _cookie_jar.clear(c.domain, c.path, c.name)

    cookie = http.cookiejar.Cookie(
        version=0, name="download-count", value="5",
        port=None, port_specified=False,
        domain=".standardebooks.org", domain_specified=True,
        domain_initial_dot=True,
        path="/", path_specified=True,
        secure=True, expires=None, discard=True,
        comment=None, comment_url=None, rest={"SameSite": "Lax"},
    )
    _cookie_jar.set_cookie(cookie)


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

    # Standard Ebooks serves an HTML page instead of the file until a
    # download-count cookie >= 5. Detect and retry with the cookie set.
    if not _is_epub(data) and b"standardebooks.org" in data[:2000]:
        _force_se_cookie()
        data = make_request(epub_url, delay=delay)
        if data is None or not _is_epub(data):
            print(f"  [FAIL] Server returned HTML instead of EPUB: {epub_url}")
            return False

    with open(filepath, "wb") as f:
        f.write(data)
    print(f"  [OK] {filepath} ({len(data)} bytes)")
    return True
