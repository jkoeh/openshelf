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

`catalog.json` is retained as a publication/audit artifact. Public catalog and
title/author search read D1 `published_books` with `no-store`. After writing the
R2 catalog, `ops catalog` submits publication keys in batches of at most 10 to
the PC-authenticated `/internal/catalog/sync`; the Worker reads and validates
the current R2 book/rendition manifests before indexing metadata/default builds.
Index synchronization requires `--api-base` or `OPENSHELF_API_BASE` plus
`OPENSHELF_PC_TOKEN`; failure propagates to the caller. Generation completion
also upserts its publication, and deletion removes its D1 catalog row.
Full catalog refresh also reconciles existing rows through a bounded
PC-authenticated cursor API. Missing/incompatible R2 publications are pruned only
after ETag and D1 revision checks, while active jobs/deletions remain protected.
An empty R2 scan still reconciles D1. Storage errors fail without pruning.
Rollout must backfill existing manual and imported publications after migration
`0009_published_catalog.sql`, `0010_catalog_revision.sql` and Worker deployment,
before client rollout.

Source reading uses `source-epubs/{provider}/{sha256(source_id)}/book.epub`, for
`gutenberg`, `standardebooks`, `wikisource`. See `source-access.md` for validation,
hash/provenance metadata and conditional-write rules.
This cache is uploaded through the PC-authenticated internal Worker route during
source catalog import for every provider, after archive, OPF identity/rights and spoken-text
validation. It is separate from audiobook builds and annotated `books/.../book.epub`.
The Worker limits copies to 32 MiB and streams them with `no-store` for Read now,
so reading does not depend on upstream availability or audio generation. Source
cache refreshes are operator-run through the existing catalog import command.

Owner documents use `owner-uploads/{uuid}/original.{pdf|docx|txt}`. These raw
objects have only authenticated owner/PC API access; they are not catalog or
source EPUB objects. After owner acknowledgement and PC extraction, generated
audio and annotated EPUBs use the normal public `books/...` layout with source
`local`, a stable upload-specific title suffix, and the existing build contract.

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
