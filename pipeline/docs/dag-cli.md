# Pipeline DAG CLI

**Module:** `src/openshelf/pipeline/dag_cli.py`
**Test:** `tests/pipeline/test_dag_cli.py`

## Purpose

Expose file-to-file pipeline stages as local commands so expensive work can be
resumed, repaired, and parallelized. Commands read explicit artifacts, write
explicit artifacts, and avoid calling earlier stages implicitly.

This document starts with the assembly repair command because it is the safest
stage boundary: it needs no EPUB parsing, LLM, TTS, WhisperX, ffmpeg, network, or
R2 access.

## Commands

### `parse`

```bash
python -m openshelf.pipeline.dag_cli parse \
  --epub download/books/kafka/metamorphosis.epub \
  --out audio/kafka/metamorphosis/book_parse.json \
  --source gutenberg
```

Input:

- an EPUB file

Output:

- `book_parse.json` (see `docs/step1b-book-parse.md`)

Behavior:

- Parses the EPUB with `parse_epub`, computes the EPUB content hash, reads
  title/author from EPUB metadata, and records `--source`.
- Deterministic for a given EPUB + parser version. Identical output skips;
  different output fails unless `--force`.
- Does not call the LLM, TTS, WhisperX, ffmpeg, or R2.

### `chunk`

```bash
python -m openshelf.pipeline.dag_cli chunk \
  --book-parse audio/kafka/metamorphosis/book_parse.json \
  --build-dir audio/kafka/metamorphosis/audio/{rendition}/builds/{build} \
  --chapters 2,4-5
```

Input:

- `book_parse.json`

Output:

- `chapter-NN.chunks.json` for each selected chapter

Behavior:

- Reconstructs each chapter's spoken paragraphs and element IDs from
  `book_parse.json` and calls `text_chunker.chunk_text(...)`, producing chunk
  artifacts byte-identical to the inline `convert-book.py` path.
- `--chapters` accepts a number/range filter (e.g. `2` or `2,4-5`); omitted
  means all chapters.
- Identical output skips; different output fails unless `--force`.
- Does not call the LLM, TTS, WhisperX, ffmpeg, or R2.

### `assemble`

```bash
python -m openshelf.pipeline.dag_cli assemble \
  --build-dir audio/{rendition}/builds/{build} \
  --rendition kokoro-af-heart \
  --build-id 2a4f9c1b3d8e7f60
```

Input:

- `chapter-NN.chunks.json` for every chapter to assemble
- `chapter-NN.sync.json` for every chapter to assemble

Output:

- `chapter_data.json`

Behavior:

- Chapters are ordered by chapter number from the chunk artifact filenames.
- Every selected chapter must have both a chunk artifact and a sync artifact.
- The output shape is the existing public `chapter_data.json` contract:
  version, rendition, build, chapters, chapter chunks, and inline word timings.
- If `chapter_data.json` already exists with identical content, the command
  succeeds without rewriting it.
- If `chapter_data.json` exists with different content, the command fails unless
  `--force` is provided.
- The command does not call the LLM, TTS, WhisperX, ffmpeg, or R2.

### `coverage`

```bash
python -m openshelf.pipeline.dag_cli coverage \
  --build-dir audio/{rendition}/builds/{build}
python -m openshelf.pipeline.dag_cli coverage \
  --build-dir audio/{rendition}/builds/{build} --json
```

Input:

- `chapter-NN.sync.json` for every chapter in the build

Output:

- A human-readable coverage report on stdout (default), or a JSON report with
  `--json`.

Behavior:

- Reads the `coverage` block already recorded inside each `chapter-NN.sync.json`
  and aggregates it into book-level totals: reader word count, aligned word
  count, coverage ratio, and the first missing word offset when detectable.
- Chapters are ordered by chapter number from the sync artifact filenames.
- This command is **diagnostic only**. It never fails on low or missing
  coverage and never blocks upload. Per the Sync Coverage Policy, sync gaps are
  a pipeline bug to detect and fix, not a policy gate or a separate build health
  state. The command exits non-zero only on real I/O errors (e.g. no sync
  artifacts found).
- The command does not call the LLM, TTS, WhisperX, ffmpeg, or R2.

### `upload`

```bash
python -m openshelf.pipeline.dag_cli upload \
  --book-dir audio/kafka/metamorphosis \
  --rendition kokoro-af-heart \
  --build-id 2a4f9c1b3d8e7f60
```

Input (all local, already produced by earlier stages):

- `audio/{rendition}/builds/{build}/chapter-NN.m4a`, `chapter_data.json`,
  `rendition-manifest.json` (and optional `character_registry.json`,
  `voice_direction.json`, `run.json`)
- book-level `cover.{jpg,png}` and `book-annotated.epub` when present
- book-level `manifest.json` carrying the rendition entry

Output:

- the immutable per-(rendition, build) objects on R2, plus the mutable book
  `manifest.json` updated so this build is `current_build` for the rendition.

Behavior:

- Wraps `r2.upload_cover` / `upload_epub` / `upload_rendition_build` and merges
  the local rendition entry into the prior R2 book manifest via
  `manifest.merge_book_manifest`, so other renditions survive.
- Lets a repaired build publish without rerunning parse/direct/synth/sync.
- The rendition-manifest is the single-gate idempotency signal: an already
  uploaded build is skipped unless `--force` (the existing
  `upload_rendition_build` rule).
- boto3/R2 access is imported lazily, so the offline commands above never
  require R2 credentials or boto3.

### `direct`

```bash
python -m openshelf.pipeline.dag_cli registry \
  --book-parse book_parse.json \
  --build-dir audio/{rendition}/builds/{build} \
  --engine chatterbox --voice chatterbox-bf_emma

python -m openshelf.pipeline.dag_cli direct \
  --build-dir audio/{rendition}/builds/{build} \
  --chapter 2 --engine chatterbox \
  --performance-direction batched
```

Input:

- `chapter-NN.chunks.json` (reader text per chunk)
- `character_registry.json` (narrator voice + cast)

Output:

- `character_registry.json` from `registry`

- `chapter-NN.voice_direction.json` (the LLM↔audio boundary)

Behavior:

- `registry` builds only the narrator/character registry from `book_parse.json`
  and writes it to the build directory. `--voice` bypasses the registry LLM for
  the narrator by selecting an engine voice directly; omitted `--voice` lets the
  LLM choose from the engine's voice pool.
- Runs `AudioDirector.direct_chapter` to produce directed segments (speaker,
  voice, and engine-supported performance steering), then writes the per-chapter
  voice-direction artifact. `direct --performance-direction {batched,chunk,off}`
  selects the same engine-aware performance mode as `convert-book.py`.
- `registry` and `direct` are the only repair stages that may call the LLM.
- **Solo cast mode only.** Multicast registry repair / converting an existing
  solo build to multicast is out of scope (see the resumable-repair plan); the
  command rejects `--cast-mode multicast`.
- `rendition` and `build` are derived from the `audio/{rendition}/builds/{build}`
  path. Identical output skips; different output fails unless `--force`.
- Does not call TTS, WhisperX, ffmpeg, or R2.

### `synth`

```bash
python -m openshelf.pipeline.dag_cli synth \
  --build-dir audio/{rendition}/builds/{build} \
  --chapter 2 --engine kokoro --device cuda
```

Input:

- `chapter-NN.voice_direction.json` (directed segments)
- `chapter-NN.chunks.json` (canonical text + paragraph boundaries)
- `character_registry.json` (narrator voice fallback, optional)

Output:

- `chapter-NN.m4a` and `chapter-NN.sync.json`

Behavior:

- Builds the per-chunk `ChunkInfo` list from the chunks artifact (paragraph
  boundaries) and the voice-direction artifact (segments), runs TTS + WhisperX
  alignment, encodes to AAC, and writes the sync artifact. Covers both fresh
  synthesis and pause/stitch-policy **restitch** repair — there are no durable
  WAV/unit intermediates to restitch from, so audio is regenerated from the
  existing direction (no separate `restitch` verb).
- `--device` is the selected runtime device for both the TTS engine adapter
  when that engine accepts a device (Kokoro, F5-TTS, Chatterbox) and the
  WhisperX aligner when forced alignment is required.
- Idempotency: if both the m4a and sync artifacts already exist, the stage
  **skips** unless `--force` (TTS is expensive and not deterministic, so the
  file-exists gate stands in for an input fingerprint).
- Does not call the LLM. After `synth`, run `assemble` then `upload`.

### `sync`

```bash
python -m openshelf.pipeline.dag_cli sync \
  --build-dir audio/{rendition}/builds/{build} \
  --chapter 2 --device cuda --force
```

Input:

- `chapter-NN.m4a` (chapter audio)
- `chapter-NN.chunks.json` (reader text per chunk)
- `chapter-NN.sync.json` (the **prior** sync artifact — its
  `chunk_audio_starts` are the per-chunk audio offsets produced at synthesis
  time and are not recoverable from the m4a alone; `align_chapter` requires them)

Output:

- a rewritten `chapter-NN.sync.json` with improved word timings + coverage

Behavior:

- Re-runs WhisperX forced alignment and regroups words back into chunks. This is
  a **re-alignment repair**: it improves timings for an existing sync but cannot
  create one from scratch (no prior `chunk_audio_starts` ⇒ nothing to align
  against). For a chapter that has never been synthesized, run `synth`.
- Because the output differs from the prior artifact by design, the command
  **requires `--force`** to overwrite (the standard idempotency rule: identical
  output skips; different output fails unless forced).
- Does not call the LLM or TTS. After `sync`, run `assemble` to rebuild
  `chapter_data.json`.

## Full local pipeline

`run` is the canonical full-book orchestrator. It performs the same staged work
as the manual commands below, writes the same build artifacts, and is the path
used by `process-books.py`. `convert-book.py` is a compatibility wrapper around
this runner.

```bash
python -m openshelf.pipeline.dag_cli run \
  --epub book.epub \
  --output audio \
  --source gutenberg \
  --engine chatterbox \
  --device cuda \
  --performance-direction batched \
  --upload
```

The equivalent manual stage sequence is:

```bash
pipeline parse   --epub book.epub --out book_parse.json --source gutenberg
pipeline chunk   --book-parse book_parse.json --build-dir {build_dir}
pipeline registry --book-parse book_parse.json --build-dir {build_dir} --engine kokoro --voice af_heart
pipeline direct  --build-dir {build_dir} --chapter N --engine kokoro
pipeline synth   --build-dir {build_dir} --chapter N --engine kokoro --device cuda
pipeline assemble --build-dir {build_dir} --rendition {r} --build-id {b}
pipeline upload  --book-dir {book_dir} --rendition {r} --build-id {b}
```

`direct`, `synth`, and `sync` run per chapter and are safe to parallelize across
chapters once `character_registry.json` exists. `assemble`, `coverage`, and
`upload` operate on the whole build. `registry` runs once per book/build before
chapter direction. `sync` is a later re-alignment repair command; initial word
sync is produced by `synth`.

`run` preserves the legacy CLI flags from `convert-book.py`: `--epub`,
`--output`, `--source`, `--engine`, `--voice`, `--rendition`, `--cast-mode`,
`--performance-direction`, `--device`, `--chapters`, `--dry-run`, `--keep-wav`,
`--upload`, `--log-dir`, `--build-id`, `--resume`, and `--force`.
For engines with a device-aware adapter, `run --device cuda` passes `cuda` into
the engine constructor before lazy model load; it also uses the same selected
device for WhisperX forced alignment.
