# Step 2b: Voice Director

**Module:** `src/openshelf/pipeline/voice_director.py`
**Related modules:** `pipeline/llm.py`, `pipeline/tts_engine.py`, `pipeline/engines/*`
**Tests:** `tests/pipeline/test_voice_director.py`, `tests/pipeline/test_audio_director.py`

## Purpose

Prepare each text chunk for directed TTS while preserving the public audio contract. The director:

1. Builds a book-level character registry once.
2. Assigns narrator and character voices from the selected engine's voice pool.
3. Expands the registry per chapter when new quoted speakers appear.
4. Classifies chapter quote spans in compact LLM batches, then projects the assignments back to chunks.
5. Validates the chunk spans as a sync firewall.
6. Applies the selected cast mode. The default `solo` mode renders with the
   narrator voice only; opt-in `multicast` mode may switch voices by character.
7. Adds engine-owned performance direction only when the engine supports it.

The public playback contract remains `.m4a` plus `chapter_data.json` word timestamps. The build also writes two immutable audit/client-feature artifacts beside them: `character_registry.json` and `voice_direction.json`. These files are uploaded to R2 under the build prefix but must not change the reader text or sync source.

## Cast Modes

`CAST_MODE` controls whether character attribution changes synthesis voices:

- `solo` (default): one narrator voice renders the chapter. The LLM still
  selects the narrator, builds the character registry, and may expand that
  registry as new speakers appear, but generated audio does not hand off to
  character voices mid-prose. Each chunk is represented as a narrator
  `DirectedSegment`; `voice_direction.json` records `cast_mode: "solo"` so the
  artifact remains auditable.
- `multicast`: experimental full-cast behavior. The LLM speaker assignments are
  converted into character/narrator segments and resolved to character voices.
  This mode is opt-in because prose such as `"..." she said "..."` can sound
  stitched when rendered as many separately synthesized actor clips.

`--voice` still overrides narrator selection in both modes. `character_registry.json`
is written in both modes.

## Data Types

```python
@dataclass
class BookContext:
    title: str
    author: str
    language: str
    opening_text: str

@dataclass
class CharacterProfile:
    canonical: str
    aliases: list[str]
    description: str
    gender: str = "unknown"
    age: str = "unknown"
    persona_of: str | None = None
    voice: VoiceSpec | None = None

@dataclass
class CharacterRegistry:
    narrator_voice: VoiceSpec
    characters: dict[str, CharacterProfile]

@dataclass
class ChunkWindow:
    prev: str
    text: str
    next: str

@dataclass
class ChapterWindow:
    title: str
    chunks: list[ChunkWindow]

@dataclass
class SpeakerSpan:
    start: int
    end: int
    speaker: str
    delivery_type: str = "narration"
    voice_policy: str = "narrator"
```

`ChunkWindow.prev` and `ChunkWindow.next` are empty strings for missing first/last neighbors. `ChapterWindow` is the preferred production attribution boundary. It provides chapter-scale context and lets the director discover new speaking characters once per chapter. `direct_chunk` remains available as a fallback and for small tests.

## Registry

`AudioDirector.build_registry(ctx)` makes one LLM call per book unless `--voice` provides a narrator override. With an explicit narrator override, code creates a narrator-only `CharacterRegistry` immediately and does not call the registry LLM. The registry prompt, when used, includes:

- title, author, language, opening text
- engine voice pool description
- narrator casting guidance from the engine, including which voices are suited to literary narration, children/young-adult fiction, comedy, solemn narration, or character-only use
- dominant POV guidance: narrator gender, age impression, and temperament should
  be strongly influenced by the main narrative persona in the opening/chapter
  sample. For first-person or close-third prose, prefer a narrator whose voice
  plausibly carries that POV unless genre, frame narration, or explicit
  authorial distance argues otherwise.
- rules for characters with quoted speech, quoted thoughts, self-talk, or lines described as spoken aloud, even if the character appears only once in the sample
- rules that exclude labels, signs, book titles, object text, imagined concepts, silent mentions, and pronouns

The LLM returns a strict registry shape. Each character item includes `canonical`, `aliases`, `description`, `gender`, `age`, `persona_of`, `voice_id`, and `first_evidence_quote`. `first_evidence_quote` is an audit field that explains why the character belongs in the registry and helps catch empty or hallucinated registries. If the opening sample contains actual quoted speech/self-talk, quoted thought, or explicit spoken-aloud narration, an empty character list is invalid and the registry call is retried with an explicit correction prompt before failing. Ordinary narration that merely contains words such as "thought" does not require a character registry entry when there is no quoted/spoken sample text.

The LLM may suggest `narrator_voice_id` and character `voice_id` values, but code remains authoritative. Narrator choice is either an explicit `--voice`/override, which bypasses the registry LLM entirely and starts with an empty character registry, or a valid engine voice ID chosen from the engine's voice-pool description. If the LLM returns an invalid narrator ID, code falls back to the engine's first available voice. For Kokoro, the voice-pool description must include enough qualitative guidance for narrator selection; whimsical children's fiction should prefer warm, expressive, approachable voices and avoid overly grave/deep narrator voices unless the book tone asks for that. F5-TTS bootstraps its initial voice choices from the same Kokoro preset catalog, exposed as `f5tts-{kokoro_preset}` IDs, and its voice-pool description must preserve the Kokoro qualitative casting guidance while making clear that the selected F5 voice clones the matching local reference clip. Chatterbox may expose both curated local reference profiles and Kokoro-derived reference profiles; Kokoro-derived references are the default because their narrator qualities are inherited from the stronger Kokoro preset catalog, while curated human/public-domain references remain selectable only when they win decisively over the best Kokoro-derived candidate across multiple concrete book-fit axes such as dominant POV, narrative distance, prose period or genre, emotional register, and requested voice quality.

`assign_voices()` validates character voice IDs against `engine.available_voices()`, fills gaps deterministically, uses gender/age hints where possible, and wraps the pool if more characters exist than voices.

The narrator is always present. A first-person narrator who is also a named character should appear as a narrator alias rather than a duplicate character unless the text clearly treats the identity separately.

### Chapter Expansion

Before directing a chapter, `AudioDirector.direct_chapter(...)` extracts quote spans from every chunk and sends them to the LLM in compact chapter-ordered batches. Short chapters usually fit in one call; dialogue-heavy chapters are split by quote count so the model does not need to emit one large fragile JSON object. Each batch response may include `new_characters` plus `quote_speakers`. Each quote assignment includes:

- `speaker`: literal speaker or `narrator`
- `delivery_type`: `spoken_dialogue`, `internal_thought`, `self_talk`, `recitation`, `written_text`, `embedded_text`, or `ambiguous`
- `voice_policy`: `narrator`, `narrator_compatible`, `character`, or `distinct_character`

The LLM identifies the dramatic function of the quote but code remains authoritative about final voices. In the default Kokoro style, internal thought, self-talk, written text, ambiguous quotes, and close-POV quote/tag units prefer narrator or narrator-compatible delivery. Distinct character voices are reserved for clearly spoken dialogue by present characters.

New characters use the same strict registry item shape as the book-level registry. They are merged into `CharacterRegistry` only when:

- the canonical name is non-empty and not `narrator`
- the canonical or alias does not already resolve to an existing character
- the profile includes an evidence quote
- the canonical spelling can be grounded in nearby source text, or a tiny misspelling can be repaired to the unique nearby source spelling
- the assigned voice comes from the selected engine's voice pool or can be filled deterministically

The updated `character_registry.json` is written locally and uploaded with the build. It may grow as later chapters introduce new speakers. Clients may later use it for character-list editing or display, but `chapter_data.json` remains the playback text and sync source.

## Speaker Annotation Firewall

The director extracts quote spans from the exact, unmodified chunk text before calling the LLM. In production it sends quote spans in chapter-ordered batches. Each quote item carries a stable chapter-level `quote_id`, `chunk_index`, quote text, and short before/after context. The LLM returns speaker assignments for those quote IDs. Code, not the LLM, tiles narrator spans around the quote spans and produces final offsets into the original chunk text:

```json
{
  "new_characters": [],
  "quote_speakers": [
    {
      "quote_id": 0,
      "speaker": "Alice",
      "delivery_type": "internal_thought",
      "voice_policy": "narrator_compatible"
    }
  ]
}
```

The internal `SpeakerSpan` list still uses original 0-based offsets and must tile exactly before it can become `DirectedSegment`s. Delivery metadata is attached to the validated spans, but it never changes offsets or reader text. This keeps the public synchronization firewall while avoiding fragile full-passage offset generation by the LLM.

Before validating a chapter batch, speaker labels go through a narrow repair path:

1. exact registry canonical/alias match wins
2. exact same-response `new_characters[].canonical` match wins
3. tiny edit-distance repairs are allowed only when one unique source-observed spelling appears in the quote context and the proposed spelling does not
4. ambiguous cases may call a targeted identity adjudicator LLM that can return only `same_character`, `new_character`, or `unresolved`
5. unresolved speakers fail validation and fall back at the smallest available unit

This protects cases like `Dormmouse` -> `Dormouse` without blindly merging names that may refer to different people, such as `James` and `Jamie`.

`validate_spans(spans, text)` enforces:

- first span starts at 0
- last span ends at `len(text)`
- spans are sorted and contiguous
- no gaps, overlaps, negative offsets, or out-of-bounds offsets
- every speaker resolves to the narrator or a registry canonical/alias

Any validation failure, LLM error, malformed JSON, unknown speaker, or exception returns one narrator `DirectedSegment` covering the whole chunk. This is the synchronization firewall: bad LLM output can only degrade to single-voice audio, never corrupt word highlighting.

Unknown speakers must not be silently rewritten to narrator during validation. Speaker resolution may use canonical names or aliases, but an unresolvable non-narrator speaker is a validation failure after the repair/adjudication path. Ambiguous duplicate aliases also fail resolution rather than choosing the first matching character. If two distinct characters share a display name, their registry canonical names must include a disambiguator such as `John (sailor)` and `John (butler)`, while aliases and descriptions carry the source name and context. A bare ambiguous `John` assignment must be adjudicated or rejected.

Quoted non-speech remains narrator/object text. The assignment prompt explicitly tells the LLM to return `narrator` for labels, signs, book titles, jar/bottle/cake text, and other written object text.

Embedded quoted dialogue inside a poem, song, letter, or story being recited by
an in-scene character follows `EMBEDDED_DIALOGUE_MODE`:

- `reciter` (default): assign embedded quoted lines to the in-scene reciter when the text makes one clear. This is the literal-speaker accuracy mode.
- `performed`: allow the LLM to add embedded speakers as `new_characters` for a more theatrical performance.

## Pure Transformations

```python
def needs_annotation(text: str) -> bool
def extract_quote_spans(text: str) -> list[QuoteSpan]
def annotate_chapter_speakers(chapter, registry, llm, cfg) -> ChapterAttribution
def validate_spans(spans: list[SpeakerSpan], text: str) -> None
def validate_span_speakers(spans, registry) -> None
def resolve_voice(speaker: str, registry: CharacterRegistry) -> VoiceSpec
def spans_to_segments(spans, text, registry) -> list[DirectedSegment]
def group_performance_units(spans, text, registry) -> list[DirectedSegment]
def merge_adjacent_segments(segments) -> list[DirectedSegment]
def assign_voices(characters, voice_pool) -> dict[str, VoiceSpec]
```

`needs_annotation` is a cheap gate: chunks without straight or curly double quotes return a narrator segment without an LLM call. Apostrophes alone do not trigger annotation.

`spans_to_segments` must preserve text exactly:

```python
"".join(segment.original_text or segment.text for segment in segments) == chunk_text
```

`group_performance_units` is the production conversion from validated spans to
renderable TTS units. It may merge adjacent spans when that improves performance
continuity, but it must preserve source text exactly:

```python
"".join(segment.original_text or segment.text for segment in segments) == chunk_text
```

Default grouping rules:

- `internal_thought`, `self_talk`, `written_text`, `embedded_text`, and
  `ambiguous` quote spans use narrator voice unless a later style mode opts in
  to stronger characterization.
- A quote plus an immediate tag such as `she said`, `thought Alice`, or `cried
  Alice` may be grouped into one narrator-compatible unit when the quote is
  close POV, internal thought, or self-talk.
- Clear spoken dialogue can retain the character voice, but the following
  narrator tag uses a tight join rather than a full voice-transition pause.
- Written labels/signs/object text stay narrator.
- The main character in close third-person children's fiction should be
  narrator-compatible by default. A distinct main-character voice is used only
  when the nearby quote structure shows an actual dialogue exchange with a
  different character.

## Performance Direction

Kokoro does not use LLM performance/emotion direction in the default audiobook
pipeline. Kokoro has no reliable native emotion-conditioning interface, and
overdirecting it with per-segment cues can make otherwise natural speech worse.
For Kokoro, the LLM is responsible for casting, speaker attribution,
`delivery_type`, `voice_policy`, and `join_policy`; the synthesized text remains
the original EPUB-derived text after deterministic sanitization. Kokoro may still
use engine-level speed defaults and tight joins, but it must not receive
LLM-written inline cues, bracket directions, SSML-like tags, or rewritten
performance text.

These annotations are never reader text. They may modify `DirectedSegment.text` for synthesis only, while `ChunkInfo.text` remains the original chunk text and is the only text serialized to `chapter_data.json`. The LLM may return `pause_after_ms` for audit/backward compatibility, but final seam pause durations are owned by the TTS `PausePolicy`, not by voice direction.

Direction is capability-gated:

- Kokoro: casting and structural voice direction only; skip the LLM
  performance/emotion pass; final sync via WhisperX.
- F5-TTS: `emotion_control=True`; use engine-owned emotion labels, optional
  intensity, pace mapping, and style reference selection.
- Chatterbox: `emotion_control=True`; use engine-owned emotion labels,
  optional intensity, pace metadata, and adapter-owned mapping to
  `exaggeration`/`cfg_weight`.

The default batched performance pass may choose one whole-chunk annotation or
split a single parent segment into smaller performance units when the split
marks a meaningful delivery change. Splits are still conservative: complete
quoted speech, inner thought, and self-talk may stand alone when that sounds
natural, but punctuation-driven fragments and tiny prose tails are merged back
into nearby context or rejected. The validator caps intensity for short units so
brief quoted lines can be expressive without becoming melodramatic. It may
include `intensity` as a normalized `0.0`-to-`1.0` hint. Speed labels map
deterministically to audiobook-safe values:

```python
{"slow": 0.85, "normal": 0.95, "fast": 1.05}
```

Narrator pacing is additionally capped so long narrator passages cannot be sped
up by an overactive performance prompt. Narrator `fast` is allowed only as a
small lift on short transition lines; long narrator segments are capped at the
normal audiobook value. Character dialogue may use the full map for short bursts
of urgency.

LLM pause direction is deliberately limited. Ordinary quote/tag/quote joins,
narrator-compatible internal thought, and tight joins must not receive
LLM-added pause. Other performance pauses are clamped to a small maximum so
they can suggest breath without creating pasted-clip gaps. Paragraph/chunk
spacing remains the responsibility of the TTS timing model, not the LLM.

If an engine uses paralinguistic markers, the engine prompt config owns the marker format and injection rules. Inline marker text is allowed only for engines with tested support that guarantees markers are not spoken. F5-TTS uses metadata labels, not inline markers.

Engine adapters may translate generic direction into engine-specific controls.
F5-TTS maps `emotion` to same-voice style/reference clips where available.
Chatterbox maps `emotion` plus `intensity` to its documented expression
controls (`exaggeration` and `cfg_weight`). These controls are serialized in
`voice_direction.json` under `engine_controls` for audit and must not be
serialized into `chapter_data.json`.

On any direction failure, return a conservative neutral direction for the
affected chunk rather than preserving an unsafe overactive split.

TTS synthesis also has a final steering firewall. For engines without safe
inline-marker support, synthesis strips bracket cues and SSML-like tags before
calling the engine; those cues remain auditable in `voice_direction.json` but
must never be spoken in reader audio. If a directed segment contains only
punctuation, cue text, or other non-spoken separators after sanitization,
synthesis treats it as silence instead of calling the engine. If an engine
rejects steered `synthesis_text`, synthesis retries the same segment with
`original_text`, a normal speed, and no inline direction before the chunk can
be marked skipped. This prevents an overactive performance prompt from
deleting a valid chunk or leaking visible/audio control text.

### Direction Artifact

`voice_direction.json` is written once per build and uploaded beside `chapter_data.json`. It is an audit artifact for the exact cast/performance plan used to synthesize the audio.

For long-running local builds, the pipeline also writes a local-only chapter snapshot immediately after each chapter is directed:

```text
chapter-01.voice_direction.json
chapter-02.voice_direction.json
...
```

Each chapter snapshot uses the same top-level shape as the final aggregate but
contains only the completed chapter. The chapter snapshot is the canonical local
input to synthesis for that chapter: after direction succeeds, audio generation
reconstructs `DirectedSegment`s from `chapter-NN.voice_direction.json` rather
than relying on transient in-memory LLM output. This lets an audio repair rerun
avoid another LLM call as long as the directive artifact is present and valid.

Full DAG runs default to reusing local chapter direction before calling the LLM.
Unless `--new-voice-direction` is passed, `dag run` looks for an earlier local
`chapter-NN.voice_direction.json` for the same author/title, engine, and cast
mode, regardless of voice/rendition/build. The cached chapter must also have
the same chapter number and chunk text. A compatible cached chapter is copied
into the new build prefix, with the top-level `rendition`/`build` and narrator
voice specs remapped to the current build's selected narrator before synthesis.
The copied artifact then becomes the same idempotent synthesis input as a
freshly directed chapter. Passing `--new-voice-direction` disables this cache
lookup and preserves the historical behavior: run the registry and
chapter-direction LLM paths for the current build, then write fresh chapter
snapshots.

The chapter snapshots are not part of the public playback contract. The log
also records a speed summary for each chapter before synthesis begins, including
total segment count, counts by numeric speed, and whether any narrator segment
exceeds `1.0`.

The final aggregate shape is:

```json
{
  "version": 1,
  "rendition": "kokoro-af-heart",
  "build": "2a4f9c1b3d8e7f60",
  "engine": "kokoro",
  "cast_mode": "solo",
  "chapters": [
    {
      "number": 1,
      "title": "Down the Rabbit-Hole",
      "chunks": [
        {
          "index": 0,
          "text": "Alice was beginning to get very tired...",
          "segments": [
            {
              "speaker": "narrator",
              "voice": {"id": "am_michael", "preset_name": "am_michael"},
              "original_text": "Alice was beginning...",
              "synthesis_text": "Alice was beginning...",
              "emotion": "gentle",
              "engine_controls": {
                "intensity": 0.35,
                "exaggeration": 0.55,
                "cfg_weight": 0.5
              },
              "speed": 1.0,
              "pause_after_ms": 0,
              "delivery_type": "narration",
              "voice_policy": "narrator",
              "join_policy": "normal"
            }
          ]
        }
      ]
    }
  ]
}
```

`text` and `original_text` are copied from the unmodified EPUB-derived chunk text. `synthesis_text` is the only field that may include engine-owned delivery hints. `delivery_type`, `voice_policy`, and `join_policy` describe the internal performance decision that produced the segment. `pause_after_ms` is preserved in the artifact for compatibility and audit, but synthesis uses the shared seam pause policy for actual emitted silence. For Kokoro in this phase, `synthesis_text` must match the source segment text because Kokoro skips LLM performance direction. The reader UI must continue to render `chapter_data.json`, not `voice_direction.json`.

## AudioDirector

```python
class AudioDirector:
    def build_registry(self, ctx: BookContext) -> CharacterRegistry: ...
    def direct_chapter(self, title: str, windows: list[ChunkWindow], registry: CharacterRegistry) -> tuple[CharacterRegistry, list[list[DirectedSegment]]]: ...
    def direct_chunk(self, window: ChunkWindow, registry: CharacterRegistry) -> list[DirectedSegment]: ...
    def synthesize_chunk(self, segments: list[DirectedSegment], prior_frames: int) -> tuple[np.ndarray, list[WordTimestamp]]: ...
```

`direct_chapter` is the production path. In `solo` mode, it returns one narrator segment per chunk. In `multicast` mode, it performs chapter-level speaker attribution and registry expansion before producing renderable segments. Engines that advertise `emotion_control` or `performance_direction` then apply performance direction according to `performance_direction_mode`:

- `batched` (default): group consecutive chunks into direction batches capped at about 1,500 words or 10,000 characters. The LLM decides per chunk whether one shared performance setting should apply to the whole chunk or whether that chunk should be split into smaller performance units. `whole` mode applies one emotion/speed/intensity decision to every existing segment in the chunk. `split` mode is accepted only when the chunk currently has one parent segment and the returned unit texts concatenate exactly to that segment text. Code validates exact coverage and text preservation, retries a failed batch as smaller halves, and finally falls back to deterministic neutral direction for that failed chunk.
- `chunk`: preserve the previous one-LLM-call-per-chunk behavior. This is mainly a debugging or narrow repair mode.
- `off`: skip performance LLM calls and apply deterministic neutral/default direction.

The output remains `list[list[DirectedSegment]]`, aligned to the original TTS chunks. Adaptive batching may increase the number of `DirectedSegment`s inside a chunk, but it does not change `chapter-NN.voice_direction.json`, `chapter_data.json`, TTS chunking, R2 layout, or client behavior. Kokoro advertises neither capability in the default path, so it avoids extra LLM performance calls regardless of mode.

`direct_chunk` follows the same cast mode. In `solo`, it returns a single narrator segment. In `multicast`, it runs chunk-level speaker annotation with fallback. It is used by tests, debugging harnesses, and as the fallback if chapter-level attribution fails.

`openshelf-pipeline dag run --performance-direction {batched,chunk,off}` selects the mode, and `books process` forwards the same option to local conversion. The default is `batched`. `dag run --new-voice-direction` forces fresh per-chapter direction instead of reusing compatible local chapter snapshots; `books process --new-voice-direction` forwards the same flag.

## LLM Clients

Production uses the provider selected by `LLM_PROVIDER`, loaded from `pipeline/.env` or the shell:

- `LLM_PROVIDER=anthropic`: uses `AnthropicLLM` with `ANTHROPIC_API_KEY`; model comes from `ANTHROPIC_MODEL`, then `LLM_MODEL`, then the adapter default.
- `LLM_PROVIDER=openai`: uses `OpenAILLM` with `OPENAI_API_KEY`; model comes from `OPENAI_MODEL`, then `LLM_MODEL`, then `gpt-5-nano`.
- `LLM_PROVIDER=ollama`: uses `OllamaLLM` against a local Ollama-compatible HTTP server. The base URL comes from `OLLAMA_BASE_URL` and defaults to `http://127.0.0.1:11434`; the model comes from `OLLAMA_MODEL`, then `LLM_MODEL`, then `llama3.1`. The adapter sends the same JSON schema through Ollama's native chat `format` field and returns parsed JSON through the shared protocol.
- `LLM_PROVIDER=replay`: uses local JSON fixtures and never calls the network.

The OpenAI adapter uses the Responses API with a `json_schema` text format so registry, span, and performance-direction calls still return parsed JSON through the same `LLMClient.complete_json(...)` protocol. The Ollama adapter uses the local server's native JSON-schema chat format and then parses the assistant message content. Provider adapters must not leak provider-specific response objects past `llm.py`.

Offline tests use:

- `StubLLM`: canned responses in sequence
- `ReplayLLM`: JSON fixtures keyed by `sha256(system + user)[:16]`
- `RecordingLLM`: optional wrapper for creating fixtures from real calls

`openshelf-pipeline profile direction` is the local directing harness for
real-book prompt experiments without TTS synthesis. It parses an EPUB, runs the
same registry/chapter directing path as `dag run`, records LLM call
timings, and writes a JSON artifact containing each directed segment's speaker,
voice, original text, synthesis text, `delivery_type`, `voice_policy`, and
`join_policy`.

Tests must never require network, GPU, or ffmpeg.

## Test Strategy

- Pure functions: deterministic unit tests
- Span validation: exact cases plus fixed-seed valid tilings
- Annotation fallback: no quotes, valid spans, malformed spans, LLM error
- AudioDirector: fake engine and StubLLM
- Kokoro: verifies emotion step is skipped
- F5-TTS: verifies capability gates and forced-alignment selection without real synthesis
