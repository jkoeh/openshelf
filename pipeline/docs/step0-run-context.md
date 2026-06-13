# Step 0: Run Context

**Module:** `src/openshelf/pipeline/run_context.py`
**Test:** `tests/pipeline/test_run_context.py`

## Purpose

`run.json` is the local per-build contract that lets a pipeline run resume into
the same output prefix without mixing artifacts from different inputs or
configuration.

The default pipeline still mints a fresh random 16-hex build ID. A caller may
instead provide `--build-id` to target an existing build prefix. If that prefix
already contains `run.json`, the caller must pass either `--resume` or
`--force`.

## CLI Contract

```bash
convert-book.py book.epub
convert-book.py book.epub --build-id 2a4f9c1b3d8e7f60 --resume
convert-book.py book.epub --build-id 2a4f9c1b3d8e7f60 --force
```

- No `--build-id`: mint a fresh random build as before.
- `--build-id`: use the caller-provided 16-hex build ID.
- `--resume`: reuse an existing `run.json` only if immutable inputs match.
- `--force`: overwrite `run.json` and downstream artifacts in the selected
  build prefix.

If `run.json` exists and neither `--resume` nor `--force` is provided, the
pipeline fails before expensive synthesis work. If `--resume` is provided and
the existing context does not match the current invocation, the pipeline fails.

## Artifact

`run.json` lives beside the build artifacts:

```text
audio/{rendition}/builds/{build}/run.json
```

Shape:

```json
{
  "version": 1,
  "build": "2a4f9c1b3d8e7f60",
  "pipeline_version": "1",
  "epub_path": "C:/books/alice.epub",
  "epub_sha256": "...",
  "author_slug": "lewis-carroll",
  "title_slug": "alices-adventures-in-wonderland",
  "book_author": "Lewis Carroll",
  "book_title": "Alice's Adventures in Wonderland",
  "engine": "chatterbox",
  "voice": "chatterbox-af_heart",
  "rendition": "chatterbox-af-heart",
  "cast_mode": "solo",
  "performance_direction_mode": "batched",
  "language": "en",
  "chapters": [1, 2, 3]
}
```

The file intentionally omits timestamps so a valid resumed run does not rewrite
the context for wall-clock-only reasons.

## Boundaries

`run.json` is written after EPUB metadata, chunking, narrator selection, and
rendition derivation, because the build path includes `rendition` and the
default rendition may depend on the selected narrator. It is still written
before TTS synthesis, encoding, manifest generation, or upload.

For the strongest resume behavior before any LLM work, callers should provide
both `--voice` and `--rendition`.

## Validation

Resume validation compares immutable fields:

- EPUB content hash
- author/title slugs
- engine
- narrator voice
- rendition
- cast mode
- performance direction mode
- language
- selected chapter set
- pipeline version

Mutable upload state is not part of `run.json`; R2 upload idempotency remains
gated by `rendition-manifest.json`.
