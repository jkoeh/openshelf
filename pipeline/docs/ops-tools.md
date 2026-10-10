# Pipeline Ops Tools

## Outbound generation consumer

`scripts/open-job-monitor.cmd` launches the native Windows queue monitor with
the root `.venv`; `pipeline/scripts/job-monitor.pyw` can also be run with that
environment's `pythonw.exe`. It requires no inbound port. It reads the
production owner token from `worker/.secrets/prod-owner-token`, refreshes the
owner-only queue view every 15 seconds, and shows active/recent jobs, priority,
attempts, stage, heartbeat/lease expiry, local consumer status, and local log
tail. An expired running lease is labeled stuck; a long but heartbeating job is
still working. Only a queued job may be set high/normal priority. Cancel asks
for confirmation, revokes its Worker lease, and the PC stops a running child at
its next heartbeat. The window can start the existing outbound consumer using
`worker/.secrets/prod-pc-token`, but closing the window leaves it running. The
two tokens and the OpenAI key remain local and are never displayed or logged.
The owner API client uses only the fixed production HTTPS origin and does not
follow redirects with its bearer token. It sends the same OpenShelf User-Agent
as the PC consumer so Cloudflare does not reject Python's default client identity.

`openshelf-pipeline books consume-jobs --api-base URL` uses `OPENSHELF_PC_TOKEN`
from the PC environment and polls the Worker over HTTPS. `--sync-pages N` first
indexes up to N Gutendex pages through the authenticated source-sync route;
N is capped at 10 per invocation to bound D1 writes. Use `books sync-catalog`
for a broader rights-checked import.
One job runs at a time. A claimed job names an exact Gutenberg ID and EPUB URL;
the consumer validates its host and EPUB archive, rejects ZIP files above
2 MiB central-directory metadata, 256 MiB expanded size, or 5,000 entries,
parses the EPUB locally and rejects more than 100,000 source spoken words
(body plus spoken headings) by default before any LLM or TTS call. Generated
opening and closing credits are outside this source-word budget. `--max-words N`
explicitly changes that per-job limit. A rejected book is reported as failed without
starting the expensive pipeline. Standard jobs call `books process --epub ...
--engine kokoro --voice af_heart --rendition kokoro-af-heart --build-id ...
--upload` without a model call. Expressive jobs call the same exact-EPUB path
with `--engine chatterbox --voice chatterbox-af_heart --rendition
chatterbox-af-heart --performance-direction batched`, and set
`LLM_PROVIDER=openai` only in the child environment. The consumer includes
expressive jobs in its claim request only when a local `OPENAI_API_KEY` exists;
the key never crosses the Worker API. It renews the lease while processing and uses
`--resume` only when the same build has a local `run.json`. A rejected lease
immediately terminates the child; only network failures get a bounded grace
period. On success it reports the resulting slugs; the Worker
independently verifies R2 before marking the job complete.

For a `BOOK_TOO_LONG` failure, the local log reports the exact parsed spoken
word count and configured limit. Review that count, retry the failed job through
the owner API, and run `books consume-jobs --api-base URL --max-words N --once`
with an explicit sufficient limit when this is the only queued job. Retry keeps
the original build ID and still performs all rights and archive checks. Raising
the CLI limit for one invocation does not change the default consumer budget.
The independent source EPUB download in the client does not require a running
consumer or successful audio preflight.

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
Manual `--upload` accepts local EPUBs without the job consumer's automatic
Gutenberg rights check. The operator verifies rights before publishing.

## `books sync-catalog`

The operator downloads Gutenberg's weekly CSV.gz and RDF tar.bz2 feeds into
`download/`, then runs `books sync-catalog --catalog <csv.gz> --rights-archive
<rdf.tar.bz2> --api-base <Worker /api/v1 URL>`. A dedicated `OPENSHELF_PC_TOKEN`
authorizes batches of at most 50. The command considers at most 500 English
`Text` candidates by default, or up to 1,000 with `--max-books`; `--after-id`
continues from a numeric Gutenberg ID. The CSV provides display metadata, while
the exact RDF record must explicitly say `Public domain in the USA.` and list a
matching EPUB URL. Unknown rights and missing EPUBs are skipped. Archive parsing
is bounded and never extracts files. The command never claims a generation job
or invokes a model; `--dry-run` performs the local rights join without API writes.
The current bulk archive takes roughly 30 seconds to scan for a 500-candidate
batch on the owner's PC. Before another batch, check the account's D1 daily
row-write usage.

## `ops doctor`

```bash
openshelf-pipeline ops doctor \
  --build-dir audio/lewis-carroll/alices-adventures-in-wonderland/audio/chatterbox-bf-emma/builds/e3eebabdbf3e88ca \
  --log logs/20260613-071007-dag-run-alices-adventures-in-wonderland-e3eebabdbf3e88ca.log
```

Behavior:

- verifies the build directory exists
- counts section chunk, direction, audio, sync, and manifest artifacts
- compares section sets across `section-NN.chunks.json`,
  `section-NN.voice_direction.json`, `section-NN.m4a`, and
  `section-NN.sync.json`
- checks `section_data.json` and `rendition-manifest.json` section counts when
  present
- reports sync artifacts with skipped chunk markers or low coverage as warnings
- scans an optional log for errors, failed stages, and tracebacks
- treats torchaudio/torio FFmpeg extension probe tracebacks as known debug noise
  instead of pipeline failures

The doctor is diagnostic. Warnings do not fail the command unless
`--fail-on-warning` is provided. Errors fail the command.
