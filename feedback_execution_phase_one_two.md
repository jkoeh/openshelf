# Design Review Feedback: Execution Plans Phase 1 & 2

**Reviewer perspective:** Staff engineer, TTS/audio production
**Date:** 2026-03-07
**Scope:** PLAN.md, EXECUTION_PLAN_PHASE1.md, EXECUTION_PLAN_PHASE2.md

---

## Overall Assessment

The plan is well-structured for a POC. Pipeline decomposition is correct, test-first discipline is sound, and idempotency is built in from the start. The issues below are ranked by how expensive they become if addressed later.

---

## Critical — Fix before scaling past POC

### 1. Chunking splits across paragraph boundaries

**File:** `text_chunker.py`
**Problem:** The chunker treats text as a flat stream of sentences and packs them greedily up to 450 words. This means a single chunk can span a paragraph break. TTS engines model prosody (pacing, intonation, pauses) within a chunk as continuous speech. A paragraph break mid-chunk produces unnatural delivery — the model doesn't "know" a new thought started.

**Impact:** Every generated audiobook carries this artifact. Fixing it later means regenerating all audio.

**Recommendation:** Split at paragraph boundaries (`\n\n`) first, then split oversized paragraphs at sentence boundaries. The chunk unit should be one or more complete paragraphs, never a window that crosses `\n\n`.

```
Current:  text → sentences → greedy pack by word count → chunks
Proposed: text → paragraphs → (split oversized paragraphs at sentences) → greedy pack paragraphs by word count → chunks
```

**Test impact:** Add `TestChunkTextParagraphAwareness` — verify chunks never contain `\n\n` internally, verify paragraph boundaries are preserved.

### 2. No audio normalization between chunks

**File:** `tts.py` (`synthesize_chapter`)
**Problem:** Raw TTS output arrays are concatenated directly. Neural TTS does not guarantee consistent amplitude across invocations. The result is audible volume jumps between chunks within the same chapter.

**Impact:** Audiobooks sound amateur. Fixing later requires re-encoding all audio.

**Recommendation:** Peak-normalize each chunk's audio array to a target (e.g., -1 dB) before concatenation. Simple implementation:

```python
def _normalize(audio: np.ndarray, target_peak: float = 0.89) -> np.ndarray:  # -1 dB
    peak = np.abs(audio).max()
    if peak > 0:
        audio = audio * (target_peak / peak)
    return audio
```

Add before the concatenation step. No new dependencies.

**Test impact:** Add `test_chunks_are_normalized` — verify each audio segment passed to concatenation has been scaled.

### 3. Manifest schema is missing fields needed at scale

**File:** `manifest.py` (Phase 3, but schema is defined in PLAN.md)
**Problem:** `manifest.json` has no `voice`, `language`, or `manifest_version` field. When you add multi-voice or multi-language support, every existing manifest breaks or needs migration.

**Impact:** Schema change on live data requires either backwards-compatibility code or full regeneration.

**Recommendation:** Add these fields now (costs nothing):

```json
{
  "manifest_version": 1,
  "voice": "af_heart",
  "language": "en-us",
  ...
}
```

---

## Important — Address before production

### 4. WAV intermediate is a disk space bomb

**File:** `tts.py`, `encoder.py`
**Problem:** A full book at 24kHz mono float32 produces ~2GB of WAV before MP3 encoding. Processing multiple books sequentially (WAV written, then encoded, then deleted) means peak disk usage scales linearly with chapter count. On constrained environments (cloud VMs, CI runners), this causes silent failures.

**Impact:** Pipeline crashes on large books or resource-limited machines.

**Recommendation for POC:** Acceptable as-is, but ensure WAV deletion in `encoder.py` happens immediately per chapter (not batched). The current plan does this — just verify the runner calls encode immediately after synthesis per chapter, not after all chapters.

**Recommendation for scale:** Eliminate WAV intermediate entirely. Generate audio array → encode to MP3 in memory (ffmpeg via subprocess with stdin pipe). Removes the disk bottleneck.

### 5. No sample rate contract enforcement

**File:** `tts.py`, `encoder.py`
**Problem:** Sample rate (24000 Hz) is a parameter in `synthesize_chapter` but is not validated anywhere. If Kokoro returns audio at a different rate, or a future TTS engine defaults differently, the MP3 encoder silently produces distorted audio (pitch-shifted, wrong duration).

**Impact:** Corrupted audio that passes all existing tests (since tests are mocked).

**Recommendation:** Write sample rate into WAV metadata (already happens via `soundfile.write`). In `encoder.py`, read it back and assert it matches expected value before encoding. Add to manifest for downstream validation.

### 6. Skipped TTS chunks = silent sentence deletion

**File:** `tts.py` (`synthesize_chapter`)
**Problem:** The plan says "chunk yields no audio → log warning, skip chunk, continue." A skipped chunk means missing sentences in the final audiobook. The listener has no way to know content was dropped.

**Impact:** Data loss in the output artifact with no audit trail.

**Recommendation:**
- Track skipped chunks: return them alongside the audio result
- Write `skipped_chunks` count into the manifest per chapter
- Flag chapters with any skipped chunks so they can be retried or reviewed
- Consider: if >20% of chunks in a chapter fail, fail the chapter entirely rather than producing a heavily incomplete recording

**Test impact:** Extend `test_failed_chunk_skipped` to verify the skip count is tracked and returned.

---

## Minor — Fix anytime, low risk

### 7. pydub is an unnecessary abstraction layer

**File:** `encoder.py`
**Problem:** pydub shells out to ffmpeg internally. Calling ffmpeg directly via `subprocess.run` gives you identical results, eliminates a dependency, and provides access to ffmpeg's full option set (loudness filters, metadata tagging, concat demuxer).

**Recommendation:** Not blocking for POC. When you need ffmpeg features (and you will — LUFS normalization, ID3 tags), switch to direct subprocess calls.

### 8. Source deduplication by slug is fragile

**File:** `runner.py` (Phase 3)
**Problem:** Dedup relies on `sanitize()` producing identical slugs for the same author across sources. "Fyodor Dostoevsky" vs "Fedor Dostoevsky" vs "Dostoyevsky, Fyodor" produce different slugs. Both Gutenberg and Standard Ebooks will have entries.

**Recommendation:** Acceptable for POC. At scale, consider a canonical author/title mapping table.

### 9. R2 keys have no versioning

**File:** `r2.py` (Phase 3)
**Problem:** Regenerating audio (new voice, fixed chunking, better model) overwrites existing R2 keys. Combined with `Cache-Control: max-age=31536000` on MP3s, CDN edge caches will serve stale content for up to a year after overwrite.

**Recommendation:** Add a generation identifier to the key path:
```
{author-slug}/{title-slug}/v{short-hash}/chapter-01.mp3
```
Old versions can be garbage-collected on a schedule. The manifest references the versioned path.

### 10. No integration smoke test with a real EPUB

**Problem:** All tests are fully mocked. This is correct for CI, but there's no documented way to verify the pipeline end-to-end with a real (small) EPUB. A single short public-domain text (e.g., a Kafka short story) as a fixture would catch integration issues that mocks hide — malformed HTML patterns, encoding edge cases, ebooklib quirks.

**Recommendation:** Add a `tests/fixtures/` directory with one small EPUB. Add an integration test (skipped in CI, run manually) that exercises Steps 1-2 end-to-end. Phase 2 can extend it to Steps 3-4 with a CPU-only TTS run.

---

## Phase 1 Specific Notes

### text_chunker.py
- Test coverage is thorough. The invariant tests (`test_no_chunk_exceeds_max`, `test_all_words_preserved`, `test_order_preserved`) are excellent — keep these.
- The abbreviation set is reasonable. Missing: "Mt." (Mount/Saint), "Ft." (Fort), "Capt.", "Lt.", "Maj.", "Col." — common in 19th-century literature which is your primary corpus. Easy to extend.
- `_split_at_commas` fallback is a good pragmatic choice for the rare oversized sentence.

### epub_parser.py
- `<p>` tag extraction with `soup.get_text()` fallback covers the two main EPUB patterns well.
- `<sup>`/`<sub>` decomposition is correct for TTS — footnote markers read aloud are jarring.
- Consider also stripping `<a>` tags that are internal cross-references (e.g., `[1]` linking to endnotes). Common in Gutenberg EPUBs.
- The titlepage-kept decision is a nice UX touch for audiobook listeners.

## Phase 2 Specific Notes

### tts.py
- Separating `load_pipeline()` from `synthesize_chapter()` is the right call. Pipeline loading is expensive (model weights into VRAM); amortizing it across chapters is essential.
- `get_device()` as a standalone function is good for logging and testability.
- The `_generate_silence` helper is clean. Consider making silence configurable per-book in the future (some books benefit from longer pauses).

### encoder.py
- Thin and correct. Not much to critique here.
- The `delete_wav=True` default is right for production, `False` for debugging. Good API.
- `os.makedirs(exist_ok=True)` for output dir is a nice defensive detail.

---

## Action Items Summary

| # | Issue | Severity | When to fix | Effort |
|---|-------|----------|-------------|--------|
| 1 | Paragraph-aware chunking | Critical | Before generating any keeper audio | Medium |
| 2 | Audio normalization | Critical | Before generating any keeper audio | Small |
| 3 | Manifest schema fields | Critical | Before first manifest is published | Trivial |
| 4 | WAV disk usage | Important | Before processing large books | Medium |
| 5 | Sample rate validation | Important | Phase 2 implementation | Trivial |
| 6 | Track skipped chunks | Important | Phase 2 implementation | Small |
| 7 | pydub → direct ffmpeg | Minor | When you need ffmpeg features | Small |
| 8 | Author slug dedup | Minor | When duplicates appear | Medium |
| 9 | R2 key versioning | Minor | Before public launch | Medium |
| 10 | Integration smoke test | Minor | Anytime | Small |

Items 1-3 should be incorporated into the current phase plans before implementation begins. They are cheap now and expensive later because they affect the format of every generated artifact.
