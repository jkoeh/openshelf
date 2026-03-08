# Execution Plan: EPUB → TTS Pipeline

## Overview

Convert downloaded EPUB books into AI-narrated MP3 audiobooks, organized by chapter, with metadata manifests uploaded to Cloudflare R2 for global streaming.

## Pipeline Flow

```
EPUB file → parse chapters → chunk text → Kokoro TTS → WAV → MP3 → manifest.json → R2 upload
              Step 1           Step 2       Step 3      Step 4   Step 5       Step 6
```

## Input (already exists)

```
download/books/
  gutenberg/{author-slug}/{title-slug}.epub
  standard-ebooks/{author-slug}/{title-slug}.epub
```

## Output (this pipeline creates)

```
audio/{author-slug}/{title-slug}/
  manifest.json
  chapter-01.mp3
  chapter-02.mp3
  ...
```

---

## Step 1: EPUB → Chapters (`epub_parser.py`)

Parse EPUB into a list of `Chapter(number, title, text, word_count)`.

**Rules:**
- Use `ebooklib` to read EPUB, `BeautifulSoup` to extract text
- Chapter title from first `h1`/`h2`/`h3` tag, fallback to `"Chapter N"`
- Skip items whose filename contains `nav`, `toc`, or `cover` (titlepage is kept as audiobook opening)
- Skip items with < 50 words (front matter, copyright)
- Strip `<sup>`/`<sub>` tags (footnote markers)
- Strip `<a>` tags with numeric-only content (internal cross-references like `[1]`)
- Preserve paragraph structure: double newline between paragraphs
- Normalize whitespace to single spaces within paragraphs

**Tests:** Feed a minimal EPUB fixture (or mock ebooklib), verify chapter extraction, title detection, skipping logic, text cleaning.

---

## Step 2: Text → Chunks (`text_chunker.py`)

Split chapter text into paragraph-aware chunks at sentence boundaries for TTS input.

**Rules:**
- Max 450 words per chunk (`config.CHUNK_MAX_WORDS`)
- Split on paragraph boundaries (`\n\n`) first — a chunk never crosses a paragraph break
- Within oversized paragraphs, split at sentence boundaries (`.`, `!`, `?`)
- Pack multiple short paragraphs into one chunk (greedy, up to max words)
- If a single sentence exceeds 450 words, split at comma boundaries
- Each chunk is a plain string with no internal `\n\n`

**Tests:** Pure function, easy to unit test. Test paragraph awareness, normal splitting, long sentences, edge cases (single sentence, empty input).

---

## Step 3: Chunks → Audio (`tts.py`)

Generate WAV audio for each chunk using Kokoro TTS, stitch with silence gaps.

**Config:**
- Voice: `af_heart` (from `config.TTS_VOICE`)
- Language: `en-us`
- Sample rate: 24000 Hz
- Silence between chunks: 400ms
- Device auto-detection: `cuda` → `mps` → `cpu`

**Implementation:**
- Load Kokoro pipeline once, reuse across all chunks
- Generate audio array per chunk
- Peak-normalize each chunk's audio to -1 dB before concatenation
- Insert 400ms silence (zero samples) between chunks
- Track skipped chunks (TTS failures) and return count alongside audio
- Concatenate into single WAV per chapter
- Write WAV to temp location

**Tests:** Mock the Kokoro pipeline, verify chunk-to-audio flow, silence insertion, device detection logic.

---

## Step 4: WAV → MP3 (`encoder.py`)

Convert WAV to MP3 using pydub (requires ffmpeg).

**Config:**
- Bitrate: 128kbps
- Delete WAV after successful conversion
- Output filename: `chapter-01.mp3` (zero-padded)

**Implementation:**
- `pydub.AudioSegment.from_wav(path).export(mp3_path, format="mp3", bitrate="128k")`
- Return MP3 path and duration in seconds

**Tests:** Mock pydub, verify correct export params and cleanup.

---

## Step 5: Generate Manifest (`manifest.py`)

Write `manifest.json` after all chapters for a book are processed.

**Format:**
```json
{
  "manifest_version": 1,
  "title": "Crime and Punishment",
  "author": "Fyodor Dostoevsky",
  "slug": "fyodor-dostoevsky/crime-and-punishment",
  "source": "gutenberg",
  "voice": "af_heart",
  "language": "en-us",
  "sample_rate": 24000,
  "generated_at": "2024-01-15T10:30:00Z",
  "total_duration_seconds": 77400,
  "chapters": [
    {
      "number": 1,
      "title": "Part I, Chapter I",
      "filename": "chapter-01.mp3",
      "duration_seconds": 1847,
      "word_count": 3241,
      "skipped_chunks": 0,
      "r2_key": "fyodor-dostoevsky/crime-and-punishment/chapter-01.mp3"
    }
  ]
}
```

**Tests:** Pure data construction, straightforward to test.

---

## Step 6: Upload to R2 (`r2.py`)

Upload MP3 files and manifest to Cloudflare R2 (S3-compatible API via boto3).

**R2 keys:**
```
{author-slug}/{title-slug}/chapter-01.mp3
{author-slug}/{title-slug}/manifest.json
```

**Config:**
- Always forward slashes in keys (regardless of OS)
- MP3: `Content-Type: audio/mpeg`, `Cache-Control: public, max-age=31536000`
- Manifest: `Content-Type: application/json`, `Cache-Control: public, max-age=3600`
- Skip upload if key already exists (HEAD check)
- Credentials from env vars: `R2_ACCOUNT_ID`, `R2_ACCESS_KEY`, `R2_SECRET_KEY`, `R2_BUCKET`

**Tests:** Mock boto3 client, verify key construction, content types, skip-if-exists logic.

---

## Step 7: Orchestrator (`runner.py`)

Wire steps 1–6 together. This is the CLI entry point.

**Flow per book:**
1. Find all EPUBs in input directory
2. For each EPUB:
   - Check if `manifest.json` already exists with all chapters → skip
   - Parse chapters (Step 1)
   - For each chapter:
     - If `chapter-NN.mp3` exists locally → skip
     - Chunk text (Step 2)
     - Generate audio (Step 3)
     - Encode to MP3 (Step 4)
   - Write manifest (Step 5)
   - Upload all files to R2 (Step 6)

**CLI:**
```bash
python3 -m openshelf.pipeline.runner                          # all books
python3 -m openshelf.pipeline.runner --filter dostoevsky      # filter
python3 -m openshelf.pipeline.runner --dry-run                # preview
python3 -m openshelf.pipeline.runner --no-upload              # local only
python3 -m openshelf.pipeline.runner --input DIR --output DIR # custom paths
python3 -m openshelf.pipeline.runner --voice af_heart         # voice selection
```

**Idempotency:**
- Chapter level: skip if MP3 exists locally
- Upload level: skip if R2 key exists
- Book level: skip if manifest exists and all chapters present

**Error handling:**
- TTS chunk fails → log, skip chunk, continue
- Upload fails → log, continue, report at end
- EPUB parse fails → log, skip book entirely
- Never crash the whole pipeline for one bad book
- Print summary at end: books processed, chapters generated, failures

---

## Implementation Order

1. **`text_chunker.py`** — pure function, no deps, easy to test first
2. **`epub_parser.py`** — needs ebooklib + bs4, testable with fixtures
3. **`manifest.py`** — pure data, trivial
4. **`encoder.py`** — thin pydub wrapper
5. **`tts.py`** — needs Kokoro + torch, GPU-dependent
6. **`r2.py`** — needs boto3 + R2 credentials
7. **`runner.py`** — orchestrator, integrate all steps

Steps 1–4 can be built and tested without GPU or cloud credentials. Steps 5–6 need hardware/credentials but are straightforward with mocks.

---

## Dependencies

```
# Already in pyproject.toml
kokoro          # TTS engine
soundfile       # WAV I/O + duration
numpy           # audio array ops
pydub           # WAV → MP3 (requires ffmpeg installed)
ebooklib        # EPUB parsing
beautifulsoup4  # HTML → text
boto3           # R2 upload (S3-compatible)
python-dotenv   # .env file loading
torch           # GPU device detection for Kokoro
```

System requirement: `ffmpeg` must be installed (`brew install ffmpeg` on macOS).

---

## Source Deduplication

Both Gutenberg and Standard Ebooks may have the same book. Since the audio output flattens to `{author-slug}/{title-slug}/`, we need a preference rule:

- **Prefer Standard Ebooks** (higher quality formatting, cleaner HTML)
- When scanning input EPUBs, collect by `(author-slug, title-slug)` key
- If both sources have it, use the SE version, skip Gutenberg

---

## Console Output

```
Found 12 EPUB(s) to process
Loading Kokoro pipeline on cuda...
Ready.

[1/12] fyodor-dostoevsky/crime-and-punishment (gutenberg)
  38 chapters

  [1/38] Part I, Chapter I (3241 words)
    chunk 1/7 ... chunk 2/7 ... ... chunk 7/7
    chapter-01.mp3 (30.7 min) → uploaded

  [2/38] Part I, Chapter II
    [SKIP] chapter-02.mp3 exists

  ...
  manifest.json written → uploaded

Done. 12 books processed, 0 failed.
```
