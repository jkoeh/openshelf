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

Later DAG commands (`parse`, `chunk`, `direct`, `synth`, `sync`, `upload`) should
follow the same idempotency rule: identical output skips, different output fails
unless forced.
