# Step 5c: Rendition Manifest (per-build)

**Module:** `src/openshelf/pipeline/manifest.py`
**Test:** `tests/pipeline/test_manifest.py`

## Purpose

The per-build `rendition-manifest.json` is an **immutable artifact** that lives alongside the audio and chapter_data for a single (rendition, build) pair. It records exactly which chapters that build produced, their filenames, durations, and word counts.

Splitting these per-build details out of the book-level manifest lets that pointer file stay tiny and stable, while making the per-build snapshot self-describing — anything a client needs to play back this build comes from one R2 prefix.

```mermaid
graph TD
    A[ChapterMeta list] --> B[generate_rendition_manifest]
    B --> C[rendition-manifest.json]
    C --> D[Upload under audio/{rendition}/builds/{build}/]
```

## Interface

### Public Function

```python
def generate_rendition_manifest(
    rendition: str,
    build_id: str,
    voice: str,
    engine: str,
    pipeline_version: str,
    chapters: list[ChapterMeta],
    output_dir: str,
) -> str  # returns path to rendition-manifest.json
```

## Behavior

### `rendition-manifest.json` shape

```json
{
  "build": "2a4f9c1b3d8e7f60",
  "rendition": "kokoro-af-heart",
  "voice": "af_heart",
  "engine": "kokoro",
  "pipeline_version": "1",
  "total_duration_seconds": 1847.3,
  "chapters": [
    {
      "number": 1,
      "title": "I",
      "filename": "chapter-01.m4a",
      "duration_seconds": 1847.3,
      "word_count": 3241
    }
  ]
}
```

### Fields

| Field | Source |
|---|---|
| `build` | `new_build_id()` output (16-hex random) |
| `rendition`, `voice`, `engine` | CLI / config |
| `pipeline_version` | `config.PIPELINE_VERSION` |
| `total_duration_seconds` | Sum of chapter durations |
| `chapters[].title` | The original (pre-normalization) chapter title |
| `chapters[].filename` | Constructed by caller (e.g. `chapter-01.m4a`) |
| `chapters[].duration_seconds` | From encoder / ffprobe |
| `chapters[].word_count` | From `epub_parser` |

There is intentionally no `generated_at` field. Wall-clock metadata belongs on the mutable book manifest, not on a per-build immutable artifact. Each pipeline run assigns a fresh random `build_id` — so any two runs produce different files under different keys, and the byte-stability question for a *given* build never comes up: nothing else ever writes to the same key.

### Idempotency

Like every per-build artifact, this file is written once into a build-versioned R2 path. Because each pipeline run mints a new `build_id`, the upload is gated solely on resuming a partial same-run upload (manifest-last single-gate). A `--force` reprocess produces a fresh build with a fresh ID and uploads under a new prefix.

### Worker usage

The worker reads this file at request time to enrich `GET /books/:a/:t` responses. For each rendition in the book manifest, the worker fetches the rendition-manifest for that rendition's `current_build` and inlines its `chapters` array into the response. The file is not exposed via its own HTTP route — see `worker/CLAUDE.md` for the merged response shape.

## Dependencies

- Standard library only (json, os)
