# Step 3: TTS Synthesis

**Module:** `src/openshelf/pipeline/tts.py`
**Related modules:** `pipeline/tts_engine.py`, `pipeline/engines/*`, `pipeline/word_aligner.py`
**Tests:** `tests/pipeline/test_tts.py`, `tests/pipeline/test_engines_kokoro.py`, `tests/pipeline/test_engines_f5tts.py`

## Purpose

Generate section audio from a deterministic narrator heading plus directed body
segments. The public output contract is the same for every engine:

- section WAV, later encoded to `.m4a`
- heading word timestamps
- `chunk_audio_starts`
- `chunk_words: list[list[WordTimestamp]]`
- `section-NN.sync.json` as the durable per-section word timing artifact
- `section-NN.synthesis_units.json` as the private seam/unit audit artifact

The client, worker, `section_data.json`, `manifest.json`, and
`rendition-manifest.json` schemas do not change when the TTS engine changes.
The synthesis-unit artifact is not a reader contract; it exists so repair tools
can modify seam pauses without rerunning the LLM or TTS when exact seam metadata
is available.

## Engine Boundary

TTS engines implement `TTSEngine` from `tts_engine.py`. The selected engine is configured with `TTS_ENGINE` or `openshelf-pipeline ... --engine`.

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

Kokoro is the fully wired default adapter. It uses preset voices and LLM
narrator casting. In default `solo` cast mode, Kokoro renders every chunk with
the narrator voice; `multicast` mode is opt-in and may use speaker attribution,
narrator-compatible voice policy, and tight joins. Kokoro does not use LLM
emotion/performance text steering by default. Kokoro native token timestamps are
not used for final sync. F5-TTS declares emotion control, voice cloning, and
forced-alignment requirements. In this phase F5 voice choices are bootstrapped
from Kokoro's preset catalog: `af_heart` becomes
`f5tts-af_heart`, `bm_george` becomes `f5tts-bm_george`, and so on. The F5
registry prompt preserves Kokoro's qualitative casting guidance so narrator and
character selection behaves the same way, but the selected F5 voice clones a
local reference clip instead of calling Kokoro at synthesis time.

Chatterbox is a zero-shot voice-cloning adapter with expression controls. It
uses local reference clips, maps generic direction to the upstream
`exaggeration` and `cfg_weight` controls, returns `words=None`, and relies on
WhisperX for final sync. Chatterbox does not ship an OpenShelf-ready preset
catalog like Kokoro; OpenShelf exposes local reference profiles instead. The
adapter reads curated profiles from `pipeline/voices/chatterbox/profiles.json`
when their WAV files exist, then supplements any missing coverage with the
Kokoro-derived `pipeline/voices/chatterbox/{preset}.wav` references using
`chatterbox-{preset}` IDs and the same qualitative narrator guidance as Kokoro.
Automatic Chatterbox narrator selection treats Kokoro-derived references as the
default and chooses a curated profile only when it is a decisive book-specific
fit beyond the best Kokoro-derived option.
Passing `--voice` still forces one narrator and bypasses automatic narrator
selection.

F5 reference clips are generated locally from Kokoro with:

```bash
python pipeline/scripts/openshelf-pipeline.py voices bootstrap-f5tts
```

The script writes one WAV per Kokoro preset under `pipeline/voices/f5tts/`.
`bootstrap_kokoro_reference_voices(...)` in `engines/f5tts.py` is the same
behavior as a reusable Python API. Existing clips are skipped unless
`--overwrite` is provided. These reference files are local artifacts and are not
uploaded to R2.

Chatterbox Kokoro-derived reference clips are generated with:

```bash
python pipeline/scripts/openshelf-pipeline.py voices bootstrap-chatterbox
```

The script writes one WAV per Kokoro preset under `pipeline/voices/chatterbox/`.
Curated human/public-domain Chatterbox profiles may be added separately through
`pipeline/voices/chatterbox/profiles.json`; the adapter only includes those
profiles when the referenced local WAV exists.

F5-TTS synthesis uses the official `f5_tts.api.F5TTS` Python API lazily at the
first synthesis call. `F5TTSAdapter.synthesize(...)` passes the selected
`VoiceSpec.ref_audio_path` as `ref_file`, `VoiceSpec.ref_text` as `ref_text`,
the sanitized segment text as `gen_text`, and the directed numeric speed as
`speed`. When `segment.emotion` has a matching style reference for that voice,
the adapter uses the style reference clip and records `style_ref` in
`engine_controls`; otherwise it falls back to the neutral/base reference. It
returns the generated waveform and sample rate as `TTSResult` with `words=None`;
WhisperX remains the final sync source. The adapter must raise a clear error
before model load when the selected voice has no reference audio or the
reference WAV is missing, and it must raise an install-oriented error if the
`f5-tts` package is unavailable. Tests inject a fake F5 runtime object so unit
tests never download models.

F5 style clips are optional local inputs. The adapter recognizes
`pipeline/voices/f5tts/{preset}/{emotion}.wav` and
`pipeline/voices/f5tts/{preset}-{emotion}.wav`; missing style clips fall back
to the base `{preset}.wav`.

Chatterbox synthesis uses `chatterbox.tts.ChatterboxTTS` lazily at the first
synthesis call. The adapter must be constructed with the selected pipeline
device so upstream `ChatterboxTTS.from_pretrained(device=...)` loads classic
Chatterbox on CUDA/MPS/CPU consistently with the CLI `--device` option.
The default CLI device policy is accelerator-first. If Chatterbox auto-resolves
to CPU, the pipeline fails fast before model load; CPU Chatterbox requires an
explicit `--device cpu` override.
`ChatterboxAdapter.synthesize(...)` prepares the selected
`VoiceSpec.ref_audio_path` with upstream `prepare_conditionals(...)` the first
time a reference voice is used, reuses those conditionals for consecutive
segments with the same reference clip, and then calls `generate(...)` with the
sanitized segment text plus adapter-owned expression controls as `exaggeration`
and `cfg_weight`. This keeps classic Chatterbox's CFG/expression path while
avoiding repeated reference-conditioning work in solo-narrator audiobook runs.
Because classic Chatterbox has high per-call overhead but can run up to its
internal token ceiling on long prompts, the adapter opts into packed synthesis
units. The generic TTS layer lets Chatterbox see the full directed segment
instead of first splitting true paragraph breaks into separate synthesis calls;
then Chatterbox's optional `split_synthesis_units(text)` hook packs prose into
sentence-sized generation units bounded by its max character window before
`generate(...)` is called. The packer rebalances short trailing fragments so a
natural phrase such as `two hundred` is not separated into an audible orphan
unit. Internal Chatterbox unit joins use the shared `PausePolicy`, not an
adapter-local constant: sentence, phrase, and paragraph joins keep the normal
audiobook cadence, while unavoidable in-sentence technical splits are labeled
`technical` and receive no inserted silence. The split is internal to TTS and
alignment; reader text and `section_data.json` stay unchanged. Kokoro keeps the generic
paragraph break behavior because its lower per-call overhead and native timing
path make explicit paragraph pauses useful.
Before generation, the adapter disables upstream `tqdm` progress bars inside
Chatterbox model modules so unattended pipeline runs cannot fail because a
caller-owned stderr stream was closed or invalidated while synthesis continues.
It also patches the upstream English T3 Hugging Face backend to avoid requesting
attention tensors and full hidden-state lists during autoregressive inference;
OpenShelf only needs the final hidden state for speech logits, and WhisperX
performs final alignment separately.
Chatterbox output is watermarked by the upstream model when `perth` exposes its
implicit watermarker. On Windows installs where the optional compiled
watermarker is unavailable, the adapter substitutes `perth.DummyWatermarker`
before model construction so local generation can still run. Either way,
WhisperX remains the final sync source before serialization. Chatterbox adapter
logs include per-segment conditioning and generation timings so slow runs can
distinguish reference setup, model decode, vocoding/watermarking, and later
forced alignment.

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
class VoiceSpec:
    id: str
    preset_name: str | None = None
    ref_audio_path: str | None = None
    ref_text: str | None = None
    style_ref_audio_paths: dict[str, str] = field(default_factory=dict)
    style_ref_texts: dict[str, str] = field(default_factory=dict)

@dataclass
class DirectedSegment:
    text: str
    voice: VoiceSpec
    speaker: str
    emotion: str | None = None
    speed: float = 1.0
    pause_after_ms: int = 0
    original_text: str | None = None
    delivery_type: str = "narration"
    voice_policy: str = "narrator"
    join_policy: str = "normal"
    engine_controls: dict[str, Any] = field(default_factory=dict)

@dataclass
class SynthesisResult:
    duration_seconds: float
    skipped_chunks: int
    chunk_audio_starts: list[float]
    chunk_words: list[list[WordTimestamp]]
    synthesis_units: list[ChunkSynthesisAudit]
    heading_words: list[WordTimestamp]
```

`ChunkInfo.directed_segments` is optional. If absent, `synthesize_chapter`
wraps `ChunkInfo.text` in one narrator segment using the same complete
`narrator_voice` supplied for the heading. This fallback is engine-neutral.

Production section audio generation should populate body `ChunkInfo.directed_segments`
by loading `section-NN.voice_direction.json`. The directive artifact, not
transient in-memory LLM output, is the boundary between direction and synthesis.
This preserves repairability: regenerating audio from an existing directive must
not call the LLM again. Adaptive performance direction may split one reader
chunk into multiple directed segments when the LLM decides the chunk needs
different controls internally; those segments still synthesize and align as one
reader chunk.

## Section Sync Artifact

After section audio is synthesized/aligned and encoded, the pipeline writes one
local sync artifact beside the audio:

```text
audio/{rendition}/builds/{build}/section-NN.sync.json
```

The artifact is the canonical section-level timing input for `section_data.json`
assembly and sync repair:

```json
{
  "version": 2,
  "sequence": 1,
  "audio_filename": "section-01.m4a",
  "heading_words": [
    {"word": "Chapter", "start": 0.25, "end": 0.54}
  ],
  "chunk_audio_starts": [0.0, 12.34],
  "coverage": {
    "reader_word_count": 42,
    "aligned_word_count": 40,
    "coverage_ratio": 0.9524,
    "first_missing_word_offset": 40,
    "chunks": [
      {
        "index": 0,
        "reader_word_count": 20,
        "aligned_word_count": 20,
        "coverage_ratio": 1.0,
        "first_missing_word_offset": null
      }
    ]
  },
  "chunks": [
    {
      "index": 0,
      "words": [
        {"word": "Alice", "start": 0.0, "end": 0.42}
      ]
    }
  ]
}
```

`section_data.json` is assembled from `section-NN.chunks.json` plus
`section-NN.sync.json`. Heading words remain a separate region from body chunk
words. In-memory `chunk_words` remains available as a runtime
result for backward-compatible helpers, but production assembly should prefer
the sync artifact when present.

Each public section's `word_count` is the total number of words in
`heading.spoken_text` plus its body chunk text. It does not derive chapter
numbering from playback sequence.

Coverage metrics are diagnostics only. They help identify partial WhisperX
alignment during development and repair, but they do not block upload and do
not create a separate build health state.

## Synthesis Units Artifact

Every successful section synthesis writes a private seam audit beside the audio:

```text
audio/{rendition}/builds/{build}/section-NN.synthesis_units.json
```

The artifact records exact frame ranges for chunk seams, directed-segment
seams, and engine-owned synthesis-unit seams after generated boundary silence
has been trimmed and before the final AAC encode. It is uploaded with the build
for audit and repair tooling, but the worker/client do not read it.

```json
{
  "version": 2,
  "sequence": 9,
  "audio_filename": "section-09.m4a",
  "sample_rate": 24000,
  "policy": "audiobook-v2",
  "chunks": [
    {
      "chunk_index": 0,
      "start_frame": 1200,
      "end_frame": 365120,
      "segments": [
        {
          "segment_index": 0,
          "speaker": "narrator",
          "emotion": "neutral",
          "start_frame": 1200,
          "end_frame": 365120,
          "units": [
            {
              "unit_index": 0,
              "text": "A large rose-tree ... red.",
              "start_frame": 1200,
              "end_frame": 302400
            }
          ]
        }
      ],
      "seams": [
        {
          "kind": "engine_unit",
          "break_type": "sentence",
          "after_chunk_index": 0,
          "after_segment_index": 0,
          "after_unit_index": 0,
          "pause_start_frame": 302400,
          "pause_end_frame": 310800,
          "pause_ms": 350,
          "before_text": "... painting them red.",
          "after_text": "Alice thought this ..."
        }
      ]
    }
  ]
}
```

`PausePolicy` is the single owner of pause durations. Splitters identify text
boundaries; they do not choose final millisecond values. The default
`audiobook-v2` policy is:

| Break type | Pause |
|---|---:|
| Paragraph or true double-newline break | 600 ms |
| Sentence boundary (`.`, `?`, `!`) | 350 ms |
| Phrase boundary (comma, semicolon, colon, dash) | 180 ms |
| Quote/dialogue-tag boundary | 180 ms |
| Inner thought transition | 180 ms |
| Mid-sentence word-level technical split | 120 ms |

The policy is deterministic. It does not use random jitter, because repair,
resume, and artifact comparison need stable outputs.

The default cast mode also supplies a single narrator `DirectedSegment` per
chunk. This is distinct from a fallback: the character registry may still exist
for audit/future style guidance, but the audio does not switch narrator identity
inside the audiobook unless `CAST_MODE=multicast` or `--cast-mode multicast` is
selected.

## Public Functions

```python
def get_device() -> str

def load_pipeline(device: str | None = None) -> KPipeline

def synthesize_chapter(
    pipeline_or_engine: Any,
    chunks: list[ChunkInfo],
    output_path: str,
    narrator_voice: VoiceSpec | None = None,
    sample_rate: int = TTS_SAMPLE_RATE,
    aligner: WordAligner | None = None,
    heading_text: str = "",
) -> SynthesisResult
```

`pipeline_or_engine` accepts either the legacy Kokoro `KPipeline` object or a `TTSEngine`. New code should pass a `TTSEngine`; the legacy path exists as a regression guard while callers are migrated.

`narrator_voice` is the complete registry `VoiceSpec`, not a voice ID. The
shared synthesis layer passes that same object unchanged to the independent
heading segment and to any narrator fallback body segment whose
`ChunkInfo.directed_segments` is absent. This keeps heading construction
engine-independent while preserving Kokoro presets, F5-TTS reference
audio/text, Chatterbox reference audio, and future adapter-owned voice fields.
When omitted, the legacy Kokoro path constructs the configured default preset
once at the boundary.

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
   calls. Single line breaks collapse to synthesis spaces. Double line breaks
   inside body chunks are treated as internal paragraph boundaries and become
   controlled synthesis silence. Section headings are never inserted into
   these body chunks; they are synthesized through the separate heading path.
2. Optionally prepend the previous spoken sentence as synthesis-only rolling
   context when `OPENSHELF_TTS_ROLLING_CONTEXT=1`. The context may help some
   engines pronounce the first words of a chunk/segment naturally, but it must
   never be audible in the final reader audio. It is disabled by default because
   real WhisperX trim boundaries can be missed, causing duplicate synthesis
   attempts without improving the public sync contract.
3. Call `engine.synthesize(segment)` with the sanitized/contextualized text.
   Before this call, engines may optionally split a long synthesis unit into
   smaller engine-owned units with `split_synthesis_units(text)`. Chatterbox
   uses this to avoid long paragraph prompts that approach the upstream
   1000-token decode ceiling. Engine splitters return only unit text; seam
   pauses are assigned by the shared `PausePolicy`. The split units are still
   aligned against the corresponding original reader text slices and do not
   change public data shape.
4. If contextual synthesis succeeds and the aligner declares context-trim
   support, align the generated audio against
   `context + current_segment_text`, trim away the context audio, and keep only
   the current segment. If the trim boundary cannot be found, retry the same
   segment without context.
5. If the sanitized synthesis text has no spoken characters, render silence for its requested pause instead of calling the engine.
6. If steered synthesis fails or returns no audio, retry once with the segment's `original_text`, normal speed, and no inline direction.
7. Ignore engine-native timestamps for final sync when the post-processing
   config requires forced alignment.
8. Normalize audio, apply boundary fades, and clamp engine-generated
   leading/trailing silence before timestamping the unit.
9. For forced-alignment engines, collect each rendered synthesis unit's
   original text and exact start time inside the stitched chunk. After the
   chunk audio is complete, call
   `aligner.align_segments(chunk_audio, unit_texts, unit_starts, sample_rate)`
   when the aligner supports it. This gives WhisperX full acoustic continuity
   plus precise text/time boundaries, which is more reliable for dialogue-heavy
   multi-voice chunks than either one vague whole-chunk block or many isolated
   tiny clips. If segment-aware alignment is unavailable, fall back to the
   legacy single-text alignment path.
10. Insert seam silence from `PausePolicy` at directed-segment and engine-unit
    boundaries. Engine-configured voice-transition silence remains limited to
    actual voice changes unless the next segment's `join_policy` asks for a
    tight join.
11. Record every emitted unit and seam in `section-NN.synthesis_units.json`.
12. Offset word timestamps by the chunk's absolute frame position.

If a forced-alignment unit fails, the aligner returns `[]` for that unit and
the pipeline continues with the rest of the chunk. A bad LLM response cannot
corrupt sync because speaker annotation was validated before segments reached
TTS, and alignment is always performed against original text slices, never
against LLM-rewritten reader text.

## Section heading and timing model

Each section may have a deterministic narrator heading region. Generated
opening/closing credits are heading-only sections. For a normal section:

```text
[lead-in][heading audio][750 ms heading gap][body chunk 0][body seams...]
```

Heading synthesis receives `heading.spoken_text` as one independent engine
call before body synthesis. It is forced-aligned independently and serialized
as `heading.words`. It must never be included in a body `ChunkInfo`, directed
segment, or body chunk text.

## Body timing model

A short lead-in silence (`LEAD_IN_SILENCE_MS`, 50ms) is prepended to every
section. Variable silence is then inserted between chunks by `PausePolicy`, so
implementation chunk boundaries follow the same seam rules as directed segments
and engine-owned synthesis units:

```
[lead_in_silence][chunk0_audio][paragraph_gap][chunk1_audio][mid_para_gap][chunk2_audio]
```

- Lead-in: `LEAD_IN_SILENCE_MS`
- Paragraph/newline seam: 600 ms
- Sentence seam: 350 ms
- Phrase/quote/inner-thought seam: 180 ms
- Word-level technical seam: 120 ms
- Internal paragraph break inside a packed chunk: 600 ms
- `chunk_audio_start = frames_so_far / sample_rate`

Voice-transition silence is internal to a chunk and configured by the engine.
F5-TTS also clamps generated boundary padding to keep stitched quote/tag/quote
phrases such as `"..." she thought "..."` from sounding like separate clips.
Synthesis-only line breaks are normalized before engine calls. Single line
breaks collapse to spaces. Double line breaks inside a packed chunk render as
explicit silence only when they are true internal prose paragraph breaks.
Body chunks cannot contain chapter labels or titles. The prior short-fragment
merge rule applies only to genuine body paragraph fragments and must not merge
heading audio with prose.
Engine-specific pause direction can also insert silence inside a chunk without
changing reader text.

`DirectedSegment.join_policy` controls local segment boundaries:

- `tight`: no extra engine transition silence. Use for quote-to-tag joins and narrator-compatible groupings where a pause sounds like a bad edit.
- `normal`: use the engine's normal voice-transition silence when adjacent voices differ.
- `scene`: reserved for future stronger boundaries; current synthesis treats it like `normal` and relies on paragraph/chunk gaps.

Directed segment speed is already normalized by `voice_director.py` before TTS sees it. TTS engines receive numeric speeds only; they do not reinterpret LLM pace labels. Kokoro skips the LLM speed/emotion pass, so its directed segments keep the default audiobook speed unless a deterministic caller-level setting changes them.

## Word Timestamps

Kokoro may emit token timestamps. `_extract_words` remains available for tests
and debugging, but Kokoro token timestamps are not serialized into
`section_data.json`.

All engines use `WhisperXAligner` for final sync. The aligner wraps `word_aligner.py` and converts `WordEntry(word, start, end, chunk_idx)` to `WordTimestamp(word, start, end)` at its return boundary. `WhisperXAligner` also declares support for context trimming, but rolling synthesis context is used only when `OPENSHELF_TTS_ROLLING_CONTEXT=1` and the aligner supports context trim. WhisperX is lazy-imported and fully mocked in tests.

When `word_aligner.py` splits an alignment chunk into sentence-sized WhisperX
segments, it first loads the audio and resolves every segment window to a
finite `[start, end]` range. The final chunk/unit uses the actual audio duration
as its end boundary. Sentence windows must never be distributed across
`inf`, because later audio-duration clipping would drop every sentence whose
derived start moved past the real file end.

After each chunk is synthesized, `synthesize_chapter` stores the accumulated
absolute chapter-relative word timestamps in `chunk_words[i]`. Directed
forced-alignment chunks receive these words from segment-aware alignment inside
`_synthesize_segments`; legacy undirected chunks may still fall back to a
single chunk-level alignment pass. Failed chunks get
`chunk_audio_starts[i] == -1.0` and `chunk_words[i] == []`.

Before serialization, word timestamps are normalized in reader order so a
forced-aligner oddity cannot make time move backward inside a chunk. If an
aligner returns a zero-duration or out-of-order word, the pipeline keeps the
word text and clamps its start/end just after the previous emitted word.

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
