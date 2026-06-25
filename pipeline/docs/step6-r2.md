# Step 6: R2 Upload — Version 2

**Modules:** `src/openshelf/pipeline/r2.py`,
`src/openshelf/pipeline/r2_keys.py`

## Layout

```text
books/{author}/{title}/
  book.epub
  cover.jpg
  manifest.json
  audio/{rendition}/builds/{build}/
    section-01.m4a
    section-01.synthesis_units.json
    section_data.json
    character_registry.json
    voice_direction.json
    run.json
    rendition-manifest.json
```

Every build object is immutable. `rendition-manifest.json` is uploaded last.
`manifest.json` remains the short-cached mutable pointer.

## `section_data.json`

```json
{
  "version": 2,
  "rendition": "kokoro-af-heart",
  "build": "2a4f9c1b3d8e7f60",
  "sections": [
    {
      "sequence": 3,
      "section_type": "chapter",
      "ordinal": 1,
      "heading": {
        "display_label": "I",
        "display_title": "Down the Rabbit-Hole",
        "spoken_text": "Chapter One. Down the Rabbit-Hole.",
        "words": [{"word": "Chapter", "start": 0.05, "end": 0.31}]
      },
      "word_count": 2143,
      "chunks": [
        {
          "text": "Alice was beginning...",
          "words": [{"word": "Alice", "start": 2.1, "end": 2.35}]
        }
      ]
    }
  ]
}
```

Heading words and body chunk words are separate. The worker adds
`region: "heading" | "body"` and nullable body `chunk_idx` when flattening.

`upload_rendition_build` uploads `section-*.m4a`, synthesis audits,
`section_data.json`, optional audit artifacts, and the rendition manifest last.

After the new manifest pointer is published, `delete_build_prefixes` deletes
superseded version-1 build prefixes. Deletion uses paginated listing and
batched object deletion. Failures are logged and raised for operator retry;
they do not alter the published version-2 pointer.
