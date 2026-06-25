# Step 5c: Rendition Manifest (version 2)

**Module:** `src/openshelf/pipeline/manifest.py`
**Test:** `tests/pipeline/test_manifest.py`

The immutable rendition manifest describes ordered audiobook sections:

```json
{
  "version": 2,
  "build": "2a4f9c1b3d8e7f60",
  "rendition": "kokoro-af-heart",
  "voice": "af_heart",
  "engine": "kokoro",
  "pipeline_version": "2",
  "total_duration_seconds": 1847.3,
  "section_count": 15,
  "sections": [
    {
      "sequence": 2,
      "section_type": "epigraph",
      "ordinal": null,
      "display_label": "Epigraph",
      "display_title": "",
      "filename": "section-02.m4a",
      "duration_seconds": 90.2,
      "word_count": 229
    }
  ]
}
```

`sequence` is playback order. `ordinal` is source chapter numbering and is
nullable. The manifest is uploaded last and remains the build completion
signal. `word_count` is the total spoken heading plus body word count for the
section. Version-1 manifests containing `chapters` are incompatible.
