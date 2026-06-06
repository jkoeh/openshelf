# Step 3: TTS Synthesis

**Module:** `src/openshelf/pipeline/tts.py`
**Related modules:** `pipeline/tts_engine.py`, `pipeline/engines/*`, `pipeline/word_aligner.py`
**Tests:** `tests/pipeline/test_tts.py`, `tests/pipeline/test_engines_kokoro.py`, `tests/pipeline/test_engines_f5tts.py`

## Purpose

Generate chapter audio from directed text segments. The public output contract is unchanged for every engine:

- chapter WAV, later encoded to `.m4a`
- `chunk_audio_starts`
- `chunk_words: list[list[WordTimestamp]]`

The client, worker, R2 key layout, `chapter_data.json`, `manifest.json`, and `rendition-manifest.json` schemas do not change when the TTS engine changes.

## Engine Boundary

TTS engines implement `TTSEngine` from `tts_engine.py`. The selected engine is configured with `TTS_ENGINE` or `convert-book.py --engine`.

```python
@runtime_checkable
class TTSEngine(Protocol):
    name: str
    capabilities: TTSCapabilities

    def registry_prompt_config(self) -> RegistryPromptConfig: ...
    def annotation_prompt_config(self) -> AnnotationPromptConfig: ...
    def emotion_prompt_config(self) -> EmotionPromptConfig | None: ...
    def post_processing_config(self) -> PostProcessingConfig: ...
    def available_voices(self) -> list[VoiceSpec]: ...
    def synthesize(self, segment: DirectedSegment) -> TTSResult: ...
```

Kokoro is the fully wired default adapter. It uses preset voices, LLM casting,
speaker attribution, narrator-compatible voice policy, and tight joins, but it
does not use LLM emotion/performance text steering by default. Kokoro native
token timestamps are not used for final sync. F5-TTS declares emotion control,
voice cloning, and forced-alignment requirements. In this phase F5 voice choices
are bootstrapped from Kokoro's preset catalog: `af_heart` becomes
`f5tts-af_heart`, `bm_george` becomes `f5tts-bm_george`, and so on. The F5
registry prompt preserves Kokoro's qualitative casting guidance so narrator and
character selection behaves the same way, but the selected F5 voice clones a
local reference clip instead of calling Kokoro at synthesis time.

F5 reference clips are generated locally from Kokoro with:

```bash
python pipeline/scripts/bootstrap-f5tts-voices.py
```

The script writes one WAV per Kokoro preset under `pipeline/voices/f5tts/`.
`bootstrap_kokoro_reference_voices(...)` in `engines/f5tts.py` is the same
behavior as a reusable Python API. Existing clips are skipped unless
`--overwrite` is provided. These reference files are local artifacts and are not
uploaded to R2.

F5-TTS synthesis uses the official `f5_tts.api.F5TTS` Python API lazily at the
first synthesis call. `F5TTSAdapter.synthesize(...)` passes the selected
`VoiceSpec.ref_audio_path` as `ref_file`, `VoiceSpec.ref_text` as `ref_text`,
the sanitized segment text as `gen_text`, and the directed numeric speed as
`speed`. It returns the generated waveform and sample rate as `TTSResult` with
`words=None`; WhisperX remains the final sync source. The adapter must raise a
clear error before model load when the selected voice has no reference audio or
the reference WAV is missing, and it must raise an install-oriented error if the
`f5-tts` package is unavailable. Tests inject a fake F5 runtime object so unit
tests never download models.

## Dataclasses

```python
@dataclass
class ChunkInfo:
    text: str
    ends_paragraph: bool = True
    directed_segments: list[DirectedSegment] | None = None

@dataclass
class WordTimestamp:
    word: str
    start: float
    end: float

@dataclass
class TTSResult:
    audio: np.ndarray
    sample_rate: int
    words: list[WordTimestamp] | None

@dataclass
class SynthesisResult:
    duration_seconds: float
    skipped_chunks: int
    chunk_audio_starts: list[float]
    chunk_words: list[list[WordTimestamp]]
```

`ChunkInfo.directed_segments` is optional for backward compatibility. If absent, `synthesize_chapter` wraps `ChunkInfo.text` in a single narrator segment using the legacy Kokoro voice path so existing tests and scripts keep working.

## Public Functions

```python
def get_device() -> str

def load_pipeline(device: str | None = None) -> KPipeline

def synthesize_chapter(
    pipeline_or_engine: Any,
    chunks: list[ChunkInfo],
    output_path: str,
    voice: str = TTS_VOICE,
    sample_rate: int = TTS_SAMPLE_RATE,
    aligner: WordAligner | None = None,
) -> SynthesisResult
```

`pipeline_or_engine` accepts either the legacy Kokoro `KPipeline` object or a `TTSEngine`. New code should pass a `TTSEngine`; the legacy path exists as a regression guard while callers are migrated.

## Segment Synthesis

`_synthesize_segments` replaces `_synthesize_single_chunk` as the core implementation:

```python
def _synthesize_segments(
    engine: TTSEngine,
    aligner: WordAligner,
    segments: list[DirectedSegment],
    post_cfg: PostProcessingConfig,
    sample_rate: int,
    prior_audio_frames: int,
) -> tuple[np.ndarray, list[WordTimestamp]]
```

For each segment:

1. Sanitize synthesis-only text for the selected engine before calling TTS.
   Engines without safe inline-marker support strip bracket cues such as
   `[softly]` and SSML-like tags such as `<break time="350ms"/>`; these remain
   in `voice_direction.json` for audit only and must not be spoken. The
   sanitizer also removes BOM/zero-width no-break characters before engine
   calls, while the original chunk text remains available for alignment and
   reader output.
2. Call `engine.synthesize(segment)` with the sanitized text.
3. If the sanitized synthesis text has no spoken characters, render silence for its requested pause instead of calling the engine.
4. If steered synthesis fails or returns no audio, retry once with the segment's `original_text`, normal speed, and no inline direction.
5. Ignore engine-native timestamps for final sync when the post-processing config requires forced alignment.
6. Call `aligner.align(chunk_audio, original_chunk_text, sample_rate)` after all directed segments for a chunk have been rendered.
7. Normalize audio and apply boundary fades.
8. Clamp engine-generated leading/trailing silence when the selected engine
   declares a maximum boundary pad. This is especially important for F5-TTS,
   whose cloned-voice calls can return audible silence at both sides of every
   segment.
9. Insert engine-configured silence when the voice changes between adjacent segments, unless the next segment's `join_policy` asks for a tight join.
10. Offset word timestamps by the chunk's absolute frame position.

If forced alignment fails, the aligner returns `[]` and the pipeline continues. A bad LLM response cannot corrupt sync because speaker annotation was validated before segments reached TTS, and alignment is always performed against the original chunk text.

## Timing Model

A short lead-in silence (`LEAD_IN_SILENCE_MS`, 50ms) is prepended to every chapter. Variable silence is then inserted between chunks, but the defaults are intentionally short because EPUB chunk boundaries are implementation details rather than audiobook scene breaks:

```
[lead_in_silence][chunk0_audio][paragraph_gap][chunk1_audio][mid_para_gap][chunk2_audio]
```

- Lead-in: `LEAD_IN_SILENCE_MS`
- Paragraph break: `SILENCE_PARAGRAPH_BREAK_MS`
- Mid-paragraph: `SILENCE_MID_PARAGRAPH_MS`
- `chunk_audio_start = frames_so_far / sample_rate`

Voice-transition silence is internal to a chunk and configured by the engine.
F5-TTS also clamps generated boundary padding to keep stitched quote/tag/quote
phrases such as `"..." she thought "..."` from sounding like separate clips.
Engine-specific pause direction can also insert silence inside a chunk without
changing reader text.

`DirectedSegment.join_policy` controls local segment boundaries:

- `tight`: no extra engine transition silence. Use for quote-to-tag joins and narrator-compatible groupings where a pause sounds like a bad edit.
- `normal`: use the engine's normal voice-transition silence when adjacent voices differ.
- `scene`: reserved for future stronger boundaries; current synthesis treats it like `normal` and relies on paragraph/chunk gaps.

Directed segment speed is already normalized by `voice_director.py` before TTS sees it. TTS engines receive numeric speeds only; they do not reinterpret LLM pace labels. Kokoro skips the LLM speed/emotion pass, so its directed segments keep the default audiobook speed unless a deterministic caller-level setting changes them.

## Word Timestamps

Kokoro may emit token timestamps. `_extract_words` remains available for tests and debugging, but Kokoro token timestamps are not serialized into `chapter_data.json`.

All engines use `WhisperXAligner` for final sync. The aligner wraps `word_aligner.py` and converts `WordEntry(word, start, end, chunk_idx)` to `WordTimestamp(word, start, end)` at its return boundary. WhisperX is lazy-imported and fully mocked in tests.

After each chunk is synthesized, `synthesize_chapter` aligns the final rendered chunk audio against the original `ChunkInfo.text`, then stores absolute chapter-relative word timestamps in `chunk_words[i]`. Failed chunks get `chunk_audio_starts[i] == -1.0` and `chunk_words[i] == []`.

## Error Handling

- Empty chunks list: raise `ValueError`
- Single chunk failure: log warning, mark skipped, continue
- All chunks fail: raise `RuntimeError`
- Failed alignment: return audio with empty word list for that chunk
- Failed steered segment: retry original text before skipping the chunk
- Missing optional engine package, model load failure, or missing F5 reference
  audio fails loudly at synthesis time

## Dependencies

- `kokoro` and `torch`: lazy imports for Kokoro
- `f5-tts`: lazy import for F5-TTS runtime
- `numpy`, `soundfile`: audio assembly and WAV writing
- `whisperx`: lazy import for non-native timestamp engines
- Config: `TTS_VOICE`, `TTS_ENGINE`, `TTS_LANGUAGE`, `TTS_SAMPLE_RATE`, silence constants, `CROSSFADE_MS`
