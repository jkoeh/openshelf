# Execution Plan Phase 3: Metadata & Cloud Upload (Steps 5 & 6)

## Context

Phase 1 delivered `epub_parser.py` + `text_chunker.py` (EPUB → text chunks).
Phase 2 delivered `tts.py` + `encoder.py` (text chunks → MP3 files).
This phase closes the pipeline: generate a `manifest.json` per book and upload everything to Cloudflare R2.

## Dependencies

- `boto3` — S3-compatible client for Cloudflare R2
- `botocore` — ships with boto3; used for `ClientError` handling
- Standard library only for manifest: `json`, `os`, `datetime`

No new pip installs required — all deps are already in `pyproject.toml`.

## Implementation Order

1. Write `tests/pipeline/test_manifest.py` (tests first)
2. Implement `src/openshelf/pipeline/manifest.py`
3. Write `tests/pipeline/test_r2.py` (tests first)
4. Implement `src/openshelf/pipeline/r2.py`

---

## Step 5: manifest.py — Build manifest.json

### Data Structures

```python
@dataclass
class ChapterMeta:
    number: int
    title: str
    filename: str           # e.g. "chapter-01.mp3"
    duration_seconds: float
    word_count: int
```

Defined in `manifest.py` (not imported from `tts.py`) — keeps manifest step decoupled from TTS internals. The runner will convert `ChapterAudio` → `ChapterMeta`.

### Public Function

```python
def generate_manifest(
    author: str,
    title: str,
    source: str,
    chapters: list[ChapterMeta],
    output_dir: str,
) -> str:
```

Writes `manifest.json` into `output_dir`. Returns the full path to the manifest file.

### Manifest JSON Schema

```json
{
  "title": "The Trial",
  "author": "Franz Kafka",
  "source": "gutenberg",
  "generated_at": "2026-03-09T12:00:00+00:00",
  "total_duration_seconds": 7234.5,
  "chapters": [
    {
      "number": 1,
      "title": "Before the Law",
      "filename": "chapter-01.mp3",
      "duration_seconds": 423.7,
      "word_count": 1820
    }
  ]
}
```

### Logic

1. `os.makedirs(output_dir, exist_ok=True)`
2. Build `manifest_path = os.path.join(output_dir, "manifest.json")`
3. If `os.path.exists(manifest_path)` → log and return path (idempotent)
4. Build manifest dict with all fields
5. `json.dump(manifest, f, indent=2, ensure_ascii=False)`
6. Return `manifest_path`

### Test Cases (`tests/pipeline/test_manifest.py`)

Mock strategy: mock `os.path.exists`, `os.makedirs`, `builtins.open`, `json.dump`.

**TestChapterMeta:**
- `test_fields` — all fields stored correctly

**TestGenerateManifestIO:**
- `test_writes_json_file` — `open()` called once
- `test_returns_manifest_path` — return value ends with `"manifest.json"`
- `test_skips_if_exists` — when file exists, `open()` NOT called, path still returned
- `test_creates_output_directory` — `os.makedirs` called with `exist_ok=True`

**TestGenerateManifestContent:**
- `test_manifest_has_title` — `manifest["title"]` correct
- `test_manifest_has_author` — `manifest["author"]` correct
- `test_manifest_has_source` — `manifest["source"]` correct
- `test_manifest_has_chapters` — `manifest["chapters"]` has correct length
- `test_chapter_fields` — number, title, filename, duration_seconds, word_count all correct
- `test_manifest_has_generated_at` — present and parseable as ISO datetime
- `test_manifest_total_duration` — sum of chapter durations
- `test_total_duration_empty_chapters` — 0.0 when no chapters
- `test_single_chapter` — single chapter list works
- `test_chapters_preserve_order` — chapter numbers in order

---

## Step 6: r2.py — Upload to Cloudflare R2

### Design Principles (Cloudflare R2 specific)

**Single idempotency gate — do not HEAD every key.**

R2 bills per Class B API operation (HEAD, LIST: $0.36/million). Checking existence for every
file before upload means N+1 HEAD requests per book (one per chapter + one for manifest). For
a 20-chapter book processed 1,000 times across re-runs, that is 21,000 wasted HEAD calls.

The correct pattern exploits the upload ordering contract: **manifest is always written last**.
If `manifest.json` exists on R2 → all MP3s are guaranteed to be there (they were uploaded in
the same run, before the manifest). One HEAD request at the top of `upload_book` is sufficient.
If manifest does not exist, upload everything unconditionally. PUT is idempotent on R2 — the
same bytes produce the same object. A partial previous run's MP3s are safely overwritten.

**Cache-Control headers are required, not optional.**

R2 objects served via Cloudflare CDN inherit their `Cache-Control` header. Without it,
Cloudflare uses a default short TTL (typically 2 hours), causing repeated cache misses that
incur R2 egress charges and increase latency for listeners. Audio files are immutable once
written — they must be served with `immutable` so CDN edge nodes never revalidate them.

**ContentDisposition: inline on audio.**

Without `ContentDisposition: inline`, browsers prompt a file download instead of streaming.
Web audio players (`<audio src="...">`) require inline disposition to play in-browser.

### Config Constants (in `config.py`)

```python
R2_CACHE_CONTROL_AUDIO    = "public, max-age=31536000, immutable"
R2_CACHE_CONTROL_MANIFEST = "public, max-age=60"
```

Audio files: 1-year TTL, `immutable` — browsers and CDN edges never revalidate.
Manifest: 60-second TTL — allows updates to propagate promptly if a book is reprocessed.

### Public Functions

```python
def make_client() -> Any:
```
Create boto3 S3 client pointing at Cloudflare R2. Uses `R2_ACCOUNT_ID`, `R2_ACCESS_KEY`,
`R2_SECRET_KEY` from config.

```python
def key_exists(client: Any, bucket: str, key: str) -> bool:
```
`HEAD` the object. Returns `True` if present, `False` on 404, re-raises on any other error.

```python
def upload_book(
    client: Any,
    bucket: str,
    author_slug: str,
    title_slug: str,
    audio_dir: str,
    manifest_path: str,
) -> list[str]:
```
Upload all `.mp3` files from `audio_dir` + `manifest_path` to R2. Returns list of uploaded R2
keys. If `manifest.json` already exists in R2 → skip entire book (returns `[]`).

### R2 Key Format

```
{author_slug}/{title_slug}/chapter-01.mp3
{author_slug}/{title_slug}/chapter-02.mp3
{author_slug}/{title_slug}/manifest.json
```

### Logic for `upload_book`

1. Build `manifest_key = f"{author_slug}/{title_slug}/manifest.json"`
2. `key_exists(client, bucket, manifest_key)` → if True, log and return `[]`
3. Collect MP3s: `sorted(f for f in os.listdir(audio_dir) if f.endswith(".mp3"))`
4. For each MP3:
   - `client.upload_file(local_path, bucket, key, ExtraArgs={ContentType, CacheControl, ContentDisposition})`
   - Append key to `uploaded`
5. Upload manifest with `ExtraArgs={ContentType: application/json, CacheControl: 60s}`
6. Return `uploaded`

### Upload ExtraArgs

MP3 files:
```python
ExtraArgs={
    "ContentType": "audio/mpeg",
    "CacheControl": R2_CACHE_CONTROL_AUDIO,
    "ContentDisposition": "inline",
}
```

manifest.json:
```python
ExtraArgs={
    "ContentType": "application/json",
    "CacheControl": R2_CACHE_CONTROL_MANIFEST,
}
```

### Test Cases (`tests/pipeline/test_r2.py`)

Mock strategy: mock `boto3.client`, mock `key_exists` (when testing `upload_book`), use
`tempfile.TemporaryDirectory` for real MP3/manifest files on disk.

**TestMakeClient:**
- `test_creates_s3_client` — first positional arg to `boto3.client` is `"s3"`
- `test_uses_r2_endpoint` — `endpoint_url` contains `R2_ACCOUNT_ID`
- `test_uses_r2_credentials` — `aws_access_key_id` and `aws_secret_access_key` match patched values

**TestKeyExists:**
- `test_returns_true_when_key_exists` — `head_object` succeeds → `True`
- `test_calls_head_object_with_correct_args` — Bucket and Key passed correctly
- `test_returns_false_on_404` — `ClientError(code=404)` → `False`
- `test_raises_on_other_client_errors` — `ClientError(code=403)` → re-raises

**TestUploadBookUploads:**
- `test_uploads_mp3_and_manifest` — 2 MP3s + manifest → `upload_file` called 3 times
- `test_r2_key_format_for_chapters` — keys are `author/title/chapter-01.mp3` etc.
- `test_r2_key_format_for_manifest` — `author/title/manifest.json` in uploaded keys
- `test_returns_uploaded_keys` — returns list of 3 keys when all new

**TestUploadBookIdempotency:**
- `test_skips_entire_book_if_manifest_exists` — `key_exists` returns True → `upload_file` never called
- `test_returns_empty_list_when_manifest_exists` — returns `[]`
- `test_only_one_head_request` — `key_exists` called exactly once regardless of chapter count

**TestUploadBookHeaders:**
- `test_mp3_content_type` — `ContentType: audio/mpeg`
- `test_manifest_content_type` — `ContentType: application/json`
- `test_mp3_cache_control` — `CacheControl: public, max-age=31536000, immutable`
- `test_manifest_cache_control` — `CacheControl: public, max-age=60`
- `test_mp3_content_disposition_inline` — `ContentDisposition: inline`

---

## Files to Create / Modify

| File | Action |
|------|--------|
| `tests/pipeline/test_manifest.py` | Create (tests first) |
| `src/openshelf/pipeline/manifest.py` | Implement |
| `tests/pipeline/test_r2.py` | Create (tests first) |
| `src/openshelf/pipeline/r2.py` | Implement |
| `src/openshelf/config.py` | Add `R2_CACHE_CONTROL_AUDIO` and `R2_CACHE_CONTROL_MANIFEST` |

## Conventions (carried from Phase 1 & 2)

- Tests use `unittest.TestCase` + `unittest.mock`, no pytest
- `sys.path.insert(0, ...)` at top of test files
- Constants from `config.py`, never hardcoded
- All tests fully mocked — no real network calls, no real R2 credentials needed
- Follow patterns established in prior phase tests

## Verification

```bash
python3 -m unittest tests.pipeline.test_manifest -v
python3 -m unittest tests.pipeline.test_r2 -v
python3 -m unittest discover -s tests -v  # all tests still pass
```

## Design Notes

- **`ChapterMeta` vs `ChapterAudio`**: `manifest.py` defines its own `ChapterMeta` dataclass. This decouples Step 5 from Step 3's internals. The runner converts between them. Avoids a cross-step import chain.
- **Manifest-as-completion-signal**: Uploading manifest last is the atomic commit point. If the process crashes mid-upload, manifest is absent, and the next run re-uploads cleanly. No partial state can be mistaken for a completed upload.
- **Single HEAD idempotency**: Checking only `manifest.json` existence collapses O(N) HEAD requests to O(1). Re-uploading MP3s in a retry scenario is safe — R2 PUT is idempotent for the same content.
- **Immutable cache on audio**: `max-age=31536000, immutable` tells both browsers and Cloudflare CDN edges to never revalidate these objects. Reduces R2 egress to near zero for popular books after initial cache warm.
- **Short cache on manifest**: 60 seconds allows re-processed books to propagate updated metadata promptly while still benefiting from edge caching.
- **`ContentDisposition: inline`**: Required for browser `<audio>` tag streaming. Without it, clicking a direct R2 URL downloads the file instead of playing it.
- **No `make_client` inside `upload_book`**: Client injected as parameter — testable and lets runner control lifecycle.
- **Sorted MP3 upload**: Chapters upload in chapter order. Consistent, predictable, no surprises in logs.
