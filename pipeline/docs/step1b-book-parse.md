# Step 1b — `book_parse.json` (durable parse artifact)

**Module:** `src/openshelf/pipeline/epub_parser.py`
**Test:** `tests/pipeline/test_epub_parser.py`

## Purpose

Persist the output of `parse_epub` so downstream stages (`chunk`, EPUB
annotation) can run without re-parsing the EPUB. This is the first durable
artifact in the DAG: deterministic for a given EPUB + parser version.

`book_parse.json` is a **local per-build durable artifact**. It is kept on the
local filesystem to make `parse`/`chunk` resumable, but it is **not** part of
the public R2 contract and is not uploaded. (Contrast with WAV intermediates,
which are not kept at all; `book_parse.json` is cheap and worth keeping locally.)

## Shape

Derived from the `Chapter` / `ContentElement` dataclasses in `epub_parser.py`.

```json
{
  "version": 1,
  "epub_hash": "sha256:<hex of epub bytes>",
  "parser_version": "1",
  "metadata": { "title": "...", "author": "...", "source": "gutenberg" },
  "chapters": [
    {
      "number": 1,
      "title": "Chapter I",
      "epub_item_name": "chapter-1.xhtml",
      "word_count": 1234,
      "elements": [
        { "id": "ch1-el0001", "tag": "p", "html": "<p id=...>...</p>",
          "text": "...", "spoken": true }
      ]
    }
  ]
}
```

- `parser_version` mirrors `config.PIPELINE_VERSION`. A change in parser version
  changes the artifact and is the signal to re-parse.
- `metadata.source` is supplied by the caller (it is not in the EPUB);
  `title`/`author` come from the EPUB's Dublin Core metadata.
- `elements` is the full `ContentElement` list (spoken and non-spoken), so the
  artifact is sufficient to reconstruct `paragraphs` (spoken element text) and
  `spoken_ids` (spoken element IDs) for chunking and to drive EPUB annotation.

## Functions

```python
def build_book_parse_artifact(
    chapters: list[Chapter], epub_hash: str, metadata: dict
) -> dict
def read_book_parse_artifact(path: str) -> dict
def write_book_parse_artifact(path: str, payload: dict, force: bool = False) -> str
def epub_sha256(epub_path: str) -> str          # "sha256:<hex>"
def read_book_metadata(epub_path: str) -> dict   # {"title","author"}
```

Idempotent write follows the DAG rule: if the file exists with identical
content, skip; if it exists with different content, raise unless `force=True`.

## Consumed by

- `dag_cli chunk` — reconstructs each chapter's spoken `paragraphs` and
  `element_ids`, then calls `text_chunker.chunk_text(...)` to write
  `chapter-NN.chunks.json`. Output is byte-identical to the inline path in
  `convert-book.py`.
