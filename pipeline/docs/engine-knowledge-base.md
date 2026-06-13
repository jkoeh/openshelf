# TTS Engine Knowledge Base

**Related modules:** `pipeline/tts_engine.py`, `pipeline/engines/*`, `pipeline/tts.py`, `pipeline/voice_director.py`

## Purpose

This document is the progressive-disclosure map for every TTS engine adapter in
OpenShelf. Before changing an engine, read only that engine's section here,
then open the linked adapter and the relevant step docs:

- `pipeline/docs/step2b-voice-director.md` for registry, speaker, emotion, and pace direction
- `pipeline/docs/step3-tts.md` for synthesis, stitching, fallback, and WhisperX alignment
- `pipeline/src/openshelf/pipeline/tts_engine.py` for the shared adapter contract
- `pipeline/src/openshelf/pipeline/engines/<engine>.py` for the implementation

All engines must preserve the public contract: chapter `.m4a` files plus
`chapter_data.json` with original reader text and WhisperX word timestamps.
Engine-native timestamps, prompt markers, emotion labels, and adapter-specific
controls are synthesis/audit details only unless a future spec explicitly
changes the client contract.

## Shared Adapter Contract

Every engine implements `TTSEngine`:

```python
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

`synthesize()` receives a `DirectedSegment` whose `text` is synthesis-only. The
reader text remains `ChunkInfo.text` and is serialized by `convert-book.py`.
Engine steering lives on `DirectedSegment.emotion`, `DirectedSegment.speed`,
`DirectedSegment.pause_after_ms`, and `DirectedSegment.engine_controls`.
Adapters return `TTSResult(audio, sample_rate, words)`. Current production
engines set `words=None`; `WhisperXAligner` remains the canonical final sync
source.

## Kokoro

**Implementation:** `pipeline/src/openshelf/pipeline/engines/kokoro.py`

### Runtime API

Kokoro uses `kokoro.KPipeline`, loaded lazily through `tts.load_pipeline()`:

```python
pipeline = KPipeline(lang_code=TTS_LANGUAGE, device=device)
results = list(pipeline(segment.text, voice=voice))
results = list(pipeline(segment.text, voice=voice, speed=segment.speed))
```

Each result contributes audio. Kokoro may expose token timing data, but
OpenShelf intentionally ignores Kokoro-native timestamps for final sync.

### Voice Model

Voices are preset IDs such as `af_heart`, `bf_alice`, and `bm_george`.
`KOKORO_VOICE_POOL_DESCRIPTION` is the narrator/character casting knowledge
shown to the registry LLM. Keep literary guidance there, not in generic prompts.

### Capabilities

```python
TTSCapabilities(
    emotion_control=False,
    paralinguistic_markers=False,
    speed_control=True,
    provides_timestamps=False,
    voice_cloning=False,
    performance_direction=False,
)
```

Kokoro does not run the LLM emotion/performance pass. It uses casting,
speaker attribution, join policy, deterministic sanitization, optional numeric
speed when supplied by code, and WhisperX alignment.

### Post Processing

```python
PostProcessingConfig(
    needs_forced_alignment=True,
    voice_transition_silence_ms=20,
    normalize_cross_voice=False,
)
```

Voice changes inside a chunk get a short transition silence unless the segment
uses `join_policy="tight"`.

## F5-TTS

**Implementation:** `pipeline/src/openshelf/pipeline/engines/f5tts.py`

**Upstream reference:** `https://github.com/SWivid/F5-TTS`

### Runtime API

F5-TTS loads `f5_tts.api.F5TTS` lazily:

```python
runtime = F5TTS(model=self.model, device=self.device)
wav, sample_rate, _ = runtime.infer(
    ref_file=segment.voice.ref_audio_path,
    ref_text=segment.voice.ref_text,
    gen_text=segment.text,
    speed=segment.speed,
    seed=self.seed,
    remove_silence=self.remove_silence,
)
```

The adapter must validate `ref_audio_path` and `ref_text` before loading the
runtime so missing local reference clips fail clearly and cheaply.

### Voice Model

F5 voices are bootstrapped from Kokoro presets. `af_heart` becomes
`f5tts-af_heart`, backed by a local WAV such as
`pipeline/voices/f5tts/af_heart.wav`. Generate those clips with:

```bash
python pipeline/scripts/bootstrap-f5tts-voices.py
```

The reference WAVs are local synthesis inputs, not R2 artifacts.

Optional emotion/style references for the same voice may be placed at either:

```text
pipeline/voices/f5tts/{preset}/{emotion}.wav
pipeline/voices/f5tts/{preset}-{emotion}.wav
```

For example, `pipeline/voices/f5tts/af_heart/anxious.wav` is selected for
`f5tts-af_heart` when the directed segment has `"emotion": "anxious"`.

### Capabilities

```python
TTSCapabilities(
    emotion_control=True,
    paralinguistic_markers=False,
    speed_control=True,
    provides_timestamps=False,
    voice_cloning=True,
    performance_direction=True,
)
```

F5 receives numeric `speed` directly. Emotion labels are mapped to
reference-audio selection because the local F5 API is
reference-text/reference-audio driven, not `emotion=` driven. When
`segment.emotion` is present, the adapter looks for a matching style reference
for the same logical voice and falls back to neutral/base voice references when
the style clip is missing. The selected style name is recorded in
`segment.engine_controls["style_ref"]` before synthesis.

### Post Processing

```python
PostProcessingConfig(
    needs_forced_alignment=True,
    voice_transition_silence_ms=0,
    normalize_cross_voice=True,
    max_generated_boundary_silence_ms=25,
)
```

F5 trims generated boundary padding and uses tighter stitching than Kokoro.

## Chatterbox

**Implementation:** `pipeline/src/openshelf/pipeline/engines/chatterbox.py`

**Upstream references:**

- `https://github.com/resemble-ai/chatterbox`
- `https://www.resemble.ai/chatterbox/`

### Runtime API

The upstream package is `chatterbox-tts`. The basic local API uses:

```python
from chatterbox.tts import ChatterboxTTS

model = ChatterboxTTS.from_pretrained(device=device)
wav = model.generate(
    text=segment.text,
    audio_prompt_path=segment.voice.ref_audio_path,
    exaggeration=exaggeration,
    cfg_weight=cfg_weight,
)
```

Pipeline CLIs that accept `--device` must pass the selected device into
`ChatterboxAdapter` before lazy model load. Passing the device only to WhisperX
or only to Kokoro leaves classic Chatterbox at the upstream default, which is
effectively CPU on Windows.
When no device is supplied, OpenShelf resolves accelerator-first. Classic
Chatterbox must fail fast if that automatic resolution reaches CPU; callers who
really want the slow path must pass `--device cpu` explicitly.

The upstream package initializes `perth.PerthImplicitWatermarker` during model
construction. Some Windows installs expose that symbol as `None` because the
optional compiled watermarker is unavailable. The OpenShelf adapter may patch
that one symbol to `perth.DummyWatermarker` before constructing the model so
Chatterbox remains usable for local generation. Do not treat watermark presence
as part of the reader sync contract; WhisperX alignment remains the canonical
post-processing step.

The original Chatterbox model exposes emotion/expression through inference
controls such as `exaggeration` and `cfg_weight`; higher exaggeration can make
speech more dramatic and may also speed up delivery, so pair it with lower
`cfg_weight` for more deliberate audiobook pacing. Chatterbox Turbo advertises
native paralinguistic tags such as `[laugh]`, `[chuckle]`, and `[cough]`.
OpenShelf's classic Chatterbox adapter keeps CFG/exaggeration enabled. To avoid
turning every segment into a fresh reference-cloning setup, it prepares upstream
conditionals once for the current reference clip with `prepare_conditionals(...)`
and reuses them for consecutive same-voice segments. When a multicast run
switches to a different reference clip, the adapter prepares that clip before
the next `generate(...)` call.
The adapter also owns prompt-length control for classic Chatterbox. Long
paragraph-sized synthesis units are packed into sentence-sized units with short
internal pauses before calling `generate(...)`; this avoids needless runs toward
the upstream `max_new_tokens=1000` ceiling while preserving reader text and
WhisperX as the final sync source.
For English classic Chatterbox runs, the adapter patches the upstream
`T3HuggingfaceBackend.forward(...)` call path to request only the transformer
outputs needed for speech logits. It disables attention tensors and full
hidden-state lists because OpenShelf does not use upstream attentions for sync
or reader data.

### Direction Implications

Do not ask the LLM to write raw Chatterbox API parameters directly. Keep the
generic direction layer literary and stable:

```json
{
  "emotion": "anxious",
  "intensity": 0.7,
  "speed": "fast",
  "pause_after_ms": 80
}
```

Then map that direction inside the Chatterbox adapter:

```json
{
  "exaggeration": 0.75,
  "cfg_weight": 0.35
}
```

The default adapter maps `emotion` and `intensity` to Chatterbox's documented
`exaggeration` and `cfg_weight` controls, then calls:

```python
wav = model.generate(
    text=segment.text,
    exaggeration=exaggeration,
    cfg_weight=cfg_weight,
)
```

If paralinguistic tags are enabled, they must be engine-owned synthesis text
only. They may appear in `voice_direction.json` for audit, but must never be
serialized into `chapter_data.json` or reader text. Keep tags disabled until
tests prove the selected Chatterbox model will not speak them literally.

### Expected Capabilities

```python
TTSCapabilities(
    emotion_control=True,
    paralinguistic_markers=False,  # true only for tested Turbo tag support
    speed_control=False,           # expression controls affect pace indirectly
    provides_timestamps=False,
    voice_cloning=True,
    performance_direction=True,
)
```

Use WhisperX for final sync.

## Adding Or Updating An Engine

1. Update this knowledge base and the relevant step docs first.
2. Add or update the adapter under `pipeline/src/openshelf/pipeline/engines/`.
3. Wire the factory in `engines/__init__.py`.
4. Keep `chapter_data.json` unchanged unless the public contract intentionally changes.
5. Add mocked unit tests for capabilities, runtime-call parameters, failure modes, and forced-alignment selection.
6. If the engine adds prompt markers or expression controls, add tests proving those controls cannot leak into reader text.
