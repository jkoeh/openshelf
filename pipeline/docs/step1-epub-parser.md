# Step 1: EPUB Section Parser

**Module:** `src/openshelf/pipeline/epub_parser.py`
**Test:** `tests/pipeline/test_epub_parser.py`

## Purpose

Parse an EPUB into ordered, structurally typed audiobook sections while
preserving heading display text separately from body reader text. EPUB spine
order determines playback `sequence`; it never determines chapter ordinal.

## Interface

```python
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
```

```python
def parse_epub(epub_path: str) -> list[Section]
```

Supported `section_type` values are `chapter`, `prologue`, `epilogue`,
`epigraph`, `preface`, `introduction`, `afterword`, `appendix`, `part`, and
`other`. Generated credits are added later because they depend on rendition
voice metadata.

## Document and section discovery

HTML documents are processed in EPUB spine order when available, falling back
to document order. Source title pages, half-title pages, navigation, TOCs,
covers, imprints, colophons, illustration lists, copyright/uncopyright pages,
and Gutenberg legal boilerplate are excluded from audiobook sections.

Each semantic `section` or `article` container with its own meaningful content
becomes an audiobook section. When no semantic child container exists, the
document body is one candidate. Multiple sections in one XHTML document retain
document order.

Classification precedence is:

1. `epub:type` tokens on the candidate container or heading;
2. explicit heading labels, including `Chapter`, Roman/Arabic ordinals,
   `Prologue`, and `Epilogue`;
3. navigation label and known filename;
4. a standalone leading title becomes an unnumbered `chapter`;
5. `other`.

Spine position never creates an ordinal. `ordinal` is populated only from a
source chapter label or ordinal. Named special sections remain unnumbered.

Recognized named sections are retained even when short. Other candidates need
at least 50 spoken body words. Empty/decorative candidates are skipped.

## Heading extraction

The section heading is built from leading heading-like elements:

- semantic heading tags `h1`–`h6`;
- leading elements with `epub:type` containing `title`, `subtitle`, or
  `ordinal`;
- a known semantic navigation/filename label when source markup omits one.

For a chapter:

- a Roman/Arabic or `Chapter N` label becomes `display_label`;
- a following title/subtitle becomes `display_title`;
- a standalone non-numeric heading becomes `display_title`;
- `spoken_text` deterministically expands an English ordinal, e.g. display
  `I` becomes `Chapter One`, and then appends the display title.

For named special sections, the source label/title is spoken without adding a
chapter number. Heading elements stay in `elements` for EPUB annotation but are
excluded from `body_elements`, `paragraphs`, `text`, and `word_count`.

Display HTML and source heading text are never rewritten for TTS.

## Content extraction

Content tags are `p`, `h1`–`h6`, `blockquote`, `li`, `figcaption`, and
meaningful leaf block-level `div` elements. Nested content is not extracted
twice. `<sup>`, `<sub>`, numeric-only footnote anchors, and invisible format
controls are removed from spoken text. Elements under `footnote`, `endnote`,
`toc`, or `pagebreak` semantics are `spoken=False`.

Element IDs use `sec{sequence}-el{index:04d}` and remain stable within a parse.

There is no whole-document fallback paragraph. Div-based EPUBs preserve
meaningful leaf block boundaries and IDs.

## Alice regression

The Standard Ebooks edition parses as:

1. unnumbered `epigraph`;
2. chapter ordinal 1, label `I`, title `Down the Rabbit-Hole`;
3. chapter ordinal 2;
4. … through chapter ordinal 12.

The source title page is excluded. Chapter 1 body starts with
`Alice was beginning...`.
