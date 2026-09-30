"""Outbound, single-job Worker consumer for a trusted owner PC."""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from xml.etree import ElementTree

from openshelf.config import PROJECT_ROOT
from openshelf.scrapers.http import sanitize

MAX_EPUB_BYTES = 50 * 1024 * 1024
MAX_CENTRAL_DIRECTORY_BYTES = 2 * 1024 * 1024
MAX_EXPANDED_BYTES = 256 * 1024 * 1024
MAX_EPUB_ENTRIES = 5_000
DEFAULT_MAX_WORDS = 100_000
MAX_RIGHTS_BYTES = 128 * 1024
GUTENBERG_HOSTS = {"www.gutenberg.org", "dev.gutenberg.org", "gutenberg.org"}


class BookTooLong(ValueError):
    """The selected edition exceeds the owner's generation budget."""


class RightsNotVerified(ValueError):
    """The exact edition is not verified public domain in the USA."""


def validate_epub_url(url: str, source_id: str) -> str:
    match = re.fullmatch(r"gutenberg:([1-9][0-9]*)", source_id)
    parsed = urllib.parse.urlparse(url)
    if not match or parsed.scheme != "https" or parsed.hostname not in GUTENBERG_HOSTS:
        raise ValueError("untrusted Gutenberg URL")
    number = match.group(1)
    if not re.search(rf"/(?:ebooks|cache/epub)/{number}(?:[./-]|$)", parsed.path):
        raise ValueError("Gutenberg URL does not match source ID")
    if parsed.username or parsed.password or parsed.port or parsed.fragment:
        raise ValueError("unexpected Gutenberg URL component")
    return url


class GutenbergRedirects(urllib.request.HTTPRedirectHandler):
    def __init__(self, source_id: str):
        self.source_id = source_id

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_epub_url(newurl, self.source_id)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_epub(url: str, source_id: str, destination: Path) -> None:
    validate_epub_url(url, source_id)
    opener = urllib.request.build_opener(GutenbergRedirects(source_id))
    req = urllib.request.Request(url, headers={"User-Agent": "OpenShelf/1.0"})
    with opener.open(req, timeout=45) as response:
        data = response.read(MAX_EPUB_BYTES + 1)
    if len(data) > MAX_EPUB_BYTES or not data.startswith(b"PK\x03\x04"):
        raise ValueError("EPUB is missing, oversized, or not a ZIP")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = destination.with_suffix(".epub.part")
    temp.write_bytes(data)
    try:
        validate_local_epub(temp)
        temp.replace(destination)
    finally:
        temp.unlink(missing_ok=True)


def validate_local_epub(path: Path) -> None:
    if path.stat().st_size > MAX_EPUB_BYTES:
        raise ValueError("cached EPUB is oversized")
    with path.open("rb") as source:
        source.seek(max(0, path.stat().st_size - 65_557))
        tail = source.read()
    for offset in range(len(tail) - 22, -1, -1):
        if tail[offset:offset + 4] != b"PK\x05\x06":
            continue
        _, disk, central_disk, disk_entries, entries, central_size, _, comment_size = \
            struct.unpack_from("<4sHHHHIIH", tail, offset)
        if offset + 22 + comment_size != len(tail):
            continue
        if disk or central_disk or disk_entries != entries or entries == 0xffff or central_size == 0xffffffff:
            raise ValueError("unsupported EPUB ZIP directory")
        if entries > MAX_EPUB_ENTRIES or central_size > MAX_CENTRAL_DIRECTORY_BYTES:
            raise ValueError("EPUB ZIP directory exceeds limits")
        break
    else:
        raise ValueError("EPUB ZIP directory is missing")
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        if len(entries) > MAX_EPUB_ENTRIES or sum(entry.file_size for entry in entries) > MAX_EXPANDED_BYTES:
            raise ValueError("EPUB archive expands beyond limits")
        if archive.testzip() is not None or "META-INF/container.xml" not in archive.namelist():
            raise ValueError("cached EPUB is invalid")


def check_epub_rights(path: Path) -> None:
    """Confirm embedded rights and reject Gutenberg's explicit copyright banner."""
    with zipfile.ZipFile(path) as archive:
        opfs = [item for item in archive.infolist() if item.filename.lower().endswith(".opf")]
        if len(opfs) != 1 or opfs[0].file_size > 1024 * 1024:
            raise RightsNotVerified("EPUB package rights are missing or ambiguous")
        try:
            package = ElementTree.fromstring(archive.read(opfs[0]))
        except ElementTree.ParseError as exc:
            raise RightsNotVerified("invalid EPUB package rights") from exc
        rights = [node.text.strip() for node in package.iter("{http://purl.org/dc/elements/1.1/}rights")
                  if node.text]
        if rights != ["Public domain in the USA."]:
            raise RightsNotVerified("EPUB is not verified public domain in the USA")
        banner = re.compile(r"this\s+is\s+a\s+copyrighted\s+project\s+gutenberg\s+ebook", re.I)
        for item in archive.infolist():
            if not item.filename.lower().endswith((".htm", ".html", ".xhtml", ".txt")):
                continue
            with archive.open(item) as source:
                front = source.read(64 * 1024)
            readable = html.unescape(re.sub(r"<[^>]*>", " ", front.decode("utf-8", "ignore")))
            if banner.search(readable):
                raise RightsNotVerified("EPUB contains an explicit copyright notice")


def public_domain_epub_url(data: bytes, source_id: str) -> str:
    """Read one official RDF record; reject ambiguous rights or EPUB formats."""
    match = re.fullmatch(r"gutenberg:([1-9][0-9]*)", source_id)
    if not match or len(data) > MAX_RIGHTS_BYTES or b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
        raise RightsNotVerified("invalid or oversized rights metadata")
    number = match.group(1)
    try:
        root = ElementTree.fromstring(data)
    except ElementTree.ParseError as exc:
        raise RightsNotVerified("invalid rights metadata") from exc
    ns = {"pg": "http://www.gutenberg.org/2009/pgterms/",
          "dc": "http://purl.org/dc/terms/",
          "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#"}
    ebook = root.find("pg:ebook", ns)
    if ebook is None or ebook.get(f"{{{ns['rdf']}}}about") != f"ebooks/{number}":
        raise RightsNotVerified("rights record does not match Gutenberg ID")
    rights = [node.text.strip() for node in ebook.findall("dc:rights", ns) if node.text]
    if rights != ["Public domain in the USA."]:
        raise RightsNotVerified("edition is not verified public domain in the USA")
    urls = []
    for item in root.findall(".//pg:file", ns):
        if not any((value.text or "").strip() == "application/epub+zip"
                   for value in item.findall(".//rdf:value", ns)):
            continue
        url = item.get(f"{{{ns['rdf']}}}about")
        if not url:
            continue
        try:
            urls.append(validate_epub_url(url, source_id))
        except ValueError:
            continue
    if not urls:
        raise RightsNotVerified("verified edition has no trusted EPUB")
    return next((url for url in urls if url.endswith(".epub.images")), urls[0])


def verify_public_domain(source_id: str) -> None:
    """Require the official RDF rights marker before any expensive work."""
    match = re.fullmatch(r"gutenberg:([1-9][0-9]*)", source_id)
    if not match:
        raise RightsNotVerified("invalid Gutenberg ID")
    number = match.group(1)
    opener = urllib.request.build_opener(GutenbergRedirects(source_id))
    for host in ("dev.gutenberg.org", "www.gutenberg.org"):
        url = f"https://{host}/cache/epub/{number}/pg{number}.rdf"
        request = urllib.request.Request(url, headers={"User-Agent": "OpenShelf/1.0"})
        try:
            with opener.open(request, timeout=15) as response:
                data = response.read(MAX_RIGHTS_BYTES + 1)
            public_domain_epub_url(data, source_id)
            return
        except OSError:
            continue
    raise RightsNotVerified("could not verify Gutenberg rights")


def check_word_budget(path: Path, max_words: int) -> int:
    from openshelf.pipeline.epub_parser import parse_epub

    count = sum(section.word_count + len(section.heading.spoken_text.split())
                for section in parse_epub(str(path)))
    if count < 1:
        raise ValueError("EPUB has no spoken words")
    if count > max_words:
        raise BookTooLong(f"Book has {count} words; limit is {max_words}")
    return count


class JobAPI:
    def __init__(self, base: str, token: str):
        parsed = urllib.parse.urlparse(base)
        if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost"}):
            raise ValueError("API must use HTTPS outside localhost")
        if not token or len(token) < 24:
            raise ValueError("OPENSHELF_PC_TOKEN is required")
        self.base = base.rstrip("/")
        self.token = token

    def post(self, path: str, payload: dict | None = None) -> dict:
        body = json.dumps(payload or {}).encode("utf-8")
        request = urllib.request.Request(
            self.base + path, data=body, method="POST",
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json",
                     "User-Agent": "OpenShelf/1.0 (+https://github.com/jkoeh/openshelf)"},
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)


def sync_gutenberg(api: JobAPI, pages: int) -> int:
    url = "https://gutendex.com/books/"
    total = 0
    for _ in range(pages):
        if not url:
            break
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != "https" or parsed.hostname != "gutendex.com":
            raise ValueError("untrusted Gutendex pagination URL")
        request = urllib.request.Request(url, headers={"User-Agent": "OpenShelf/1.0 (+https://github.com/jkoeh/openshelf)"})
        with urllib.request.urlopen(request, timeout=30) as response:
            page = json.load(response)
        books = []
        for book in page.get("results", []):
            if (book.get("copyright") is not False or book.get("media_type") != "Text"
                    or "en" not in book.get("languages", [])):
                continue
            epub_url = book.get("formats", {}).get("application/epub+zip")
            source_id = f"gutenberg:{book.get('id')}"
            try:
                validate_epub_url(epub_url, source_id)
            except (ValueError, TypeError):
                continue
            authors = book.get("authors", [])
            books.append({"source_id": source_id, "title": book.get("title") or "Untitled",
                          "author": authors[0].get("name", "Unknown") if authors else "Unknown",
                          "epub_url": epub_url})
        for offset in range(0, len(books), 50):
            api.post("/internal/source-books/sync", {"books": books[offset:offset+50]})
            total += len(books[offset:offset+50])
        url = page.get("next")
    return total


def process_job(api: JobAPI, job: dict, *, download_root: Path, audio_root: Path, device: str,
                max_words: int = DEFAULT_MAX_WORDS) -> None:
    source_id = job["source_id"]
    author_slug = sanitize(job["author"]) or "unknown"
    title_slug = (sanitize(job["title"]) or "untitled") + "-g" + source_id.split(":")[1]
    epub = download_root / "gutenberg" / author_slug / f"{title_slug}.epub"
    lease = job["lease_token"]
    route = f"/internal/generation-jobs/{job['id']}"
    try:
        verify_public_domain(source_id)
        if not epub.exists():
            fetch_epub(job["epub_url"], source_id, epub)
        validate_local_epub(epub)
        check_epub_rights(epub)
        check_word_budget(epub, max_words)
        build_dir = audio_root / author_slug / title_slug / "audio" / "kokoro-af-heart" / "builds" / job["build_id"]
        command = [sys.executable, "-m", "openshelf.pipeline.cli", "books", "process", "--epub", str(epub),
                   "--output", str(audio_root), "--engine", "kokoro", "--voice", "af_heart",
                   "--rendition", "kokoro-af-heart", "--build-id", job["build_id"], "--device", device, "--upload"]
        if (build_dir / "run.json").exists():
            command.append("--resume")
        api.post(route + "/progress", {"lease_token": lease, "stage": "synthesis"})
        env = os.environ.copy()
        env["PYTHONPATH"] = str(PROJECT_ROOT / "pipeline" / "src") + os.pathsep + env.get("PYTHONPATH", "")
        child = subprocess.Popen(command, env=env, cwd=PROJECT_ROOT)
        last_renewal = time.monotonic()
        last_attempt = last_renewal
        try:
            while child.poll() is None:
                time.sleep(1)
                if time.monotonic() - last_attempt >= 30:
                    last_attempt = time.monotonic()
                    try:
                        api.post(route + "/heartbeat", {"lease_token": lease})
                        last_renewal = time.monotonic()
                    except urllib.error.HTTPError as exc:
                        # A rejected lease is definitive, unlike a dropped connection.
                        child.terminate()
                        raise RuntimeError("lease rejected") from exc
                    except (urllib.error.URLError, OSError):
                        if time.monotonic() - last_renewal > 90:
                            child.terminate()
                            raise RuntimeError("lease renewal failed")
            if child.returncode:
                raise RuntimeError(f"pipeline exited {child.returncode}")
        except BaseException:
            if child.poll() is None:
                child.terminate()
                child.wait(timeout=20)
            raise
        api.post(route + "/finish", {"lease_token": lease, "success": True,
                                      "author_slug": author_slug, "title_slug": title_slug})
    except Exception as exc:
        # Never include the API token, EPUB URL, or arbitrary exception text in a job.
        try:
            api.post(route + "/finish", {"lease_token": lease, "success": False,
                                          "error_code": type(exc).__name__[:60]})
        except (urllib.error.URLError, OSError):
            pass
        raise


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="openshelf-pipeline books consume-jobs")
    parser.add_argument("--api-base", required=True, help="Worker URL including /api/v1")
    parser.add_argument("--sync-pages", type=int, default=0, help="Gutenberg pages to index before polling")
    parser.add_argument("--once", action="store_true", help="Claim at most one job")
    parser.add_argument("--device", choices=["auto", "cuda", "mps", "cpu"], default="auto")
    parser.add_argument("--max-words", type=int, default=DEFAULT_MAX_WORDS,
                        help="Maximum source spoken words per queued book (default: 100000)")
    args = parser.parse_args(argv)
    if args.sync_pages < 0 or args.sync_pages > 3000:
        parser.error("--sync-pages must be 0..3000")
    if args.max_words < 1:
        parser.error("--max-words must be positive")
    api = JobAPI(args.api_base, os.environ.get("OPENSHELF_PC_TOKEN", ""))
    if args.sync_pages:
        print(f"Indexed {sync_gutenberg(api, args.sync_pages)} Gutenberg editions")
    while True:
        try:
            job = api.post("/internal/generation-jobs/claim")["job"]
            if job:
                print(f"Processing {job['source_id']} ({job['id']})")
                process_job(api, job, download_root=PROJECT_ROOT / "download" / "books",
                            audio_root=PROJECT_ROOT / "audio", device=args.device,
                            max_words=args.max_words)
            elif args.once:
                return 0
            else:
                time.sleep(45)
        except KeyboardInterrupt:
            return 130
        except (urllib.error.URLError, OSError, RuntimeError, ValueError) as exc:
            print(f"Consumer error: {type(exc).__name__}", file=sys.stderr)
            if args.once:
                return 1
            time.sleep(60)
        if args.once:
            return 0
