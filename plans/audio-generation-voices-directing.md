# Plan: Multi-Voice Audio Generation with Voice Direction

## Context

OpenShelf currently generates audiobooks with a single Kokoro TTS voice (`af_heart`) for all text — narration and dialogue alike. This plan adds three capabilities:

1. **Multi-character voices** — different Kokoro voices per character, assigned by LLM analysis
2. **Voice directing** — emotion/speed annotations per sentence, active on capable TTS engines
3. **TTS engine abstraction** — adapter pattern so Kokoro, F5-TTS, Chatterbox, or any future engine can be swapped via config with zero client/worker/schema changes

The output contract (`.m4a` + `chapter_data.json` with word timestamps) is unchanged. The client, worker, and R2 layout do not change.

---

## Research basis

- **[MultiActor-Audiobook](https://arxiv.org/abs/2505.13082)** (Interspeech 2025) — validates LLM-as-director approach: LLM identifies characters, generates per-sentence instructions, separate TTS renders. Face→voice step not applicable here.
- **[LLM Quotation Attribution in Literary Texts — LLaMa3](https://arxiv.org/html/2406.11380v1)** (2024) — LLMs beat all encoder-based pipelines (BookNLP etc.) on literary speaker attribution by large margin. BookNLP considered and rejected: accuracy lower, heavy deps, different tokenization from our char-offset contract.
- **[Improving Quotation Attribution with Fictional Character Embeddings](https://arxiv.org/pdf/2406.11368)** (2024) — registry entries should carry descriptive prose per character, not just name+aliases.
- **[TTS-Story](https://github.com/Xerophayze/TTS-Story)** — validates Gemini pre-processing → tagged text → per-voice TTS. Manual/interactive tool; confirms approach works.

---

## Finalized design decisions

| # | Decision |
|---|---|
| 1 | `WhisperXAligner` converts `WordEntry → WordTimestamp` at its return boundary |
| 2 | Registry saved as `character_registry.json` in build dir — auditable, survives interruption |
| 3 | Static registry for v1 — unknown speakers fall back to narrator voice |
| 4 | `--voice` kept as override that skips LLM narrator selection when provided |
| 5 | Rendition slug auto-derived as `{engine}-{narrator_voice_id}` after LLM selection; `--rendition` overrides |
| 6 | `ChunkWindow` uses empty string `""` for missing prev/next (first/last chunks) |
| 7 | WhisperX failure → return `[]` words, log warning, pipeline continues |
| 8 | Speed from LLM as coarse label `"slow"/"normal"/"fast"` → each engine adapter maps to float |
| 9 | Reference audio dir: `pipeline/voices/{engine}/`, constant `VOICES_DIR` in `config.py` |
| 10 | Continuity hint **dropped** — `ChunkWindow(prev, text, next)` only; no annotation chaining |

---

## Architecture overview

```
ONCE PER BOOK
─────────────────────────────────────────────────────────────────────
parse_epub  →  build BookContext(title, author, lang, opening_text)
               │
               ▼
          AudioDirector.build_registry(ctx)
               │  1 LLM call — engine provides voice pool description
               │  assign_voices() — pure, deterministic, no LLM
               ▼
          CharacterRegistry  ←  saved to {build_dir}/character_registry.json
               {narrator_voice: VoiceSpec,
                characters: {canonical: {voice, aliases, description}}}

ONCE PER CHAPTER (existing, unchanged)
─────────────────────────────────────────────────────────────────────
chunk_text(chapter.paragraphs)  →  list[Chunk]

ONCE PER CHUNK  (new work)
─────────────────────────────────────────────────────────────────────
ChunkWindow(prev="", text=chunk.text, next="")
     │
     ▼
AudioDirector.direct_chunk(window, registry)
     │
     ├─ Step 1 (always): annotate_with_fallback
     │    needs_annotation gate (has quotes?)
     │    → annotate_chunk_speakers (LLM: prev+current+next + registry)
     │    → validate_spans (FIREWALL: must tile text exactly)
     │    → spans_to_segments → merge_adjacent_segments
     │    fallback: single narrator segment on any failure
     │
     └─ Step 2 (if engine.capabilities.emotion_control):
          add_emotion_direction
          → one LLM call for all segments in chunk
          → if marker_format: inject [laughs] etc into segment.text
          → if not: set segment.emotion label only
          fallback: original segments unchanged
     │
     ▼
list[DirectedSegment]
     {text, voice: VoiceSpec, speaker, emotion, speed}

AudioDirector.synthesize_chunk(segments, prior_frames)
     │
     ├─ engine.synthesize(segment)  →  TTSResult(audio, sample_rate, words|None)
     │
     ├─ POST: word timestamps
     │    if engine.capabilities.provides_timestamps:  use result.words
     │    else:  aligner.align(audio, text, sr)  →  WhisperX
     │
     ├─ POST: normalize, boundary fades (universal)
     ├─ POST: voice-transition silence (per post_processing_config)
     └─ POST: offset timestamps by cumulative prior frames
     │
     ▼
(chunk_audio: np.ndarray, chunk_words: list[WordTimestamp])
     ← SAME CONTRACT as today's _synthesize_single_chunk

UNCHANGED FROM HERE
─────────────────────────────────────────────────────────────────────
synthesize_chapter outer loop  →  chapter WAV
encode_to_aac  →  chapter-NN.m4a
chapter_data.json  (chunks + word timestamps — schema unchanged)
rendition-manifest.json, manifest.json, R2 upload
```

### Determinism boundary

```
NONDETERMINISTIC              typed contract          DETERMINISTIC
┌─────────────────┐         SpeakerSpan              ┌──────────────────────────┐
│ LLM: registry   │  ──────────────────────────────▶ │ validate_spans           │
│ LLM: annotation │  (char offsets into chunk.text)  │ → spans_to_segments      │
│ LLM: emotion    │                                  │ → resolve_voice          │
└─────────────────┘                                  │ → merge_adjacent         │
                                                     │ → _synthesize_segments   │
                                                     │ → offset timestamps      │
                                                     └──────────────────────────┘
```

`validate_spans` is the sync firewall: if LLM returns spans that don't tile the text exactly, the whole chunk falls back to narrator. Synchronization cannot be corrupted by a bad LLM response — it degrades to single-voice only.

---

## Core data types  (`tts_engine.py`)

```python
@dataclass
class TTSCapabilities:
    emotion_control: bool           # accepts emotion annotation
    paralinguistic_markers: bool    # [laughs],[sighs] injected into text
    speed_control: bool             # speed float is consumed
    provides_timestamps: bool       # TTSResult.words populated natively
    voice_cloning: bool             # needs ref_audio_path vs preset_name

@dataclass
class VoiceSpec:
    id: str                         # canonical: "narrator", "Sherlock Holmes"
    preset_name: str | None         # Kokoro: "bm_george"
    ref_audio_path: str | None      # F5/Chatterbox: "pipeline/voices/f5tts/gruff_male.wav"
    ref_text: str | None            # F5-TTS: transcript of ref clip

@dataclass
class DirectedSegment:
    text: str                       # final text — may contain injected markers
    voice: VoiceSpec
    speaker: str                    # canonical name or "narrator"
    emotion: str | None             # "anxious", "amused" — ignored by Kokoro
    speed: float = 1.0              # derived from "slow"/"normal"/"fast" label

@dataclass
class TTSResult:
    audio: np.ndarray               # float32
    sample_rate: int
    words: list[WordTimestamp] | None   # None = engine didn't provide

@dataclass
class PostProcessingConfig:
    needs_forced_alignment: bool        # True = run WhisperX after synthesis
    voice_transition_silence_ms: int    # silence at voice boundaries
    normalize_cross_voice: bool         # heavier normalize for ref-clip engines

# Prompt configs — engine owns content, director uses structure
@dataclass
class RegistryPromptConfig:
    voice_pool_description: str     # engine describes its available voices
    schema_extra_fields: dict       # engine-specific fields in registry JSON

@dataclass
class AnnotationPromptConfig:
    speaker_rules: str              # engine-specific attribution rules

@dataclass
class EmotionPromptConfig:
    emotion_vocabulary: list[str]   # emotions this engine understands
    marker_format: str | None       # "[{emotion}]" for Chatterbox; None for F5-TTS
    injection_rules: str            # how to inject markers or set labels
    speed_labels: list[str]         # ["slow", "normal", "fast"]
```

---

## TTSEngine Protocol  (`tts_engine.py`)

```python
@runtime_checkable
class TTSEngine(Protocol):
    name: str
    capabilities: TTSCapabilities

    def registry_prompt_config(self) -> RegistryPromptConfig: ...
    def annotation_prompt_config(self) -> AnnotationPromptConfig: ...
    def emotion_prompt_config(self) -> EmotionPromptConfig | None: ...
    # None = engine doesn't support directing (Kokoro returns None)

    def post_processing_config(self) -> PostProcessingConfig: ...
    def available_voices(self) -> list[VoiceSpec]: ...
    def synthesize(self, segment: DirectedSegment) -> TTSResult: ...
```

---

## Adapter implementations

### KokoroAdapter  (`engines/kokoro.py`)

```python
capabilities = TTSCapabilities(
    emotion_control=False,
    paralinguistic_markers=False,
    speed_control=True,
    provides_timestamps=True,   # from Misaki tokens via _extract_words
    voice_cloning=False,
)

def post_processing_config(self):
    return PostProcessingConfig(
        needs_forced_alignment=False,
        voice_transition_silence_ms=20,
        normalize_cross_voice=False,
    )

def emotion_prompt_config(self) -> None:
    return None   # never called — AudioDirector checks capabilities first
```

Voice pool for registry prompt (all valid Kokoro voice IDs with gender/tone labels):
```
af_heart (warm female), af_bella (bright female), af_jessica (clear female),
af_nicole (soft female), af_sarah (neutral female), af_sky (airy female),
am_adam (deep male), am_echo (warm male), am_eric (clear male),
am_michael (calm male), am_liam (young male),
bf_emma (british female), bf_alice (british female), bf_lily (bright british),
bm_george (authoritative british), bm_daniel (warm british), bm_fable (expressive british)
```

### F5TTSAdapter  (`engines/f5tts.py`) — stub

```python
capabilities = TTSCapabilities(
    emotion_control=True,
    paralinguistic_markers=False,   # uses CFG strength, not markers
    speed_control=True,
    provides_timestamps=False,      # needs WhisperX
    voice_cloning=True,
)

def post_processing_config(self):
    return PostProcessingConfig(
        needs_forced_alignment=True,
        voice_transition_silence_ms=50,
        normalize_cross_voice=True,
    )

def emotion_prompt_config(self):
    return EmotionPromptConfig(
        emotion_vocabulary=["neutral", "happy", "sad", "angry",
                            "anxious", "surprised", "amused"],
        marker_format=None,    # emotion is metadata label, NOT injected into text
        injection_rules="Label each speech segment with ONE emotion. "
                        "Narrator is always 'neutral'.",
        speed_labels=["slow", "normal", "fast"],
    )

def synthesize(self, segment):
    raise NotImplementedError("F5-TTS not yet wired")
```

### ChatterboxAdapter  (`engines/chatterbox.py`) — stub

```python
capabilities = TTSCapabilities(
    emotion_control=True,
    paralinguistic_markers=True,   # markers injected INTO segment.text
    speed_control=True,
    provides_timestamps=False,
    voice_cloning=True,
)

def emotion_prompt_config(self):
    return EmotionPromptConfig(
        emotion_vocabulary=["laughs", "sighs", "whispers", "shouts",
                            "nervously", "softly", "crying"],
        marker_format="[{emotion}]",   # injected inline in speech text
        injection_rules="Insert ONE bracketed marker INSIDE quoted speech only. "
                        "Never in narrator text. "
                        "Example: '\"I don't know. [sighs]\"' — marker is inside the quote.",
        speed_labels=["slow", "normal", "fast"],
    )

def synthesize(self, segment):
    raise NotImplementedError("Chatterbox not yet wired")
```

---

## LLM interface  (`llm.py`)

```python
@runtime_checkable
class LLMClient(Protocol):
    name: str
    def complete_json(self, *, system: str, user: str, schema: dict) -> dict:
        """Return parsed JSON. Temperature 0. Raises LLMError on failure."""

class LLMError(Exception): ...

class StubLLM:          # returns canned dicts in sequence; raises StubExhausted when empty
class ReplayLLM:        # reads JSON keyed by sha256(system+user)[:16]; raises FixtureMissing
class RecordingLLM:     # wraps real client, writes fixture on each call
class AnthropicLLM:     # production; reads ANTHROPIC_API_KEY from env; temperature=0 forced
```

Factory:
```python
def create_llm(provider: str | None = None) -> LLMClient:
    match (provider or LLM_PROVIDER):
        case "anthropic": return AnthropicLLM(model="claude-haiku-4-5-20251001")
        case "replay":    return ReplayLLM("pipeline/tests/fixtures/llm/")
```

---

## Prompt templates

### Registry (once per book)

**System:**
```
You are casting voices for an audiobook. Identify the recurring SPEAKING
characters so each can be given a distinct narration voice.

Rules:
- A "character" is an entity that speaks dialogue at least once.
  Entities only mentioned but never speaking are NOT included.
- The NARRATOR is always present. First-person narrator who is also a
  named character: capture their name as a narrator alias.
- Aliases: every surface form used to refer to the same speaker —
  given name, surname, full name, title, epithet, nickname, role
  ("the doctor", "the old woman"). Pronouns are NOT aliases.
- Description: one sentence capturing vocal quality, age, personality
  as the text establishes it. Used to match to a voice.
- Personas: if one physical person has distinct identities the text
  treats separately (secret identity, disguise), record each as its
  own entry with "persona_of" set to the underlying person's canonical name.
- gender: "male"/"female"/"unknown" — only when text makes it clear.
- age: "child"/"adult"/"elderly"/"unknown" — only when clear.
- Output only characters who speak. Short accurate list > long speculative one.

{voice_pool_description}
```

**User:**
```
Title: {title}
Author: {author}
Language: {language}

Opening text:
---
{opening_text}
---
```

**Schema:**
```json
{
  "narrator_voice_id": "string",
  "characters": [
    {
      "canonical": "string",
      "aliases": ["string"],
      "description": "string",
      "gender": "male|female|unknown",
      "age": "child|adult|elderly|unknown",
      "persona_of": "string|null",
      "voice_id": "string"
    }
  ]
}
```

Note: `voice_id` values must come from the engine's voice pool. `assign_voices()` (pure) validates and fills gaps deterministically after the LLM call — the LLM's voice_id suggestions are treated as hints, not ground truth.

---

### Chunk annotation

**System:**
```
You are labeling who speaks each part of an audiobook passage.
Label only the CURRENT passage. PREVIOUS and NEXT are context only.

Output spans that EXACTLY tile the CURRENT passage:
- Character offsets into CURRENT (0-based, end-exclusive).
- Contiguous, no gaps, no overlaps.
- First span starts at 0. Last span ends at len(CURRENT).
- Every character of CURRENT belongs to exactly one span.

Speaker assignment:
- "narrator": descriptive prose, action, dialogue tags ("he said").
- Quoted speech: the character speaking it.
- Use PREVIOUS and NEXT only to resolve ambiguous attribution.
- Use ONLY canonical names from the registry. Resolve aliases:
  "the detective" → canonical name in registry.
- If speaker genuinely unknown from context: use "narrator".
  Never guess. Never invent a name not in the registry.
- Keep spans coarse: merge adjacent same-speaker text into one span.

{speaker_rules}
```

**User:**
```
Registry:
{registry_block}

PREVIOUS (context only):
---
{prev_text}
---

CURRENT (annotate this):
---
{text}
---

NEXT (context only):
---
{next_text}
---
```

**Schema:**
```json
{
  "spans": [
    {"start": 0, "end": 0, "speaker": "string"}
  ]
}
```

---

### Emotion direction (only called if `engine.capabilities.emotion_control`)

**System:**
```
You are directing an audiobook performance. For each speech segment,
assign an emotion and a speaking pace.

{injection_rules}

Pace labels: "slow", "normal", "fast".
Narrator segments: always "neutral", "normal" unless clearly otherwise.
One emotion per segment. Do not split segments.
```

**User:**
```
Available emotions: {emotion_vocabulary}

Segments (JSON array, annotate each by index):
{segments_json}

Context:
---
{window_text}
---
```

**Schema:**
```json
{
  "annotations": [
    {"index": 0, "emotion": "string", "speed": "slow|normal|fast"}
  ]
}
```

---

## WordAligner Protocol  (`tts_engine.py`)

```python
class WordAligner(Protocol):
    def align(self, audio: np.ndarray, text: str, sample_rate: int) -> list[WordTimestamp]: ...

class NullAligner:
    def align(self, audio, text, sr) -> list[WordTimestamp]:
        return []

class WhisperXAligner:
    # wraps word_aligner.py
    # converts WordEntry(word, start, end, chunk_idx) → WordTimestamp(word, start, end)
    # on failure: logs warning, returns []

def create_aligner(engine: TTSEngine) -> WordAligner:
    if engine.post_processing_config().needs_forced_alignment:
        return WhisperXAligner()
    return NullAligner()
```

---

## `_synthesize_segments` (replaces `_synthesize_single_chunk` in `tts.py`)

```python
def _synthesize_segments(
    engine: TTSEngine,
    aligner: WordAligner,
    segments: list[DirectedSegment],
    post_cfg: PostProcessingConfig,
    sample_rate: int,
    prior_audio_frames: int,          # for absolute timestamp offset
) -> tuple[np.ndarray, list[WordTimestamp]]:

    audio_parts: list[np.ndarray] = []
    all_words: list[WordTimestamp] = []
    frames_so_far = 0
    prev_voice_id: str | None = None

    for seg in segments:
        result = engine.synthesize(seg)

        # timestamps: from model or from aligner
        if result.words is not None:
            raw_words = result.words
        else:
            raw_words = aligner.align(result.audio, seg.text, result.sample_rate)

        audio = _normalize(result.audio, cross_voice=post_cfg.normalize_cross_voice)
        audio = _apply_boundary_fades(audio, sample_rate)

        # voice-transition silence
        if prev_voice_id is not None and seg.voice.id != prev_voice_id:
            silence = _generate_silence(sample_rate, post_cfg.voice_transition_silence_ms)
            audio_parts.append(silence)
            frames_so_far += len(silence)

        # absolute offset for this segment
        offset_s = (prior_audio_frames + frames_so_far) / sample_rate
        all_words.extend([
            WordTimestamp(w.word, round(w.start + offset_s, 4), round(w.end + offset_s, 4))
            for w in raw_words
        ])

        audio_parts.append(audio)
        frames_so_far += len(audio)
        prev_voice_id = seg.voice.id

    return np.concatenate(audio_parts), all_words
```

`synthesize_chapter` outer loop is unchanged — it still drives silence gaps between chunks, `chunk_audio_starts`, and `SynthesisResult`. It calls `_synthesize_segments` in place of the old `_synthesize_single_chunk`.

---

## Config changes  (`config.py`)

```python
# LLM
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "anthropic")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# TTS engine
TTS_ENGINE = os.getenv("TTS_ENGINE", "kokoro")
VOICES_DIR = PROJECT_ROOT / "pipeline" / "voices"

# Voice direction
REGISTRY_OPENING_CHARS = 4000    # chars of opening text sent to registry LLM call
```

---

## `convert-book.py` changes

New CLI flags:
- `--engine` (default: `TTS_ENGINE` env var) — selects adapter
- `--voice` kept: when provided, skips LLM narrator selection; passed as `VoiceSpec(preset_name=voice)`

Rendition derivation (replaces hardcoded `--rendition` default):
```python
# After build_registry():
if args.rendition != R2_DEFAULT_RENDITION:
    rendition = args.rendition          # explicit override
else:
    rendition = f"{engine.name}-{registry.narrator_voice.preset_name or 'custom'}"
```

Main loop replacement (lines ~236–275):
```python
engine   = create_engine(args.engine)
llm      = create_llm()
aligner  = create_aligner(engine)
director = AudioDirector(engine, llm, aligner)

book_ctx = BookContext(
    title=book_title, author=book_author,
    language=TTS_LANGUAGE,
    opening_text=_get_opening_text(chapters, REGISTRY_OPENING_CHARS),
)

registry = director.build_registry(book_ctx)   # saves character_registry.json
_write_json(os.path.join(build_dir, "character_registry.json"), registry.to_dict())

# derive rendition after selection
rendition = ...  # as above

# per chapter loop
all_chunk_texts = [c.text for c in chapter_chunks]
for idx, chunk in enumerate(chapter_chunks):
    window = ChunkWindow(
        prev=all_chunk_texts[idx - 1] if idx > 0 else "",
        text=chunk.text,
        next=all_chunk_texts[idx + 1] if idx < len(all_chunk_texts) - 1 else "",
    )
    segments = director.direct_chunk(window, registry)
    # segments passed into synthesize_chapter per-chunk synthesis
```

---

## New file layout

```
pipeline/src/openshelf/pipeline/
  llm.py                          NEW — LLMClient Protocol + StubLLM + ReplayLLM + AnthropicLLM
  tts_engine.py                   NEW — TTSEngine Protocol + all dataclasses + WordAligner Protocol
  voice_director.py               NEW — AudioDirector + all pure preprocessing functions
  engines/
    __init__.py                   NEW — create_engine() + create_aligner() + create_llm()
    kokoro.py                     NEW — KokoroAdapter (fully wired)
    f5tts.py                      NEW — F5TTSAdapter (stub — correct interface, synthesize raises NotImplementedError)
    chatterbox.py                 NEW — ChatterboxAdapter (stub)
  tts.py                          MODIFIED — _synthesize_single_chunk → _synthesize_segments(engine, aligner, ...)
  word_aligner.py                 UNMODIFIED (QA tooling; WhisperXAligner wraps it)

pipeline/tests/pipeline/
  test_llm.py                     NEW
  test_voice_director.py          NEW
  test_tts_engine.py              NEW
  test_engines_kokoro.py          NEW
  test_word_aligner_protocol.py   NEW
  test_audio_director.py          NEW
  test_tts.py                     MUST STAY GREEN (regression guard)

pipeline/tests/fixtures/
  llm/                            NEW directory — ReplayLLM JSON fixtures
  dialogue_golden.json            NEW — multi-genre eval corpus

pipeline/voices/
  kokoro/                         (no files needed — Kokoro uses preset names)
  f5tts/                          (reference WAV files — curated manually when wiring F5-TTS)
  chatterbox/                     (reference WAV files — curated manually when wiring Chatterbox)

pipeline/docs/
  step2b-voice-director.md        NEW — full spec
  step3-tts.md                    MODIFIED — updated interface

pipeline/requirements.txt         MODIFIED — add anthropic
config.py                         MODIFIED — new constants
scripts/convert-book.py           MODIFIED — new CLI flags + AudioDirector wiring

plans/
  audio-generation-voices-directing.md   THIS FILE

CLAUDE.md (root)                  MODIFIED — mermaid flow updated
pipeline/CLAUDE.md                MODIFIED — structure + commands updated
```

---

## Implementation phases

### MANDATORY: follow docs-first order. No phase starts until its gate passes.

---

### Phase 0 — Docs

Update all four docs to describe the TARGET state before any code changes:

1. `CLAUDE.md` (root) — mermaid flow: add `voice_director` step between chunking and TTS; add `character_registry.json` to R2 build layout
2. `pipeline/CLAUDE.md` — add new modules to structure block; add `TTS_ENGINE`, `ANTHROPIC_API_KEY`, `LLM_PROVIDER`, `VOICES_DIR` to env; add `--engine` flag to convert-book.py commands; add `engines/` subpackage
3. `pipeline/docs/step3-tts.md` — update interface section: `ChunkInfo` gains context; `_synthesize_single_chunk` → `_synthesize_segments`; `TTSEngine` Protocol owns synthesis; `PostProcessingConfig` owns timestamp strategy; add `engines/` subsection
4. `pipeline/docs/step2b-voice-director.md` — write full spec (new file): `BookContext`, `CharacterRegistry`, `ChunkWindow`, `SpeakerSpan`, `DirectedSegment`, `AudioDirector`; preprocessing steps + capability gates; all pure function contracts and invariants; test strategy per node

**Gate:** re-read all four docs and confirm they describe the same system.

---

### Phase 1 — Core protocols and dataclasses

**Files:** `llm.py`, `tts_engine.py`

Implement exactly the types and protocols defined in this plan. No logic yet — only dataclasses, Protocols, and the stub/replay LLM implementations.

`StubLLM`: takes `responses: list[dict]`, returns them in sequence, raises `StubExhausted` when empty.
`ReplayLLM`: reads `{fixtures_dir}/{sha256(system+user)[:16]}.json`; raises `FixtureMissing` for unknown prompts (prevents silent real network calls in CI).

**Tests:** `test_llm.py`
- `StubLLM` returns in sequence
- `StubLLM` raises `StubExhausted` when exhausted
- `ReplayLLM` returns recorded dict for known hash
- `ReplayLLM` raises `FixtureMissing` for unknown hash
- `isinstance(StubLLM([]), LLMClient)` — Protocol conformance
- `isinstance(NullAligner(), WordAligner)` — Protocol conformance

**Gate:** `python3 -m unittest pipeline/tests/pipeline/test_llm.py` — offline, all pass.

---

### Phase 2 — Pure voice_director functions

**File:** `voice_director.py` (pure functions only — no `AudioDirector` class yet)

Implement in dependency order:

| Function | Critical test |
|---|---|
| `needs_annotation(text) -> bool` | apostrophe-only → False; `"` quote → True; `"` curly → True |
| `validate_spans(spans, text) -> None` | 6 cases: perfect/gap/overlap/oob/unsorted/empty |
| `resolve_voice(speaker, registry) -> str` | alias→canonical; unknown→narrator; persona_of redirect |
| `spans_to_segments(spans, text, registry) -> list[DirectedSegment]` | concat segment texts == original text |
| `merge_adjacent_segments(segs) -> list[DirectedSegment]` | same-voice adjacent merges; alternating preserved |
| `assign_voices(characters, pool) -> dict[str, VoiceSpec]` | deterministic; gender hint used; pool exhaustion wraps |
| `annotate_with_fallback(window, registry, llm, cfg) -> list[DirectedSegment]` | 4 paths: no-quotes/good/malformed/LLMError |

**Tests:** `test_voice_director.py`
- One test class per function
- `validate_spans` property test: random valid tilings always pass and reconstruct original (fixed seed `random.seed(42)`)
- `annotate_with_fallback`: all 4 paths via `StubLLM` — zero real API calls
- `test_sync_invariant`: for any valid output of `annotate_with_fallback`, `"".join(s.text for s in segments) == chunk_text`

**Gate:** `python3 -m unittest pipeline/tests/pipeline/test_voice_director.py` — offline, all pass.

---

### Phase 3 — KokoroAdapter + `_synthesize_segments`

**Files:** `engines/__init__.py`, `engines/kokoro.py`, `engines/f5tts.py` (stub), `engines/chatterbox.py` (stub), `tts.py` (modified)

`KokoroAdapter.synthesize()`: wraps existing `KPipeline` call. Moves `_extract_words` call inside. Returns `TTSResult(audio=..., sample_rate=TTS_SAMPLE_RATE, words=[...])`.

`tts.py`: replace `_synthesize_single_chunk` with `_synthesize_segments(engine, aligner, segments, post_cfg, sample_rate, prior_audio_frames)`. `synthesize_chapter` outer loop unchanged — call site is the only change.

**Tests:** `test_engines_kokoro.py`

`FakeKPipeline`: for any `(text, voice)` input, returns a `Result` with audio of length `len(text.split()) * 100` samples and tokens with evenly-spaced timestamps.

- `test_concat_length`: total audio == sum of segment audio lengths + transition silences
- `test_word_offsets`: segment N words start at ≥ sum of prior segment durations — **the sync math test**
- `test_single_segment_matches_legacy`: one narrator segment produces same contract as old `_synthesize_single_chunk`
- `test_voice_transition_silence_inserted`: two different-voice segments → silence between them
- `test_same_voice_no_silence`: two same-voice segments → no extra silence

**Regression:** `test_tts.py` must still pass unchanged.

**Gate:** all new tests + existing `test_tts.py` pass.

---

### Phase 4 — AudioDirector + LLM prompt functions

**File:** `voice_director.py` (add `AudioDirector` class + `build_character_registry` + `add_emotion_direction`)

`build_character_registry(ctx, llm, prompt_cfg) -> CharacterRegistry`:
- Construct system + user prompts from templates in this plan
- Call `llm.complete_json(system=..., user=..., schema=REGISTRY_SCHEMA)`
- Call `assign_voices()` on result (pure — validates voice IDs, fills gaps)
- Return `CharacterRegistry`

`add_emotion_direction(segments, window, llm, prompt_cfg) -> list[DirectedSegment]`:
- One LLM call for all segments in the chunk
- If `prompt_cfg.marker_format` set: inject marker into `segment.text`
- If not: set `segment.emotion` label only
- Map speed label → float: `{"slow": 0.85, "normal": 1.0, "fast": 1.15}`
- On any failure: return original segments unchanged

`AudioDirector`:
- `build_registry(ctx)` — calls `build_character_registry`, saves JSON to disk
- `direct_chunk(window, registry)` — runs steps 1+2 with capability gate
- `synthesize_chunk(segments, prior_frames)` — calls `_synthesize_segments`

**Tests:** `test_audio_director.py`
- `StubLLM` + `FakeKokoroEngine` (no GPU)
- `test_kokoro_skips_emotion`: `AudioDirector(KokoroAdapter(), ...)` — `StubLLM` that raises if called twice; assert emotion step never triggers
- `test_registry_saved_to_disk`: `character_registry.json` written to build dir after `build_registry()`
- `test_fallback_on_llm_error`: `StubLLM` raises → single narrator segment, no exception propagated
- `test_full_chunk_pipeline`: window → segments → (audio, words) — timestamps monotonic, bounded by audio duration

**Gate:** all `test_audio_director.py` pass offline via `StubLLM` + `FakeKokoroEngine`.

---

### Phase 5 — WhisperX aligner integration

**File:** `word_aligner_protocol.py`

`WhisperXAligner.align(audio, text, sr) -> list[WordTimestamp]`:
- Calls into existing `word_aligner.py` internals (lazy import — keeps module importable without GPU deps)
- Converts `WordEntry(word, start, end, chunk_idx)` → `WordTimestamp(word, start, end)`
- On any exception: `logger.warning(...)`, return `[]`

Update `engines/__init__.py`: `create_aligner()` returns `WhisperXAligner()` when `post_cfg.needs_forced_alignment`.

**Tests:** `test_word_aligner_protocol.py`
- Mock `whisperx` at import level (follow pattern in existing `test_word_aligner.py`)
- `test_type_conversion`: `WordEntry` list → correct `WordTimestamp` list
- `test_failure_returns_empty`: underlying call raises → `[]` returned, no propagation
- `test_null_aligner_returns_empty`: `NullAligner` always returns `[]`

**Gate:** tests pass offline with mocked WhisperX.

---

### Phase 6 — `convert-book.py` wiring + config

**Files:** `config.py`, `requirements.txt`, `scripts/convert-book.py`

Add to `config.py`: `LLM_PROVIDER`, `ANTHROPIC_API_KEY`, `TTS_ENGINE`, `VOICES_DIR`, `REGISTRY_OPENING_CHARS = 4000`.

Add to `requirements.txt`: `anthropic`

In `convert-book.py`:
- Add `--engine` flag (default: `TTS_ENGINE`)
- Keep `--voice` as override
- Replace lines ~236–275 with `AudioDirector` orchestration per architecture section above
- Auto-derive rendition slug after `build_registry()` unless `--rendition` explicitly set
- `BookContext` built from EPUB metadata + `_get_opening_text(chapters, REGISTRY_OPENING_CHARS)` (new helper: concat first N chars of first chapter spoken text)

Helper `_get_opening_text(chapters, max_chars) -> str`: join paragraphs from chapter 1 up to `max_chars`.

**Gate:** 
1. `python3 pipeline/scripts/convert-book.py <epub> --dry-run` works
2. `python3 -m unittest discover -s pipeline/tests -v` — all existing tests still pass
3. Full run on one short book (`TTS_ENGINE=kokoro`, no `ANTHROPIC_API_KEY` needed with `--voice` override) produces valid `.m4a` + `chapter_data.json`
4. With `ANTHROPIC_API_KEY` set: full run produces `character_registry.json` in build dir and multi-voice audio

---

### Phase 7 — Eval corpus + replay fixtures

**Files:** `pipeline/tests/fixtures/llm/`, `pipeline/tests/fixtures/dialogue_golden.json`, `pipeline/tests/pipeline/test_eval_attribution.py`

`dialogue_golden.json` structure:
```json
[
  {
    "id": "first_person_memoir",
    "description": "Narrator is named character; 'I said' = narrator",
    "text": "...",
    "expected_spans": [{"start": 0, "end": 42, "speaker": "narrator"}, ...]
  },
  ...
]
```

Required genres: first-person memoir, patronymic aliases (Russian-style), unnamed animal speakers (children's book), multi-persona / secret identity, unattributed rapid exchange (6+ lines no attribution tags), pure narration (zero LLM calls expected).

`test_eval_attribution.py`:
- Gated: `@skipUnless(os.getenv("RUN_LLM_EVALS"), "set RUN_LLM_EVALS=1")`
- Runs `annotate_with_fallback` on each golden fixture with real `AnthropicLLM`
- Asserts aggregate accuracy ≥ 0.85 across all fixtures
- Saves responses as replay fixtures in `pipeline/tests/fixtures/llm/`
- Offline CI uses `ReplayLLM` against same fixtures — deterministic

**Gate:** 
- Offline (CI): replay fixtures exist → `ReplayLLM` runs eval deterministically, passes
- Online (manual, `RUN_LLM_EVALS=1`): real API, accuracy threshold met, fixtures regenerated

---

## Test strategy summary

| Layer | Determinism | Runs in CI |
|---|---|---|
| Unit — pure functions (Phases 1–3) | fully deterministic | ✅ always |
| Integration — `AudioDirector` (Phase 4) | `StubLLM` + `FakeKokoroEngine` | ✅ always |
| Aligner (Phase 5) | mocked WhisperX | ✅ always |
| Eval — attribution quality (Phase 7) | `ReplayLLM` in CI / real API manually | ✅ offline replay |
| Regression — existing `test_tts.py` | unchanged, always green | ✅ every phase |

All tests: no real network, no GPU, no ffmpeg required to pass in CI.

---

## What does NOT change

- `chapter_data.json` schema
- Worker routes and response shapes
- Client (`useSyncEngine`, word highlighting, tap-to-seek)
- R2 key layout
- `manifest.json` / `rendition-manifest.json` schemas
- `text_chunker.py`
- `epub_parser.py`, `epub_annotator.py`, `encoder.py`, `r2.py`
