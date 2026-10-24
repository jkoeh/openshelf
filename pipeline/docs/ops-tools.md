# Pipeline Ops Tools

For supervised recovery, the PC-authenticated claim request accepts an optional
`job_id` UUID. It leases only that queued or eligible expired job and never
claims an unrelated edition. Capability checks, ordering-independent attempt
limits, start caps and normal lease/finish validation still apply. Ordinary
consumers omit it and retain priority/FIFO selection.

## Mandarin production runtime

For standard English, `OPENSHELF_ENGLISH_PYTHON` optionally selects an isolated
Python >=3.12 with Kokoro/Misaki English and WhisperX >=3.8.4. The consumer probes
it before claiming standard-English work. Validate a short real CPU synthesis
and alignment before changing this setting; a failed configured probe leaves
standard-English jobs queued. The default current interpreter is preserved when
the variable is absent. This does not redirect expressive jobs or change voices.
The runtime needs Kokoro/Misaki English dependencies and the official small
English spaCy model (`en_core_web_sm`) in that exact interpreter. Its probe checks
the model is already installed so jobs do not trigger implicit model setup.

### Recovering a long Mandarin job locally

`ops recover-source-job` supervises one standard Mandarin source using ignored
local owner/PC credentials and a persisted state file under `audio/`. It never
resets attempts, decrements quotas or changes a historical failed job. It may
create at most three ordinary fresh jobs (configurable up to five), uses the
normal capped owner retry when allowed, claims only its exact job ID, and runs
the normal consumer validation, heartbeat, upload and finish path. Network
failures pause retry attempts; canceled jobs stop recovery.

For a legacy orphan that holds hours of uncheckpointed work, `--wait-pid` and
`--after-section` wait for a stable completed section, verify the exact process
command/build identity, decode the AAC and validate original chunks, sync and
seam bounds. A separate isolated-runtime certification creates a local hashed
completion receipt before the old process tree is stopped at that boundary.
An idle existing production consumer is stopped only after confirming it has no
active job or synthesis child, preventing a second GPU producer. Compatible
work is adopted into the fresh build with `build_recovery.py`. No publication
manifests are copied; the entire new job must finish normally.

Ordinary consumers, including a consumer opened by Studio during recovery,
wait before claiming while an exact same-checkout recovery command holds its
live PID/start-time state lock. Foreign working directories, unrelated commands
and stale/reused PID locks do not block the queue. This check is only in normal
queue polling; the supervisor's targeted claim and `process_job` path continue.
Closing Studio's ordinary idle consumer does not stop the independent recovery.

Example (the PID is checked against the build command, never trusted alone):

```text
openshelf-pipeline ops recover-source-job --source-id gutenberg:24264 --source-build audio/cao-xueqin-1717-1763/untitled-g24264/audio/qwen3-serena/builds/2db4b630693c46a7 --wait-pid 55024 --after-section 2 --state-file audio/hongloumeng-recovery.json
```

Re-run the exact command after a reboot; its state records the current job/build
without credentials or lease tokens. Region checkpoints survive interruption.
The supervisor sets `OPENSHELF_REQUIRE_COMPLETE_AUDIO=1` for its child: any skipped
body chunk fails before manifest publication, while completed regions remain
resumable. This opt-in guard preserves the existing manual pipeline's diagnostic
partial-output behavior. Keep the PC awake and allow about 100 GPU hours for
this edition at the measured single-item Qwen throughput. Short forced alignment
coverage checks do not certify pronunciation. This command starts no paid cloud
GPU resources.

Mandarin standard jobs use Qwen3 CustomVoice Serena and an isolated Python 3.12
environment. Install CUDA PyTorch 2.8 first as in the benchmark setup below,
then install `pipeline/requirements-mandarin.txt` into that environment.
Set `OPENSHELF_MANDARIN_PYTHON` in the ignored root/pipeline `.env` to its absolute
Python executable. `OPENSHELF_QWEN_CACHE` may point to the prepared Hugging Face
cache (for this checkout, the benchmark cache can be reused). Restart the local
consumer while idle so it reads these settings. The consumer probes the runtime
once at startup with a 30-second bound and advertises Mandarin capability only
when Qwen, fixed dependencies, Chinese alignment and the selected accelerator
are usable. The Worker leaves Mandarin jobs queued for a capable consumer;
older consumers can continue claiming English work. This does not change caps,
leases or source validation. Public Mandarin requests use `qwen3-serena`;
expressive mode remains the existing owner-only English Chatterbox path.

Manual exact-EPUB processing in this environment supports:

```text
openshelf-pipeline books process --epub <validated-zh.epub> --engine qwen3 --voice qwen3-serena --device cuda
```

The Qwen model revision and narration settings are pinned in `run.json`, and
WhisperX >=3.8.4 supplies the same version-2 sync contract. Existing English jobs
and prior Kokoro Mandarin builds retain their original engine/rendition identity.

## Local Qwen3-TTS comparison

`qa tts-benchmark` is an opt-in real-model diagnostic. Keep its isolated runtime
and generated files under ignored `download/` and `audio/`; do not install Qwen
into the existing production environment. Example setup from the repository root:

```powershell
uv venv --python 3.12 download/qwen3-benchmark/venv
uv pip install --python download/qwen3-benchmark/venv/Scripts/python.exe torch==2.8.0 torchaudio==2.8.0 --index-url https://download.pytorch.org/whl/cu128
uv pip install --python download/qwen3-benchmark/venv/Scripts/python.exe qwen-tts==0.1.1 whisperx==3.8.4
$env:PYTHONPATH = (Resolve-Path pipeline/src).Path
$env:HF_HOME = (Join-Path (Get-Location) 'download/qwen3-benchmark/huggingface')
& download/qwen3-benchmark/venv/Scripts/python.exe pipeline/scripts/openshelf-pipeline.py qa tts-benchmark --engine qwen3 --language zh --voice Serena --output-dir audio/tts-benchmark/qwen3-serena-zh
& download/qwen3-benchmark/venv/Scripts/python.exe pipeline/scripts/openshelf-pipeline.py qa tts-benchmark --align-only --output-dir audio/tts-benchmark/qwen3-serena-zh
```

CUDA defaults to BF16/SDPA and batch size one. `--revision` pins a checkpoint;
`--device cpu` selects float32. The command fails rather than silently falling
back to CPU. Run `--engine kokoro --language zh --voice zf_xiaobei` in the current
environment for the same Chinese samples. English comparisons use `--language en`
with Qwen `Ryan`, Kokoro `af_heart`, or Chatterbox `chatterbox-af_heart` (an
existing local reference WAV is required). Qwen Chinese also offers `Uncle_Fu`.
Alignment can run with the existing environment when healthy, or with the isolated
Python 3.12 environment above. WhisperX 3.8.2 was withdrawn for timestamp defects;
the benchmark uses 3.8.4. On this PC the existing Python 3.13 pandas/PyArrow stack
also crashes during real alignment; the benchmark does not modify that stack.
`--sample ID` limits the run to one fixture. Existing reports/outputs are not
overwritten unless `--overwrite` is explicit. `--align-only` adds alignment to
the report without repeating synthesis and may be rerun after a failed alignment.

`report.json` includes original sample text/hash, WAV/hash, sample rate, duration,
load time, per-sample synthesis seconds, RTF (lower is faster), process peak CUDA
allocated bytes, model checkpoint/revision, package versions and generation
settings. Alignment records elapsed time, per-character/word timestamps, coverage
and violations; empty alignment fails the command. All samples must have finite,
nonempty audio and timestamps within its duration. A local `index.html` provides
audio controls and original text, with highlights after alignment. It embeds no
external scripts or services. No R2 writes, source imports, jobs or public engine
changes occur. Compare listening quality, omitted/repeated text, pronunciation,
voice consistency and memory/time separately. Forced alignment alone is not a
speech-accuracy metric, and short samples cannot establish full-book reliability.

### Initial local measurements (2026-10-15)

RTX 4070 Ti SUPER (16 GiB), batch size one. Qwen checkpoint
`0c0e3051f131929182e2c023b9537f8b1c68adfe`, qwen-tts 0.1.1,
PyTorch 2.8.0/CUDA 12.8, BF16/SDPA; existing adapters used PyTorch 2.8.0/CUDA
12.6. Times below exclude model loading and alignment but include the first
sample's pronunciation/voice initialization. These are initial short-run
observations, not a controlled or optimized throughput comparison.

| Engine / voice | Language | Audio seconds | Synthesis seconds | Aggregate RTF | Peak allocated CUDA GiB |
|---|---|---:|---:|---:|---:|
| Qwen / Serena | zh | 55.84 | 72.65 | 1.30 | 4.28 |
| Kokoro / zf_xiaobei | zh | 47.93 | 3.02 | 0.06 | 0.77 |
| Qwen / Ryan | en | 37.20 | 50.45 | 1.36 | 4.18 |
| Kokoro / af_heart | en | 32.48 | 1.46 | 0.05 | 0.76 |
| Chatterbox / chatterbox-af_heart | en | 29.20 | 17.88 | 0.61 | 3.27 |

All 17 samples subsequently aligned with WhisperX 3.8.4 on CUDA with 100%
original-text-unit coverage and zero timestamp invariant violations. The
Chatterbox comparison uses its existing adapter/default controls and local
reference, without the production OpenAI direction pass. Listening quality has
not been scored. Qwen local synthesis/alignment is verified; this benchmark
does not establish long-form quality. Production setup is documented above.
The ignored listening artifact is `audio/tts-benchmark/index.html`, with each
voice's measured report and highlighting page beside its WAVs. A local server
bound to 127.0.0.1 may serve this output directory for listening.

## Outbound generation consumer

`scripts/open-job-monitor.cmd` launches Windows OpenShelf Studio with
the root `.venv`; `pipeline/scripts/job-monitor.pyw` can also be run with that
environment's `pythonw.exe`. Its HTTP server binds only to 127.0.0.1 on a random
port inside a private embedded WebView2 window. A native request handler adds
the session token only to this server's API requests; there are no saved web
links or browser session tokens to expire. The request event is explicitly
synchronous so credentials are attached before dispatch. API requests require
that token, an exact Host, and same-origin Origin/Fetch Metadata; mutations
require JSON. No CORS, external scripts, arbitrary file paths, or proxy routes
are allowed. Credentials stay on the Python backend; known secrets are redacted
from log tails. Dashboard assets are in src/openshelf/pipeline/monitor/ and the
HTTP boundary is in job_monitor_web.py. job_monitor_desktop.py owns the window
and services. Install pywebview>=6,<7 in the root environment; Microsoft WebView2
Runtime is required. No JavaScript/Python application bridge is exposed. It reads the
production owner token from `worker/.secrets/prod-owner-token`, refreshes the
owner-only queue view every 15 seconds, and shows active/recent jobs, priority,
attempts, stage, heartbeat/lease expiry, local consumer status, and local log
tail. An expired running lease is labeled stuck; a long but heartbeating job is
still working. Only a queued job may be set high/normal priority. Cancel asks
for confirmation, revokes its Worker lease, and the PC stops a running child at
its next heartbeat. Failed/canceled jobs with fewer than three attempts offer
retry. Retry preserves the job and build, clears its error, and consumes a new
daily start only when successfully requeued within the queue and daily caps.
The dashboard can start
the existing outbound consumer using
`worker/.secrets/prod-pc-token`, and stop it only while idle (no running job or
processing child). Launch starts the consumer automatically or adopts its existing
process tree. Windows Job Objects with kill-on-close own all consumer children;
pre-existing child groups receive their own object when Windows cannot merge
their existing job hierarchies. Every handle is owned only by Studio;
closing the window or Close Studio stops both the server and consumer immediately.
Even an app crash closes the Job Object handle and kills its consumer descendants.
An interrupted running job remains leased until expiry, then can be reclaimed
within the existing three-attempt cap on
the next launch and resumed under the existing fixed-build contract. Partial
files are retained. Closing does not permanently cancel the book's queue job.
Repeated launches focus the existing window via a per-checkout named mutex.
The isolated WebView profile is deleted on exit. Startup failures clean up acquired
resources and show an actionable native message. The
two tokens and the OpenAI key remain local and are never displayed or logged.
The owner API client uses only the fixed production HTTPS origin and does not
follow redirects with its bearer token. It sends the same OpenShelf User-Agent
as the PC consumer so Cloudflare does not reject Python's default client identity.

`openshelf-pipeline books consume-jobs --api-base URL` uses `OPENSHELF_PC_TOKEN`
from the PC environment and polls the Worker over HTTPS. `--sync-pages N` first
indexes up to N Gutendex pages through the authenticated source-sync route;
N is capped at 10 per invocation to bound D1 writes. Use `books sync-catalog`
for a broader rights-checked import.
One job runs at a time. A claimed job names an exact Gutenberg or Standard Ebooks ID and EPUB URL;
the consumer validates its host and EPUB archive, rejects ZIP files above
2 MiB central-directory metadata, 256 MiB expanded size, or 5,000 entries,
parses the EPUB locally and rejects empty spoken text before any LLM or TTS call.
Standard and expressive jobs have no word-count ceiling. The `--max-words`
option is removed. Parsed spoken words include body text and spoken headings;
generated opening and closing credits are outside this nonempty-text check.
A rejected book is reported as failed without
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

Earlier jobs that failed with `BOOK_TOO_LONG` can be retried through the owner
API with the updated consumer. Retry keeps the original build ID and still
performs all rights, archive, and nonempty-text checks.
The Read now source reader does not require a running consumer or successful audio preflight.

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

## `ops catalog`

Rebuild the R2 publication/audit `catalog.json` and synchronize the D1 public
catalog from verified current R2 manifests. Supply `--api-base <Worker /api/v1 URL>`
or `OPENSHELF_API_BASE`, and set `OPENSHELF_PC_TOKEN`. Non-dry runs require both;
API errors propagate and must be repaired/retried. The sync uses batches of at
most 10 keys, also indexing manually published books without source-index rows.
After upserts it reconciles all existing D1 rows in pages of 10, including when
the R2 scan is empty. The Worker verifies R2 object ETags and D1 revisions before
pruning missing/incompatible publications; active jobs and deletions are retained.
`--dry-run` has no API or R2 writes. After applying migrations 0009/0010 and
deploying the Worker, run this once to backfill existing books before client rollout.

## `books sync-catalog`

`books sync-catalog --source standard-ebooks --api-base <Worker /api/v1 URL>`
indexes the public Standard Ebooks HTML catalog without patron feeds. It checks
canonical edition paths, exact compatible EPUB links, and the official U.S.
public-domain statement, skips future releases, delays requests by two seconds,
and imports at most 500 editions by default (1,000 maximum), in batches of 50.
`--after-page` resumes listing pagination; `--max-pages` bounds pages (20 default,
50 maximum). `--dry-run` checks live metadata without API writes. The consumer
rechecks page rights and EPUB OPF edition identity/CC0 rights before synthesis.
Downloads follow only the official exact-file `?source=download` continuation,
never a fabricated cookie. IDs and publication slugs follow
the root AGENTS.md Standard Ebooks source contract.
Real imports download the exact EPUB, check archive limits, OPF rights/identity,
and nonempty text before indexing, then upload the reading copy through the
PC-authenticated source-EPUB route (32 MiB maximum). This allows reading despite
Standard Ebooks blocking Cloudflare upstream fetches. Dry runs check metadata
without EPUB downloads or API writes. Failed EPUB validations are skipped.
Already cached editions are checked with HEAD and skipped without downloading
again. HTTP 403/429 stops the run immediately, preserving completed imports;
retry later after the upstream limit clears. No automatic retries bypass limits.

The operator downloads Gutenberg's weekly CSV.gz and RDF tar.bz2 feeds into
`download/`, then runs `books sync-catalog --catalog <csv.gz> --rights-archive
<rdf.tar.bz2> --api-base <Worker /api/v1 URL>`. A dedicated `OPENSHELF_PC_TOKEN`
authorizes batches of at most 50. The command considers at most 500 English or Chinese
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

Source import follows `source-access.md`: Gutenberg bulk imports cache exact
EPUBs from an approved mirror/local rsync collection, with `--language en|zh|all`.
`--source wikisource --editions <json>` imports curated revision-pinned API books.
Real imports validate/cache reading copies separately from audio; dry runs write
nothing.

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
