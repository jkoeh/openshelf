"""Bounded owner-authorized document extraction; no OCR or external fetches."""

import hashlib
import io
import re
import zipfile
from html import escape
from pathlib import Path
from datetime import datetime
from xml.etree import ElementTree as ET

MAX_BYTES = 16 * 1024 * 1024
MAX_TEXT = 2_000_000
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


class InvalidOwnerDocument(ValueError):
    pass


def _text(value):
    # Preserve script/punctuation and explicit paragraph order, remove XML controls.
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    if "\0" in value or len(value) > MAX_TEXT:
        raise InvalidOwnerDocument("document text exceeds limits or contains NUL bytes")
    value = re.sub(r"[\x01-\x08\x0b\x0c\x0e-\x1f]", "", value)
    if not value.strip():
        raise InvalidOwnerDocument("document has no extractable text; scanned PDFs require OCR")
    return value


def extract_text(data: bytes, format: str) -> list[tuple[str, str]]:
    if not data or len(data) > MAX_BYTES:
        raise InvalidOwnerDocument("document must be between 1 byte and 16 MiB")
    if format == "txt":
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise InvalidOwnerDocument("TXT must use UTF-8 encoding") from exc
        return [("Text", _text(text))]
    if format == "pdf":
        from pypdf import apply_configuration
        try:
            with apply_configuration(maximum_declared_stream_length=16*1024*1024,
                    array_based_stream_maximum_output_length=16*1024*1024,
                    zlib_maximum_output_length=16*1024*1024, lzw_maximum_output_length=16*1024*1024,
                    run_length_maximum_output_length=16*1024*1024, brotli_maximum_output_length=16*1024*1024,
                    stream_decoding_work_maximum_length=32*1024*1024,
                    stream_parsing_work_maximum_count=500_000,
                    page_tree_maximum_entries=1001, xform_maximum_invocations_per_extraction=100):
                return _pdf_sections(data)
        except InvalidOwnerDocument:
            raise
        except Exception as exc:
            raise InvalidOwnerDocument("could not read PDF text within resource limits") from exc
    if format == "docx":
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                entries = archive.infolist()
                if len(entries) > 5000 or sum(entry.file_size for entry in entries) > 64 * 1024 * 1024:
                    raise InvalidOwnerDocument("DOCX archive exceeds limits")
                if "word/document.xml" not in archive.namelist():
                    raise InvalidOwnerDocument("file is not a DOCX document")
                if archive.getinfo("word/document.xml").file_size > 16 * 1024 * 1024:
                    raise InvalidOwnerDocument("DOCX main XML exceeds limits")
                document = archive.read("word/document.xml")
            declarations = document.replace(b"\0", b"").upper()
            if b"<!DOCTYPE" in declarations or b"<!ENTITY" in declarations:
                raise InvalidOwnerDocument("DOCX entities are unsupported")
            root = ET.fromstring(document)
            parts = []
            visited = 0
            text_size = 0
            def visit(node):
                nonlocal visited, text_size
                visited += 1
                if visited > 200_000:
                    raise InvalidOwnerDocument("DOCX element count exceeds limits")
                if node.tag == W + "p": parts.append("\n\n")
                if node.tag == W + "t":
                    text_size += len(node.text or "")
                    if text_size > MAX_TEXT:
                        raise InvalidOwnerDocument("document text exceeds limits")
                    parts.append(node.text or "")
                elif node.tag == W + "tab": parts.append("\t")
                elif node.tag in {W + "br", W + "cr"}: parts.append("\n")
                else:
                    for child in node: visit(child)
                if node.tag == W + "p": parts.append("\n\n")
            visit(root)
            paragraphs = [part for part in re.split(r"\n[ \t]*\n+", "".join(parts)) if part.strip()]
            return [("Document", _text("\n\n".join(paragraphs)))]
        except InvalidOwnerDocument:
            raise
        except (zipfile.BadZipFile, KeyError, ET.ParseError) as exc:
            raise InvalidOwnerDocument("could not read DOCX text") from exc
    raise InvalidOwnerDocument("supported formats are PDF, DOCX and UTF-8 TXT")


def _pdf_sections(data):
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted:
        raise InvalidOwnerDocument("encrypted PDFs are unsupported")
    if len(reader.pages) > 1000:
        raise InvalidOwnerDocument("PDF exceeds 1,000 pages")
    sections, size = [], 0
    for index, page in enumerate(reader.pages):
        contents = page.get_contents()
        if contents is not None and len(contents.get_data()) > 16 * 1024 * 1024:
            raise InvalidOwnerDocument("PDF page content exceeds limits")
        text = page.extract_text() or ""
        size += len(text)
        if size > MAX_TEXT:
            raise InvalidOwnerDocument("document text exceeds limits")
        if text.strip(): sections.append((f"Page {index + 1}", _text(text)))
    if not sections:
        raise InvalidOwnerDocument("PDF has no extractable text; scanned PDFs require OCR")
    return sections


def create_epub(data: bytes, format: str, destination: Path, *, source_id: str,
                title: str, author: str, language: str, sha256: str) -> None:
    if hashlib.sha256(data).hexdigest() != sha256:
        raise InvalidOwnerDocument("uploaded document hash mismatch")
    if not re.fullmatch(r"ownerupload:[a-f0-9-]{36}", source_id) or language not in {"en", "zh"}:
        raise InvalidOwnerDocument("invalid owner document identity or language")
    sections = extract_text(data, format)
    from ebooklib import epub
    book = epub.EpubBook()
    book.set_identifier("urn:openshelf:" + source_id)
    book.set_title(title)
    book.add_author(author)
    book.set_language(language)
    book.add_metadata("DC", "rights", "Owner authorized public distribution; public-domain status not asserted.")
    book.add_metadata("DC", "source", "urn:sha256:" + sha256)
    pages = []
    for index, (heading, text) in enumerate(sections):
        page = epub.EpubHtml(title=heading, file_name=f"section-{index + 1:04}.xhtml", lang=language)
        paragraphs = [paragraph for paragraph in re.split(r"\n\s*\n", text) if paragraph.strip()]
        page.content = "<h1>" + escape(heading) + "</h1>" + "".join("<p>" + escape(paragraph).replace("\n", "<br/>") + "</p>" for paragraph in paragraphs)
        book.add_item(page)
        pages.append(page)
    book.toc = tuple(pages)
    book.spine = pages
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    # Ebooklib's UID/modified timestamp otherwise vary across retries. Fixed source
    # identity plus timestamp makes the generated EPUB's resume hash stable.
    destination.parent.mkdir(parents=True, exist_ok=True)
    epub.write_epub(str(destination), book, {"mtime": datetime(2000, 1, 1)})
    raw = destination.read_bytes()
    with zipfile.ZipFile(io.BytesIO(raw)) as original, zipfile.ZipFile(destination, "w") as stable:
        for entry in original.infolist():
            info = zipfile.ZipInfo(entry.filename, (2000, 1, 1, 0, 0, 0))
            info.compress_type = entry.compress_type
            stable.writestr(info, original.read(entry.filename))


def validate_owner_epub(path: Path, source_id: str, sha256: str, *, title: str, author: str, language: str) -> None:
    from ebooklib import epub
    book = epub.read_epub(str(path))
    expected = {"identifier": "urn:openshelf:" + source_id, "source": "urn:sha256:" + sha256,
                "title": title, "creator": author, "language": language}
    for field, value in expected.items():
        if [entry[0] for entry in book.get_metadata("DC", field)] != [value]:
            raise InvalidOwnerDocument("owner EPUB identity does not match uploaded original")


if __name__ == "__main__":
    import json
    import sys
    try:
        metadata = json.load(sys.stdin)
        create_epub(Path(sys.argv[1]).read_bytes(), metadata.pop("format"), Path(sys.argv[2]), **metadata)
    except Exception:
        # Never print private text, metadata or parser exception payloads.
        raise SystemExit(2)
