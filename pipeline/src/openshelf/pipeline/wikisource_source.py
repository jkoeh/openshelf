"""Revision-pinned Wikisource API snapshots, exported in explicit chapter order."""

from __future__ import annotations

import hashlib
import json
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from bs4 import BeautifulSoup
from ebooklib import epub

MAX_API_BYTES = 8 * 1024 * 1024
MAX_SNAPSHOT_BYTES = 64 * 1024 * 1024
MAX_PAGES = 1_000
US_PD = {"pd-1923", "pd-us-expired", "pd-old-100-1923", "pd-old-100-expired"}


def permanent_url(language: str, revision: int) -> str:
    return f"https://{language}.wikisource.org/w/index.php?oldid={revision}"


def fetch_revision(language: str, page: dict) -> dict:
    from openshelf.pipeline.job_consumer import NoRedirects
    if language not in {"en", "zh"}:
        raise ValueError("unsupported Wikisource language")
    if not isinstance(page.get("revision"), int) or isinstance(page["revision"], bool) or page["revision"] < 1:
        raise ValueError("invalid Wikisource revision")
    params = urllib.parse.urlencode({"action": "parse", "oldid": page["revision"],
                                    "prop": "text|revid|templates", "format": "json", "formatversion": 2, "maxlag": 5})
    request = urllib.request.Request(f"https://{language}.wikisource.org/w/api.php?{params}",
                                    headers={"User-Agent": "OpenShelf/1.0 (+https://github.com/jkoeh/openshelf)", "Accept": "application/json"})
    try:
        with urllib.request.build_opener(NoRedirects()).open(request, timeout=45) as response:
            data = response.read(MAX_API_BYTES + 1)
        if len(data) > MAX_API_BYTES:
            raise ValueError("Wikisource API response exceeds size limit")
        result = json.loads(data)
        if "error" in result:
            raise ValueError("Wikisource API rejected revision: " + str(result["error"].get("code", "unknown")))
        return result["parse"]
    finally:
        time.sleep(2)


def _page_snapshot(language: str, page: dict, fetcher) -> dict:
    if (not isinstance(page.get("page_id"), int) or isinstance(page["page_id"], bool) or page["page_id"] < 1
            or not isinstance(page.get("revision"), int) or isinstance(page["revision"], bool) or page["revision"] < 1
            or not isinstance(page.get("title"), str) or not 1 <= len(page["title"]) <= 300 or ":" in page["title"]):
        raise ValueError("invalid Wikisource page identity")
    data = fetcher(language, page)
    title = data.get("title", "").replace("_", " ")
    if data.get("pageid") != page["page_id"] or data.get("revid") != page["revision"] or title != page["title"].replace("_", " ") or data.get("ns", 0) != 0:
        raise ValueError("Wikisource API page does not match pinned identity")
    html = data.get("text")
    if isinstance(html, dict):
        html = html.get("*")
    if not isinstance(html, str) or len(html.encode()) > MAX_API_BYTES:
        raise ValueError("invalid Wikisource page HTML")
    if BeautifulSoup(html, "html.parser").select_one(".redirectMsg, .redirectText"):
        raise ValueError("Wikisource redirects are not edition content")
    templates = {str(item.get("title", item.get("*", ""))).split(":", 1)[-1].replace("_", " ").casefold() for item in data.get("templates", [])}
    if any(re.search(r"copyvio|copyrightvio|no.?license|侵权|侵權", name) for name in templates):
        raise ValueError("Wikisource copyright warning")
    return {"title": title, "page_id": page["page_id"], "revision": page["revision"],
            "sha256": hashlib.sha256(html.encode()).hexdigest(), "html": html, "templates": templates}


def _chapter_links(html: str, language: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    links = []
    for anchor in soup.select(".ws-summary a[href]"):
        url = urllib.parse.urlsplit(urllib.parse.urljoin(f"https://{language}.wikisource.org", anchor["href"]))
        if url.scheme != "https" or url.netloc != f"{language}.wikisource.org" or not url.path.startswith("/wiki/") or url.query or url.fragment:
            raise ValueError("ambiguous Wikisource chapter link")
        title = urllib.parse.unquote(url.path[6:]).replace("_", " ")
        if ":" in title or title in links:
            raise ValueError("duplicate or non-main Wikisource chapter")
        links.append(title)
    return links


def _body_html(html: str) -> str:
    from html import escape
    soup = BeautifulSoup(html, "html.parser")
    for node in soup.select("script, style, .ws-noexport, .ws-summary, .ws-header, .header, .navigation, .navbox, .mw-editsection, .license, .licensetpl, .copyright, .references, sup.reference"):
        node.decompose()
    for line_break in soup.find_all("br"):
        line_break.replace_with("\n")
    root = soup.select_one(".mw-parser-output") or soup
    blocks = []
    for node in root.find_all(["p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "blockquote", "div", "pre"]):
        if node.find(["p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "blockquote", "div", "pre"]):
            continue
        text = re.sub(r"\s+", " ", node.get_text(separator="")).strip()
        if text:
            blocks.append(f"<p>{escape(text)}</p>")
    if not blocks:
        raise ValueError("Wikisource chapter has no readable content")
    return "\n".join(blocks)


def build_edition(edition: dict, destination: Path | None, *, fetcher=None) -> dict:
    fetcher = fetcher or fetch_revision
    language = edition.get("language")
    if language not in {"en", "zh"}:
        raise ValueError("unsupported Wikisource language")
    year = edition.get("publication_year")
    if not isinstance(year, int) or isinstance(year, bool) or not 1 <= year <= 1929:
        raise ValueError("exact Wikisource edition/translation publication year is not verified")
    for field, maximum in (("title", 300), ("author", 200)):
        if not isinstance(edition.get(field), str) or not 1 <= len(edition[field].strip()) <= maximum:
            raise ValueError("invalid Wikisource book metadata")
    chapters = edition.get("chapters", [])
    if not isinstance(chapters, list) or not 1 <= len(chapters) <= MAX_PAGES:
        raise ValueError("invalid Wikisource chapter count")
    root = _page_snapshot(language, edition["root"], fetcher)
    if not root["templates"] & US_PD:
        raise ValueError("Wikisource edition lacks explicit U.S. public-domain evidence")
    ordered = [page["title"].replace("_", " ") for page in chapters]
    links = _chapter_links(root["html"], language)
    if ordered != links and not (not links and ordered == [root["title"]]):
        raise ValueError("Wikisource chapter order does not match edition TOC")
    pages = []
    snapshot_bytes = len(root["html"].encode())
    for page in chapters:
        snapshot = root if page == edition["root"] else _page_snapshot(language, page, fetcher)
        snapshot_bytes += len(snapshot["html"].encode())
        if snapshot_bytes > MAX_SNAPSHOT_BYTES:
            raise ValueError("Wikisource snapshot exceeds size limit")
        pages.append(snapshot)
    if len({page["page_id"] for page in pages}) != len(pages):
        raise ValueError("duplicate Wikisource chapter identity")
    snapshots = [{key: page[key] for key in ("title", "page_id", "revision", "sha256")} for page in [root, *pages]]
    snapshot_hash = hashlib.sha256(json.dumps(snapshots, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    source_id = f"wikisource:{language}:{root['page_id']}:{root['revision']}:{snapshot_hash[:16]}"
    url = permanent_url(language, root["revision"])
    provenance = {"source_url": url, "rights_url": url, "rights_statement": "Public domain in the USA.",
                  "rights_sha256": root["sha256"], "verified_at": datetime.now(timezone.utc).isoformat(),
                  "publication_year": year, "snapshot_sha256": snapshot_hash, "pages": snapshots,
                  "attribution": "Wikisource contributors; revision URLs identify the transcription contributors. Wikimedia site contributions are CC BY-SA 4.0."}
    if len(json.dumps(provenance).encode()) > 256 * 1024:
        raise ValueError("Wikisource provenance exceeds size limit")
    if destination is not None:
        book = epub.EpubBook()
        book.set_identifier("urn:openshelf:" + source_id)
        book.set_title(edition["title"])
        book.add_author(edition["author"])
        book.set_language(language)
        book.add_metadata("DC", "rights", "Public domain in the USA.")
        book.add_metadata("DC", "source", url)
        book.add_item(epub.EpubItem(uid="provenance", file_name="source-provenance.json", media_type="application/json", content=json.dumps(provenance, ensure_ascii=False).encode()))
        spine = []
        from html import escape
        for index, page in enumerate(pages):
            chapter = epub.EpubHtml(title=page["title"], file_name=f"chapter-{index:04d}.xhtml", lang=language)
            revision_url = permanent_url(language, page["revision"])
            chapter.content = f'<h1>{escape(page["title"].rsplit("/", 1)[-1])}</h1>{_body_html(page["html"])}<aside epub:type="footnote"><p>Wikisource contributors: {escape(revision_url)}</p></aside>'
            book.add_item(chapter)
            spine.append(chapter)
        book.toc = spine
        book.spine = ["nav", *spine]
        book.add_item(epub.EpubNcx())
        book.add_item(epub.EpubNav())
        destination.parent.mkdir(parents=True, exist_ok=True)
        epub.write_epub(str(destination), book)
    return {"source_id": source_id, "title": edition["title"], "author": edition["author"],
            "language": language, "epub_url": url, "provenance": provenance}


def read_editions(path: Path, max_books: int):
    if path.stat().st_size > 4 * 1024 * 1024:
        raise ValueError("Wikisource edition manifest exceeds size limit")
    editions = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(editions, list) or len(editions) > 1_000:
        raise ValueError("invalid Wikisource edition manifest")
    yield from editions[:max_books]
