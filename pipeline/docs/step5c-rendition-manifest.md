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
  "build": "2a4f9c1",
  "rendition": "kokoro-af-heart",
  "voice": "af_heart",
  "engine": "kokoro",
  "pipeline_version": "2",
  "generated_at": "2026-05-10T12:34:56+00:00",
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
| `build` | `compute_build_id()` output |
| `rendition`, `voice`, `engine` | CLI / config |
| `pipeline_version` | `config.PIPELINE_VERSION` |
| `generated_at` | UTC ISO-8601 timestamp |
| `total_duration_seconds` | Sum of chapter durations |
| `chapters[].title` | The original (pre-normalization) chapter title |
| `chapters[].filename` | Constructed by caller (e.g. `chapter-01.m4a`) |
| `chapters[].duration_seconds` | From encoder / ffprobe |
| `chapters[].word_count` | From `epub_parser` |

### Idempotency

Like every per-build artifact, this file is written once into a build-versioned R2 path. If the upload already exists for `(rendition, build)`, the upload is skipped. A `--force` rerun re-uploads but produces byte-identical content for an unchanged build (the `compute_build_id` hash would have changed otherwise).

### Worker usage

The worker does not currently expose this file directly to clients — chapter durations are surfaced via the book manifest's per-rendition entry plus the chapter route response. It exists as a self-describing companion to the audio + chapter_data within the build prefix, useful for debugging, GC, and a future "rendition details" client view.

## Dependencies

- Standard library only (json, os, datetime)
