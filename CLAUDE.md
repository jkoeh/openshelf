# CLAUDE.md

## What This Project Is

**OpenShelf** is an open source public domain audiobook platform. It downloads EPUB books from Project Gutenberg and Standard Ebooks, converts them to AI-narrated audio using Kokoro TTS, and serves them globally via Cloudflare R2.

## Project Structure

```
src/openshelf/
  config.py               # shared settings, constants, env vars
  scrapers/               # book discovery and download
    http.py               # make_request(), sanitize(), download_book()
    gutenberg.py          # Gutendex API search
    standard_ebooks.py    # HTML catalog scraping
  pipeline/               # EPUB → audio conversion
    epub_parser.py        # Step 1: EPUB → chapters
    text_chunker.py       # Step 2: text → TTS-sized chunks
    tts.py                # Step 3: chunks → WAV via Kokoro
    encoder.py            # Step 4: WAV → MP3
    manifest.py           # Step 5: chapter metadata JSON
    r2.py                 # Step 6: upload to Cloudflare R2
    runner.py             # orchestrator wiring steps 1-6

scripts/
  download-books.py       # CLI for book scraping

tests/
  scrapers/               # scraper tests (mocked, offline)
  pipeline/               # pipeline tests
```

## Language & Stack

- Python 3.11+
- Scraper: stdlib only (no pip deps)
- Pipeline: kokoro, ebooklib, beautifulsoup4, pydub, soundfile, numpy, boto3, torch
- Storage: Cloudflare R2 (S3-compatible)
- No TypeScript, no Node.js

## Commands

```bash
# Run scraper
python3 scripts/download-books.py --dry-run --author "Kafka"
python3 scripts/download-books.py --source gutenberg --author "Dostoevsky"

# Run tests
python3 -m unittest discover -s tests -v

# Install as editable package (optional)
pip install -e ".[dev]"
```

## Conventions

- Package name: `openshelf` (import path: `from openshelf.scrapers import http`)
- File naming: `snake_case.py`
- Constants: `UPPER_SNAKE_CASE` in `config.py`
- Tests mirror `src/` structure under `tests/`
- All tests must be fully mocked — no real network calls
- Scripts use `sys.path.insert` so they work without `pip install`

## Key Design Decisions

- Pipeline steps are independent modules with pure-ish functions — each testable in isolation
- Idempotent at every level: file exists → skip, R2 key exists → skip
- `sanitize()` is the single source of truth for slug generation (author dirs, title filenames)
- Downloads go to `raw-download/books/{source}/{author-slug}/{title-slug}.epub`
- Audio output goes to `audio/{author-slug}/{title-slug}/chapter-NN.mp3`

## Do NOT

- Add `any`-style loose typing or skip type hints on public functions
- Make real HTTP calls in tests
- Hard-code paths — use `config.py` constants
- Over-engineer — no abstractions until there are 2+ concrete uses
- Create files outside the established structure without updating this doc
