# CLAUDE.md

## What This Project Is

**OpenShelf** is an open source public domain audiobook platform. It downloads EPUB books from Project Gutenberg and Standard Ebooks, converts them to AI-narrated audio using Kokoro TTS, and serves them globally via Cloudflare R2.

## Project Structure

```
src/openshelf/
  __init__.py             # empty package marker
  config.py               # shared settings, constants, env vars
  scrapers/               # book discovery and download
    __init__.py
    http.py               # make_request(), sanitize(), download_book()
    gutenberg.py          # Gutendex API search
    standard_ebooks.py    # HTML catalog scraping
  pipeline/               # EPUB → audio conversion
    __init__.py
    epub_parser.py        # Step 1: EPUB → chapters (Chapter dataclass)
    text_chunker.py       # Step 2: text → TTS-sized chunks
    tts.py                # Step 3: chunks → WAV via Kokoro (lazy-loaded)
    encoder.py            # Step 4: WAV → MP3 via pydub/ffmpeg
    manifest.py           # Step 5: chapter metadata JSON
    r2.py                 # Step 6: upload to Cloudflare R2
    runner.py             # orchestrator — currently a stub (not yet implemented)

scripts/
  download-books.py       # CLI for book scraping
  convert-book.py         # CLI for EPUB → MP3 full pipeline

tests/
  scrapers/
    test_download_books.py  # covers http.py, gutenberg.py, standard_ebooks.py
  pipeline/
    test_epub_parser.py
    test_text_chunker.py
    test_tts.py
    test_encoder.py
    test_manifest.py
    test_r2.py
```

## Language & Stack

- Python 3.11+
- Scraper: stdlib only (no pip deps beyond stdlib)
- Pipeline: `kokoro`, `ebooklib`, `beautifulsoup4`, `pydub`, `soundfile`, `numpy`, `boto3`, `torch`, `python-dotenv`
- Storage: Cloudflare R2 (S3-compatible API via boto3)
- Linting: `ruff` (configured in `pyproject.toml`)
- No TypeScript, no Node.js
- ffmpeg required at runtime for MP3 encoding (pydub dependency)

## Environment Setup

Copy `.env.example` and fill in Cloudflare R2 credentials:

```bash
cp .env.example .env
# edit .env with real values
```

Required environment variables (loaded via `python-dotenv` in `config.py`):

```
R2_ACCOUNT_ID=your_account_id
R2_ACCESS_KEY=your_access_key
R2_SECRET_KEY=your_secret_key
R2_BUCKET=openshelf-audio
```

## Commands

```bash
# Install dependencies (editable)
pip install -e ".[dev]"

# Run scraper — discover and download EPUBs
python3 scripts/download-books.py --dry-run --author "Kafka"
python3 scripts/download-books.py --source gutenberg --author "Dostoevsky"
python3 scripts/download-books.py --source standard-ebooks --language en

# Convert a single EPUB to MP3 audio
python3 scripts/convert-book.py path/to/book.epub
python3 scripts/convert-book.py path/to/book.epub --dry-run       # parse/chunk only, no TTS
python3 scripts/convert-book.py path/to/book.epub --keep-wav      # retain intermediate WAV
python3 scripts/convert-book.py path/to/book.epub --device cuda   # force GPU

# Run all tests
python3 -m unittest discover -s tests -v

# Lint / format
ruff check src/ tests/
ruff format src/ tests/
```

## CLI Reference

### `download-books.py`

| Option | Default | Description |
|---|---|---|
| `--source` | `all` | `gutenberg`, `standard-ebooks`, or `all` |
| `--author` | — | Case-insensitive substring match |
| `--language` | — | Language code, e.g. `en`, `fr` |
| `--subject` | — | Subject keyword (Gutenberg only) |
| `--output` | `download/` | Base directory for downloaded EPUBs |
| `--delay` | `2` | Seconds between HTTP requests |
| `--dry-run` | false | Preview without downloading |

### `convert-book.py`

| Option | Default | Description |
|---|---|---|
| `EPUB` (positional) | required | Path to input EPUB file |
| `--output` | `audio/` | Output directory for MP3s |
| `--voice` | config default | Kokoro voice ID |
| `--device` | auto-detect | `cuda`, `mps`, `cpu`, or auto |
| `--dry-run` | false | Parse and chunk only, skip TTS/encoding |
| `--keep-wav` | false | Retain intermediate WAV files |

## Key Configuration Constants (`config.py`)

| Constant | Purpose |
|---|---|
| `PROJECT_ROOT` | Absolute path to repo root |
| `DEFAULT_INPUT_DIR` | Default EPUB download directory |
| `DEFAULT_OUTPUT_DIR` | Default MP3 output directory |
| `TTS_VOICE` | Default Kokoro voice ID |
| `TTS_LANGUAGE` | Default TTS language code |
| `TTS_SAMPLE_RATE` | WAV sample rate (Hz) |
| `CHUNK_MAX_WORDS` | Max words per TTS chunk (450) |
| `SILENCE_BETWEEN_CHUNKS_MS` | Silence gap between chunks |
| `MP3_BITRATE` | MP3 encoding bitrate |
| `R2_CACHE_CONTROL` | Cache-Control header for R2 uploads |

## Pipeline Step Details

Each step is an independent module. Steps are pure-ish functions testable in isolation.

| Step | Module | Input → Output |
|---|---|---|
| 1 | `epub_parser.py` | EPUB file → `list[Chapter]` |
| 2 | `text_chunker.py` | chapter text → `list[str]` chunks |
| 3 | `tts.py` | chunks → WAV file |
| 4 | `encoder.py` | WAV → MP3, returns duration (seconds) |
| 5 | `manifest.py` | chapter metadata → `manifest.json` |
| 6 | `r2.py` | local audio dir → Cloudflare R2 |

### Notable behaviors

- **`epub_parser.py`**: Filters out nav/toc/cover items and chapters with <50 words. Strips footnote markers and numeric anchors. Title extracted from `<h1>`/`<h2>`/`<h3>` with fallbacks.
- **`text_chunker.py`**: Respects paragraph (`\n\n`) then sentence boundaries. Handles abbreviations (Dr., Mr., etc.) without splitting. Greedy packing up to `CHUNK_MAX_WORDS`.
- **`tts.py`**: Kokoro and torch imported lazily (only on first use). Auto-detects CUDA → MPS → CPU. Normalizes audio to 0.89 peak amplitude. Skips failed chunks with a warning rather than crashing.
- **`encoder.py`**: Warns on sample rate mismatch. Optionally deletes WAV after encoding.
- **`manifest.py`**: Idempotent — skips if `manifest.json` already exists. Uploads manifest last to signal completion.
- **`r2.py`**: Idempotency via a single HEAD request on the manifest key before uploading anything. Uploads manifest last. Sets correct `Content-Type` and `Cache-Control` headers per file type.
- **`runner.py`**: Currently a stub — not yet wired up. End-to-end orchestration is done by `scripts/convert-book.py` directly.

## Conventions

- Package name: `openshelf` (import path: `from openshelf.scrapers import http`)
- File naming: `snake_case.py`
- Constants: `UPPER_SNAKE_CASE` in `config.py`
- Tests mirror `src/` structure under `tests/`
- All tests must be fully mocked — no real network calls, no GPU, no ffmpeg
- Scripts use `sys.path.insert` so they work without `pip install`
- Type hints required on all public functions — no `Any`-style loose typing

## Key Design Decisions

- Pipeline steps are independent modules with pure-ish functions — each testable in isolation
- Idempotent at every level: file exists → skip, R2 key exists → skip
- `sanitize()` in `http.py` is the single source of truth for slug generation (used for author dirs and title filenames)
- Downloads go to `download/books/{source}/{author-slug}/{title-slug}.epub`
- Audio output goes to `audio/{author-slug}/{title-slug}/chapter-NN.mp3`
- No class hierarchies — composition of functions over inheritance
- Lazy imports for heavy deps (torch, kokoro) to keep scraper startup fast

## Do NOT

- Add `Any`-style loose typing or skip type hints on public functions
- Make real HTTP calls, GPU calls, or ffmpeg calls in tests
- Hard-code paths — use `config.py` constants
- Over-engineer — no abstractions until there are 2+ concrete uses
- Create files outside the established structure without updating this doc
- Implement `runner.py` orchestration by duplicating logic from `convert-book.py` — wire to shared helpers instead
