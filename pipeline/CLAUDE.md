# Pipeline — CLAUDE.md

## What This Is

Python pipeline that downloads EPUB books, converts them to AI-narrated audio (Kokoro TTS), captures word-level timestamps directly from Kokoro's token output, and uploads everything to Cloudflare R2. WhisperX is no longer part of the public artifact set — it remains in-tree as internal QA tooling used by `test-audio-quality.py` for roundtrip ASR/WER validation.

## Structure

```
src/openshelf/
  config.py                 # shared settings, constants, env vars
  scrapers/                 # book discovery and download
    http.py                 # make_request(), sanitize(), download_book()
    gutenberg.py            # Gutendex API search
    standard_ebooks.py      # HTML catalog scraping
  pipeline/                 # EPUB -> audio conversion
    epub_parser.py          # Step 1:  EPUB -> chapters with ContentElements
    epub_annotator.py       # Step 1b: inject stable element IDs into EPUB HTML
    text_chunker.py         # Step 2:  paragraphs -> TTS-sized Chunks
    tts.py                  # Step 3:  chunks -> WAV + per-chunk word timestamps via Kokoro
    encoder.py              # Step 4:  WAV -> AAC (.m4a) via ffmpeg
    manifest.py             # Step 5a/c: book + per-build rendition manifests
    word_aligner.py         # WhisperX forced alignment — internal QA only, used by test-audio-quality
    transcriber.py          # WhisperX ASR + WER — internal QA only, used by test-audio-quality
    build.py                # new_build_id() — fresh random 16-hex per pipeline run
    r2_keys.py              # Pure-string R2 key constructors (mirrored in worker/src/utils/r2-keys.ts)
    r2.py                   # Step 6:  upload to Cloudflare R2 under build-versioned keys
    runner.py               # orchestrator (stub)

scripts/
  process-books.py          # End-to-end: search, download, convert, (optionally) upload
  reprocess-book.py         # Reprocess a downloaded book end-to-end with --force (overwrites local + R2). Does NOT redownload.
  download-books.py         # CLI for book scraping only
  convert-book.py           # CLI for EPUB -> audio conversion
  upload-books.py           # CLI for uploading pre-generated audio to R2
  build-catalog.py          # Rebuild catalog.json on R2 from all uploaded manifests
  test-audio-quality.py     # Integration test: validates audio output quality and alignment

tests/
  scrapers/                 # scraper tests (mocked, offline)
  pipeline/                 # pipeline tests (mocked, offline)
  integration/              # e2e tests (requires GPU)
```

## Language & Stack

- Python 3.11+
- Scraper: stdlib only (no pip deps)
- Pipeline: kokoro, ebooklib, beautifulsoup4, soundfile, numpy, boto3, torch, whisperx
- Audio: AAC 48kbps via ffmpeg (.m4a container)
- Storage: Cloudflare R2 (S3-compatible)

## Commands

All commands run from the **repo root**.

```bash
# Install dependencies
uv pip install -r pipeline/requirements.txt

# End-to-end: search + download + convert (+ upload)
python3 pipeline/scripts/process-books.py --author "Kafka"
python3 pipeline/scripts/process-books.py --author "Shakespeare" --book "Romeo"
python3 pipeline/scripts/process-books.py --author "Kafka" --upload
python3 pipeline/scripts/reprocess-book.py Kafka Metamorphosis  # uses local EPUB, force-overwrites local + R2
python3 pipeline/scripts/process-books.py --author "Kafka" --dry-run  # download + parse only, no audio
python3 pipeline/scripts/process-books.py --epub path/to/book.epub --upload  # local file, skip download

# process-books.py options:
#   --epub <path>             Local EPUB file (skips search/download)
#   --author <name>           Filter by author (required unless --book or --epub)
#   --book <title>            Filter by book title (required unless --author or --epub)
#   --source gutenberg|standard-ebooks|all  (default: all)
#   --upload                  Upload to R2 after conversion
#   --dry-run                 Download + parse only, no audio generation
#   --voice <id>              Kokoro voice ID (default: af_heart)
#   --device cuda|mps|cpu     (default: auto-detect)
#   --keep-wav                Keep WAV files after AAC encoding
#   --delay <seconds>         Seconds between HTTP requests (default: 2)
#   --download-dir <path>     (default: download/books)
#   --output <path>           Audio output directory (default: audio/)

# Rebuild catalog after uploading new books
python3 pipeline/scripts/build-catalog.py
python3 pipeline/scripts/build-catalog.py --dry-run  # preview without uploading

# Individual steps (if you need them separately):
python3 pipeline/scripts/download-books.py --dry-run --author "Kafka"
python3 pipeline/scripts/convert-book.py <epub-path>
python3 pipeline/scripts/convert-book.py <epub-path> --upload
python3 pipeline/scripts/upload-books.py <epub-path>

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

## Do NOT

- Make real HTTP calls in tests
- Hard-code paths — use `config.py` constants
