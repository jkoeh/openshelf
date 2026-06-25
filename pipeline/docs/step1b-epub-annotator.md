# Step 1b: EPUB Annotator

**Module:** `src/openshelf/pipeline/epub_annotator.py`
**Test:** `tests/pipeline/test_epub_annotator.py`

Inject `ContentElement.id` values from parsed `Section` objects back into the
source EPUB. Sections are grouped by `epub_item_name`, so multiple semantic
sections in one XHTML document are supported.

The annotator replays the parser’s content-tag traversal and cleanup rules,
including meaningful leaf `div` elements, then assigns IDs in source order.
Heading IDs remain addressable separately from body IDs.

```python
def annotate_epub(epub_path: str, sections: list[Section]) -> bytes
```

Items without parsed sections are unchanged. The returned EPUB bytes are the
immutable `book.epub` uploaded to R2.
