"""Bounded official Standard Ebooks edition metadata and download identity."""

import hashlib
import html
import re
import time
import urllib.parse
import urllib.request

from openshelf.scrapers.standard_ebooks import _iter_downloadable_ebook_paths

BASE = "https://standardebooks.org"
PATH_RE = re.compile(r"[a-z0-9]+(?:[-_][a-z0-9]+)*(?:/[a-z0-9]+(?:[-_][a-z0-9]+)*){1,5}")
MAX_PAGE_BYTES = 2 * 1024 * 1024


def edition_path(source_id):
    path = source_id.removeprefix("standardebooks:")
    if not source_id.startswith("standardebooks:") or len(source_id) > 300 or not PATH_RE.fullmatch(path):
        raise ValueError("invalid Standard Ebooks edition ID")
    return path


def epub_url(source_id):
    path = edition_path(source_id)
    filename = "_".join(part.replace("_", "-") for part in path.split("/"))
    return f"{BASE}/ebooks/{path}/downloads/{filename}.epub?source=download"


def validate_url(url, source_id):
    expected = urllib.parse.urlsplit(epub_url(source_id))
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme != "https" or parsed.netloc != "standardebooks.org"
            or parsed.path != expected.path or parsed.query not in ("", "source=download")
            or parsed.fragment):
        raise ValueError("EPUB URL does not match Standard Ebooks edition")
    return url


def title_slug(title, source_id):
    from openshelf.scrapers.http import sanitize
    title = sanitize(title) or "untitled"
    if source_id.startswith("gutenberg:"):
        return title + "-g" + source_id.split(":")[1]
    if re.fullmatch(r"ownerupload:[a-f0-9-]{36}", source_id):
        return title + "-upload-" + source_id.split(":")[1].replace("-", "")
    if re.fullmatch(r"wikisource:(en|zh):[1-9][0-9]*:[1-9][0-9]*:[a-f0-9]{16}", source_id):
        return title + "-ws-" + hashlib.sha256(source_id.encode()).hexdigest()[:12]
    edition_path(source_id)
    return title + "-se-" + hashlib.sha256(source_id.encode()).hexdigest()[:12]


class PageRedirects(urllib.request.HTTPRedirectHandler):
    def __init__(self):
        self.redirects = 0

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urllib.parse.urlsplit(newurl)
        if parsed.scheme != "https" or parsed.netloc != "standardebooks.org" or parsed.fragment:
            raise ValueError("untrusted Standard Ebooks page redirect")
        self.redirects += 1
        if self.redirects > 3:
            raise ValueError("too many Standard Ebooks page redirects")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_page(url, *, delay=2):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.netloc != "standardebooks.org":
        raise ValueError("untrusted Standard Ebooks page URL")
    request = urllib.request.Request(url, headers={"User-Agent": "OpenShelf/1.0 (+https://github.com/jkoeh/openshelf)"})
    try:
        with urllib.request.build_opener(PageRedirects()).open(request, timeout=30) as response:
            data = response.read(MAX_PAGE_BYTES + 1)
        if len(data) > MAX_PAGE_BYTES:
            raise ValueError("Standard Ebooks page exceeds size limit")
        return data.decode("utf-8")
    finally:
        if delay:
            time.sleep(delay)


def parse_edition(text, source_id):
    path = edition_path(source_id)
    # Require book-specific rights, not the site's footer dedication.
    if not re.search(r'class=["\']us-pd-warning["\'][^>]*>\s*This ebook is thought to be free of copyright restrictions in the United States\.', text):
        raise ValueError("Standard Ebooks edition rights are not verified")
    canonical = re.search(r'<link\b[^>]*rel=["\']canonical["\'][^>]*href=["\']([^"\']+)', text)
    if not canonical or html.unescape(canonical.group(1)) != f"{BASE}/ebooks/{path}":
        raise ValueError("Standard Ebooks canonical edition does not match")
    download = epub_url(source_id)
    if urllib.parse.urlsplit(download).path not in re.findall(r'href=["\']([^"\']+)["\']', text):
        raise ValueError("Standard Ebooks compatible EPUB is missing")
    title = re.search(r'<h1\b[^>]*property=["\']schema:name["\'][^>]*>(.*?)</h1>', text, re.S)
    author = re.search(r'<a\b[^>]*property=["\']schema:author["\'][^>]*>.*?<span\b[^>]*property=["\']schema:name["\'][^>]*>(.*?)</span>', text, re.S)
    if not title or not author:
        raise ValueError("Standard Ebooks title or author is missing")
    clean = lambda value: " ".join(html.unescape(re.sub(r"<[^>]*>", "", value)).split())
    result = {"source_id": source_id, "title": clean(title.group(1)), "author": clean(author.group(1)), "epub_url": download}
    from datetime import datetime, timezone
    result["language"] = "en"
    result["provenance"] = {"source_url": f"{BASE}/ebooks/{path}", "rights_url": f"{BASE}/ebooks/{path}",
                            "rights_statement": "Public domain in the USA.", "rights_sha256": hashlib.sha256(text.encode()).hexdigest(),
                            "verified_at": datetime.now(timezone.utc).isoformat()}
    if not 1 <= len(result["title"]) <= 300 or not 1 <= len(result["author"]) <= 200:
        raise ValueError("Standard Ebooks metadata exceeds limits")
    return result


def catalog_books(*, max_books=500, after_page=0, max_pages=20):
    seen = set()
    for page in range(after_page + 1, after_page + max_pages + 1):
        listing = fetch_page(f"{BASE}/ebooks?page={page}")
        paths = list(_iter_downloadable_ebook_paths(listing))
        if not paths:
            break
        for path in paths:
            source_id = "standardebooks:" + path.removeprefix("/ebooks/")
            if source_id in seen:
                continue
            seen.add(source_id)
            try:
                book = parse_edition(fetch_page(BASE + path), source_id)
            except ValueError:
                continue
            yield book
            max_books -= 1
            if max_books == 0:
                return
        if f"page={page + 1}" not in listing:
            break
