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
| `build` | 16-hex build ID from `new_build_id()` by default, or `--build-id` when resuming a targeted build |
| `rendition`, `voice`, `engine` | CLI / config |
| `pipeline_version` | `config.PIPELINE_VERSION` |
| `total_duration_seconds` | Sum of chapter durations |
| `chapters[].title` | The original (pre-normalization) chapter title |
| `chapters[].filename` | Constructed by caller (e.g. `chapter-01.m4a`) |
| `chapters[].duration_seconds` | From encoder / ffprobe |
| `chapters[].word_count` | From `epub_parser` |

There is intentionally no `generated_at` field. Wall-clock metadata belongs on the mutable book manifest, not on a per-build immutable artifact. Each pipeline run assigns a fresh random `build_id` — so any two runs produce different files under different keys, and the byte-stability question for a *given* build never comes up: nothing else ever writes to the same key.

### Idempotency

Like every per-build artifact, this file is written into a build-versioned R2
path. Default reprocesses mint a fresh build ID and upload under a new prefix.
Explicit `--build-id --resume` runs may reuse a local build prefix only when
`run.json` matches; R2 upload is still gated by the manifest-last single-gate
strategy.

### Worker usage

The worker reads this file at request time to enrich `GET /books/:a/:t` responses. For each rendition in the book manifest, the worker fetches the rendition-manifest for that rendition's `current_build` and inlines its `chapters` array into the response.

The worker also uses this file for `GET /books/:a/:t/builds`, the optional build-selection endpoint. That route reads the retained build IDs from the book manifest's `available_builds` list, fetches each retained build's `rendition-manifest.json`, and returns per-build engine, voice, pipeline version, duration, and chapter metadata. The build-selection endpoint uses `Cache-Control: no-store` because the set of retained builds can change often even though each individual build is immutable.

## Dependencies

- Standard library only (json, os)
