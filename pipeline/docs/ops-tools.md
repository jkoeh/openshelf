# Pipeline Ops Tools

**Modules:** `src/openshelf/pipeline/ops/*`
**Command:** `openshelf-pipeline ops ...`
**Installed command:** `openshelf-pipeline`
**Tests:** `tests/pipeline/test_gpu_preflight.py`, `tests/pipeline/test_pipeline_doctor.py`, `tests/pipeline/test_pipeline_runner.py`

## Purpose

Provide small local tools for the operator work that surrounds full audiobook
generation:

- check that the selected TTS engine will use the intended accelerator before
  an expensive run starts
- run book processing with GPU-first defaults and background PID/log support
- inspect a local build directory and logs after a run

These tools do not change the public R2/client contract. They read existing
local artifacts and delegate real generation/upload work to the DAG pipeline.

## GPU Defaults

Pipeline invocations should be accelerator-first unless the caller explicitly
forces CPU. Device resolution follows this order:

1. `--device cuda` requires a CUDA-capable PyTorch install and at least one CUDA
   device.
2. `--device mps` requires a PyTorch MPS backend.
3. `--device cpu` is an explicit slow-path override and is allowed.
4. `--device auto` selects CUDA when available, then MPS, then CPU.

For Chatterbox, `auto` must fail instead of silently falling back to CPU when no
accelerator is available. CPU Chatterbox can be forced with `--device cpu`, but
the tool reports it as a warning because full-book runs are usually impractical
on CPU.

The preflight package check is intentionally separate from synthesis:

- default checks import PyTorch, report its version, CUDA build, device count,
  and selected device
- optional `--load-engine` also constructs the selected OpenShelf adapter and
  loads its runtime so model-placement mistakes are caught before a book run

## `ops gpu-preflight`

```bash
openshelf-pipeline ops gpu-preflight --engine chatterbox
openshelf-pipeline ops gpu-preflight --engine chatterbox --device cuda
openshelf-pipeline ops gpu-preflight --engine chatterbox --device cpu
openshelf-pipeline ops gpu-preflight --engine chatterbox --load-engine
```

Behavior:

- exits non-zero when a requested accelerator is unavailable
- exits non-zero for Chatterbox `--device auto` when only CPU is available
- exits zero for explicit CPU, while warning that it is slow for Chatterbox
- prints either a human report or JSON with `--json`
- never downloads or loads a model unless `--load-engine` is provided

## `books process`

```bash
openshelf-pipeline books process \
  --epub download/books/standard-ebooks/lewis-carroll/alices-adventures-in-wonderland.epub \
  --engine chatterbox \
  --voice chatterbox-bf_emma \
  --upload

openshelf-pipeline books process \
  --author "Lewis Carroll" --book "Alice" \
  --engine chatterbox \
  --background
```

Behavior:

- runs GPU preflight before launching unless `--skip-preflight` is passed
- passes the resolved device into `dag run` so the DAG engine adapter
  is constructed on the intended device before lazy model load
- supports book selectors and pipeline flags
- foreground mode streams the DAG run output
- background mode writes stdout/stderr logs plus a PID file and returns after
  launch

`books process` is the human-facing happy path. `dag run` remains the explicit
EPUB conversion path, and individual `dag` stages remain the repair path.

## `ops doctor`

```bash
openshelf-pipeline ops doctor \
  --build-dir audio/lewis-carroll/alices-adventures-in-wonderland/audio/chatterbox-bf-emma/builds/e3eebabdbf3e88ca \
  --log logs/20260613-071007-dag-run-alices-adventures-in-wonderland-e3eebabdbf3e88ca.log
```

Behavior:

- verifies the build directory exists
- counts chapter chunk, direction, audio, sync, and manifest artifacts
- compares chapter sets across `chapter-NN.chunks.json`,
  `chapter-NN.voice_direction.json`, `chapter-NN.m4a`, and
  `chapter-NN.sync.json`
- checks `chapter_data.json` and `rendition-manifest.json` chapter counts when
  present
- reports sync artifacts with skipped chunk markers or low coverage as warnings
- scans an optional log for errors, failed stages, and tracebacks
- treats torchaudio/torio FFmpeg extension probe tracebacks as known debug noise
  instead of pipeline failures

The doctor is diagnostic. Warnings do not fail the command unless
`--fail-on-warning` is provided. Errors fail the command.
