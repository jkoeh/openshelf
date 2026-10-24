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
openshelf-pipeline dag run --epub book.epub
openshelf-pipeline dag run --epub book.epub --build-id 2a4f9c1b3d8e7f60 --resume
openshelf-pipeline dag run --epub book.epub --build-id 2a4f9c1b3d8e7f60 --force
```

- No `--build-id`: mint a fresh random build as before.
- `--build-id`: use the caller-provided 16-hex build ID.
- `--resume`: reuse an existing `run.json` only if immutable inputs match.
  Reuse completed section audio only when the local completion receipt validates
  all audio/sync/audit hashes and exact synthesis inputs. Otherwise continue
  from matching durable heading/body checkpoints. See `step3-tts.md`.
- `--force`: overwrite `run.json` and downstream artifacts in the selected
  build prefix.

If `run.json` exists and neither `--resume` nor `--force` is provided, the
pipeline fails before expensive synthesis work. If `--resume` is provided and
the existing context does not match the current invocation, the pipeline fails.

## Artifact

The `language` field preserves legacy English Kokoro configuration `a`. Chinese
builds record `zh`; the source OPF determines model language, voice compatibility
and alignment. A changed language invalidates resume just like other immutable
inputs. See `source-access.md`.

`run.json` lives beside the build artifacts:

```text
audio/{rendition}/builds/{build}/run.json
```

Shape:

```json
{
  "version": 2,
  "build": "2a4f9c1b3d8e7f60",
  "pipeline_version": "2",
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
  "sections": [1, 2, 3]
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
- voice-direction refresh mode (`--new-voice-direction`)
- language
- selected chapter set
- pipeline version
- engine settings (model/checkpoint/instruction/precision/generation settings for Qwen)

`engine_settings` is a JSON object, empty for existing adapters. Missing values
in legacy contexts normalize to `{}`. Qwen contexts record the pinned settings
from `engine.synthesis_settings`; any change fails resume. The artifact remains
version 2 because existing empty-setting contexts preserve their compatibility.

Mutable upload state is not part of `run.json`; R2 upload idempotency remains
gated by `rendition-manifest.json`.

## Recovery into an ordinary fresh job

`build_recovery.py` can adopt validated local work into a fresh build issued by
an ordinary owner generation request. It compares all resume fields except the
build ID, requires an empty target directory, writes the new `run.json`, and
copies only hash-validated completed sections or individually validated region
checkpoints. Direction artifacts retain their original directed text/voices but
record the new build ID. A local `recovery.json` records the source/target build
and adopted artifact hashes. Public manifests and publication pointers are never
copied. The normal consumer must finish every remaining section, publish, and
pass the Worker's existing lease/publication verification.

This local operation neither changes prior job history nor resets attempts,
leases, queue limits or daily starts. A running synthesis process must be stopped
at a durable section boundary before adoption; concurrent writers are prohibited.
