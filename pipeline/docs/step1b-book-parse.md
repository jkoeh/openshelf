# Step 1b — `book_parse.json`

**Module:** `src/openshelf/pipeline/epub_parser.py`
**Test:** `tests/pipeline/test_epub_parser.py`

`book_parse.json` is the local durable version-2 parse artifact. It persists
semantic sections so chunking and EPUB annotation never need to infer headings
again.

```json
{
  "version": 2,
  "epub_hash": "sha256:...",
  "parser_version": "2",
  "metadata": {"title": "...", "author": "...", "language": "en"},
  "sections": [
    {
      "sequence": 2,
      "section_type": "chapter",
      "ordinal": 1,
      "heading": {
        "display_label": "I",
        "display_title": "Down the Rabbit-Hole",
        "spoken_text": "Chapter One. Down the Rabbit-Hole.",
        "element_ids": ["sec2-el0000", "sec2-el0001"]
      },
      "epub_item_name": "text/chapter-1.xhtml",
      "word_count": 2143,
      "elements": []
    }
  ]
}
```

`elements` includes heading, body, and non-spoken source elements.
`heading.element_ids` identifies elements excluded from body chunking.
Downstream stages reconstruct body elements by selecting spoken elements whose
IDs are not heading IDs.

The artifact is local-only and idempotent. Existing version-1 artifacts are
incompatible and require a new build.
