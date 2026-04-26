# Step 6: R2 Upload

**Module:** `src/openshelf/pipeline/r2.py`
**Test:** `tests/pipeline/test_r2.py`

## Purpose

Upload all pipeline artifacts to Cloudflare R2 (S3-compatible object storage). Each upload function is independently idempotent via HEAD checks. The rendition upload uses a single-gate idempotency pattern — only the manifest existence is checked, not each individual file.

```mermaid
graph TD
    A[Pipeline outputs] --> B[make_client]
    B --> C[upload_epub]
    B --> D[upload_cover]
    B --> E[upload_rendition]
    B --> F[upload_chapter_data]
    B --> G[upload_word_alignment - opt-in]

    C --> C1{book.epub exists on R2?}
    C1 -->|Yes| C2[Skip]
    C1 -->|No| C3[PUT book.epub]

    E --> E1{manifest.json exists on R2?}
    E1 -->|Yes| E2[Skip entire rendition]
    E1 -->|No| E3[PUT all m4a files]
    E3 --> E4[PUT manifest.json last]

    F --> F1{chapter_data.json exists on R2?}
    F1 -->|Yes| F2[Skip]
    F1 -->|No| F3[PUT chapter_data.json]
```

## Interface

### Public Functions

```python
def make_client() -> boto3.client
    # S3 client configured for R2 using env vars

def key_exists(client, bucket: str, key: str) -> bool
    # HEAD request; True if exists, False on 404, re-raises other errors

def upload_rendition(
    client, bucket: str,
    author_slug: str, title_slug: str,
    rendition: str,
    audio_dir: str, manifest_path: str,
) -> list[str]
    # Returns list of uploaded R2 keys, or [] if already complete

def upload_epub(
    client, bucket: str,
    author_slug: str, title_slug: str,
    epub_path: str,
) -> str | None
    # Returns key or None if already exists

def upload_chapter_data(
    client, bucket: str,
    author_slug: str, title_slug: str,
    rendition: str,
    chapter_data_path: str,
) -> str | None

def upload_word_alignment(            # opt-in only (--whisperx)
    client, bucket: str,
    author_slug: str, title_slug: str,
    rendition: str,
    word_alignment_path: str,
) -> str | None
```

## R2 Key Layout

```
books/{author_slug}/{title_slug}/
  book.epub                                    # annotated EPUB
  cover.{jpg|png}                              # cover image
  audio/{rendition}/
    chapter-01.m4a                             # audio files
    chapter-02.m4a
    manifest.json                              # chapter metadata
    chapter_data.json                          # per-chunk text + Kokoro word timestamps
    word_alignment.json                        # only present if --whisperx was used
```

### `chapter_data.json` shape

```json
{
  "version": 1,
  "rendition": "kokoro-af-heart",
  "chapters": [
    {
      "number": 1,
      "title": "Chapter 1",
      "word_count": 1234,
      "chunks": [
        {
          "text": "Someone must have been telling lies about Josef K.",
          "words": [
            {"word": "Someone", "start": 0.0, "end": 0.31},
            {"word": "must",    "start": 0.31, "end": 0.48}
          ]
        }
      ]
    }
  ]
}
```

The worker's `/chapters/:n` endpoint reads this file directly: it returns `chunks` as flat strings, plus a flattened `words` array with `chunk_idx` injected per word.

## Behavior

### Idempotency Strategy

**Rendition upload** uses a single-gate pattern: only `manifest.json` existence is checked (one HEAD request). If it exists, the entire rendition is skipped. This works because manifest is always uploaded **last** — its presence guarantees all Opus files are already on R2. A partial previous run's files are safely overwritten on retry (PUT is idempotent).

**Other uploads** (EPUB, cover, chapter_data, word_alignment) each check their own key existence independently.

This avoids O(N) HEAD requests per book. For a 20-chapter book, it's a handful of HEAD requests total instead of 24.

### Cache Headers

| File type | Cache-Control | Rationale |
|---|---|---|
| m4a, EPUB, cover, chapter_data, alignment | `public, max-age=31536000, immutable` | Content never changes once written |
| manifest.json | `public, max-age=60` | Allows updates to propagate on reprocessing |

### Content Types

| File | Content-Type |
|---|---|
| `.m4a` | `audio/mp4` |
| `.epub` | `application/epub+zip` |
| `.json` | `application/json` |

### ContentDisposition

Opus files include `ContentDisposition: inline` so browsers stream instead of downloading.

### Client Configuration

```python
boto3.client(
    "s3",
    endpoint_url=f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
    aws_access_key_id=R2_ACCESS_KEY,
    aws_secret_access_key=R2_SECRET_KEY,
    region_name="auto",
)
```

Credentials come from environment variables: `R2_ACCOUNT_ID`, `R2_ACCESS_KEY`, `R2_SECRET_KEY`.

## Dependencies

- `boto3` — S3-compatible API client
- `botocore` — `ClientError` for HEAD 404 handling
- Config: `R2_ACCOUNT_ID`, `R2_ACCESS_KEY`, `R2_SECRET_KEY`, `R2_PREFIX_BOOKS`, `R2_CACHE_CONTROL_IMMUTABLE`, `R2_CACHE_CONTROL_MANIFEST`
