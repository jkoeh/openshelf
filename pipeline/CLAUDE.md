# Pipeline — CLAUDE.md

## What This Is

Python pipeline that downloads EPUB books, converts them to AI-narrated audio, captures word-level timestamps with WhisperX, and uploads the public artifact contract to Cloudflare R2. Kokoro, F5-TTS, and Chatterbox sit behind a shared TTS engine adapter. Kokoro uses casting and structural direction but skips LLM performance/emotion steering; F5-TTS and Chatterbox can receive engine-supported performance direction. WhisperX is the canonical sync source for every engine. The LLM voice director chooses a narrator, builds an auditable character registry, labels speakers by character offsets, and adds only engine-supported performance direction.

## Structure

```
src/openshelf/
  config.py                 # shared settings, constants, env vars
  scrapers/                 # book discovery and download
    http.py                 # make_request(), sanitize(), download_book()
    gutenberg.py            # Gutendex API search
    standard_ebooks.py      # HTML catalog scraping
  pipeline/                 # EPUB -> audio conversion
    epub_parser.py          # Step 1:  EPUB -> chapters with ContentElements; book_parse.json durable artifact
    epub_annotator.py       # Step 1b: inject stable element IDs into EPUB HTML
    text_chunker.py         # Step 2:  paragraphs -> TTS-sized Chunks
    voice_director.py       # Step 2b: registry, speaker spans, emotion/pace direction; voice_direction artifact (de)serialization
    llm.py                  # LLM adapters: stub, replay, recording, Anthropic, OpenAI
    tts_engine.py           # Engine protocols, voice specs, directed segments, aligners
    engines/                # TTSEngine adapters and factories
      kokoro.py             # Kokoro preset voices + casting guidance
      f5tts.py              # F5-TTS voice cloning adapter (local reference clips)
      chatterbox.py         # Chatterbox zero-shot cloning adapter (local reference clips + expression controls)
    tts.py                  # Step 3:  directed segments -> WAV + per-chunk word timestamps
    encoder.py              # Step 4:  WAV -> AAC (.m4a) via ffmpeg
    manifest.py             # Step 5a/c: book + per-build rendition manifests
    word_aligner.py         # WhisperX forced alignment for non-native timestamp engines + QA; chapter sync artifact + coverage metrics
    transcriber.py          # WhisperX ASR + WER — internal QA only, used by test-audio-quality
    build.py                # new_build_id() — fresh random 16-hex per pipeline run
    r2_keys.py              # Pure-string R2 key constructors (mirrored in worker/src/utils/r2-keys.ts)
    r2.py                   # Step 6:  upload to Cloudflare R2 under build-versioned keys
    dag_cli.py              # File-to-file stage commands (parse/chunk/direct/synth/sync/assemble/coverage/upload); see docs/dag-cli.md
    runner.py               # orchestrator (stub)

scripts/
  process-books.py          # End-to-end: search, download, convert, (optionally) upload
  reprocess-book.py         # Reprocess a downloaded book end-to-end under a fresh build. Does NOT redownload.
  download-books.py         # CLI for book scraping only
  convert-book.py           # CLI for EPUB -> audio conversion
  build-catalog.py          # Rebuild catalog.json on R2 from root book manifests
  test-audio-quality.py     # Integration test: validates audio output quality and alignment

tests/
  scrapers/                 # scraper tests (mocked, offline)
  pipeline/                 # pipeline tests (mocked, offline)
  integration/              # e2e tests (requires GPU)
```

## Language & Stack

- Python 3.11+
- Scraper: stdlib only (no pip deps)
- Pipeline: kokoro, f5-tts, chatterbox-tts, ebooklib, beautifulsoup4, soundfile, numpy, boto3, torch, whisperx
- LLM: Anthropic or OpenAI in production, replay/stub clients in tests
- Audio: AAC 48kbps via ffmpeg (.m4a container)
- Storage: Cloudflare R2 (S3-compatible)

## Engine Knowledge Base

Before changing a TTS engine, read `docs/engine-knowledge-base.md`. It is the
progressive-disclosure map for Kokoro, F5-TTS, and Chatterbox support:

- the runtime API each adapter calls
- voice/reference-clip model
- capability flags and post-processing config
- what reaches synthesis versus what stays audit-only in `voice_direction.json`
- how to keep engine-specific controls out of `chapter_data.json`

Use it as the entry point, then open only the relevant adapter under
`src/openshelf/pipeline/engines/` and the matching step docs.

## Commands

All commands run from the **repo root**.

```bash
# Install dependencies (auto-selects the right torch wheel: CUDA on an NVIDIA box,
# CPU/MPS elsewhere — uv probes the local driver via --torch-backend=auto)
npm run install:pipeline
# equivalently, from the repo root:
uv pip install -r pipeline/requirements.txt --torch-backend=auto
# verify the GPU build landed:
npm run verify:torch

# End-to-end: search + download + convert (+ upload)
python3 pipeline/scripts/process-books.py --author "Kafka"
python3 pipeline/scripts/process-books.py --author "Shakespeare" --book "Romeo"
python3 pipeline/scripts/process-books.py --author "Kafka" --upload
python3 pipeline/scripts/reprocess-book.py Kafka Metamorphosis  # uses local EPUB, publishes a fresh build
python3 pipeline/scripts/process-books.py --author "Kafka" --dry-run  # download + parse only, no audio
python3 pipeline/scripts/process-books.py --epub path/to/book.epub --upload  # local file, skip download

# process-books.py options:
#   --epub <path>             Local EPUB file (skips search/download)
#   --author <name>           Filter by author (required unless --book or --epub)
#   --book <title>            Filter by book title (required unless --author or --epub)
#   --source gutenberg|standard-ebooks|all  (default: all)
#   --upload                  Upload to R2 after conversion
#   --dry-run                 Download + parse only, no audio generation
#   --engine kokoro|f5tts|chatterbox
#                              TTS engine (default: TTS_ENGINE)
#   --voice <id>              Narrator override; skips LLM narrator selection
#   --device cuda|mps|cpu     (default: auto-detect)
#   --keep-wav                Keep WAV files after AAC encoding
#   --delay <seconds>         Seconds between HTTP requests (default: 2)
#   --download-dir <path>     (default: download/books)
#   --output <path>           Audio output directory (default: audio/)
#   --log-dir <path>          Local run logs (default: logs/)

`--author` and `--book` filters are matched after punctuation/diacritic normalization and by token containment, so user input like `Alice's Adventures in Wonderland` matches catalog titles such as `Alices Adventures In Wonderland`, and `Lewis Carroll` matches `Carroll, Lewis`.

Long-running pipeline scripts configure Python's stdlib `logging` through `openshelf.pipeline.logging_utils`. Each run writes a timestamped local log file under `logs/` by default. Logs include phase transitions and periodic heartbeat messages around slow stages such as LLM registry/direction, Kokoro model load, chapter synthesis/alignment, encoding, and R2 upload. The public R2 artifact contract never includes log files.

`--device` is the single device selector for runtime model work. When omitted, `tts.get_device()` chooses `cuda` when `torch.cuda.is_available()` is true, then `mps`, then `cpu`. The selected device is used for Kokoro loading and WhisperX forced alignment. If the environment has a CPU-only PyTorch wheel, auto-detect correctly chooses `cpu` even on a machine with an NVIDIA GPU; install a CUDA-enabled PyTorch wheel before expecting `--device cuda` or auto CUDA selection to work.

# Rebuild catalog after uploading new books
python3 pipeline/scripts/build-catalog.py
python3 pipeline/scripts/build-catalog.py --dry-run  # preview without uploading

# Individual steps (if you need them separately):
python3 pipeline/scripts/download-books.py --dry-run --author "Kafka"
python3 pipeline/scripts/convert-book.py <epub-path>
python3 pipeline/scripts/convert-book.py <epub-path> --upload

# Run tests
python3 -m unittest discover -s pipeline/tests -v

# Run audio quality integration test (requires GPU deps)
python3 pipeline/scripts/test-audio-quality.py
python3 pipeline/scripts/test-audio-quality.py --epub <path> --chapters 1-2
```

## Conventions

- Package name: `openshelf` (import: `from openshelf.pipeline import epub_parser`)
- File naming: `snake_case.py`
- Constants: `UPPER_SNAKE_CASE` in `config.py`
- Tests mirror `src/` structure under `tests/`
- All tests must be fully mocked — no real network, no GPU, no ffmpeg
- Scripts use `sys.path.insert` so they work without `pip install`
- Pipeline modules are pure-ish functions, testable in isolation
- Pipeline-specific secrets live in `pipeline/.env`; root `.env` remains a fallback. Shell environment variables still win.
- `character_registry.json` and `voice_direction.json` are immutable per-build artifacts uploaded to R2. `character_registry.json` is intended for future client character editing/features; `voice_direction.json` audits speaker assignments and synthesis-only performance steering.
- Public output is unchanged for every engine: `.m4a` audio plus `chapter_data.json` with original chunk text and WhisperX word timestamps.
- TTS engine API notes live in `docs/engine-knowledge-base.md`; update that file before wiring a new engine API or changing an existing adapter's runtime parameters.
- LLM steering annotations are synthesis-only. They may be passed to a TTS engine, but they must never be serialized into `chapter_data.json` or shown in the reader.
- `TTS_ENGINE`, `LLM_PROVIDER`, `LLM_MODEL`, `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL`, `OPENAI_API_KEY`, `OPENAI_MODEL`, `VOICES_DIR`, and `REGISTRY_OPENING_CHARS` are owned by `config.py`.
- `transcriber.compute_wer` prefers `jiwer` when installed and falls back to an internal word-level edit-distance implementation so offline tests do not require dependency installation.

## Do NOT

- Make real HTTP calls in tests
- Hard-code paths — use `config.py` constants
