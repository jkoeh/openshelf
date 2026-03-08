# Execution Plan Phase 2: Chunks → Audio (Steps 3 & 4)

## Context

Phase 1 delivered `epub_parser.py` (EPUB → chapters) and `text_chunker.py` (text → chunks). This phase implements the audio generation pipeline: TTS synthesis (`tts.py`) and WAV-to-MP3 encoding (`encoder.py`). Together with Phase 1, this completes the local "EPUB → MP3" workflow — no cloud credentials needed.

## Dependencies

- `kokoro` — TTS engine (generates audio arrays from text)
- `soundfile` — write WAV files
- `numpy` — audio array operations (silence insertion, concatenation)
- `torch` — device detection (cuda/mps/cpu)
- `pydub` — WAV → MP3 conversion (requires system `ffmpeg`)

System requirement: `ffmpeg` must be installed (`brew install ffmpeg` on macOS).

## Implementation Order

1. Write `tests/pipeline/test_tts.py` (tests first)
2. Implement `src/openshelf/pipeline/tts.py`
3. Write `tests/pipeline/test_encoder.py` (tests first)
4. Implement `src/openshelf/pipeline/encoder.py`

---

## Step 3: tts.py — Text Chunks → WAV Audio

### Data Structures
```python
@dataclass
class ChapterAudio:
    chapter_number: int
    title: str
    wav_path: str
    duration_seconds: float
    word_count: int
```

### Public Functions

```python
def get_device() -> str:
```
Auto-detect best available device: `cuda` → `mps` → `cpu`.

```python
def load_pipeline(device: str | None = None) -> KPipeline:
```
Load Kokoro TTS pipeline once. Uses `get_device()` if device not specified. Pipeline is configured with `TTS_LANGUAGE` from config.

```python
@dataclass
class SynthesisResult:
    duration_seconds: float
    skipped_chunks: int
```

```python
def synthesize_chapter(
    pipeline: KPipeline,
    chunks: list[str],
    output_path: str,
    voice: str = TTS_VOICE,
    sample_rate: int = TTS_SAMPLE_RATE,
    silence_ms: int = SILENCE_BETWEEN_CHUNKS_MS,
) -> SynthesisResult:
```
Generate WAV audio for a list of text chunks. Returns `SynthesisResult` with duration and skip count.

### Logic for `synthesize_chapter`
1. For each chunk, call `pipeline(chunk, voice=voice)` which yields `(graphemes, phonemes, audio_array)` results
2. Collect the audio arrays (numpy float32) from each chunk
3. Peak-normalize each chunk's audio to -1 dB via `_normalize()` before collecting
4. Track any chunks that fail TTS (increment `skipped_chunks` counter)
5. Between chunks, insert silence: `np.zeros(int(sample_rate * silence_ms / 1000), dtype=np.float32)`
6. Concatenate all arrays with `np.concatenate()`
7. Write to WAV via `soundfile.write(output_path, audio, sample_rate)`
8. Return `SynthesisResult(duration_seconds=len(audio) / sample_rate, skipped_chunks=n)`

### Edge Cases
- Empty chunks list → raise `ValueError`
- Chunk yields no audio (TTS failure) → log warning, increment skip count, continue
- If all chunks fail → raise `RuntimeError`

### Private Helpers
- `_generate_silence(sample_rate: int, duration_ms: int) -> np.ndarray`
- `_normalize(audio: np.ndarray, target_peak: float = 0.89) -> np.ndarray` — peak-normalize to -1 dB

### Test Cases (`tests/pipeline/test_tts.py`)

Mock strategy: mock `kokoro.KPipeline`, `soundfile.write`, and `torch.cuda.is_available` / `torch.backends.mps.is_available`.

Helper: `_fake_audio(n_samples)` returns a numpy array of n samples.

**TestGetDevice:**
- `test_cuda_when_available` — mock cuda available → `"cuda"`
- `test_mps_when_no_cuda` — mock cuda unavailable, mps available → `"mps"`
- `test_cpu_fallback` — both unavailable → `"cpu"`

**TestLoadPipeline:**
- `test_loads_with_language` — verify `KPipeline(lang=TTS_LANGUAGE)` called
- `test_uses_provided_device` — passing `device="cpu"` uses it
- `test_uses_auto_device` — no device arg → calls `get_device()`

**TestSynthesizeChapter:**
- `test_single_chunk` — one chunk → one audio segment, no silence, WAV written
- `test_multiple_chunks_with_silence` — 3 chunks → audio + silence + audio + silence + audio
- `test_silence_duration_correct` — verify silence array length matches `SILENCE_BETWEEN_CHUNKS_MS`
- `test_returns_synthesis_result` — verify returned `SynthesisResult` has correct duration and `skipped_chunks=0`
- `test_output_file_written` — verify `soundfile.write` called with correct path, array, sample_rate
- `test_voice_passed_to_pipeline` — verify pipeline called with `voice=TTS_VOICE`
- `test_custom_voice` — passing different voice works
- `test_chunks_are_normalized` — verify each audio segment is peak-normalized before concatenation

**TestSynthesizeChapterErrors:**
- `test_empty_chunks_raises` — `[]` → `ValueError`
- `test_failed_chunk_skipped` — one chunk raises, others succeed → WAV still written, `skipped_chunks=1`
- `test_all_chunks_fail_raises` — all chunks raise → `RuntimeError`

**TestNormalize:**
- `test_normalize_scales_to_target` — audio with peak 0.5 scaled to 0.89
- `test_normalize_silent_audio` — all zeros → stays all zeros (no division by zero)
- `test_normalize_already_at_target` — peak at 0.89 → unchanged

**TestGenerateSilence:**
- `test_silence_length` — 400ms at 24000 Hz → 9600 samples
- `test_silence_is_zeros` — all values are 0.0
- `test_silence_dtype` — float32

---

## Step 4: encoder.py — WAV → MP3

### Public Function

```python
def encode_to_mp3(
    wav_path: str,
    mp3_path: str,
    bitrate: str = MP3_BITRATE,
    delete_wav: bool = True,
) -> float:
```
Convert WAV to MP3. Returns duration in seconds. Optionally deletes the source WAV.

### Logic
1. Load WAV: `AudioSegment.from_wav(wav_path)`
2. Log warning if WAV sample rate doesn't match `TTS_SAMPLE_RATE` (soft check, not hard error)
3. Export MP3: `.export(mp3_path, format="mp3", bitrate=bitrate)`
4. Get duration: `len(audio_segment) / 1000.0` (pydub uses milliseconds)
5. If `delete_wav` is `True`, remove the WAV file via `os.remove(wav_path)`
6. Return duration in seconds

### Edge Cases
- WAV file doesn't exist → let `FileNotFoundError` propagate naturally
- MP3 output directory doesn't exist → create parent dirs with `os.makedirs(exist_ok=True)`
- Zero-length WAV → pydub handles it, return 0.0
- Unexpected sample rate → log warning, continue (pydub/ffmpeg handles resampling)

### Test Cases (`tests/pipeline/test_encoder.py`)

Mock strategy: mock `pydub.AudioSegment.from_wav` and `os.remove`.

Helper: `_mock_segment(duration_ms)` returns a MagicMock AudioSegment with correct `__len__`.

**TestEncodeToMp3Basic:**
- `test_exports_mp3` — verify `.export()` called with correct path, format, bitrate
- `test_returns_duration` — 90000ms segment → returns `90.0`
- `test_default_bitrate` — verify `MP3_BITRATE` ("128k") used by default
- `test_custom_bitrate` — passing `"192k"` → export called with `"192k"`

**TestEncodeToMp3WavCleanup:**
- `test_deletes_wav_by_default` — `os.remove` called with wav_path
- `test_keeps_wav_when_requested` — `delete_wav=False` → `os.remove` NOT called

**TestEncodeToMp3FileHandling:**
- `test_creates_output_directory` — mp3_path in non-existent dir → `os.makedirs` called
- `test_wav_not_found_raises` — mock `from_wav` raising `FileNotFoundError` → propagates
- `test_zero_duration` — 0ms segment → returns `0.0`

---

## Files to Modify

| File | Action |
|------|--------|
| `tests/pipeline/test_tts.py` | Create (tests first) |
| `src/openshelf/pipeline/tts.py` | Implement |
| `tests/pipeline/test_encoder.py` | Create (tests first) |
| `src/openshelf/pipeline/encoder.py` | Implement |

## Conventions (carried from Phase 1)

- Tests use `unittest.TestCase` + `unittest.mock`, no pytest
- `sys.path.insert(0, ...)` at top of test files for import without pip install
- Constants from `config.py`, never hardcoded
- All tests fully mocked — no real I/O, no GPU, no ffmpeg required
- Follow patterns established in Phase 1 tests

## Verification

```bash
python3 -m unittest tests.pipeline.test_tts -v
python3 -m unittest tests.pipeline.test_encoder -v
python3 -m unittest discover -s tests -v  # all tests still pass
```

## Design Notes

- **Pipeline reuse**: `load_pipeline()` is separate from `synthesize_chapter()` so the caller (runner.py) loads once and passes the pipeline to all chapters. Avoids reloading the model per chapter.
- **Device detection**: Separated into `get_device()` for testability and so runner.py can log which device is being used.
- **WAV as intermediate**: TTS outputs raw audio arrays. We write WAV first (lossless, simple) then encode to MP3. This keeps steps 3 and 4 independent — if encoding fails, the WAV still exists for retry.
- **Silence insertion**: Done at the numpy array level before WAV write, not as a post-processing step. Simpler and avoids re-reading audio files.
- **WAV cleanup**: Encoder deletes WAV after successful MP3 conversion by default to save disk space. Can be disabled for debugging.
- **No chapter-level orchestration here**: Both `tts.py` and `encoder.py` are single-chapter functions. The runner (Phase 3) will loop over chapters and wire them together.
- **Peak normalization**: Applied per-chunk before concatenation. Prevents audible volume jumps between TTS invocations. LUFS-based normalization deferred to when we switch to direct ffmpeg.
- **Skip tracking**: `synthesize_chapter` returns `SynthesisResult` with `skipped_chunks` count. Policy decisions (e.g., fail chapter if >20% skipped) belong in the runner, not here.
- **Sample rate soft check**: Encoder logs a warning on mismatch but doesn't hard-fail. Kokoro's rate is fixed at 24kHz, but defensive logging catches future TTS engine swaps.
