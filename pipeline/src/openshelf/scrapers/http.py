"""Shared HTTP utilities and helpers for scrapers."""

import http.cookiejar
import re
import time
import unicodedata
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


def normalize_search_text(value: str) -> str:
    """Normalize loose user/catalog text for search-result filtering."""
    value = unicodedata.normalize("NFKD", value.casefold())
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = re.sub(r"['\u2018\u2019\u02bc]", "", value)
    value = re.sub(r"[^\w]+", " ", value, flags=re.UNICODE)
    return " ".join(value.split())


def matches_filter(filter_value: str | None, candidate: str) -> bool:
    """Return true when every normalized filter token appears in candidate."""
    if not filter_value:
        return True
    needle = normalize_search_text(filter_value)
    haystack = normalize_search_text(candidate)
    if not needle:
        return True
    if needle in haystack:
        return True
    return all(token in haystack.split() for token in needle.split())


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


def source_id_from_url(url: str) -> str:
    from urllib.parse import urlsplit
    from openshelf.pipeline.gutenberg_source import mirror_url
    from openshelf.pipeline.standard_ebooks_source import validate_url
    parsed = urlsplit(url)
    if parsed.hostname == "standardebooks.org" and "/downloads/" in parsed.path:
        source_id = "standardebooks:" + parsed.path.removeprefix("/ebooks/").split("/downloads/", 1)[0]
        validate_url(url, source_id)
        return source_id
    match = re.match(r"/(?:ebooks|cache/epub)/([1-9][0-9]*)(?:[./]|$)", parsed.path)
    if not match:
        raise ValueError("source EPUB locator has no exact edition ID")
    source_id = "gutenberg:" + match.group(1)
    mirror_url(url, source_id)
    return source_id


def download_path(author_name, title, epub_url, source_dir):
    import os
    from openshelf.pipeline.standard_ebooks_source import title_slug
    source_id = source_id_from_url(epub_url)
    return os.path.join(source_dir, sanitize(author_name) or "unknown", title_slug(title, source_id) + ".epub")


def download_book(author_name, title, epub_url, source_dir, delay=2, dry_run=False):
    """Download a single EPUB into source_dir/author/title.epub."""
    import os

    try:
        source_id = source_id_from_url(epub_url)
        filepath = download_path(author_name, title, epub_url, source_dir)
    except ValueError as exc:
        print(f"  [FAIL] {exc}")
        return False
    author_dir = os.path.dirname(filepath)

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

    from pathlib import Path
    from openshelf.pipeline.job_consumer import fetch_epub, check_epub_rights, verify_public_domain
    try:
        verify_public_domain(source_id)
        fetch_epub(epub_url, source_id, Path(filepath))
        check_epub_rights(Path(filepath), source_id)
    except (ValueError, OSError) as exc:
        Path(filepath).unlink(missing_ok=True)
        print(f"  [FAIL] Source validation: {type(exc).__name__}")
        return False
    finally:
        if delay > 0:
            time.sleep(delay)
    print(f"  [OK] {filepath} ({os.path.getsize(filepath)} bytes)")
    return True
