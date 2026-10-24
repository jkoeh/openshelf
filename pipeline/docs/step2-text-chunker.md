# Step 2: Body Text Chunker

**Module:** `src/openshelf/pipeline/text_chunker.py`
**Test:** `tests/pipeline/test_text_chunker.py`

Chunk only section body paragraphs. Heading display/spoken text is metadata and
never participates in greedy body packing.

```python
@dataclass
class Chunk:
    text: str
    para_start: int
    para_end: int
    el_start: str = ""
    el_end: str = ""
```

`chunk_text(..., language='en')` preserves English sentence-aware 200-word
packing and body element IDs. Chinese uses 200 text units (Han characters and
non-Han words), Chinese punctuation boundaries and hard splits as needed,
without inserting character spaces. See `source-access.md` for shared units.

Each section writes `section-NN.chunks.json`:

```json
{
  "version": 2,
  "sequence": 2,
  "section_type": "chapter",
  "ordinal": 1,
  "heading": {
    "display_label": "I",
    "display_title": "Down the Rabbit-Hole",
    "spoken_text": "Chapter One. Down the Rabbit-Hole.",
    "element_ids": ["sec2-el0000", "sec2-el0001"]
  },
  "chunks": [
    {
      "index": 0,
      "text": "Alice was beginning...",
      "para_start": 0,
      "para_end": 0,
      "el_start": "sec2-el0002",
      "el_end": "sec2-el0002",
      "text_hash": "sha256:..."
    }
  ]
}
```

Credits use the same artifact with an empty body chunk list and their complete
spoken credit line in `heading.spoken_text`.

Artifact writers are idempotent. Version-1 `chapter-NN.chunks.json` artifacts
are incompatible.
