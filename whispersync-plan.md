# Whispersync: Paragraph-Level Audio-Text Synchronization

## Problem

When switching between reading (EPUB text) and listening (MP3 audio), the user needs to resume at the exact same position in both modalities. This requires a bidirectional map between paragraph indices in the text and time offsets in the audio.

## Solution

Alignment data is captured at TTS generation time for free — no post-processing needed. Two data structures work together:

### 1. `chunks.json` (v2) — Text Side

Each chunk now carries the paragraph range it was built from:

```json
{
  "version": 2,
  "chapters": [{
    "number": 1,
    "chunks": [
      { "text": "...", "para_start": 0, "para_end": 2 },
      { "text": "...", "para_start": 3, "para_end": 5 }
    ]
  }]
}
```

- `para_start` / `para_end` — inclusive indices into `Chapter.paragraphs`
- Enables: "which chunk contains paragraph N?"

### 2. `alignment.json` — Audio Side

Per-rendition file mapping each chunk to its audio start time:

```json
{
  "version": 1,
  "rendition": "kokoro-af-heart",
  "chapters": [{
    "number": 1,
    "total_duration_s": 1234.5678,
    "chunks": [
      { "chunk_idx": 0, "audio_start_s": 0.0 },
      { "chunk_idx": 1, "audio_start_s": 45.6 }
    ]
  }]
}
```

- Skipped chunks (TTS failures) are excluded; `chunk_idx` may have gaps
- Values rounded to 4 decimal places (sub-millisecond precision at 24kHz)

## Frontend Usage

### Audio -> Text (user is listening, highlight the right paragraph)

1. Get `currentTime` from the audio player
2. Binary search `alignment.json` chunks for the largest `audio_start_s <= currentTime`
3. Use matching `chunk_idx` to look up `para_start`..`para_end` from `chunks.json`
4. Highlight those paragraphs in the reader

### Text -> Audio (user taps a paragraph, seek audio to match)

1. User taps paragraph at index `N`
2. Find the chunk in `chunks.json` where `para_start <= N <= para_end`
3. Use that chunk's index to look up `audio_start_s` in `alignment.json`
4. Seek audio player to that time

## What Changed in the Pipeline

| File | Change |
|---|---|
| `epub_parser.py` | `Chapter` gains `paragraphs: list[str]` — stable indexed list of paragraph texts |
| `text_chunker.py` | `chunk_text` takes `list[str]` paragraphs, returns `list[Chunk]` with `para_start`/`para_end`; chunks.json bumped to v2 |
| `tts.py` | `SynthesisResult` gains `chunk_audio_starts: list[float]` — frame offsets tracked during synthesis |
| `alignment.py` | **New** — `build_alignment()` and `write_alignment()` |
| `r2.py` | **New** — `upload_alignment()` uploads alignment.json to R2 |
| `convert-book.py` | Wired: paragraph chunking, alignment generation, alignment upload |
| `upload-books.py` | Conditional alignment upload if file exists locally |

## Timing Model

Silence is inserted BETWEEN chunks (not before the first):

```
[chunk0_audio][silence][chunk1_audio][silence][chunk2_audio]
```

- Chunk 0 starts at frame 0
- Chunk 1 starts at `len(chunk0) + silence_frames`
- `audio_start_s = frames_so_far / sample_rate`

## Migration for Existing Books

Books converted before this change have v1 `chunks.json` (no `para_start`/`para_end`) and no `alignment.json`. To upgrade:

1. Delete local `chunks.json` (and optionally the R2 copy at `books/{author}/{title}/chunks.json`)
2. Re-run `python scripts/convert-book.py <epub> --upload`
3. Existing MP3s are skipped (idempotent), but new `chunks.json` v2 and `alignment.json` are generated
4. For pre-existing MP3s, all `chunk_audio_starts` are set to `-1.0` (timing unknown without re-synthesis)
5. Frontend should degrade gracefully: disable paragraph-seek for chapters with all-unknown alignment
