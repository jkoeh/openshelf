"""Step 1: Parse EPUB files into semantic audiobook sections."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from typing import Iterable
from urllib.parse import unquote, urldefrag

import ebooklib
from bs4 import BeautifulSoup
from ebooklib import epub

from openshelf.config import PIPELINE_VERSION


SECTION_TYPES = frozenset({
    "chapter",
    "prologue",
    "epilogue",
    "epigraph",
    "preface",
    "introduction",
    "afterword",
    "appendix",
    "part",
    "other",
})


@dataclass
class ContentElement:
    id: str
    tag: str
    html: str
    text: str
    spoken: bool


@dataclass
class SectionHeading:
    display_label: str = ""
    display_title: str = ""
    spoken_text: str = ""
    element_ids: list[str] = field(default_factory=list)


@dataclass
class Section:
    sequence: int
    section_type: str
    ordinal: int | None
    heading: SectionHeading
    elements: list[ContentElement]
    body_elements: list[ContentElement]
    paragraphs: list[str]
    text: str
    word_count: int
    epub_item_name: str = ""

    @property
    def number(self) -> int:
        """Compatibility alias for local callers while the DAG migrates."""
        return self.sequence

    @property
    def title(self) -> str:
        """Compact display title used by logs and direction metadata."""
        return self.heading.display_title or self.heading.display_label


_SKIP_PATTERNS = (
    "nav",
    "toc",
    "cover",
    "colophon",
    "imprint",
    "illustration",
    "copyright",
    "uncopyright",
    "titlepage",
    "half-title",
    "halftitle",
)
_SKIP_BASENAMES = {"loi"}
_MIN_WORD_COUNT = 50
_BASE_CONTENT_TAGS = (
    "p",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "blockquote",
    "li",
    "figcaption",
)
_CONTENT_TAGS = _BASE_CONTENT_TAGS + ("div",)
_HEADING_TAGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})
_HEADING_EPUB_TYPES = frozenset({"title", "subtitle", "ordinal", "fulltitle"})
_SKIP_EPUB_TYPES = frozenset({"footnote", "endnote", "toc", "pagebreak"})
_NON_AUDIOBOOK_EPUB_TYPES = frozenset({
    "titlepage",
    "halftitlepage",
    "imprint",
    "copyright-page",
    "colophon",
    "cover",
    "toc",
    "landmarks",
    "loi",
})
_SECTION_TYPE_MAP = {
    "chapter": "chapter",
    "prologue": "prologue",
    "epilogue": "epilogue",
    "epigraph": "epigraph",
    "preface": "preface",
    "introduction": "introduction",
    "afterword": "afterword",
    "appendix": "appendix",
    "part": "part",
}
_FILENAME_SECTION_TYPES = {
    "epigraph": "epigraph",
    "prologue": "prologue",
    "epilogue": "epilogue",
    "preface": "preface",
    "introduction": "introduction",
    "afterword": "afterword",
    "appendix": "appendix",
}
_GUTENBERG_BOILERPLATE_MARKERS = (
    "license",
    "start of the project gutenberg ebook",
    "end of the project gutenberg ebook",
)
_GUTENBERG_BOILERPLATE_IDS = frozenset({
    "pg-header",
    "pg-footer",
    "pg-start-separator",
    "pg-end-separator",
    "pg-machine-header",
})
_GUTENBERG_BOILERPLATE_CLASSES = frozenset({
    "pg-boilerplate",
    "pgheader",
    "pgfooter",
})
_GUTENBERG_SEPARATOR_RE = re.compile(
    r"^\*{3}\s+(START|END)\s+OF\s+THE\s+PROJECT\s+GUTENBERG\s+EBOOK\b",
    re.IGNORECASE,
)
_ROMAN_VALUES = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
_INVISIBLE_TEXT_RE = re.compile("[\ufeff\u200b\u200c\u200d\u2060]")
_CHAPTER_PREFIX_RE = re.compile(
    r"^chapter\s+([IVXLCDM]+|\d+)(?:\s*[.:\-—]\s*(.+))?$",
    re.IGNORECASE,
)
_ORDINAL_TITLE_RE = re.compile(
    r"^([IVXLCDM]+|\d+)(?:\s*[.:\-—]\s+(.+))?$",
    re.IGNORECASE,
)


def _epub_types(node) -> set[str]:
    value = node.get("epub:type", "") if hasattr(node, "get") else ""
    return {part.casefold() for part in str(value).split() if part}


def _roman_to_arabic(roman: str) -> int | None:
    if not roman:
        return None
    token = roman.upper()
    total = 0
    prev = 0
    for ch in token[::-1]:
        value = _ROMAN_VALUES.get(ch)
        if value is None:
            return None
        if value < prev:
            total -= value
        else:
            total += value
            prev = value
    if total <= 0 or _arabic_to_roman(total) != token:
        return None
    return total


def _arabic_to_roman(value: int) -> str:
    parts: list[str] = []
    for number, token in (
        (1000, "M"), (900, "CM"), (500, "D"), (400, "CD"),
        (100, "C"), (90, "XC"), (50, "L"), (40, "XL"),
        (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I"),
    ):
        while value >= number:
            parts.append(token)
            value -= number
    return "".join(parts)


def _parse_ordinal(token: str) -> int | None:
    stripped = token.strip().rstrip(".")
    if stripped.isdigit():
        value = int(stripped)
        return value if value > 0 else None
    return _roman_to_arabic(stripped)


def _number_to_english(value: int) -> str:
    if value <= 0:
        return str(value)
    small = (
        "Zero", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight",
        "Nine", "Ten", "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen",
        "Sixteen", "Seventeen", "Eighteen", "Nineteen",
    )
    tens = ("", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety")
    if value < 20:
        return small[value]
    if value < 100:
        quotient, remainder = divmod(value, 10)
        return tens[quotient] if remainder == 0 else f"{tens[quotient]}-{small[remainder]}"
    if value < 1000:
        quotient, remainder = divmod(value, 100)
        prefix = f"{small[quotient]} Hundred"
        return prefix if remainder == 0 else f"{prefix} {_number_to_english(remainder)}"
    if value < 1_000_000:
        quotient, remainder = divmod(value, 1000)
        prefix = f"{_number_to_english(quotient)} Thousand"
        return prefix if remainder == 0 else f"{prefix} {_number_to_english(remainder)}"
    return str(value)


def normalize_heading_for_tts(text: str) -> str:
    """Compatibility helper; parser storage keeps display and spoken text separate."""
    stripped = clean_extracted_text(text)
    match = _CHAPTER_PREFIX_RE.fullmatch(stripped)
    if match:
        ordinal = _parse_ordinal(match.group(1))
        if ordinal:
            title = clean_extracted_text(match.group(2) or "")
            return _spoken_chapter_heading(ordinal, title, "en")
    match = _ORDINAL_TITLE_RE.fullmatch(stripped)
    if match:
        ordinal = _parse_ordinal(match.group(1))
        if ordinal:
            title = clean_extracted_text(match.group(2) or "")
            return _spoken_chapter_heading(ordinal, title, "en")
    return text


def clean_extracted_text(text: str) -> str:
    without_format_controls = _INVISIBLE_TEXT_RE.sub("", text)
    return re.sub(r"\s+", " ", without_format_controls).strip()


def _should_skip(filename: str, soup: BeautifulSoup | None = None) -> bool:
    name_lower = filename.lower()
    stem = _filename_stem(filename)
    if stem in _SKIP_BASENAMES or any(pattern in name_lower for pattern in _SKIP_PATTERNS):
        return True
    if soup is not None:
        for node in (soup.body, soup.find("section"), soup.find("article")):
            if node and _epub_types(node) & _NON_AUDIOBOOK_EPUB_TYPES:
                return True
    return False


def _document_items(book) -> list:
    items = list(book.get_items_of_type(ebooklib.ITEM_DOCUMENT))
    spine = getattr(book, "spine", None)
    if not isinstance(spine, (list, tuple)):
        return items
    by_name = {item.get_name(): item for item in items}
    by_id = {}
    for item in items:
        try:
            by_id[item.get_id()] = item
        except Exception:
            continue
    ordered = []
    seen: set[str] = set()
    for entry in spine:
        item_id = entry[0] if isinstance(entry, (list, tuple)) else entry
        item = by_id.get(item_id)
        if item is None:
            try:
                item = book.get_item_with_id(item_id)
            except Exception:
                item = None
        if item is None:
            continue
        name = item.get_name()
        if name in by_name and name not in seen:
            ordered.append(item)
            seen.add(name)
    ordered.extend(item for item in items if item.get_name() not in seen)
    return ordered


def _filename_stem(filename: str) -> str:
    return filename.lower().rsplit("/", 1)[-1].rsplit(".", 1)[0]


def _semantic_type_for_node(node) -> str | None:
    for token in _epub_types(node):
        if token in _SECTION_TYPE_MAP:
            return _SECTION_TYPE_MAP[token]
    return None


def _semantic_candidates(soup: BeautifulSoup) -> list:
    semantic = [
        node
        for node in soup.find_all(["section", "article"])
        if _semantic_type_for_node(node)
    ]
    if not semantic:
        return [soup.body or soup]

    candidates = []
    for node in semantic:
        ancestors = [
            parent
            for parent in node.parents
            if getattr(parent, "name", None) in {"section", "article"}
            and _semantic_type_for_node(parent)
        ]
        if not ancestors or any(_semantic_type_for_node(parent) == "part" for parent in ancestors):
            candidates.append(node)
    return candidates


def _candidate_exclusions(candidate, all_candidates: list) -> set[int]:
    return {
        id(other)
        for other in all_candidates
        if other is not candidate and candidate in list(other.parents)
    }


def _has_nested_content_tag(tag, excluded: set[int]) -> bool:
    for child in tag.find_all(_CONTENT_TAGS):
        if any(id(parent) in excluded for parent in [child] + list(child.parents)):
            continue
        return True
    return False


def iter_content_tags(root, excluded_roots: Iterable | None = None):
    """Yield source content blocks once, including meaningful leaf divs."""
    excluded = {id(node) for node in (excluded_roots or [])}
    for tag in root.find_all(_CONTENT_TAGS):
        if any(id(parent) in excluded for parent in [tag] + list(tag.parents)):
            continue
        if tag.name == "div" and _has_nested_content_tag(tag, excluded):
            continue
        if tag.name != "div" and _has_nested_content_tag(tag, excluded):
            continue
        yield tag


def _is_spoken(element) -> bool:
    for node in [element] + list(element.parents):
        if _epub_types(node) & _SKIP_EPUB_TYPES:
            return False
    return True


def _clean_tag_text(tag) -> str:
    for marker in tag.find_all(["sup", "sub"]):
        marker.decompose()
    for anchor in tag.find_all("a"):
        if anchor.string and re.fullmatch(r"\d+", anchor.string.strip()):
            anchor.decompose()
    return clean_extracted_text(tag.get_text(separator=" "))


def _is_heading_tag(tag) -> bool:
    return tag.name in _HEADING_TAGS or bool(_epub_types(tag) & _HEADING_EPUB_TYPES)


def _leading_heading_tags(candidate, excluded_roots: Iterable | None = None) -> list:
    headings = []
    for tag in iter_content_tags(candidate, excluded_roots):
        text = clean_extracted_text(tag.get_text(separator=" "))
        if not text:
            continue
        if _is_heading_tag(tag):
            headings.append(tag)
            continue
        break
    return headings


def _heading_texts(heading_tags: list) -> list[str]:
    return [
        clean_extracted_text(tag.get_text(separator=" "))
        for tag in heading_tags
        if clean_extracted_text(tag.get_text(separator=" "))
    ]


def _section_type_from_heading(texts: list[str]) -> tuple[str | None, int | None, str]:
    first = texts[0] if texts else ""
    match = _CHAPTER_PREFIX_RE.fullmatch(first)
    if match:
        return "chapter", _parse_ordinal(match.group(1)), clean_extracted_text(match.group(2) or "")
    match = _ORDINAL_TITLE_RE.fullmatch(first)
    if match and _parse_ordinal(match.group(1)):
        return "chapter", _parse_ordinal(match.group(1)), clean_extracted_text(match.group(2) or "")
    lowered = first.casefold().strip(" .:")
    for section_type in SECTION_TYPES - {"chapter", "other"}:
        if lowered == section_type or lowered.startswith(f"{section_type} "):
            remainder = first[len(section_type):].strip(" .:—-")
            return section_type, None, remainder
    return None, None, ""


def _navigation_labels(book) -> dict[str, str]:
    labels: dict[str, str] = {}

    def visit(items):
        for entry in items or []:
            if isinstance(entry, tuple):
                visit(entry[1])
                entry = entry[0]
            href = getattr(entry, "href", "")
            title = getattr(entry, "title", "")
            if href and title:
                path = unquote(urldefrag(href)[0]).lstrip("./")
                labels[path] = clean_extracted_text(title)

    visit(getattr(book, "toc", []))
    return labels


def _classify_candidate(
    candidate,
    filename: str,
    heading_tags: list,
    heading_texts: list[str],
    nav_label: str,
) -> tuple[str, int | None, str]:
    semantic = _semantic_type_for_node(candidate)
    if semantic is None:
        semantic = next(
            (
                heading_semantic
                for tag in heading_tags
                if (heading_semantic := _semantic_type_for_node(tag)) is not None
            ),
            None,
        )
    heading_type, ordinal, inline_title = _section_type_from_heading(heading_texts)
    if semantic:
        if semantic == "chapter" and ordinal is None:
            _, ordinal, inline_title = _section_type_from_heading(heading_texts)
        return semantic, ordinal, inline_title
    if heading_type:
        return heading_type, ordinal, inline_title
    if nav_label:
        nav_type, nav_ordinal, nav_title = _section_type_from_heading([nav_label])
        if nav_type:
            return nav_type, nav_ordinal, nav_title
    filename_type = _FILENAME_SECTION_TYPES.get(_filename_stem(filename))
    if filename_type:
        return filename_type, None, ""
    if heading_texts:
        # A source may express a chapter only as a standalone title. Preserve
        # it as an unnumbered chapter instead of inventing an ordinal from
        # spine position.
        return "chapter", None, ""
    return "other", None, ""


def _spoken_chapter_heading(ordinal: int, title: str, language: str) -> str:
    if language.casefold().startswith("en"):
        label = f"Chapter {_number_to_english(ordinal)}"
    else:
        label = ""
    parts = [part for part in (label, title) if part]
    return _ensure_terminal_punctuation(". ".join(parts)) if parts else ""


def _ensure_terminal_punctuation(text: str) -> str:
    stripped = text.strip()
    if not stripped:
        return ""
    return stripped if stripped[-1] in ".?!" else f"{stripped}."


def _build_heading(
    section_type: str,
    ordinal: int | None,
    heading_texts: list[str],
    inline_title: str,
    nav_label: str,
    language: str,
    element_ids: list[str],
) -> SectionHeading:
    display_label = ""
    display_title = ""

    if section_type == "chapter":
        if heading_texts:
            first = heading_texts[0]
            if _CHAPTER_PREFIX_RE.fullmatch(first) or _ORDINAL_TITLE_RE.fullmatch(first):
                display_label = first
                display_title = inline_title or (heading_texts[1] if len(heading_texts) > 1 else "")
            else:
                display_title = first
                if len(heading_texts) > 1:
                    display_title = " — ".join(heading_texts)
        elif nav_label:
            display_title = nav_label

        if ordinal is not None and language.casefold().startswith("en"):
            spoken_text = _spoken_chapter_heading(ordinal, display_title, language)
        elif ordinal is not None:
            source_heading = " ".join(heading_texts) or " ".join(
                part for part in (display_label, display_title) if part
            )
            spoken_text = _ensure_terminal_punctuation(source_heading)
        else:
            spoken_text = display_title or display_label
    else:
        default_label = section_type.replace("_", " ").title() if section_type != "other" else ""
        if heading_texts:
            first = heading_texts[0]
            if first.casefold().strip(" .:") == section_type:
                display_label = first
                display_title = inline_title or (heading_texts[1] if len(heading_texts) > 1 else "")
            else:
                display_label = default_label
                display_title = " — ".join(heading_texts)
        else:
            display_label = default_label or nav_label
            if default_label and nav_label and nav_label.casefold() != default_label.casefold():
                display_title = nav_label
        spoken_text = ". ".join(part for part in (display_label, display_title) if part)
        spoken_text = _ensure_terminal_punctuation(spoken_text)

    return SectionHeading(
        display_label=display_label,
        display_title=display_title,
        spoken_text=spoken_text,
        element_ids=element_ids,
    )


def _extract_content_elements(
    candidate,
    sequence: int,
    excluded_roots: Iterable | None = None,
) -> tuple[list[ContentElement], list[int]]:
    elements: list[ContentElement] = []
    heading_indexes: list[int] = []
    heading_tags = {id(tag) for tag in _leading_heading_tags(candidate, excluded_roots)}
    index = 0
    for tag in iter_content_tags(candidate, excluded_roots):
        text = _clean_tag_text(tag)
        if not text:
            continue
        element_id = f"sec{sequence}-el{index:04d}"
        tag["id"] = element_id
        element = ContentElement(
            id=element_id,
            tag=tag.name,
            html=str(tag),
            text=text,
            spoken=_is_spoken(tag),
        )
        if id(tag) in heading_tags:
            heading_indexes.append(len(elements))
        elements.append(element)
        index += 1
    return elements, heading_indexes


def _candidate_body_word_count(candidate, excluded_roots: Iterable | None = None) -> int:
    heading_ids = {id(tag) for tag in _leading_heading_tags(candidate, excluded_roots)}
    words = 0
    for tag in iter_content_tags(candidate, excluded_roots):
        if id(tag) in heading_ids or not _is_spoken(tag):
            continue
        text = clean_extracted_text(tag.get_text(separator=" "))
        words += len(text.split())
    return words


def _is_project_gutenberg_boilerplate(soup: BeautifulSoup) -> bool:
    text = clean_extracted_text(soup.get_text(separator=" "))
    lowered = text.casefold()
    return "project gutenberg" in lowered and any(
        marker in lowered for marker in _GUTENBERG_BOILERPLATE_MARKERS
    )


def _remove_project_gutenberg_boilerplate(soup: BeautifulSoup) -> None:
    for tag in list(soup.find_all(True)):
        if tag.parent is None or tag.attrs is None:
            continue
        tag_id = tag.get("id", "")
        classes = tag.get("class", [])
        if isinstance(classes, str):
            classes = classes.split()
        if tag_id in _GUTENBERG_BOILERPLATE_IDS or any(
            cls in _GUTENBERG_BOILERPLATE_CLASSES for cls in classes
        ):
            tag.decompose()
            continue
        if tag.name in {"div", "p", "span"}:
            text = clean_extracted_text(tag.get_text(separator=" "))
            if _GUTENBERG_SEPARATOR_RE.match(text):
                tag.decompose()


def parse_epub(epub_path: str) -> list[Section]:
    book = epub.read_epub(epub_path)
    language_meta = book.get_metadata("DC", "language")
    language = language_meta[0][0] if language_meta else "en"
    nav_labels = _navigation_labels(book)
    sections: list[Section] = []

    for item in _document_items(book):
        filename = item.get_name()
        soup = BeautifulSoup(item.get_content(), "html.parser")
        _remove_project_gutenberg_boilerplate(soup)
        if _should_skip(filename, soup) or _is_project_gutenberg_boilerplate(soup):
            continue

        candidates = _semantic_candidates(soup)
        for candidate in candidates:
            excluded = [
                other
                for other in candidates
                if other is not candidate and candidate in list(other.parents)
            ]
            heading_tags = _leading_heading_tags(candidate, excluded)
            heading_texts = _heading_texts(heading_tags)
            nav_label = nav_labels.get(filename, "")
            section_type, ordinal, inline_title = _classify_candidate(
                candidate,
                filename,
                heading_tags,
                heading_texts,
                nav_label,
            )
            body_word_count = _candidate_body_word_count(candidate, excluded)
            recognized = section_type != "other"
            if body_word_count == 0 and not recognized:
                continue
            if body_word_count < _MIN_WORD_COUNT and not recognized:
                continue

            sequence = len(sections) + 1
            elements, heading_indexes = _extract_content_elements(candidate, sequence, excluded)
            heading_ids = [elements[index].id for index in heading_indexes]
            heading = _build_heading(
                section_type,
                ordinal,
                heading_texts,
                inline_title,
                nav_label,
                language,
                heading_ids,
            )
            body_elements = [
                element
                for element in elements
                if element.spoken and element.id not in set(heading.element_ids)
            ]
            paragraphs = [element.text for element in body_elements]
            text = "\n\n".join(paragraphs)
            sections.append(Section(
                sequence=sequence,
                section_type=section_type,
                ordinal=ordinal,
                heading=heading,
                elements=elements,
                body_elements=body_elements,
                paragraphs=paragraphs,
                text=text,
                word_count=len(text.split()),
                epub_item_name=filename,
            ))

    return sections


def extract_cover_image(epub_path: str) -> tuple[bytes, str] | None:
    book = epub.read_epub(epub_path)
    for item in book.get_items_of_type(ebooklib.ITEM_COVER):
        content = item.get_content()
        if content:
            return content, item.media_type or _guess_media_type(item.get_name())
    cover_meta = book.get_metadata("OPF", "cover")
    if cover_meta:
        cover_id = (
            cover_meta[0][1].get("content", "")
            if len(cover_meta[0]) > 1
            else cover_meta[0][0]
        )
        item = book.get_item_with_id(cover_id)
        if item and hasattr(item, "get_content"):
            return item.get_content(), item.media_type or _guess_media_type(item.get_name())
    for item_type in (ebooklib.ITEM_IMAGE, ebooklib.ITEM_COVER):
        for item in book.get_items_of_type(item_type):
            if "cover" in item.get_name().lower():
                return item.get_content(), item.media_type or _guess_media_type(item.get_name())
    images = list(book.get_items_of_type(ebooklib.ITEM_IMAGE))
    if images:
        largest = max(images, key=lambda image: len(image.get_content()))
        if len(largest.get_content()) > 5000:
            return (
                largest.get_content(),
                largest.media_type or _guess_media_type(largest.get_name()),
            )
    return None


def _guess_media_type(filename: str) -> str:
    lower = filename.lower()
    if lower.endswith(".png"):
        return "image/png"
    if lower.endswith(".gif"):
        return "image/gif"
    return "image/jpeg"


BOOK_PARSE_VERSION = 2


def epub_sha256(epub_path: str) -> str:
    digest = hashlib.sha256()
    with open(epub_path, "rb") as file:
        for block in iter(lambda: file.read(65536), b""):
            digest.update(block)
    return "sha256:" + digest.hexdigest()


def read_book_metadata(epub_path: str) -> dict:
    book = epub.read_epub(epub_path)

    def first(name: str) -> str:
        values = book.get_metadata("DC", name)
        return values[0][0] if values else ""

    return {
        "title": first("title"),
        "author": first("creator"),
        "language": first("language") or "en",
    }


def build_book_parse_artifact(
    sections: list[Section],
    epub_hash: str,
    metadata: dict,
) -> dict:
    return {
        "version": BOOK_PARSE_VERSION,
        "epub_hash": epub_hash,
        "parser_version": PIPELINE_VERSION,
        "metadata": dict(metadata),
        "sections": [
            {
                "sequence": section.sequence,
                "section_type": section.section_type,
                "ordinal": section.ordinal,
                "heading": dataclasses.asdict(section.heading),
                "epub_item_name": section.epub_item_name,
                "word_count": section.word_count,
                "elements": [dataclasses.asdict(element) for element in section.elements],
            }
            for section in sections
        ],
    }


def read_book_parse_artifact(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as file:
        payload = json.load(file)
    if payload.get("version") != BOOK_PARSE_VERSION:
        raise ValueError(
            f"incompatible book_parse version: {payload.get('version')}; "
            f"expected {BOOK_PARSE_VERSION}"
        )
    return payload


def write_book_parse_artifact(path: str, payload: dict, force: bool = False) -> str:
    if os.path.exists(path) and not force:
        existing = read_book_parse_artifact(path)
        if existing == payload:
            return path
        raise FileExistsError(
            f"book_parse artifact already exists with different payload: {path}"
        )
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as file:
        json.dump(payload, file, indent=2, ensure_ascii=False)
        file.write("\n")
    return path
