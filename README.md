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
  pipeline/               # EPUB → audio conversion (coming soon)
    epub_parser.py        # EPUB → chapters
    text_chunker.py       # chapter text → TTS-sized chunks
    tts.py                # text → audio via Kokoro TTS
    encoder.py            # WAV → MP3
    manifest.py           # generate chapter metadata
    r2.py                 # upload to Cloudflare R2
    runner.py             # orchestrator

scripts/
  download-books.py       # CLI entry point for book scraper

tests/
  scrapers/               # scraper unit tests (25 tests, all mocked)
  pipeline/               # pipeline unit tests
```

## Prerequisites

- Python 3.11+
- No pip dependencies required for the scraper (stdlib only)

## Quick Start

### Download Books

```bash
# Preview what would be downloaded (no files written)
python3 scripts/download-books.py --dry-run --author "Dostoevsky"

# Download from all sources
python3 scripts/download-books.py --author "Dostoevsky"

# Download from a specific source
python3 scripts/download-books.py --source gutenberg --author "Kafka"
python3 scripts/download-books.py --source standard-ebooks --author "Kafka"

# Filter by language
python3 scripts/download-books.py --author "Kafka" --language en

# Custom output directory
python3 scripts/download-books.py --author "Kafka" --output my-books/
```

Downloads are saved to `download/books/{source}/{author-slug}/{title-slug}.epub` and are idempotent — running the same command twice skips already-downloaded files.

### CLI Options

| Flag | Description | Default |
|------|-------------|---------|
| `--source` | `gutenberg`, `standard-ebooks`, or `all` | `all` |
| `--author` | Filter by author name (case-insensitive) | none |
| `--language` | Language code (e.g. `en`, `fr`, `de`) | none |
| `--subject` | Topic filter (Gutenberg only) | none |
| `--output` | Base output directory | `download/books` |
| `--delay` | Seconds between HTTP requests | `2` |
| `--dry-run` | List matches without downloading | off |

## Run Tests

```bash
# Run all tests
python3 -m unittest discover -s tests -v

# Run just the scraper tests
python3 -m unittest tests.scrapers.test_download_books -v
```

All tests are fully mocked — no network calls, safe to run offline.

## Development

Install as an editable package (optional, not required for scripts):

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```
