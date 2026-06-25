# Chapter Titles and Audiobook Sections — Version 2

## Current behavior and root cause

The version-1 parser treats every retained EPUB spine document as one audiobook
chapter. It assigns a gap-free `Chapter.number` from retained spine position,
extracts the first `h1`/`h2`/`h3` as `Chapter.title`, rewrites heading element
text for TTS, and also includes heading elements in `Chapter.paragraphs`.

The chunker consequently packs short headings with opening body paragraphs.
TTS then deliberately merges short opening fragments such as
`Chapter 6.\n\nPig and Pepper\n\nFor a minute...` into one synthesis call to
avoid clipped short-fragment audio. The final `chapter_data.json` cannot
distinguish title words from body words.

This causes three related failures:

- spine position is mistaken for source chapter ordinal;
- structural sections such as epigraphs, prologues, and epilogues are exposed
  as numbered chapters;
- display text, spoken heading text, body text, and timestamps are conflated.

The checked Standard Ebooks edition of *Alice’s Adventures in Wonderland*
demonstrates the bug. Its epigraph is retained first and becomes playback
chapter 1. Source chapter `I` becomes playback chapter 2, while its heading
`I`, subtitle `Down the Rabbit-Hole`, and opening sentence are packed and
synthesized together.

## Standards basis

- The EPUB spine defines default reading order; it does not define chapter
  ordinals.
- EPUB structural semantics distinguish `chapter`, `prologue`, `epilogue`,
  `epigraph`, `preface`, `introduction`, `afterword`, `appendix`, and `part`.
- DAISY synchronization practice treats section headings as separate
  synchronization units from following body content.
- Audiobook distribution practice uses explicit opening and closing credits
  and preserves named navigational sections.

Primary references checked on June 24, 2026:

- [EPUB 3.3](https://www.w3.org/TR/epub-33/) defines the spine as the default
  reading order, including linear and non-linear content; it does not assign
  semantic chapter ordinals.
- [EPUB 3 Structural Semantics Vocabulary
  1.1](https://www.w3.org/TR/epub-ssv-11/) defines distinct semantics for
  chapter, part, prologue, epilogue, epigraph, preface, introduction,
  afterword, appendix, title page, credits, and related structures.
- [DAISY Navigable Audio-only EPUB 3
  Guidelines](https://daisy.org/info-help/guidance-training/standards/navigable-audio-only-epub3-guidelines/)
  recommend content/media-overlay granularity of one document per heading.
- [DAISY 2.02 Skippable Structures
  Recommendation](https://daisy.org/activities/standards/daisy/daisy-2/daisy-2-02-skippable-structures-recommendation/)
  explicitly separates synchronized section headers from the following
  section content.
- [ACX audio submission
  requirements](https://help.acx.com/s/article/what-are-the-acx-audio-submission-requirements)
  require opening credits to identify the title, author, and narrator and
  require closing credits.

## Version-2 model

The pipeline emits ordered audiobook `Section` objects:

```json
{
  "sequence": 3,
  "section_type": "chapter",
  "ordinal": 1,
  "heading": {
    "display_label": "I",
    "display_title": "Down the Rabbit-Hole",
    "spoken_text": "Chapter One. Down the Rabbit-Hole.",
    "element_ids": ["sec3-el0000", "sec3-el0001"]
  },
  "body_elements": [],
  "word_count": 2143
}
```

- `sequence` is the 1-based playback/storage order.
- `section_type` is structural meaning, not presentation.
- `ordinal` is nullable and is populated only when the source supplies a
  valid chapter ordinal.
- `heading.display_label` and `heading.display_title` preserve source display
  text.
- `heading.spoken_text` is deterministic synthesis/alignment text and may
  differ from display text.
- heading elements never appear in body paragraphs or body chunks.

The supported section types are `opening_credits`, `chapter`, `prologue`,
`epilogue`, `epigraph`, `preface`, `introduction`, `afterword`, `appendix`,
`part`, `closing_credits`, and `other`.

## Parsing and classification

Classification precedence is:

1. `epub:type` tokens on the nearest semantic container or heading;
2. explicit source heading labels such as `Chapter 2`, Roman/Arabic ordinals,
   `Prologue`, or `Epilogue`;
3. EPUB navigation label and known semantic filename;
4. a standalone leading title becomes an unnumbered `chapter`;
5. `other`.

Spine position never creates a chapter ordinal. Multiple semantic containers
inside one XHTML spine item become separate sections. Source title-page,
half-title-page, imprint, navigation, copyright, colophon, illustration-list,
and cover documents are not audiobook sections.

Documents using meaningful block-level `div` elements are supported by
extracting leaf block boundaries with stable IDs. The parser never collapses a
whole document into one fallback paragraph.

Documents and semantic containers are retained when they contain spoken body
text or a recognized named section, even when under the prior 50-word
threshold. Empty decorative containers are skipped.

## Credits and spoken headings

Each rendition generates deterministic credits:

- opening: `{Title}. Written by {Author}. Narrated by OpenShelf using the
  {voice display name} voice.`
- closing: `The end of {Title}, written by {Author}, narrated by OpenShelf
  using the {voice display name} voice.`

English chapter ordinals are spoken as words. For example, display label `I`
with ordinal `1` becomes `Chapter One`. Unsupported languages preserve source
heading text and do not inject an English `Chapter`.

Headings and credits use narrator voice, bypass character attribution and
performance-direction LLM calls, and are synthesized before body chunks as
their own alignment region. A 750 ms pause separates a heading from the first
body chunk.

## Public compatibility

This is an intentional breaking contract:

- pipeline version and public artifact versions become 2;
- chapter artifact names become `section-NN.*`;
- `chapter_data.json` becomes `section_data.json`;
- rendition manifests expose `sections` and `section_count`;
- worker routes become `/sections/:sequence` and
  `/sections/:sequence/audio`;
- the client stores progress by section sequence.

Version-1 builds are not normalized. After a complete version-2 build is
uploaded and the mutable book manifest points only to version-2 builds, the
uploader deletes superseded version-1 build prefixes. Deletion is last,
reported, and retryable; it cannot invalidate the newly published build.

## Regression and acceptance cases

- Roman, Arabic, prefixed, standalone-title, and combined label/title headings.
- Named prologue, epilogue, epigraph, preface, introduction, afterword,
  appendix, and part sections remain unnumbered unless the source supplies an
  ordinal appropriate to that type.
- A preceding epilogue or epigraph cannot cause source chapter 1 to be exposed
  as chapter 2.
- Heading audio is a separate engine call and separate WhisperX alignment
  region before body audio.
- Heading and body timestamps remain monotonic; body `chunk_idx` values stay
  zero-based.
- Tapping a displayed heading seeks to its first spoken timestamp; body
  tap-to-seek remains unchanged.
- Alice produces opening credits, an unnumbered Epigraph section, twelve
  chapters with ordinals 1–12, and closing credits. Chapter 1 body begins with
  `Alice was beginning...`, not its heading.
