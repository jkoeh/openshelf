# OpenShelf

Open source public domain audiobook platform. Downloads EPUB books from Project Gutenberg and Standard Ebooks, converts them to AI-narrated audio with text/audio sync, and serves them globally via Cloudflare R2.

## Project Structure

```
src/openshelf/
  config.py               # shared settings and constants
  scrapers/               # book discovery and download
    http.py               # make_request(), sanitize(), download_book()
    gutenberg.py          # Project Gutenberg search via Gutendex API
    standard_ebooks.py    # Standard Ebooks search via HTML scraping
  pipeline/               # EPUB → audio conversion
    epub_parser.py        # Step 1: EPUB → chapters with ContentElements
    epub_annotator.py     # Step 1b: inject stable IDs into EPUB HTML
    text_chunker.py       # Step 2: chapter text → TTS-sized chunks (v3)
    tts.py                # Step 3: text → audio via Kokoro TTS
    encoder.py            # Step 4: WAV → MP3
    manifest.py           # Step 5a: generate chapter metadata JSON
    alignment.py          # Step 5b: generate chunk→timestamp alignment JSON
    r2.py                 # Step 6: upload to Cloudflare R2
    runner.py             # orchestrator

scripts/
  download-books.py       # CLI for book scraping
  convert-book.py         # CLI for EPUB → MP3 conversion

tests/
  scrapers/               # scraper unit tests (all mocked)
  pipeline/               # pipeline unit tests (all mocked)
```

## Prerequisites

- Python 3.11+
- Scraper: no pip dependencies (stdlib only)
- Pipeline parsing: `ebooklib`, `beautifulsoup4`
- Pipeline audio: `kokoro`, `torch`, `soundfile`, `numpy`, `pydub`
- System: `ffmpeg` (for MP3 encoding)

## Install

```bash
# Install uv (if not already installed)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Create venv and install dependencies
uv venv
source .venv/bin/activate
uv pip install -r requirements.txt

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

# Choose voice and device
python3 scripts/convert-book.py my-book.epub --voice bf_emma --device cpu

# Upload to R2 after conversion
python3 scripts/convert-book.py my-book.epub --source standard-ebooks --upload
```

Idempotent — skips chapters where the MP3 already exists.

#### Convert CLI Options

| Flag          | Description                              | Default               |
| ------------- | ---------------------------------------- | --------------------- |
| `epub`        | Path to EPUB file (required)             |                       |
| `--output`    | Output directory                         | `audio/`              |
| `--voice`     | Kokoro voice ID                          | `af_heart`            |
| `--rendition` | Rendition name (for R2 key prefix)       | `kokoro-af-heart`     |
| `--device`    | `cuda`, `mps`, or `cpu`                  | auto-detect           |
| `--dry-run`   | Parse and show chapters, no audio        | off                   |
| `--keep-wav`  | Keep intermediate WAV files              | off                   |
| `--upload`    | Upload to Cloudflare R2 after conversion | off                   |

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

## Pipeline: Download to R2

### Step 0 — Discover & Download

`download-books.py` calls `gutenberg_search()` or `se_search()` to find books, then `download_book()` fetches the EPUB and saves it to `download/books/{source}/{author-slug}/{title-slug}.epub`. Idempotent — skips existing files.

### Step 1 — Parse EPUB (`epub_parser.parse_epub`)

Reads the EPUB with `ebooklib`, walks each HTML document in spine order. For each file, BeautifulSoup finds all semantic content tags (`p`, `h1`–`h6`, `blockquote`, `li`, `figcaption`). Each becomes a `ContentElement`:

- `id` — stable, chapter-namespaced ID: `"ch{N}-el{NNNN}"`
- `tag` — element type (`"p"`, `"h2"`, etc.)
- `html` — outer HTML with the `id` attribute already set
- `text` — plain text after stripping `<sup>`, `<sub>`, and numeric anchors
- `spoken: bool` — `False` if the element or any ancestor has `epub:type` in `{footnote, endnote, toc, pagebreak}`, otherwise `True`

Chapters shorter than 50 words are filtered before a chapter number is assigned, so element IDs are always gap-free. Each chapter also stores `epub_item_name` (the filename inside the EPUB zip) for the annotator.

### Step 1b — Annotate EPUB (`epub_annotator.annotate_epub`)

Re-opens the same EPUB and replays the identical tag-walking logic from Step 1, writing each `ContentElement.id` back into the matching HTML tag as an `id` attribute. The modified EPUB is serialized to bytes and saved as `{book_dir}/book-annotated.epub`.

This is the file the reader app loads — every narrated element is now addressable in the DOM.

### Step 2 — Chunk Text (`text_chunker.chunk_text`)

Takes the spoken paragraphs and their parallel element IDs, and greedily packs them into `Chunk`s of at most `CHUNK_MAX_WORDS` (450) words. Oversized paragraphs are split first at sentence boundaries, then at commas, then by raw word count. Each chunk records:

- `para_start` / `para_end` — indices into the spoken-paragraph list
- `el_start` / `el_end` — element IDs of the first and last paragraph in the chunk

All chunks are serialized to `{book_dir}/chunks.json` (v3 format, shared across renditions).

### Step 3 — TTS (`tts.synthesize_chapter`)

Sends each chunk's text to Kokoro in sequence, tracking cumulative audio frame offsets to build `chunk_audio_starts` — the time in seconds where each chunk begins in the concatenated audio. Chunks are joined with 400ms silence gaps and written to a WAV file.

### Step 4 — Encode (`encoder.encode_to_mp3`)

Runs `ffmpeg` to encode WAV → MP3 at 128kbps. Returns the WAV duration measured via `soundfile` (exact sample counts). Optionally deletes the WAV.

### Step 5a — Manifest (`manifest.generate_manifest`)

Writes `manifest.json` to the rendition directory with chapter titles, filenames, durations, and word counts. Idempotent.

### Step 5b — Alignment (`alignment.build_alignment`)

Combines per-chapter `chunk_audio_starts` lists into `alignment.json`. Format: for each chapter, a list of `{start_s, chunk_idx}` pairs (TTS-skipped chunks are excluded). Written idempotently.

### Step 6 — Upload to R2 (`r2.py`)

Four uploads, each checking key existence first (HEAD request) to skip re-uploads:

| Upload | R2 key |
|--------|--------|
| Annotated EPUB | `books/{author}/{title}/book.epub` |
| Chunk index | `books/{author}/{title}/chunks.json` |
| MP3s + manifest | `books/{author}/{title}/audio/{rendition}/chapter-*.mp3` + `manifest.json` |
| Alignment | `books/{author}/{title}/audio/{rendition}/alignment.json` |

## Client Text/Audio Sync

The three R2 artifacts work together to enable read-along highlighting and position-preserving mode switching between audio and text:

1. Reader renders `book.epub` — every content element has a stable `id` attribute
2. As audio plays, binary-search `alignment.json` maps `current_time → chunk_idx`
3. `chunks.json` maps `chunk_idx → el_start / el_end`
4. Reader highlights `document.getElementById(el_start)` through `el_end`
5. Tap-to-seek from text: element id → find containing chunk → jump audio to `alignment[chunk_idx].start_s`

## Run Tests

```bash
# Run all tests
python3 -m unittest discover -s tests -v

# Run by module
python3 -m unittest tests.scrapers.test_download_books -v
python3 -m unittest tests.pipeline.test_epub_parser -v
python3 -m unittest tests.pipeline.test_text_chunker -v
python3 -m unittest tests.pipeline.test_epub_annotator -v
```

All tests are fully mocked — no network calls, no GPU, no ffmpeg required. Safe to run offline.

## Development

```bash
uv venv
source .venv/bin/activate
uv pip install -r requirements.txt
```
