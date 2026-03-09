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

**TestGenerateManifest:**
- `test_writes_json_file` — `open()` called once
- `test_returns_manifest_path` — return value ends with `"manifest.json"`
- `test_skips_if_exists` — when file exists, `open()` NOT called, path still returned
- `test_creates_output_directory` — `os.makedirs` called with `exist_ok=True`
- `test_manifest_has_title` — `manifest["title"]` correct
- `test_manifest_has_author` — `manifest["author"]` correct
- `test_manifest_has_source` — `manifest["source"]` correct
- `test_manifest_has_chapters` — `manifest["chapters"]` has correct length
- `test_chapter_fields` — number, title, filename, duration_seconds, word_count all correct
- `test_manifest_has_generated_at` — present and parseable as ISO datetime
- `test_manifest_total_duration` — sum of chapter durations

---

## Step 6: r2.py — Upload to Cloudflare R2

### Public Functions

```python
def make_client() -> Any:
```
Create boto3 S3 client pointing at Cloudflare R2. Uses `R2_ACCOUNT_ID`, `R2_ACCESS_KEY`, `R2_SECRET_KEY` from config.

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
Upload all `.mp3` files from `audio_dir` + `manifest_path` to R2. Returns list of R2 keys that were actually uploaded (already-existing keys skipped).

### R2 Key Format

```
{author_slug}/{title_slug}/chapter-01.mp3
{author_slug}/{title_slug}/chapter-02.mp3
{author_slug}/{title_slug}/manifest.json
```

### Logic for `upload_book`

1. Collect MP3s: `sorted(f for f in os.listdir(audio_dir) if f.endswith(".mp3"))`
2. For each MP3:
   - Build key: `f"{author_slug}/{title_slug}/{filename}"`
   - If `key_exists(...)` → log skip, continue
   - `client.upload_file(local_path, bucket, key, ExtraArgs={"ContentType": "audio/mpeg"})`
   - Append key to `uploaded`
3. Build manifest key: `f"{author_slug}/{title_slug}/manifest.json"`
4. Same idempotency check + upload with `ContentType: application/json`
5. Return `uploaded`

### Test Cases (`tests/pipeline/test_r2.py`)

Mock strategy: mock `boto3.client`, mock `key_exists` (when testing `upload_book`), use `tempfile.TemporaryDirectory` for real MP3/manifest files.

**TestMakeClient:**
- `test_creates_s3_client` — first positional arg to `boto3.client` is `"s3"`
- `test_uses_r2_endpoint` — `endpoint_url` contains `R2_ACCOUNT_ID`
- `test_uses_r2_credentials` — `aws_access_key_id` and `aws_secret_access_key` match patched config values

**TestKeyExists:**
- `test_returns_true_when_key_exists` — `head_object` succeeds → `True`
- `test_returns_false_on_404` — `head_object` raises `ClientError(code=404)` → `False`
- `test_raises_on_other_errors` — `head_object` raises `ClientError(code=403)` → re-raises

**TestUploadBook:**
- `test_uploads_mp3_files` — 2 MP3s + manifest → `upload_file` called 3 times
- `test_r2_key_format` — keys are `author/title/chapter-01.mp3` etc.
- `test_uploads_manifest` — `author/title/manifest.json` in uploaded keys
- `test_skips_existing_keys` — all exist → `upload_file` never called
- `test_returns_uploaded_keys` — returns list of 3 keys when all new
- `test_skips_mp3s_uploads_manifest` — MP3s exist, manifest new → 1 call
- `test_mp3_content_type` — `ExtraArgs={"ContentType": "audio/mpeg"}`
- `test_manifest_content_type` — `ExtraArgs={"ContentType": "application/json"}`

---

## Files to Create / Modify

| File | Action |
|------|--------|
| `tests/pipeline/test_manifest.py` | Create (tests first) |
| `src/openshelf/pipeline/manifest.py` | Implement |
| `tests/pipeline/test_r2.py` | Create (tests first) |
| `src/openshelf/pipeline/r2.py` | Implement |

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
- **Idempotency**: Both `generate_manifest` and `upload_book` are safe to re-run. Manifest skips if file exists. Upload checks `HEAD` per key before uploading. Re-running on a partial failure resumes cleanly.
- **`key_exists` as separate function**: Extracted for testability and reuse. The runner can also call it independently to check upload status.
- **Sorted MP3 upload**: `sorted()` ensures chapters upload in order. Consistent and predictable.
- **No `make_client` call inside `upload_book`**: Client is injected as a parameter. This keeps the function pure/testable and lets runner.py control client lifecycle (create once, reuse across retries).
- **Content types**: Set explicitly on upload. Browsers and CDN correctly serve `audio/mpeg` and `application/json` without guessing.
