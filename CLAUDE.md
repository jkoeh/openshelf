# CLAUDE.md

## What This Project Is

**OpenShelf** is an open source public domain audiobook platform. It downloads EPUB books from Project Gutenberg and Standard Ebooks, converts them to AI-narrated audio using Kokoro TTS with word-level alignment via WhisperX, and serves them globally via Cloudflare R2.

## Project Structure

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
    tts.py                  # Step 3:  chunks -> WAV via Kokoro TTS
    encoder.py              # Step 4:  WAV -> Opus via ffmpeg
    manifest.py             # Step 5a: chapter metadata JSON
    word_aligner.py         # Step 5b: word-level alignment via WhisperX
    r2.py                   # Step 6:  upload to Cloudflare R2
    runner.py               # orchestrator (stub)

scripts/
  download-books.py         # CLI for book scraping
  convert-book.py           # CLI for EPUB -> Opus conversion + alignment
  upload-books.py           # CLI for uploading pre-generated audio to R2

tests/
  scrapers/                 # scraper tests (mocked, offline)
  pipeline/                 # pipeline tests (mocked, offline)
```

## Language & Stack

- Python 3.11+
- Scraper: stdlib only (no pip deps)
- Pipeline: kokoro, ebooklib, beautifulsoup4, soundfile, numpy, boto3, torch, whisperx
- Audio: Opus 48kbps via ffmpeg (libopus)
- Storage: Cloudflare R2 (S3-compatible)

## Commands

```bash
# Install dependencies
uv pip install -r requirements.txt

# Run scraper
python3 scripts/download-books.py --dry-run --author "Kafka"

# Convert a book
python3 scripts/convert-book.py <epub-path>
python3 scripts/convert-book.py <epub-path> --upload

# Upload pre-generated audio
python3 scripts/upload-books.py <epub-path>

# Run tests
python3 -m unittest discover -s tests -v
```

## Conventions

- Package name: `openshelf` (import: `from openshelf.pipeline import epub_parser`)
- File naming: `snake_case.py`
- Constants: `UPPER_SNAKE_CASE` in `config.py`
- Tests mirror `src/` structure under `tests/`
- All tests must be fully mocked — no real network, no GPU, no ffmpeg
- Scripts use `sys.path.insert` so they work without `pip install`
- Pipeline modules are pure-ish functions, testable in isolation
- Idempotent at every level: file exists -> skip, R2 key exists -> skip
- `sanitize()` in `scrapers/http.py` is the single source of truth for slug generation

## Do NOT

- Make real HTTP calls in tests
- Hard-code paths — use `config.py` constants
- Over-engineer — no abstractions until there are 2+ concrete uses
- Create files outside the established structure without updating this doc
