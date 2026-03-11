# OpenShelf

Open source public domain audiobook platform. Downloads EPUB books from Project Gutenberg and Standard Ebooks, converts them to AI-narrated audio, and serves them globally via Cloudflare R2.

## Project Structure

```
src/openshelf/
  config.py               # shared settings and constants
  scrapers/               # book discovery and download
    http.py               # make_request(), sanitize(), download_book()
    gutenberg.py          # Project Gutenberg search via Gutendex API
    standard_ebooks.py    # Standard Ebooks search via HTML scraping
  pipeline/               # EPUB → audio conversion
    epub_parser.py        # EPUB → chapters
    text_chunker.py       # chapter text → TTS-sized chunks
    tts.py                # text → audio via Kokoro TTS
    encoder.py            # WAV → MP3
    manifest.py           # generate chapter metadata
    r2.py                 # upload to Cloudflare R2
    runner.py             # orchestrator

scripts/
  download-books.py       # CLI for book scraping
  convert-book.py         # CLI for EPUB → MP3 conversion

tests/
  scrapers/               # scraper unit tests (all mocked)
  pipeline/               # pipeline unit tests (all mocked)
```

## Prerequisites

- Python 3.9+
- Scraper: no pip dependencies (stdlib only)
- Pipeline parsing: `ebooklib`, `beautifulsoup4`
- Pipeline audio: `kokoro`, `torch`, `soundfile`, `numpy`, `pydub`
- System: `ffmpeg` (for MP3 encoding)

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# ffmpeg (macOS) — required for MP3 encoding
brew install ffmpeg
```

## Quick Start

### 1. Download Books

```bash
# Preview what would be downloaded (no files written)
python3 scripts/download-books.py --dry-run --author "Dostoevsky, Fyodor"

# Download from all sources
python3 scripts/download-books.py --author "Dostoevsky, Fyodor"

# Download from a specific source
python3 scripts/download-books.py --source gutenberg --author "Franz Kafka"
python3 scripts/download-books.py --source standard-ebooks --author "Franz Kafka"

# Filter by language
python3 scripts/download-books.py --author "Franz Kafka" --language en

# Custom output directory
python3 scripts/download-books.py --author "Franz Kafka" --output my-books/
```

Downloads are saved to `download/books/{source}/{author-slug}/{title-slug}.epub` and are idempotent — running the same command twice skips already-downloaded files.

#### Scraper CLI Options

| Flag         | Description                              | Default          |
| ------------ | ---------------------------------------- | ---------------- |
| `--source`   | `gutenberg`, `standard-ebooks`, or `all` | `all`            |
| `--author`   | Filter by author name (case-insensitive) | none             |
| `--language` | Language code (e.g. `en`, `fr`, `de`)    | none             |
| `--subject`  | Topic filter (Gutenberg only)            | none             |
| `--output`   | Base output directory                    | `download/books` |
| `--delay`    | Seconds between HTTP requests            | `2`              |
| `--dry-run`  | List matches without downloading         | off              |

### 2. Convert EPUB to Audio

```bash
# Convert a book (auto-detects GPU)
python3 scripts/convert-book.py download/books/gutenberg/dostoyevsky-fyodor/the-grand-inquisitor.epub

# Preview chapters without generating audio
python3 scripts/convert-book.py download/books/gutenberg/dostoyevsky-fyodor/the-grand-inquisitor.epub --dry-run

# Custom output directory
python3 scripts/convert-book.py my-book.epub --output audiobooks/my-book/

# Choose voice and device
python3 scripts/convert-book.py my-book.epub --voice bf_emma --device cpu

# Keep WAV files (for debugging)
python3 scripts/convert-book.py my-book.epub --keep-wav
```

Idempotent — skips chapters where the MP3 already exists.

#### Convert CLI Options

| Flag         | Description                          | Default      |
| ------------ | ------------------------------------ | ------------ |
| `epub`       | Path to EPUB file (required)         |              |
| `--output`   | Output directory                     | `audio/`     |
| `--voice`    | Kokoro voice ID                      | `af_heart`   |
| `--device`   | `cuda`, `mps`, or `cpu`              | auto-detect  |
| `--dry-run`  | Parse and show chapters, no audio    | off          |
| `--keep-wav` | Keep intermediate WAV files          | off          |

#### Pipeline configuration

All constants are in `src/openshelf/config.py`:

| Constant                    | Default      | Description                 |
| --------------------------- | ------------ | --------------------------- |
| `CHUNK_MAX_WORDS`           | `450`        | Max words per TTS chunk     |
| `TTS_VOICE`                 | `"af_heart"` | Kokoro voice ID             |
| `TTS_LANGUAGE`              | `"en-us"`    | TTS language                |
| `TTS_SAMPLE_RATE`           | `24000`      | Audio sample rate (Hz)      |
| `SILENCE_BETWEEN_CHUNKS_MS` | `400`        | Silence between chunks (ms) |
| `MP3_BITRATE`               | `"128k"`     | MP3 encoding bitrate        |

## Run Tests

```bash
# Run all tests (122 tests)
python3 -m unittest discover -s tests -v

# Run by module
python3 -m unittest tests.scrapers.test_download_books -v
python3 -m unittest tests.pipeline.test_epub_parser -v
python3 -m unittest tests.pipeline.test_text_chunker -v
python3 -m unittest tests.pipeline.test_tts -v
python3 -m unittest tests.pipeline.test_encoder -v
```

All tests are fully mocked — no network calls, no GPU, no ffmpeg required. Safe to run offline.

## Development

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```
