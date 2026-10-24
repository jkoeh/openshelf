"""Outbound, single-job Worker consumer for a trusted owner PC."""

from __future__ import annotations

import argparse
import html
import hashlib
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

from openshelf.config import OPENAI_API_KEY, PROJECT_ROOT
from openshelf.scrapers.http import sanitize
from openshelf.pipeline.gutenberg_source import mirror_url, rights_url, source_number
from openshelf.pipeline.language import normalize_language, unit_count

MAX_EPUB_BYTES = 50 * 1024 * 1024
MAX_CENTRAL_DIRECTORY_BYTES = 2 * 1024 * 1024
MAX_EXPANDED_BYTES = 256 * 1024 * 1024
MAX_EPUB_ENTRIES = 5_000
MAX_RIGHTS_BYTES = 128 * 1024


class RightsNotVerified(ValueError):
    """The exact edition is not verified public domain in the USA."""


def validate_epub_url(url: str, source_id: str) -> str:
    if source_id.startswith("standardebooks:"):
        from openshelf.pipeline.standard_ebooks_source import validate_url
        return validate_url(url, source_id)
    mirror_url(url, source_id)
    return url


class GutenbergRedirects(urllib.request.HTTPRedirectHandler):
    def __init__(self, source_id: str):
        self.source_id = source_id
        self.redirects = 0

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_epub_url(newurl, self.source_id)
        if self.source_id.startswith("gutenberg:") and urllib.parse.urlsplit(newurl).hostname != "gutenberg.pglaf.org":
            raise ValueError("Gutenberg redirect left approved mirror")
        self.redirects += 1
        if self.redirects > 3:
            raise ValueError("too many source EPUB redirects")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_epub(url: str, source_id: str, destination: Path) -> None:
    validate_epub_url(url, source_id)
    if source_id.startswith("standardebooks:"):
        from openshelf.pipeline.standard_ebooks_source import epub_url
        url = epub_url(source_id)
    else:
        url = mirror_url(url, source_id)
        local_root = os.environ.get("OPENSHELF_GUTENBERG_MIRROR_DIR")
        if local_root:
            # The local root is the generated rsync collection (contains ID dirs).
            relative = urllib.parse.urlsplit(url).path.removeprefix("/cache/epub/")
            local = Path(local_root).resolve() / relative
            if local.is_file():
                if local.stat().st_size > MAX_EPUB_BYTES:
                    raise ValueError("local mirror EPUB exceeds size limit")
                data = local.read_bytes()
                _store_epub(data, destination)
                return
    opener = urllib.request.build_opener(GutenbergRedirects(source_id))
    req = urllib.request.Request(url, headers={"User-Agent": "OpenShelf/1.0"})
    with opener.open(req, timeout=45) as response:
        data = response.read(MAX_EPUB_BYTES + 1)
    _store_epub(data, destination)


def _store_epub(data: bytes, destination: Path) -> None:
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


def check_epub_rights(path: Path, source_id: str | None = None) -> None:
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
        if source_id and source_id.startswith("standardebooks:"):
            from openshelf.pipeline.standard_ebooks_source import BASE, edition_path
            identifiers = [node.text for node in package.iter("{http://purl.org/dc/elements/1.1/}identifier")]
            if identifiers != [f"{BASE}/ebooks/{edition_path(source_id)}"]:
                raise RightsNotVerified("EPUB identifier does not match Standard Ebooks edition")
            if (len(rights) != 1 or "believed to be in the United States public domain" not in rights[0]
                    or "https://creativecommons.org/publicdomain/zero/1.0/" not in rights[0]
                    or "dedicate their contributions to the worldwide public domain" not in rights[0]):
                raise RightsNotVerified("Standard Ebooks EPUB rights are not verified")
        elif rights != ["Public domain in the USA."]:
            raise RightsNotVerified("EPUB is not verified public domain in the USA")
        if source_id and source_id.startswith("gutenberg:"):
            number = source_number(source_id)
            identifiers = {node.text.strip() for node in package.iter("{http://purl.org/dc/elements/1.1/}identifier") if node.text}
            accepted = {f"{scheme}://www.gutenberg.org/{prefix}{number}"
                        for scheme in ("http", "https") for prefix in ("ebooks/", "")}
            if len(identifiers) != 1 or not identifiers.issubset(accepted):
                raise RightsNotVerified("EPUB identifier does not match Gutenberg ID")
        if source_id and source_id.startswith("wikisource:"):
            identifiers = [node.text for node in package.iter("{http://purl.org/dc/elements/1.1/}identifier")]
            if identifiers != [f"urn:openshelf:{source_id}"]:
                raise RightsNotVerified("EPUB identifier does not match Wikisource snapshot")
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
            urls.append(mirror_url(url, source_id))
        except ValueError:
            continue
    if not urls:
        raise RightsNotVerified("verified edition has no trusted EPUB")
    return next((url for url in urls if "-images" in url), urls[0])


def verify_public_domain(source_id: str) -> dict:
    """Require the official RDF rights marker before any expensive work."""
    if source_id.startswith("standardebooks:"):
        from openshelf.pipeline.standard_ebooks_source import BASE, edition_path, fetch_page, parse_edition
        try:
            book = parse_edition(fetch_page(f"{BASE}/ebooks/{edition_path(source_id)}"), source_id)
        except ValueError as exc:
            raise RightsNotVerified("Standard Ebooks edition rights are not verified") from exc
        return book["provenance"]
    match = re.fullmatch(r"gutenberg:([1-9][0-9]*)", source_id)
    if not match:
        raise RightsNotVerified("invalid Gutenberg ID")
    number = match.group(1)
    local_root = os.environ.get("OPENSHELF_GUTENBERG_MIRROR_DIR")
    if local_root:
        local = Path(local_root) / number / f"pg{number}.rdf"
        if local.is_file():
            with local.open("rb") as source:
                data = source.read(MAX_RIGHTS_BYTES + 1)
            public_domain_epub_url(data, source_id)
            return _gutenberg_provenance(source_id, data)
    request = urllib.request.Request(rights_url(source_id), headers={"User-Agent": "OpenShelf/1.0 (+https://github.com/jkoeh/openshelf)"})
    try:
        with urllib.request.build_opener(NoRedirects()).open(request, timeout=15) as response:
            data = response.read(MAX_RIGHTS_BYTES + 1)
        public_domain_epub_url(data, source_id)
        return _gutenberg_provenance(source_id, data)
    except OSError:
        pass
    raise RightsNotVerified("could not verify Gutenberg rights")


def _gutenberg_provenance(source_id: str, data: bytes) -> dict:
    from datetime import datetime, timezone
    return {"source_url": public_domain_epub_url(data, source_id), "rights_url": rights_url(source_id),
            "rights_statement": "Public domain in the USA.", "rights_sha256": hashlib.sha256(data).hexdigest(),
            "verified_at": datetime.now(timezone.utc).isoformat()}


def check_spoken_text(path: Path) -> int:
    from openshelf.pipeline.epub_parser import parse_epub

    count = sum(section.word_count + unit_count(section.heading.spoken_text)
                for section in parse_epub(str(path)))
    if count < 1:
        raise ValueError("EPUB has no spoken words")
    return count


class NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError("unexpected API redirect")


class JobAPI:
    def __init__(self, base: str, token: str):
        parsed = urllib.parse.urlparse(base)
        if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost"}):
            raise ValueError("API must use HTTPS outside localhost")
        if not token or len(token) < 24:
            raise ValueError("OPENSHELF_PC_TOKEN is required")
        self.base = base.rstrip("/")
        self.token = token

    def download_owner_document(self, upload_id: str) -> bytes:
        if not re.fullmatch(r"[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}", upload_id):
            raise ValueError("invalid owner upload ID")
        request = urllib.request.Request(self.base + "/internal/uploads/" + upload_id,
            headers={"Authorization": f"Bearer {self.token}", "User-Agent": "OpenShelf/1.0 (+https://github.com/jkoeh/openshelf)"})
        with urllib.request.build_opener(NoRedirects()).open(request, timeout=45) as response:
            data = response.read(16 * 1024 * 1024 + 1)
        if len(data) > 16 * 1024 * 1024:
            raise ValueError("owner document exceeds limit")
        return data

    def post(self, path: str, payload: dict | None = None) -> dict:
        body = json.dumps(payload or {}).encode("utf-8")
        request = urllib.request.Request(
            self.base + path, data=body, method="POST",
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json",
                     "User-Agent": "OpenShelf/1.0 (+https://github.com/jkoeh/openshelf)"},
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)

    def upload_source_epub(self, source_id: str, path: Path) -> dict:
        if path.stat().st_size > 32 * 1024 * 1024:
            raise ValueError("source EPUB exceeds reader size limit")
        request = urllib.request.Request(
            self.base + "/internal/source-books/" + urllib.parse.quote(source_id, safe="") + "/epub",
            data=path.read_bytes(), method="POST",
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/epub+zip",
                     "User-Agent": "OpenShelf/1.0 (+https://github.com/jkoeh/openshelf)"},
        )
        with urllib.request.urlopen(request, timeout=90) as response:
            return json.load(response)

    def source_epub_cached(self, source_id: str) -> bool:
        request = urllib.request.Request(
            self.base + "/source-books/" + urllib.parse.quote(source_id, safe="") + "/epub?inline=1",
            method="HEAD", headers={"User-Agent": "OpenShelf/1.0 (+https://github.com/jkoeh/openshelf)"},
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.status == 200
        except urllib.error.HTTPError as exc:
            if exc.code in (404, 502):
                return False
            raise

    def download_source_epub(self, source_id: str, destination: Path) -> bool:
        request = urllib.request.Request(
            self.base + "/source-books/" + urllib.parse.quote(source_id, safe="") + "/epub?inline=1",
            headers={"User-Agent": "OpenShelf/1.0 (+https://github.com/jkoeh/openshelf)"},
        )
        try:
            with urllib.request.build_opener(NoRedirects()).open(request, timeout=45) as response:
                data = response.read(32 * 1024 * 1024 + 1)
            if len(data) > 32 * 1024 * 1024:
                raise ValueError("reading copy exceeds size limit")
            _store_epub(data, destination)
            return True
        except urllib.error.HTTPError as exc:
            if exc.code in (404, 502):
                return False
            raise


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
                    or not {"en", "zh"} & set(book.get("languages", []))):
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
                          "epub_url": mirror_url(epub_url, source_id),
                          "language": "zh" if "zh" in book["languages"] else "en"})
        from openshelf.pipeline.source_catalog import cache_book
        for book in books:
            if api.source_epub_cached(book["source_id"]):
                continue
            try:
                book["provenance"] = verify_public_domain(book["source_id"])
                cache_book(api, book)
                total += 1
            except RightsNotVerified:
                continue
        url = page.get("next")
    return total


def mandarin_runtime(device: str) -> str | None:
    """Probe the configured isolated runtime once before advertising capability."""
    configured = os.environ.get("OPENSHELF_MANDARIN_PYTHON", "")
    if not configured or not Path(configured).is_file():
        return None
    probe = (
        "import importlib.metadata as m, torch, qwen_tts, whisperx.alignment; "
        "import ebooklib, bs4, boto3, dotenv, openai, anthropic, psutil; "
        "assert tuple(map(int,m.version('whisperx').split('.')[:3])) >= (3,8,4); "
        "assert m.version('qwen-tts') == '0.1.1'; "
        + ("assert torch.cuda.is_available()" if device != "cpu" else "assert True")
    )
    try:
        result = subprocess.run([configured, "-c", probe], capture_output=True, timeout=30,
                                check=False, cwd=PROJECT_ROOT)
        return configured if result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def process_job(api: JobAPI, job: dict, *, download_root: Path, audio_root: Path, device: str,
                mandarin_python: str | None = None, english_python: str | None = None) -> None:
    source_id = job["source_id"]
    author_slug = sanitize(job["author"]) or "unknown"
    from openshelf.pipeline.standard_ebooks_source import title_slug as edition_title_slug
    title_slug = edition_title_slug(job["title"], source_id)
    owner_document = source_id.startswith("ownerupload:")
    source = "local" if owner_document else "standard-ebooks" if source_id.startswith("standardebooks:") else "wikisource" if source_id.startswith("wikisource:") else "gutenberg"
    epub = download_root / source / author_slug / f"{title_slug}.epub"
    lease = job["lease_token"]
    route = f"/internal/generation-jobs/{job['id']}"
    try:
        if owner_document:
            proof = job.get("provenance", {})
            if (proof.get("rights_acknowledged") is not True or proof.get("public_publication_acknowledged") is not True
                    or proof.get("upload_id") != source_id.split(":")[1]):
                raise ValueError("owner document lacks rights/publication acknowledgement")
            if not epub.exists():
                prepare_owner_epub(api, job, epub)
        cached_proof = job.get("epub_sha256")
        if not cached_proof and not owner_document:
            verify_public_domain(source_id)
        if not epub.exists():
            cached = api.download_source_epub(source_id, epub) if hasattr(api, "download_source_epub") else False
            if not cached:
                if source == "wikisource" or cached_proof:
                    raise ValueError("validated reading copy is unavailable")
                fetch_epub(job["epub_url"], source_id, epub)
        validate_local_epub(epub)
        if owner_document:
            from openshelf.pipeline.owner_documents import validate_owner_epub
            validate_owner_epub(epub, source_id, proof.get("sha256", ""),
                                title=job["title"], author=job["author"], language=job.get("language", "en"))
        if cached_proof:
            if hashlib.sha256(epub.read_bytes()).hexdigest() != cached_proof:
                raise ValueError("source cache hash does not match edition")
            if job.get("provenance", {}).get("rights_statement") != "Public domain in the USA.":
                raise RightsNotVerified("source cache lacks verified U.S. rights evidence")
        if not owner_document:
            check_epub_rights(epub, source_id)
        language = normalize_language(job.get("language", "en"))
        from openshelf.pipeline.epub_parser import read_book_metadata
        if normalize_language(read_book_metadata(str(epub)).get("language", "en")) != language:
            raise ValueError("EPUB language does not match indexed edition")
        mode = job.get("mode", "standard")
        check_spoken_text(epub)
        if mode == "standard":
            if language not in {"en", "zh"}:
                raise ValueError("unsupported book language")
            if language == "zh":
                if not mandarin_python:
                    raise ValueError("Mandarin runtime is not configured")
                engine, voice, rendition = "qwen3", "qwen3-serena", "qwen3-serena"
            else:
                engine, voice, rendition = "kokoro", "af_heart", "kokoro-af-heart"
        elif mode == "expressive":
            if language != "en":
                raise ValueError("expressive generation requires English")
            if not OPENAI_API_KEY:
                raise ValueError("OpenAI direction is not configured on this PC")
            engine, voice, rendition = "chatterbox", "chatterbox-af_heart", "chatterbox-af-heart"
        else:
            raise ValueError("unsupported generation mode")
        build_dir = audio_root / author_slug / title_slug / "audio" / rendition / "builds" / job["build_id"]
        interpreter = mandarin_python if language == "zh" else english_python if mode == "standard" and english_python else sys.executable
        command = [interpreter, "-m", "openshelf.pipeline.cli", "books", "process", "--epub", str(epub),
                   "--output", str(audio_root), "--engine", engine, "--voice", voice,
                   "--rendition", rendition, "--build-id", job["build_id"], "--device", device, "--upload"]
        if owner_document:
            # Explicit local provenance; filenames/authors cannot accidentally
            # trigger the manual command's provider-directory inference.
            command[2:5] = ["openshelf.pipeline.dag.cli", "run"]
            command += ["--source", "local"]
        if mode == "expressive":
            command += ["--performance-direction", "batched"]
        if (build_dir / "run.json").exists():
            command.append("--resume")
        api.post(route + "/progress", {"lease_token": lease, "stage": "synthesis"})
        env = os.environ.copy()
        env["OPENSHELF_API_BASE"] = api.base
        env["OPENSHELF_PC_TOKEN"] = api.token
        env["OPENSHELF_REQUIRE_COMPLETE_AUDIO"] = "1"
        env["PYTHONPATH"] = str(PROJECT_ROOT / "pipeline" / "src") + os.pathsep + env.get("PYTHONPATH", "")
        if mode == "expressive":
            env["LLM_PROVIDER"] = "openai"
        if language == "zh" and env.get("OPENSHELF_QWEN_CACHE"):
            env["HF_HOME"] = env["OPENSHELF_QWEN_CACHE"]
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
                        terminate_child_tree(child)
                        raise RuntimeError("lease rejected") from exc
                    except (urllib.error.URLError, OSError):
                        if time.monotonic() - last_renewal > 90:
                            terminate_child_tree(child)
                            raise RuntimeError("lease renewal failed")
            if child.returncode:
                raise RuntimeError(f"pipeline exited {child.returncode}")
        except BaseException:
            if child.poll() is None:
                terminate_child_tree(child)
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


def terminate_child_tree(child) -> None:
    """Stop virtualenv launchers and their base-Python/DAG descendants together."""
    import psutil
    # Small fake children used by offline tests model only one process.
    if not hasattr(child, "pid"):
        child.terminate()
        child.wait(timeout=20)
        return
    try:
        root = psutil.Process(child.pid)
        processes = root.children(recursive=True) + [root]
    except psutil.NoSuchProcess:
        child.wait(timeout=20)
        return
    for process in reversed(processes):
        try:
            process.terminate()
        except psutil.NoSuchProcess:
            pass
    _, alive = psutil.wait_procs(processes, timeout=10)
    for process in alive:
        try:
            process.kill()
        except psutil.NoSuchProcess:
            pass
    _, alive = psutil.wait_procs(alive, timeout=10)
    if alive:
        raise RuntimeError("synthesis process tree could not be stopped")
    child.wait(timeout=20)


def prepare_owner_epub(api: JobAPI, job: dict, epub: Path) -> None:
    """Extract in a bounded subprocess so parsers cannot stall the consumer lease."""
    from openshelf.pipeline.owner_documents import InvalidOwnerDocument
    proof = job["provenance"]
    route = f"/internal/generation-jobs/{job['id']}/heartbeat"
    api.post(route, {"lease_token": job["lease_token"]})
    data = api.download_owner_document(proof["upload_id"])
    if hashlib.sha256(data).hexdigest() != proof.get("sha256"):
        raise InvalidOwnerDocument("uploaded document hash mismatch")
    epub.parent.mkdir(parents=True, exist_ok=True)
    original = epub.with_suffix(".original")
    pending = epub.with_suffix(".pending.epub")
    original.write_bytes(data)
    metadata = {"format": proof["format"], "source_id": job["source_id"], "title": job["title"],
                "author": job["author"], "language": normalize_language(job.get("language", "en")),
                "sha256": proof["sha256"]}
    env = os.environ.copy()
    env["PYTHONPATH"] = str(PROJECT_ROOT / "pipeline" / "src") + os.pathsep + env.get("PYTHONPATH", "")
    try:
        api.post(route, {"lease_token": job["lease_token"]})
        child = subprocess.Popen([sys.executable, "-m", "openshelf.pipeline.owner_documents", str(original), str(pending)],
                                 stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env,
                                 cwd=PROJECT_ROOT)
        try:
            child.communicate(json.dumps(metadata).encode(), timeout=60)
        except subprocess.TimeoutExpired as exc:
            terminate_child_tree(child)
            raise InvalidOwnerDocument("document extraction exceeded 60 seconds") from exc
        except BaseException:
            terminate_child_tree(child)
            raise
        if child.returncode != 0:
            raise InvalidOwnerDocument("document could not be extracted within supported limits")
        pending.replace(epub)
    finally:
        original.unlink(missing_ok=True)
        pending.unlink(missing_ok=True)
    api.post(route, {"lease_token": job["lease_token"]})


def document_runtime_ready() -> bool:
    try:
        import ebooklib
        import pypdf
        return bool(ebooklib and hasattr(pypdf, "apply_configuration"))
    except (ImportError, OSError):
        return False


def english_runtime() -> str | None:
    """Validate an explicit standard-English runtime without loading any model."""
    configured = os.environ.get("OPENSHELF_ENGLISH_PYTHON", "")
    if not configured:
        return None
    if not Path(configured).is_file():
        raise ValueError("configured English runtime does not exist")
    probe = (
        "import sys,importlib.metadata as m; assert sys.version_info >= (3,12); "
        "import kokoro,misaki.en,whisperx.alignment,ebooklib,bs4,boto3,dotenv,openai,anthropic,psutil,spacy; "
        "assert tuple(map(int,m.version('whisperx').split('.')[:3])) >= (3,8,4); "
        "assert spacy.util.is_package('en_core_web_sm')"
    )
    try:
        result = subprocess.run([configured, "-c", probe], stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, timeout=30, check=False, cwd=PROJECT_ROOT)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError("configured English runtime did not pass readiness checks") from exc
    if result.returncode:
        raise ValueError("configured English runtime did not pass readiness checks")
    return configured


def production_recovery_active(root: Path = PROJECT_ROOT) -> bool:
    """Recognize only a live, PID-locked recovery owned by this checkout."""
    import psutil
    root = root.resolve()
    script = root / "pipeline/scripts/openshelf-pipeline.py"

    def option(arguments, flag):
        values = [argument.split("=", 1)[1] for argument in arguments if argument.startswith(flag + "=")]
        values += [arguments[index + 1] for index, argument in enumerate(arguments[:-1]) if argument == flag]
        return values[0] if len(values) == 1 else None

    for process in psutil.process_iter(["cmdline", "create_time"]):
        try:
            arguments = process.info["cmdline"] or []
            if not any(arguments[index:index + 2] == ["ops", "recover-source-job"]
                       for index in range(len(arguments) - 1)):
                continue
            cwd = Path(process.cwd()).resolve()
            if cwd != root:
                continue
            prefix = arguments[:arguments.index("ops")]
            entrypoint = any((cwd / argument).resolve() == script for argument in prefix
                             if Path(argument).name == "openshelf-pipeline.py")
            module = any(prefix[index:index + 2] == ["-m", "openshelf.pipeline.cli"]
                         for index in range(len(prefix) - 1))
            console = any(Path(argument).resolve() == root / ".venv/Scripts/openshelf-pipeline.exe"
                          for argument in prefix)
            if not (entrypoint or module or console):
                continue
            source_build = option(arguments, "--source-build")
            state_file = option(arguments, "--state-file")
            if not source_build or not state_file:
                continue
            source_build = (cwd / source_build).resolve()
            state_file = (cwd / state_file).resolve()
            if (not source_build.is_relative_to(root / "audio")
                    or not re.fullmatch(r"[0-9a-f]{16}", source_build.name)
                    or not state_file.is_relative_to(root / "audio")):
                continue
            lock = state_file.with_suffix(state_file.suffix + ".lock")
            if lock.stat().st_size > 4096:
                continue
            identity = json.loads(lock.read_text(encoding="utf-8"))
            if (identity.get("pid") == process.pid
                    and identity.get("created") == process.info["create_time"]
                    and process.is_running()):
                return True
        except (psutil.Error, OSError, ValueError, TypeError, KeyError):
            continue
    return False


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="openshelf-pipeline books consume-jobs")
    parser.add_argument("--api-base", required=True, help="Worker URL including /api/v1")
    parser.add_argument("--sync-pages", type=int, default=0, help="Gutenberg pages to index before polling")
    parser.add_argument("--once", action="store_true", help="Claim at most one job")
    parser.add_argument("--device", choices=["auto", "cuda", "mps", "cpu"], default="auto")
    args = parser.parse_args(argv)
    if args.sync_pages < 0 or args.sync_pages > 10:
        parser.error("--sync-pages must be 0..10")
    api = JobAPI(args.api_base, os.environ.get("OPENSHELF_PC_TOKEN", ""))
    mandarin_python = mandarin_runtime(args.device)
    documents_ready = document_runtime_ready()
    english_python = None
    english_ready = True
    try:
        english_python = english_runtime()
    except ValueError:
        english_ready = False
        print("Configured English runtime is unavailable; standard English jobs remain queued", file=sys.stderr)
    if args.sync_pages:
        print(f"Indexed {sync_gutenberg(api, args.sync_pages)} Gutenberg editions")
    recovery_paused = False
    while True:
        try:
            if production_recovery_active():
                if not recovery_paused:
                    print("Local source recovery owns audio generation; ordinary consumer is waiting", flush=True)
                    recovery_paused = True
                if args.once:
                    return 0
                time.sleep(45)
                continue
            if recovery_paused:
                print("Local source recovery finished; ordinary queue polling resumed", flush=True)
                recovery_paused = False
            job = api.post("/internal/generation-jobs/claim", {
                "expressive": bool(OPENAI_API_KEY), "mandarin": bool(mandarin_python), "documents": documents_ready, "english": english_ready})["job"]
            if job:
                print(f"Processing {job['source_id']} ({job['id']})")
                process_job(api, job, download_root=PROJECT_ROOT / "download" / "books",
                            audio_root=PROJECT_ROOT / "audio", device=args.device,
                            mandarin_python=mandarin_python, english_python=english_python)
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
