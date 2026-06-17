"""LLM-directed voice casting and speaker annotation."""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

from openshelf.pipeline.llm import LLMClient, LLMError
from openshelf.pipeline.tts_engine import (
    AnnotationPromptConfig,
    EmotionPromptConfig,
    DirectedSegment,
    RegistryPromptConfig,
    TTSEngine,
    VoiceSpec,
    WordAligner,
    WordTimestamp,
)

logger = logging.getLogger(__name__)


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
    first_evidence_quote: str = ""
    voice: VoiceSpec | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["voice"] = asdict(self.voice) if self.voice else None
        return payload


@dataclass
class CharacterRegistry:
    narrator_voice: VoiceSpec
    characters: dict[str, CharacterProfile]

    def to_dict(self) -> dict[str, Any]:
        return {
            "narrator_voice": asdict(self.narrator_voice),
            "characters": {
                name: profile.to_dict()
                for name, profile in self.characters.items()
            },
        }


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


@dataclass
class QuoteSpan:
    quote_id: int
    start: int
    end: int
    text: str


@dataclass
class ChapterQuoteSpan:
    quote_id: int
    chunk_index: int
    start: int
    end: int
    text: str


@dataclass
class ChapterAttribution:
    registry: CharacterRegistry
    chunk_spans: list[list[SpeakerSpan]]


CHARACTER_ITEM_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "canonical": {"type": "string"},
        "aliases": {
            "type": "array",
            "items": {"type": "string"},
        },
        "description": {"type": "string"},
        "gender": {
            "type": "string",
            "enum": ["female", "male", "unknown"],
        },
        "age": {"type": "string"},
        "persona_of": {
            "anyOf": [
                {"type": "string"},
                {"type": "null"},
            ],
        },
        "voice_id": {"type": "string"},
        "first_evidence_quote": {"type": "string"},
    },
    "required": [
        "canonical",
        "aliases",
        "description",
        "gender",
        "age",
        "persona_of",
        "voice_id",
        "first_evidence_quote",
    ],
}


QUOTE_SPEAKER_SCHEMA = {
    "type": "object",
    "x-openai-strict": True,
    "additionalProperties": False,
    "properties": {
        "quote_speakers": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "quote_id": {"type": "integer"},
                    "speaker": {"type": "string"},
                    "delivery_type": {"type": "string"},
                    "voice_policy": {"type": "string"},
                },
                "required": ["quote_id", "speaker", "delivery_type", "voice_policy"],
            },
        },
    },
    "required": ["quote_speakers"],
}


CHAPTER_QUOTE_SPEAKER_SCHEMA = {
    "type": "object",
    "x-openai-strict": True,
    "additionalProperties": False,
    "properties": {
        "new_characters": {
            "type": "array",
            "items": CHARACTER_ITEM_SCHEMA,
        },
        "quote_speakers": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "quote_id": {"type": "integer"},
                    "speaker": {"type": "string"},
                    "delivery_type": {"type": "string"},
                    "voice_policy": {"type": "string"},
                },
                "required": ["quote_id", "speaker", "delivery_type", "voice_policy"],
            },
        },
    },
    "required": ["new_characters", "quote_speakers"],
}

IDENTITY_ADJUDICATION_SCHEMA = {
    "type": "object",
    "x-openai-strict": True,
    "additionalProperties": False,
    "properties": {
        "decision": {
            "type": "string",
            "enum": ["same_character", "new_character", "unresolved"],
        },
        "canonical": {"type": "string"},
    },
    "required": ["decision", "canonical"],
}


SPEAKER_SPAN_SCHEMA = {
    "type": "object",
    "x-openai-strict": True,
    "additionalProperties": False,
    "properties": {
        "spans": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "start": {"type": "integer"},
                    "end": {"type": "integer"},
                    "speaker": {"type": "string"},
                },
                "required": ["start", "end", "speaker"],
            },
        },
    },
    "required": ["spans"],
}

REGISTRY_SCHEMA = {
    "type": "object",
    "x-openai-strict": True,
    "additionalProperties": False,
    "properties": {
        "narrator_voice_id": {"type": "string"},
        "characters": {
            "type": "array",
            "items": CHARACTER_ITEM_SCHEMA,
        },
    },
    "required": ["narrator_voice_id", "characters"],
}

EMOTION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "annotations": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "index": {"type": "integer"},
                    "emotion": {"type": "string"},
                    "speed": {"type": "string"},
                    "intensity": {"type": "number"},
                    "synthesis_text": {"type": "string"},
                    "pause_after_ms": {"type": "integer"},
                },
                "required": ["index", "emotion", "speed"],
            },
        },
    },
    "required": ["annotations"],
}

BATCHED_EMOTION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "chunks": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "chunk_index": {"type": "integer"},
                    "mode": {"type": "string", "enum": ["whole", "split"]},
                    "emotion": {"type": "string"},
                    "speed": {"type": "string"},
                    "intensity": {"type": "number"},
                    "pause_after_ms": {"type": "integer"},
                    "units": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "text": {"type": "string"},
                                "emotion": {"type": "string"},
                                "speed": {"type": "string"},
                                "intensity": {"type": "number"},
                                "pause_after_ms": {"type": "integer"},
                            },
                            "required": ["text", "emotion", "speed"],
                        },
                    },
                },
                "required": ["chunk_index", "mode"],
            },
        },
    },
    "required": ["chunks"],
}

SPEED_MAP = {"slow": 0.85, "normal": 0.95, "fast": 1.05}
NARRATOR_MAX_SPEED = 1.0
NARRATOR_LONG_SEGMENT_WORDS = 24
CHAPTER_ATTRIBUTION_QUOTE_BATCH_SIZE = 20
PERFORMANCE_DIRECTION_MODES = {"batched", "chunk", "off"}
PERFORMANCE_DIRECTION_BATCH_WORDS = 1500
PERFORMANCE_DIRECTION_BATCH_CHARS = 10000
MAX_ADAPTIVE_UNITS_PER_SEGMENT = 8
DELIVERY_TYPES = {
    "spoken_dialogue",
    "internal_thought",
    "self_talk",
    "recitation",
    "written_text",
    "embedded_text",
    "ambiguous",
    "narration",
}
VOICE_POLICIES = {
    "narrator",
    "narrator_compatible",
    "character",
    "distinct_character",
}
NARRATOR_COMPATIBLE_DELIVERY_TYPES = {
    "internal_thought",
    "self_talk",
    "written_text",
    "embedded_text",
    "ambiguous",
}
SPEECH_TAG_RE = re.compile(
    r"^\s*[,;:!?\u2014-]*\s*(?:"
    r"(?:he|she|it|they|we|i|alice|rabbit|queen|king|hatter|gryphon|mock turtle|dormouse|mouse|duchess|cat)\s+"
    r")?(?:"
    r"said|thought|cried|replied|asked|answered|continued|went on|whispered|shouted|muttered|"
    r"remarked|exclaimed|called|sang|sighed|sobbed"
    r")\b",
    re.IGNORECASE,
)


def needs_annotation(text: str) -> bool:
    return '"' in text or "\u201c" in text or "\u201d" in text


def _directed_speed(speed_label: str, segment: DirectedSegment) -> float:
    speed = SPEED_MAP[speed_label]
    if _norm(segment.speaker) != "narrator":
        return speed
    original_text = segment.original_text if segment.original_text is not None else segment.text
    word_count = len(re.findall(r"\b\w+\b", original_text))
    if word_count > NARRATOR_LONG_SEGMENT_WORDS:
        return min(speed, SPEED_MAP["normal"])
    return min(speed, NARRATOR_MAX_SPEED)


def _directed_intensity(value: Any) -> float:
    if value is None:
        return 0.5
    try:
        intensity = float(value)
    except (TypeError, ValueError):
        raise ValueError("intensity must be numeric") from None
    if intensity < 0.0 or intensity > 1.0:
        raise ValueError("intensity out of supported range")
    return intensity


def _directed_pause_after_ms(value: Any, segment: DirectedSegment) -> int:
    try:
        pause_after_ms = int(value or 0)
    except (TypeError, ValueError):
        raise ValueError("pause_after_ms must be an integer")
    if pause_after_ms < 0 or pause_after_ms > 2000:
        raise ValueError("pause_after_ms out of supported range")
    if segment.join_policy == "tight":
        return 0
    if segment.voice_policy == "narrator_compatible":
        return 0
    if segment.delivery_type in {"internal_thought", "self_talk"}:
        return 0
    return min(pause_after_ms, 120)


def _performance_system_prompt(cfg: EmotionPromptConfig) -> str:
    return (
        "You are directing an audiobook performance. For each speech segment, "
        "assign one emotion and one speaking pace. Do not split segments. "
        "Avoid pause_after_ms for ordinary joins; use it only for a brief breath "
        "at real rhetorical breaks.\n"
        f"{cfg.injection_rules}\n"
        f"Pace labels: {', '.join(cfg.speed_labels)}."
    )


def _adaptive_performance_system_prompt(cfg: EmotionPromptConfig) -> str:
    return (
        "You are directing an audiobook performance. For each chunk, decide "
        "whether one shared performance setting is best or whether the chunk "
        "needs smaller performance units with different controls. Use split "
        "mode only for meaningful shifts in delivery, such as emotionally "
        "charged close-POV narration, inner thought, or a sharp tonal change. "
        "Most chunks should use whole mode, often neutral. Do not rewrite text; "
        "split unit text must be copied exactly and concatenate to the parent "
        "segment text.\n"
        f"{cfg.injection_rules}\n"
        f"Pace labels: {', '.join(cfg.speed_labels)}."
    )


def _segment_annotation_prompt(segment: DirectedSegment, index: int) -> dict[str, Any]:
    return {
        "index": index,
        "speaker": segment.speaker,
        "delivery_type": segment.delivery_type,
        "voice_policy": segment.voice_policy,
        "text": segment.text,
    }


def _apply_emotion_annotation(
    segment: DirectedSegment,
    item: dict[str, Any],
    cfg: EmotionPromptConfig,
) -> DirectedSegment:
    emotion = str(item.get("emotion", "neutral"))
    if emotion not in cfg.emotion_vocabulary:
        raise ValueError("emotion is not in engine vocabulary")
    speed_label = str(item.get("speed", "normal"))
    if speed_label not in SPEED_MAP:
        raise ValueError("speed label is not supported")
    intensity = _directed_intensity(item.get("intensity"))
    text = segment.text
    synthesis_text = item.get("synthesis_text")
    if isinstance(synthesis_text, str) and synthesis_text.strip():
        text = synthesis_text
    if cfg.marker_format:
        marker = cfg.marker_format.format(emotion=emotion)
        text = f"{marker} {text}"
    pause_after_ms = _directed_pause_after_ms(
        item.get("pause_after_ms", 0),
        segment,
    )
    return replace(
        segment,
        text=text,
        emotion=emotion,
        speed=_directed_speed(speed_label, segment),
        pause_after_ms=pause_after_ms,
        original_text=segment.original_text or segment.text,
        engine_controls={
            **segment.engine_controls,
            "intensity": intensity,
        },
    )


def neutral_emotion_direction(
    segments: list[DirectedSegment],
    cfg: EmotionPromptConfig,
) -> list[DirectedSegment]:
    emotion = "neutral" if "neutral" in cfg.emotion_vocabulary else cfg.emotion_vocabulary[0]
    return [
        _apply_emotion_annotation(
            segment,
            {
                "emotion": emotion,
                "speed": "normal",
                "intensity": 0.5,
                "pause_after_ms": 0,
            },
            cfg,
        )
        for segment in segments
    ]


def _clean_delivery_type(value: Any, speaker: str) -> str:
    delivery_type = str(value or "").strip()
    if delivery_type in DELIVERY_TYPES:
        return delivery_type
    if _norm(speaker) == "narrator":
        return "written_text"
    return "spoken_dialogue"


def _clean_voice_policy(value: Any, speaker: str, delivery_type: str) -> str:
    voice_policy = str(value or "").strip()
    if voice_policy in VOICE_POLICIES:
        return voice_policy
    if _norm(speaker) == "narrator":
        return "narrator"
    if delivery_type in NARRATOR_COMPATIBLE_DELIVERY_TYPES:
        return "narrator_compatible"
    return "character"


def extract_quote_spans(text: str) -> list[QuoteSpan]:
    spans: list[QuoteSpan] = []
    quote_id = 0
    start: int | None = None
    start_char: str | None = None
    for idx, char in enumerate(text):
        if char == "\u201c":
            if start is None:
                start = idx
                start_char = char
            continue
        if char == "\u201d":
            if start is not None:
                end = idx + 1
                spans.append(QuoteSpan(
                    quote_id=quote_id,
                    start=start,
                    end=end,
                    text=text[start:end],
                ))
                quote_id += 1
                start = None
                start_char = None
            continue
        if char == '"':
            if start is None:
                start = idx
                start_char = char
            elif start_char == '"':
                end = idx + 1
                spans.append(QuoteSpan(
                    quote_id=quote_id,
                    start=start,
                    end=end,
                    text=text[start:end],
                ))
                quote_id += 1
                start = None
                start_char = None
    return spans


def validate_spans(spans: list[SpeakerSpan], text: str) -> None:
    if not spans:
        if text:
            raise ValueError("spans must not be empty")
        return

    expected_start = 0
    text_len = len(text)
    for span in spans:
        if span.start < 0 or span.end < 0:
            raise ValueError("span offsets must be non-negative")
        if span.start != expected_start:
            raise ValueError("spans must be contiguous and sorted")
        if span.end <= span.start:
            raise ValueError("span end must be greater than start")
        if span.end > text_len:
            raise ValueError("span end is out of bounds")
        expected_start = span.end

    if expected_start != text_len:
        raise ValueError("spans must tile the full text")


def validate_span_speakers(spans: list[SpeakerSpan], registry: CharacterRegistry) -> None:
    for span in spans:
        if _norm(span.speaker) == "narrator":
            continue
        if resolve_profile(span.speaker, registry) is None:
            raise ValueError(f"unknown speaker: {span.speaker}")


def _norm(value: str) -> str:
    return " ".join(value.casefold().split())


def _voice_for_profile(profile: CharacterProfile, registry: CharacterRegistry) -> VoiceSpec:
    if profile.persona_of and profile.persona_of in registry.characters:
        parent = registry.characters[profile.persona_of]
        if parent.voice:
            return parent.voice
    return profile.voice or registry.narrator_voice


def resolve_profile(speaker: str, registry: CharacterRegistry) -> CharacterProfile | None:
    if _norm(speaker) == "narrator":
        return None
    speaker_key = _norm(speaker)
    exact_matches: list[CharacterProfile] = []
    matches: list[CharacterProfile] = []
    for canonical, profile in registry.characters.items():
        names = _profile_names(canonical, profile)
        if any(_norm(name) == speaker_key for name in names):
            exact_matches.append(profile)
        if any(_norm(name) == speaker_key for name in _profile_short_names(canonical, profile)):
            matches.append(profile)
    if len(exact_matches) == 1:
        return exact_matches[0]
    if len(exact_matches) > 1:
        return None
    if len(matches) == 1:
        return matches[0]
    return None


def _profile_names(canonical: str, profile: CharacterProfile) -> list[str]:
    names = [canonical, profile.canonical, *profile.aliases]
    expanded = []
    for name in names:
        expanded.append(name)
        normalized = " ".join(str(name).split())
        lowered = normalized.casefold()
        for article in ("the ", "a ", "an "):
            if lowered.startswith(article):
                expanded.append(normalized[len(article):])
    return expanded


def _profile_short_names(canonical: str, profile: CharacterProfile) -> list[str]:
    names = []
    for name in [canonical, profile.canonical, *profile.aliases]:
        parts = str(name).replace(",", " ").split()
        if len(parts) > 1:
            names.append(parts[-1])
    return names


def resolve_speaker_name(speaker: str, registry: CharacterRegistry) -> str:
    if _norm(speaker) == "narrator":
        return "narrator"
    profile = resolve_profile(speaker, registry)
    if profile is None:
        raise ValueError(f"unknown speaker: {speaker}")
    return profile.canonical


def resolve_voice(speaker: str, registry: CharacterRegistry) -> VoiceSpec:
    profile = resolve_profile(speaker, registry)
    if profile is None:
        return registry.narrator_voice
    return _voice_for_profile(profile, registry)


def spans_to_segments(
    spans: list[SpeakerSpan],
    text: str,
    registry: CharacterRegistry,
) -> list[DirectedSegment]:
    segments: list[DirectedSegment] = []
    for span in spans:
        speaker = resolve_speaker_name(span.speaker, registry)
        segments.append(DirectedSegment(
            text=text[span.start:span.end],
            voice=resolve_voice(span.speaker, registry),
            speaker=speaker,
            original_text=text[span.start:span.end],
            delivery_type=span.delivery_type,
            voice_policy=span.voice_policy,
        ))
    return segments


def _span_text(span: SpeakerSpan, text: str) -> str:
    return text[span.start:span.end]


def _span_is_narrator(span: SpeakerSpan) -> bool:
    return _norm(span.speaker) == "narrator"


def _span_prefers_narrator(span: SpeakerSpan) -> bool:
    return (
        span.voice_policy in {"narrator", "narrator_compatible"}
        or span.delivery_type in NARRATOR_COMPATIBLE_DELIVERY_TYPES
    )


def _is_main_pov_profile(profile: CharacterProfile | None) -> bool:
    if profile is None:
        return False
    text = " ".join([
        profile.canonical,
        *profile.aliases,
        profile.description,
        profile.age,
    ]).casefold()
    markers = (
        "main pov",
        "main character",
        "protagonist",
        "central character",
        "title character",
        "heroine",
        "hero",
    )
    return any(marker in text for marker in markers)


def _is_main_pov_span(span: SpeakerSpan, registry: CharacterRegistry) -> bool:
    if _span_is_narrator(span):
        return False
    return _is_main_pov_profile(resolve_profile(span.speaker, registry))


def _is_real_dialogue_exchange(
    spans: list[SpeakerSpan],
    index: int,
    registry: CharacterRegistry,
) -> bool:
    span = spans[index]
    if span.delivery_type != "spoken_dialogue":
        return False
    speaker = resolve_speaker_name(span.speaker, registry)
    for direction in (-1, 1):
        seen_quote = 0
        neighbor_index = index + direction
        while 0 <= neighbor_index < len(spans) and seen_quote < 4:
            neighbor = spans[neighbor_index]
            if not _span_is_narrator(neighbor):
                seen_quote += 1
                if neighbor.delivery_type == "spoken_dialogue":
                    try:
                        neighbor_speaker = resolve_speaker_name(neighbor.speaker, registry)
                    except ValueError:
                        neighbor_speaker = ""
                    if neighbor_speaker and neighbor_speaker != speaker:
                        return True
            neighbor_index += direction
    return False


def _span_effective_voice_policy(
    spans: list[SpeakerSpan],
    index: int,
    registry: CharacterRegistry,
) -> str:
    span = spans[index]
    if (
        _is_main_pov_span(span, registry)
        and not _is_real_dialogue_exchange(spans, index, registry)
    ):
        return "narrator_compatible"
    return span.voice_policy


def _is_dialogue_tag(text: str) -> bool:
    if len(text.strip()) > 120:
        return False
    return SPEECH_TAG_RE.search(text) is not None


def _make_grouped_segment(
    spans: list[SpeakerSpan],
    text: str,
    registry: CharacterRegistry,
    voice: VoiceSpec,
    speaker: str,
    delivery_type: str,
    voice_policy: str,
    join_policy: str = "normal",
) -> DirectedSegment:
    start = spans[0].start
    end = spans[-1].end
    original_text = text[start:end]
    return DirectedSegment(
        text=original_text,
        voice=voice,
        speaker=speaker,
        original_text=original_text,
        delivery_type=delivery_type,
        voice_policy=voice_policy,
        join_policy=join_policy,
    )


def group_performance_units(
    spans: list[SpeakerSpan],
    text: str,
    registry: CharacterRegistry,
) -> list[DirectedSegment]:
    segments: list[DirectedSegment] = []
    i = 0
    while i < len(spans):
        span = spans[i]
        effective_voice_policy = _span_effective_voice_policy(spans, i, registry)
        effective_span = SpeakerSpan(
            span.start,
            span.end,
            span.speaker,
            delivery_type=span.delivery_type,
            voice_policy=effective_voice_policy,
        )
        if not _span_is_narrator(span) and _span_prefers_narrator(effective_span):
            group = [span]
            j = i + 1
            while (
                j + 1 < len(spans)
                and _span_is_narrator(spans[j])
                and _is_dialogue_tag(_span_text(spans[j], text))
                and not _span_is_narrator(spans[j + 1])
                and _norm(spans[j + 1].speaker) == _norm(span.speaker)
                and _span_prefers_narrator(SpeakerSpan(
                    spans[j + 1].start,
                    spans[j + 1].end,
                    spans[j + 1].speaker,
                    delivery_type=spans[j + 1].delivery_type,
                    voice_policy=_span_effective_voice_policy(spans, j + 1, registry),
                ))
            ):
                group.extend([spans[j], spans[j + 1]])
                j += 2
            if (
                j < len(spans)
                and _span_is_narrator(spans[j])
                and _is_dialogue_tag(_span_text(spans[j], text))
            ):
                group.append(spans[j])
                j += 1
            segments.append(_make_grouped_segment(
                group,
                text,
                registry,
                registry.narrator_voice,
                span.speaker,
                span.delivery_type,
                "narrator_compatible",
            ))
            i = j
            continue

        if _span_is_narrator(span):
            join_policy = "normal"
            if (
                i > 0
                and not _span_is_narrator(spans[i - 1])
                and _is_dialogue_tag(_span_text(span, text))
            ):
                join_policy = "tight"
            segments.append(_make_grouped_segment(
                [span],
                text,
                registry,
                registry.narrator_voice,
                "narrator",
                span.delivery_type,
                span.voice_policy,
                join_policy=join_policy,
            ))
        else:
            voice = resolve_voice(span.speaker, registry)
            speaker = resolve_speaker_name(span.speaker, registry)
            segments.append(_make_grouped_segment(
                [span],
                text,
                registry,
                voice,
                speaker,
                span.delivery_type,
                span.voice_policy,
            ))
        i += 1

    if "".join(segment.original_text or segment.text for segment in segments) != text:
        raise ValueError("performance units do not preserve chunk text")
    return segments


def merge_adjacent_segments(segments: list[DirectedSegment]) -> list[DirectedSegment]:
    merged: list[DirectedSegment] = []
    for segment in segments:
        if (
            merged
            and merged[-1].speaker == segment.speaker
            and merged[-1].voice == segment.voice
            and merged[-1].emotion == segment.emotion
            and merged[-1].speed == segment.speed
            and merged[-1].pause_after_ms == 0
            and segment.pause_after_ms == 0
        ):
            merged[-1] = DirectedSegment(
                text=merged[-1].text + segment.text,
                voice=merged[-1].voice,
                speaker=merged[-1].speaker,
                emotion=merged[-1].emotion,
                speed=merged[-1].speed,
                pause_after_ms=0,
                original_text=(
                    (merged[-1].original_text or merged[-1].text)
                    + (segment.original_text or segment.text)
                ),
                delivery_type=merged[-1].delivery_type,
                voice_policy=merged[-1].voice_policy,
                join_policy=merged[-1].join_policy,
            )
        else:
            merged.append(segment)
    return merged


def _voice_gender(voice: VoiceSpec) -> str:
    source = voice.preset_name or voice.id
    if source.startswith(("af_", "bf_")):
        return "female"
    if source.startswith(("am_", "bm_")):
        return "male"
    return "unknown"


def _character_field(character: CharacterProfile | dict, field: str, default: Any = None) -> Any:
    if isinstance(character, CharacterProfile):
        return getattr(character, field)
    return character.get(field, default)


def assign_voices(
    characters: list[CharacterProfile | dict],
    pool: list[VoiceSpec],
) -> dict[str, VoiceSpec]:
    if not pool:
        raise ValueError("voice pool must not be empty")

    by_id = {voice.id: voice for voice in pool}
    by_preset = {
        voice.preset_name: voice for voice in pool
        if voice.preset_name is not None
    }
    assigned: dict[str, VoiceSpec] = {}
    used: set[str] = set()
    next_idx = 0

    for character in characters:
        canonical = _character_field(character, "canonical")
        hint = _character_field(character, "voice_id")
        chosen = by_id.get(hint) or by_preset.get(hint)
        if chosen is None:
            gender = _character_field(character, "gender", "unknown")
            candidates = [
                voice for voice in pool
                if voice.id not in used and _voice_gender(voice) == gender
            ]
            if not candidates:
                candidates = [voice for voice in pool if voice.id not in used]
            if candidates:
                chosen = candidates[0]
            else:
                chosen = pool[next_idx % len(pool)]
                next_idx += 1
        assigned[canonical] = chosen
        used.add(chosen.id)

    return assigned


def _voice_from_id(voice_id: str | None, pool: list[VoiceSpec]) -> VoiceSpec:
    by_id = {voice.id: voice for voice in pool}
    by_preset = {
        voice.preset_name: voice for voice in pool
        if voice.preset_name is not None
    }
    if voice_id and voice_id in by_id:
        return by_id[voice_id]
    if voice_id and voice_id in by_preset:
        return by_preset[voice_id]
    return pool[0]


def _registry_requires_characters(text: str) -> bool:
    lowered = text.casefold()
    spoken_aloud = re.search(
        r"\b(?:said|spoke|spoken|called|cried|shouted|whispered|muttered)\s+aloud\b",
        lowered,
    )
    return needs_annotation(text) or spoken_aloud is not None


def _clean_registry_characters(raw_characters: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_characters, list):
        return []

    cleaned: list[dict[str, Any]] = []
    for item in raw_characters:
        if not isinstance(item, dict):
            continue
        canonical = str(item.get("canonical", "")).strip()
        if not canonical or _norm(canonical) == "narrator":
            continue
        aliases = item.get("aliases", [])
        if not isinstance(aliases, list):
            aliases = []
        clean_aliases = [
            str(alias).strip()
            for alias in aliases
            if str(alias).strip() and _norm(str(alias)) != "narrator"
        ]
        cleaned.append({
            "canonical": canonical,
            "aliases": clean_aliases,
            "description": str(item.get("description", "")).strip(),
            "gender": str(item.get("gender", "unknown")).strip() or "unknown",
            "age": str(item.get("age", "unknown")).strip() or "unknown",
            "persona_of": item.get("persona_of"),
            "voice_id": str(item.get("voice_id", "")).strip(),
            "first_evidence_quote": str(item.get("first_evidence_quote", "")).strip(),
        })
    return cleaned


def _complete_registry_json(
    llm: LLMClient,
    system: str,
    user: str,
) -> dict:
    try:
        return llm.complete_json(system=system, user=user, schema=REGISTRY_SCHEMA)
    except LLMError:
        retry_user = (
            f"{user}\n\n"
            "Retry with compact valid JSON only. Keep descriptions and evidence "
            "short. Include no prose outside the JSON object."
        )
        return llm.complete_json(system=system, user=retry_user, schema=REGISTRY_SCHEMA)


def _complete_chapter_json(
    llm: LLMClient,
    system: str,
    user: str,
) -> dict:
    try:
        return llm.complete_json(
            system=system,
            user=user,
            schema=CHAPTER_QUOTE_SPEAKER_SCHEMA,
        )
    except LLMError:
        retry_user = (
            f"{user}\n\n"
            "Retry with compact valid JSON only. Include every quote_id exactly "
            "once in quote_speakers. Keep new character descriptions short. "
            "Include no prose outside the JSON object."
        )
        return llm.complete_json(
            system=system,
            user=retry_user,
            schema=CHAPTER_QUOTE_SPEAKER_SCHEMA,
        )


def _complete_identity_json(
    llm: LLMClient,
    user: str,
) -> dict:
    system = (
        "You resolve audiobook character identity for one uncertain speaker "
        "label. Return only JSON. Do not invent offsets or rewrite source text. "
        "Choose same_character only when the proposed label and canonical clearly "
        "refer to the same person/creature in the provided context. Choose "
        "new_character only when the label is a distinct real speaker. Choose "
        "unresolved when names may refer to different people or evidence is weak."
    )
    return llm.complete_json(
        system=system,
        user=user,
        schema=IDENTITY_ADJUDICATION_SCHEMA,
    )


_OBSERVED_NAME_RE = re.compile(
    r"\b[A-Z][A-Za-z]+(?:[-'][A-Za-z]+)?"
    r"(?:\s+[A-Z][A-Za-z]+(?:[-'][A-Za-z]+)?)*\b"
)


def _contains_name(text: str, name: str) -> bool:
    if not name:
        return False
    return re.search(rf"(?<!\w){re.escape(name)}(?!\w)", text, re.IGNORECASE) is not None


def _observed_name_forms(text: str) -> list[str]:
    seen: set[str] = set()
    forms: list[str] = []
    for match in _OBSERVED_NAME_RE.finditer(text):
        form = " ".join(match.group(0).split())
        key = _norm(form)
        if key not in seen:
            seen.add(key)
            forms.append(form)
    return forms


def _edit_distance_at_most(a: str, b: str, limit: int) -> int | None:
    a = _norm(a)
    b = _norm(b)
    if abs(len(a) - len(b)) > limit:
        return None
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        cur = [i]
        row_min = i
        for j, cb in enumerate(b, start=1):
            cost = 0 if ca == cb else 1
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost))
            row_min = min(row_min, cur[-1])
        if row_min > limit:
            return None
        prev = cur
    return prev[-1] if prev[-1] <= limit else None


def _source_spelling_repair(name: str, source_text: str) -> str | None:
    """Repair tiny model-only misspellings to a unique nearby source spelling."""
    stripped = name.strip()
    if len(stripped) < 7 or _contains_name(source_text, stripped):
        return None
    candidates: list[tuple[int, str]] = []
    for observed in _observed_name_forms(source_text):
        if _norm(observed) == _norm(stripped):
            return observed
        distance = _edit_distance_at_most(stripped, observed, 1)
        if distance is not None:
            candidates.append((distance, observed))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1]))
    best_distance = candidates[0][0]
    best = [name for distance, name in candidates if distance == best_distance]
    if len(best) == 1:
        return best[0]
    return None


def _ground_character_spelling(
    item: dict[str, Any],
    source_text: str | None,
) -> dict[str, Any] | None:
    if not source_text:
        return item
    canonical = item["canonical"]
    if _contains_name(source_text, canonical):
        return item
    if any(_contains_name(source_text, alias) for alias in item["aliases"]):
        return item
    repaired = _source_spelling_repair(canonical, source_text)
    if repaired is None:
        return None
    grounded = dict(item)
    grounded["canonical"] = repaired
    grounded["aliases"] = [
        alias for alias in item["aliases"]
        if _norm(alias) != _norm(canonical)
    ]
    if not any(_norm(alias) == _norm(repaired) for alias in grounded["aliases"]):
        grounded["aliases"].append(repaired)
    return grounded


def _minimal_character(
    canonical: str,
    quote_text: str,
    voice_pool: list[VoiceSpec],
) -> dict[str, Any]:
    return {
        "canonical": canonical,
        "aliases": [canonical],
        "description": f"Speaking character inferred from nearby source text: {canonical}.",
        "gender": "unknown",
        "age": "unknown",
        "persona_of": None,
        "voice_id": voice_pool[0].id if voice_pool else "",
        "first_evidence_quote": quote_text,
    }


def _quote_context_text(item: dict[str, Any]) -> str:
    return f"{item.get('before', '')} {item.get('text', '')} {item.get('after', '')}"


def _repair_or_adjudicate_speaker(
    speaker: str,
    registry: CharacterRegistry,
    llm: LLMClient,
    source_text: str,
    quote_item: dict[str, Any],
    voice_pool: list[VoiceSpec],
) -> tuple[str, dict[str, Any] | None]:
    if _norm(speaker) == "narrator":
        return "narrator", None
    profile = resolve_profile(speaker, registry)
    if profile is not None:
        return profile.canonical, None

    quote_context = _quote_context_text(quote_item)
    repair_source = f"{quote_context}\n{source_text}"
    repaired = _source_spelling_repair(speaker, repair_source)
    if repaired is not None:
        profile = resolve_profile(repaired, registry)
        if profile is not None:
            return profile.canonical, None
        return repaired, _minimal_character(
            repaired,
            str(quote_item.get("text", "")),
            voice_pool,
        )

    observed = ", ".join(_observed_name_forms(quote_context)[:12])
    registry_names = ", ".join(registry.characters.keys())
    user = (
        f"Proposed speaker: {speaker}\n"
        f"Registry characters: {registry_names or '(none)'}\n"
        f"Observed source names nearby: {observed or '(none)'}\n"
        f"Quote: {quote_item.get('text', '')}\n"
        f"Before: {quote_item.get('before', '')}\n"
        f"After: {quote_item.get('after', '')}\n"
    )
    try:
        decision = _complete_identity_json(llm, user)
    except Exception:
        return speaker, None

    canonical = str(decision.get("canonical", "")).strip()
    if decision.get("decision") == "same_character" and canonical:
        profile = resolve_profile(canonical, registry)
        if profile is not None:
            return profile.canonical, None
        repaired = _source_spelling_repair(canonical, repair_source)
        if repaired and resolve_profile(repaired, registry) is not None:
            return resolve_profile(repaired, registry).canonical, None  # type: ignore[union-attr]
    if decision.get("decision") == "new_character" and canonical:
        grounded = _ground_character_spelling(
            _minimal_character(canonical, str(quote_item.get("text", "")), voice_pool),
            repair_source,
        )
        if grounded is not None:
            return grounded["canonical"], grounded
    return speaker, None


def _profiles_from_raw_characters(
    raw_characters: list[dict[str, Any]],
    voice_pool: list[VoiceSpec],
    narrator_voice: VoiceSpec,
) -> dict[str, CharacterProfile]:
    assigned = assign_voices(raw_characters, voice_pool or [narrator_voice])
    characters: dict[str, CharacterProfile] = {}
    for item in raw_characters:
        canonical = item["canonical"]
        characters[canonical] = CharacterProfile(
            canonical=canonical,
            aliases=item["aliases"],
            description=item["description"],
            gender=item["gender"],
            age=item["age"],
            persona_of=item.get("persona_of"),
            first_evidence_quote=item["first_evidence_quote"],
            voice=assigned.get(canonical, narrator_voice),
        )
    return characters


def merge_new_characters(
    registry: CharacterRegistry,
    raw_characters: list[dict[str, Any]],
    voice_pool: list[VoiceSpec],
    source_text: str | None = None,
) -> CharacterRegistry:
    if not raw_characters:
        return registry

    unique: list[dict[str, Any]] = []
    for item in raw_characters:
        item = _ground_character_spelling(item, source_text)
        if item is None:
            continue
        canonical = item["canonical"]
        if _norm(canonical) == "narrator":
            continue
        if resolve_profile(canonical, registry) is not None:
            continue
        if any(resolve_profile(alias, registry) is not None for alias in item["aliases"]):
            continue
        if not item.get("first_evidence_quote"):
            continue
        unique.append(item)

    if not unique:
        return registry

    additions = _profiles_from_raw_characters(unique, voice_pool, registry.narrator_voice)
    return CharacterRegistry(
        narrator_voice=registry.narrator_voice,
        characters={**registry.characters, **additions},
    )


def build_character_registry(
    ctx: BookContext,
    llm: LLMClient,
    prompt_cfg: RegistryPromptConfig,
    voice_pool: list[VoiceSpec],
    narrator_voice_override: VoiceSpec | None = None,
) -> CharacterRegistry:
    if narrator_voice_override is not None:
        return CharacterRegistry(
            narrator_voice=narrator_voice_override,
            characters={},
        )

    if not voice_pool and narrator_voice_override is None:
        raise ValueError("voice pool must not be empty")

    system = (
        "You are casting voices for an audiobook. Identify characters who should "
        "receive distinct speaking voices.\n"
        "The narrator is always present but must not be included in characters. "
        "Choose narrator_voice_id from the available voice pool to fit the book's "
        "genre, intended audience, protagonist, dominant POV, and prose tone. "
        "Narrator gender, age impression, and temperament should be strongly "
        "influenced by the main narrative persona in the opening sample. For "
        "first-person or close-third prose, prefer a narrator whose voice "
        "plausibly carries that POV unless genre, frame narration, or explicit "
        "authorial distance argues otherwise. Voice source must not imply "
        "priority unless the engine-specific voice pool guidance says "
        "otherwise; choose by fit and voice-quality description, not whether a "
        "voice is curated, human, synthetic, or Kokoro-derived. Do not default to a "
        "deep or authoritative voice for whimsical children's fiction unless "
        "the text itself calls for that style.\n"
        "Include named characters with quoted "
        "speech, quoted thoughts, self-talk, or lines described as said aloud, "
        "even if they appear only once in the sample.\n"
        "Do not include silent mentions, labels, signs, book titles, jar/bottle/"
        "cake text, imagined concepts, or addressees who are not actually present.\n"
        "Aliases should include names, titles, roles, and epithets, but not pronouns.\n"
        "Every character must include first_evidence_quote copied briefly from "
        "the sample to justify inclusion.\n"
        f"{prompt_cfg.voice_pool_description}"
    )
    user = (
        f"Title: {ctx.title}\n"
        f"Author: {ctx.author}\n"
        f"Language: {ctx.language}\n\n"
        f"Opening text:\n---\n{ctx.opening_text}\n---"
    )

    response = _complete_registry_json(llm, system, user)
    raw_characters = _clean_registry_characters(response.get("characters", []))
    raw_characters = [
        grounded for grounded in (
            _ground_character_spelling(item, ctx.opening_text)
            for item in raw_characters
        )
        if grounded is not None
    ]
    if _registry_requires_characters(ctx.opening_text) and not raw_characters:
        correction_user = (
            f"{user}\n\n"
            "The opening sample contains quoted speech, quoted thought, self-talk, "
            "or spoken-aloud text. Return the registry again and include those "
            "actual speaking characters. For Alice-style narration, Alice talking "
            "to herself counts as Alice. Labels such as Drink Me or Eat Me do not "
            "count as speakers."
        )
        response = _complete_registry_json(llm, system, correction_user)
        raw_characters = _clean_registry_characters(response.get("characters", []))
        raw_characters = [
            grounded for grounded in (
                _ground_character_spelling(item, ctx.opening_text)
                for item in raw_characters
            )
            if grounded is not None
        ]
    if _registry_requires_characters(ctx.opening_text) and not raw_characters:
        raise ValueError("character registry is empty despite quoted/spoken sample text")

    narrator_voice = narrator_voice_override or _voice_from_id(
        response.get("narrator_voice_id"),
        voice_pool,
    )

    characters = _profiles_from_raw_characters(
        raw_characters,
        voice_pool,
        narrator_voice,
    )

    return CharacterRegistry(narrator_voice=narrator_voice, characters=characters)


def narrator_segment(text: str, registry: CharacterRegistry) -> list[DirectedSegment]:
    return [DirectedSegment(
        text=text,
        voice=registry.narrator_voice,
        speaker="narrator",
        original_text=text,
    )]


def _registry_block(registry: CharacterRegistry) -> str:
    lines = [f"narrator: voice={registry.narrator_voice.id}"]
    for profile in registry.characters.values():
        aliases = ", ".join(profile.aliases)
        lines.append(
            f"{profile.canonical}: aliases=[{aliases}], "
            f"description={profile.description}"
        )
    return "\n".join(lines)


def _quote_prompt_items(text: str, quote_spans: list[QuoteSpan]) -> list[dict[str, Any]]:
    items = []
    for span in quote_spans:
        before = text[max(0, span.start - 120):span.start]
        after = text[span.end:span.end + 120]
        items.append({
            "quote_id": span.quote_id,
            "text": span.text,
            "before": before,
            "after": after,
        })
    return items


def _chapter_quote_spans(windows: list[ChunkWindow]) -> list[ChapterQuoteSpan]:
    chapter_spans: list[ChapterQuoteSpan] = []
    quote_id = 0
    for chunk_index, window in enumerate(windows):
        for span in extract_quote_spans(window.text):
            chapter_spans.append(ChapterQuoteSpan(
                quote_id=quote_id,
                chunk_index=chunk_index,
                start=span.start,
                end=span.end,
                text=span.text,
            ))
            quote_id += 1
    return chapter_spans


def _chapter_quote_prompt_items(
    windows: list[ChunkWindow],
    quote_spans: list[ChapterQuoteSpan],
) -> list[dict[str, Any]]:
    items = []
    for span in quote_spans:
        text = windows[span.chunk_index].text
        before = text[max(0, span.start - 120):span.start]
        after = text[span.end:span.end + 120]
        items.append({
            "quote_id": span.quote_id,
            "chunk_index": span.chunk_index,
            "text": span.text,
            "before": before,
            "after": after,
        })
    return items


def _quote_batches(
    quote_spans: list[ChapterQuoteSpan],
    batch_size: int = CHAPTER_ATTRIBUTION_QUOTE_BATCH_SIZE,
) -> list[list[ChapterQuoteSpan]]:
    return [
        quote_spans[i:i + batch_size]
        for i in range(0, len(quote_spans), batch_size)
    ]


def quote_speaker_assignments_to_spans(
    quote_spans: list[QuoteSpan],
    assignments: dict[int, Any],
    text: str,
) -> list[SpeakerSpan]:
    expected_ids = {span.quote_id for span in quote_spans}
    if set(assignments) != expected_ids:
        raise ValueError("quote speaker assignments must cover every quote exactly once")

    spans: list[SpeakerSpan] = []
    cursor = 0
    for quote in quote_spans:
        if quote.start < cursor:
            raise ValueError("quote spans overlap")
        if quote.start > cursor:
            spans.append(SpeakerSpan(cursor, quote.start, "narrator"))
        assignment = assignments[quote.quote_id]
        if isinstance(assignment, dict):
            speaker = str(assignment.get("speaker", "narrator"))
            delivery_type = _clean_delivery_type(
                assignment.get("delivery_type"),
                speaker,
            )
            voice_policy = _clean_voice_policy(
                assignment.get("voice_policy"),
                speaker,
                delivery_type,
            )
        else:
            speaker = str(assignment)
            delivery_type = _clean_delivery_type(None, speaker)
            voice_policy = _clean_voice_policy(None, speaker, delivery_type)
        spans.append(SpeakerSpan(
            quote.start,
            quote.end,
            speaker,
            delivery_type=delivery_type,
            voice_policy=voice_policy,
        ))
        cursor = quote.end
    if cursor < len(text):
        spans.append(SpeakerSpan(cursor, len(text), "narrator"))

    merged: list[SpeakerSpan] = []
    for span in spans:
        if (
            merged
            and merged[-1].speaker == span.speaker
            and merged[-1].end == span.start
            and merged[-1].delivery_type == span.delivery_type
            and merged[-1].voice_policy == span.voice_policy
        ):
            merged[-1] = SpeakerSpan(
                merged[-1].start,
                span.end,
                span.speaker,
                delivery_type=span.delivery_type,
                voice_policy=span.voice_policy,
            )
        else:
            merged.append(span)
    return merged


def annotate_chapter_speakers(
    chapter: ChapterWindow,
    registry: CharacterRegistry,
    llm: LLMClient,
    cfg: AnnotationPromptConfig,
    voice_pool: list[VoiceSpec],
) -> ChapterAttribution:
    quote_spans = _chapter_quote_spans(chapter.chunks)
    chapter_text = "\n".join(window.text for window in chapter.chunks)
    if not quote_spans:
        return ChapterAttribution(
            registry=registry,
            chunk_spans=[
                [SpeakerSpan(0, len(window.text), "narrator")]
                for window in chapter.chunks
            ],
        )

    from openshelf.config import EMBEDDED_DIALOGUE_MODE

    embedded_rules = (
        "For quoted dialogue inside a poem, song, letter, or story being "
        "recited by an in-scene character, assign those embedded quoted lines "
        "to the reciter when the reciter is clear. Do not create new characters "
        "for poem/story speakers in this mode.\n"
        if EMBEDDED_DIALOGUE_MODE == "reciter"
        else
        "For quoted dialogue inside a poem, song, letter, or story being "
        "recited by an in-scene character, you may create embedded speakers as "
        "new_characters when that improves theatrical clarity.\n"
    )
    system = (
        "You are assigning audiobook performance direction to pre-extracted "
        "quoted spans for one chapter batch.\n"
        "Use the existing registry when possible. If a quoted speaker is a real "
        "new character in this chapter, add it to new_characters and assign that "
        "speaker name to its quote_ids.\n"
        "Every quote_speakers[].speaker must be exactly one of: narrator, an "
        "existing registry canonical name, an existing registry alias, or a "
        "canonical name included in new_characters in this same response. Do not "
        "invent alternate spellings.\n"
        "For every quote_speakers item, include delivery_type and voice_policy. "
        "delivery_type must be one of: spoken_dialogue, internal_thought, "
        "self_talk, recitation, written_text, embedded_text, ambiguous. "
        "voice_policy must be one of: narrator, narrator_compatible, character, "
        "distinct_character.\n"
        "Default style is solo narrator with selective character color, not full "
        "cast. Use distinct_character only for clearly spoken dialogue by a "
        "present character. Use narrator or narrator_compatible for internal "
        "thoughts, self-talk, close third-person POV, written text, labels, "
        "signs, and quote/tag units that would sound unnatural if hard-switched.\n"
        "For a main POV/protagonist character, prefer narrator_compatible for "
        "that character's lines unless the quote is clearly part of a dialogue "
        "exchange with another present character.\n"
        "For new characters, use the exact name spelling found in QUOTE_SPANS "
        "before/after context whenever a name is visible.\n"
        "Do not calculate offsets. Return one speaker assignment for every quote_id "
        "in this batch.\n"
        "Use narrator for quoted labels, signs, book titles, jar/bottle/cake text, "
        "written object text, and any quote that is not actually spoken or thought "
        "by a character.\n"
        "The narrator is always present but must not be included in new_characters.\n"
        "New character aliases should include names, titles, roles, and epithets, "
        "but not pronouns. Include first_evidence_quote for every new character.\n"
        f"{embedded_rules}"
        f"{cfg.speaker_rules}"
    )
    updated_registry = registry
    assignments: dict[int, dict[str, str]] = {}

    batches = _quote_batches(quote_spans)
    for batch_index, batch in enumerate(batches):
        batch_prompt_items = _chapter_quote_prompt_items(chapter.chunks, batch)
        user = (
            f"Chapter: {chapter.title}\n"
            f"Batch: {batch_index + 1} of {len(batches)}\n\n"
            f"Registry:\n{_registry_block(updated_registry)}\n\n"
            "QUOTE_SPANS:\n"
            f"{json.dumps(batch_prompt_items, ensure_ascii=False)}"
        )
        response = _complete_chapter_json(llm, system, user)
        raw_new_characters = _clean_registry_characters(response.get("new_characters", []))
        updated_registry = merge_new_characters(
            updated_registry,
            raw_new_characters,
            voice_pool,
            source_text=chapter_text,
        )

        batch_expected_ids = {span.quote_id for span in batch}
        batch_assignments: dict[int, dict[str, str]] = {}
        quote_items_by_id = {
            int(item["quote_id"]): item for item in batch_prompt_items
        }
        for item in response.get("quote_speakers", []):
            quote_id = int(item["quote_id"])
            if quote_id in assignments or quote_id in batch_assignments:
                raise ValueError("duplicate quote speaker assignment")
            speaker = str(item["speaker"])
            repaired_speaker, new_character = _repair_or_adjudicate_speaker(
                speaker,
                updated_registry,
                llm,
                chapter_text,
                quote_items_by_id.get(quote_id, {}),
                voice_pool,
            )
            if new_character is not None:
                updated_registry = merge_new_characters(
                    updated_registry,
                    [new_character],
                    voice_pool,
                    source_text=chapter_text,
                )
            delivery_type = _clean_delivery_type(
                item.get("delivery_type"),
                repaired_speaker,
            )
            voice_policy = _clean_voice_policy(
                item.get("voice_policy"),
                repaired_speaker,
                delivery_type,
            )
            batch_assignments[quote_id] = {
                "speaker": repaired_speaker,
                "delivery_type": delivery_type,
                "voice_policy": voice_policy,
            }
        if set(batch_assignments) != batch_expected_ids:
            raise ValueError("chapter speaker assignments must cover every quote in the batch")
        assignments.update(batch_assignments)

    expected_ids = {span.quote_id for span in quote_spans}
    if set(assignments) != expected_ids:
        raise ValueError("chapter speaker assignments must cover every quote exactly once")

    spans_by_chunk: list[list[SpeakerSpan]] = []
    for chunk_index, window in enumerate(chapter.chunks):
        chunk_quotes = [
            QuoteSpan(
                quote_id=span.quote_id,
                start=span.start,
                end=span.end,
                text=span.text,
            )
            for span in quote_spans
            if span.chunk_index == chunk_index
        ]
        if not chunk_quotes:
            chunk_spans = [SpeakerSpan(0, len(window.text), "narrator")]
        else:
            chunk_assignments = {
                quote.quote_id: assignments[quote.quote_id]
                for quote in chunk_quotes
            }
            chunk_spans = quote_speaker_assignments_to_spans(
                chunk_quotes,
                chunk_assignments,
                window.text,
            )
        validate_spans(chunk_spans, window.text)
        validate_span_speakers(chunk_spans, updated_registry)
        spans_by_chunk.append(chunk_spans)

    return ChapterAttribution(registry=updated_registry, chunk_spans=spans_by_chunk)


def annotate_chunk_speakers(
    window: ChunkWindow,
    registry: CharacterRegistry,
    llm: LLMClient,
    cfg: AnnotationPromptConfig,
) -> list[SpeakerSpan]:
    quote_spans = extract_quote_spans(window.text)
    if not quote_spans:
        return [SpeakerSpan(0, len(window.text), "narrator")]

    system = (
        "You are assigning audiobook performance direction to pre-extracted "
        "quoted spans.\n"
        "Label only the CURRENT passage. PREVIOUS and NEXT are context only.\n"
        "Do not calculate offsets. Return one speaker assignment for every quote_id.\n"
        "Use only narrator, registry canonical names, or registry aliases.\n"
        "When possible include delivery_type and voice_policy for each quote. "
        "Use narrator/narrator_compatible for thoughts, self-talk, written text, "
        "and close POV; use distinct_character only for clearly spoken dialogue.\n"
        "Use narrator for quoted labels, signs, book titles, jar/bottle/cake text, "
        "written object text, and any quote that is not actually spoken or thought "
        "by a character.\n"
        f"{cfg.speaker_rules}"
    )
    user = (
        f"Registry:\n{_registry_block(registry)}\n\n"
        f"PREVIOUS:\n---\n{window.prev}\n---\n\n"
        f"CURRENT:\n---\n{window.text}\n---\n\n"
        "QUOTE_SPANS:\n"
        f"{json.dumps(_quote_prompt_items(window.text, quote_spans), ensure_ascii=False)}\n\n"
        f"NEXT:\n---\n{window.next}\n---"
    )
    response = llm.complete_json(system=system, user=user, schema=QUOTE_SPEAKER_SCHEMA)
    assignments: dict[int, dict[str, str]] = {}
    for item in response.get("quote_speakers", []):
        quote_id = int(item["quote_id"])
        if quote_id in assignments:
            raise ValueError("duplicate quote speaker assignment")
        speaker = str(item["speaker"])
        delivery_type = _clean_delivery_type(item.get("delivery_type"), speaker)
        voice_policy = _clean_voice_policy(
            item.get("voice_policy"),
            speaker,
            delivery_type,
        )
        assignments[quote_id] = {
            "speaker": speaker,
            "delivery_type": delivery_type,
            "voice_policy": voice_policy,
        }
    return quote_speaker_assignments_to_spans(quote_spans, assignments, window.text)


def annotate_with_fallback(
    window: ChunkWindow,
    registry: CharacterRegistry,
    llm: LLMClient,
    cfg: AnnotationPromptConfig,
) -> list[DirectedSegment]:
    if not needs_annotation(window.text):
        return narrator_segment(window.text, registry)

    try:
        spans = annotate_chunk_speakers(window, registry, llm, cfg)
        validate_spans(spans, window.text)
        validate_span_speakers(spans, registry)
        segments = group_performance_units(spans, window.text, registry)
        merged = merge_adjacent_segments(segments)
        if "".join(segment.text for segment in merged) != window.text:
            raise ValueError("segments do not preserve chunk text")
        return merged
    except (Exception, LLMError):
        return narrator_segment(window.text, registry)


def add_emotion_direction(
    segments: list[DirectedSegment],
    window: ChunkWindow,
    llm: LLMClient,
    cfg: EmotionPromptConfig,
) -> list[DirectedSegment]:
    if not segments:
        return segments

    system = _performance_system_prompt(cfg)
    segments_json = [
        _segment_annotation_prompt(segment, i)
        for i, segment in enumerate(segments)
    ]
    user = (
        f"Available emotions: {', '.join(cfg.emotion_vocabulary)}\n\n"
        f"Segments:\n{json.dumps(segments_json, ensure_ascii=False)}\n\n"
        f"Context:\n---\n{window.text}\n---"
    )

    try:
        response = llm.complete_json(system=system, user=user, schema=EMOTION_SCHEMA)
        updated = list(segments)
        for item in response.get("annotations", []):
            index = int(item["index"])
            if index < 0 or index >= len(updated):
                raise ValueError("emotion annotation index out of range")
            updated[index] = _apply_emotion_annotation(updated[index], item, cfg)
        return updated
    except Exception:
        return segments


def _count_words(text: str) -> int:
    return len(re.findall(r"\b\w+\b", text))


def performance_direction_batches(
    windows: list[ChunkWindow],
    directed_chunks: list[list[DirectedSegment]],
    max_words: int = PERFORMANCE_DIRECTION_BATCH_WORDS,
    max_chars: int = PERFORMANCE_DIRECTION_BATCH_CHARS,
) -> list[list[int]]:
    batches: list[list[int]] = []
    current: list[int] = []
    current_words = 0
    current_chars = 0

    for index, (window, segments) in enumerate(zip(windows, directed_chunks)):
        if not segments:
            continue
        words = _count_words(window.text)
        chars = len(window.text)
        should_flush = (
            bool(current)
            and (
                current_words + words > max_words
                or current_chars + chars > max_chars
            )
        )
        if should_flush:
            batches.append(current)
            current = []
            current_words = 0
            current_chars = 0
        current.append(index)
        current_words += words
        current_chars += chars

    if current:
        batches.append(current)
    return batches


def _batched_performance_prompt_items(
    windows: list[ChunkWindow],
    directed_chunks: list[list[DirectedSegment]],
    batch: list[int],
) -> list[dict[str, Any]]:
    return [
        {
            "chunk_index": chunk_index,
            "previous": windows[chunk_index].prev,
            "text": windows[chunk_index].text,
            "next": windows[chunk_index].next,
            "segments": [
                {
                    **_segment_annotation_prompt(segment, segment_index),
                    "segment_index": segment_index,
                }
                for segment_index, segment in enumerate(directed_chunks[chunk_index])
            ],
        }
        for chunk_index in batch
    ]


def _apply_whole_chunk_annotation(
    segments: list[DirectedSegment],
    item: dict[str, Any],
    cfg: EmotionPromptConfig,
) -> list[DirectedSegment]:
    if "emotion" not in item or "speed" not in item:
        raise ValueError("whole chunk direction requires emotion and speed")
    return [_apply_emotion_annotation(segment, item, cfg) for segment in segments]


def _split_segment_by_adaptive_units(
    segment: DirectedSegment,
    units: Any,
    cfg: EmotionPromptConfig,
) -> list[DirectedSegment]:
    if not isinstance(units, list) or not units:
        raise ValueError("split direction requires non-empty units")
    if len(units) > MAX_ADAPTIVE_UNITS_PER_SEGMENT:
        raise ValueError("split direction has too many units")

    unit_texts: list[str] = []
    directed: list[DirectedSegment] = []
    for unit in units:
        if not isinstance(unit, dict):
            raise ValueError("split unit must be an object")
        text = unit.get("text")
        if not isinstance(text, str) or not text:
            raise ValueError("split unit requires text")
        unit_texts.append(text)
        unit_segment = replace(
            segment,
            text=text,
            original_text=text,
        )
        directed.append(_apply_emotion_annotation(unit_segment, unit, cfg))

    if "".join(unit_texts) != segment.text:
        raise ValueError("split unit text must preserve parent segment text exactly")
    return directed


def _apply_batched_emotion_response(
    directed_chunks: list[list[DirectedSegment]],
    batch: list[int],
    response: dict[str, Any],
    cfg: EmotionPromptConfig,
) -> list[list[DirectedSegment]]:
    updated = [list(segments) for segments in directed_chunks]
    expected = set(batch)
    seen: set[int] = set()
    for item in response.get("chunks", []):
        chunk_index = int(item["chunk_index"])
        if chunk_index in seen:
            raise ValueError("duplicate chunk direction")
        if chunk_index not in expected:
            raise ValueError("chunk direction index out of range")
        mode = str(item.get("mode", ""))
        if mode == "whole":
            updated[chunk_index] = _apply_whole_chunk_annotation(
                updated[chunk_index],
                item,
                cfg,
            )
        elif mode == "split":
            segments = updated[chunk_index]
            if len(segments) != 1:
                raise ValueError("split direction requires exactly one parent segment")
            updated[chunk_index] = _split_segment_by_adaptive_units(
                segments[0],
                item.get("units"),
                cfg,
            )
        else:
            raise ValueError("chunk direction mode must be whole or split")
        seen.add(chunk_index)
    if seen != expected:
        raise ValueError("batched emotion directions must cover every chunk")
    return updated


def add_batched_emotion_direction(
    directed_chunks: list[list[DirectedSegment]],
    windows: list[ChunkWindow],
    llm: LLMClient,
    cfg: EmotionPromptConfig,
    max_words: int = PERFORMANCE_DIRECTION_BATCH_WORDS,
    max_chars: int = PERFORMANCE_DIRECTION_BATCH_CHARS,
) -> list[list[DirectedSegment]]:
    if len(directed_chunks) != len(windows):
        raise ValueError("directed chunk count must match windows")
    if not directed_chunks:
        return directed_chunks

    system = _adaptive_performance_system_prompt(cfg)
    result = [list(segments) for segments in directed_chunks]
    batches = performance_direction_batches(
        windows,
        directed_chunks,
        max_words=max_words,
        max_chars=max_chars,
    )

    def apply_batch(batch: list[int]) -> None:
        nonlocal result
        if not batch:
            return
        user = (
            f"Available emotions: {', '.join(cfg.emotion_vocabulary)}\n\n"
            "Return one direction object for every chunk_index exactly once.\n"
            "For each chunk choose mode='whole' when one shared emotion, pace, "
            "and intensity should apply to the whole chunk, neutral or otherwise. "
            "Choose mode='split' only when materially different performance "
            "controls are needed within a single parent segment, such as calm "
            "narration plus anxious inner thought. For split mode, return ordered "
            "units whose text values concatenate exactly to the parent segment "
            "text; do not rewrite, omit, normalize, or add text. Prefer whole "
            "and neutral unless a split is clearly beneficial.\n\n"
            "Chunks:\n"
            f"{json.dumps(_batched_performance_prompt_items(windows, result, batch), ensure_ascii=False)}"
        )
        try:
            response = llm.complete_json(system=system, user=user, schema=BATCHED_EMOTION_SCHEMA)
            result = _apply_batched_emotion_response(result, batch, response, cfg)
        except Exception as exc:
            if len(batch) > 1:
                midpoint = len(batch) // 2
                logger.warning(
                    "Batched emotion direction failed for chunks %s; retrying halves: %s",
                    batch,
                    exc,
                )
                apply_batch(batch[:midpoint])
                apply_batch(batch[midpoint:])
                return
            logger.warning(
                "Batched emotion direction failed for chunk %s; using neutral direction: %s",
                batch[0],
                exc,
            )
            chunk_index = batch[0]
            result[chunk_index] = neutral_emotion_direction(result[chunk_index], cfg)

    for batch in batches:
        apply_batch(batch)
    return result


class AudioDirector:
    def __init__(
        self,
        engine: TTSEngine,
        llm: LLMClient,
        aligner: WordAligner,
        build_dir: str | os.PathLike[str] | None = None,
        sample_rate: int | None = None,
        cast_mode: str = "solo",
        performance_direction_mode: str = "batched",
    ):
        self.engine = engine
        self.llm = llm
        self.aligner = aligner
        self.build_dir = Path(build_dir) if build_dir is not None else None
        self.sample_rate = sample_rate
        if cast_mode not in {"solo", "multicast"}:
            raise ValueError("cast_mode must be 'solo' or 'multicast'")
        if performance_direction_mode not in PERFORMANCE_DIRECTION_MODES:
            raise ValueError("performance_direction_mode must be 'batched', 'chunk', or 'off'")
        self.cast_mode = cast_mode
        self.performance_direction_mode = performance_direction_mode
        self.last_chapter_error: str | None = None
        self.last_chapter_fallback_used = False

    def build_registry(
        self,
        ctx: BookContext,
        narrator_voice_override: VoiceSpec | None = None,
    ) -> CharacterRegistry:
        registry = build_character_registry(
            ctx=ctx,
            llm=self.llm,
            prompt_cfg=self.engine.registry_prompt_config(),
            voice_pool=self.engine.available_voices(),
            narrator_voice_override=narrator_voice_override,
        )
        if self.build_dir is not None:
            self.build_dir.mkdir(parents=True, exist_ok=True)
            path = self.build_dir / "character_registry.json"
            with open(path, "w", encoding="utf-8") as f:
                json.dump(registry.to_dict(), f, indent=2, ensure_ascii=False, sort_keys=True)
        return registry

    def _write_registry(self, registry: CharacterRegistry) -> None:
        if self.build_dir is None:
            return
        self.build_dir.mkdir(parents=True, exist_ok=True)
        path = self.build_dir / "character_registry.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(registry.to_dict(), f, indent=2, ensure_ascii=False, sort_keys=True)

    def _supports_performance_direction(self) -> EmotionPromptConfig | None:
        if not (
            self.engine.capabilities.emotion_control
            or self.engine.capabilities.performance_direction
        ):
            return None
        return self.engine.emotion_prompt_config()

    def _apply_engine_performance_controls(
        self,
        directed_chunks: list[list[DirectedSegment]],
    ) -> list[list[DirectedSegment]]:
        apply_controls = getattr(self.engine, "apply_performance_controls", None)
        if not callable(apply_controls):
            return directed_chunks
        return [
            [apply_controls(segment) for segment in segments]
            for segments in directed_chunks
        ]

    def _add_chapter_performance_direction(
        self,
        directed_chunks: list[list[DirectedSegment]],
        windows: list[ChunkWindow],
    ) -> list[list[DirectedSegment]]:
        emotion_cfg = self._supports_performance_direction()
        if emotion_cfg is None:
            return directed_chunks
        if self.performance_direction_mode == "off":
            directed = [
                neutral_emotion_direction(segments, emotion_cfg)
                for segments in directed_chunks
            ]
        elif self.performance_direction_mode == "chunk":
            directed = [
                add_emotion_direction(segments, window, self.llm, emotion_cfg)
                for segments, window in zip(directed_chunks, windows)
            ]
        else:
            directed = add_batched_emotion_direction(
                directed_chunks,
                windows,
                self.llm,
                emotion_cfg,
            )
        return self._apply_engine_performance_controls(directed)

    def _add_performance_direction(
        self,
        segments: list[DirectedSegment],
        window: ChunkWindow,
    ) -> list[DirectedSegment]:
        return self._add_chapter_performance_direction([segments], [window])[0]

    def _base_solo_segments(
        self,
        window: ChunkWindow,
        registry: CharacterRegistry,
    ) -> list[DirectedSegment]:
        return [
            DirectedSegment(
                text=window.text,
                voice=registry.narrator_voice,
                speaker="narrator",
                original_text=window.text,
                delivery_type="narration",
                voice_policy="narrator",
                join_policy="normal",
            )
        ]

    def _solo_segments(
        self,
        window: ChunkWindow,
        registry: CharacterRegistry,
    ) -> list[DirectedSegment]:
        return self._add_performance_direction(
            self._base_solo_segments(window, registry),
            window,
        )

    def direct_chapter(
        self,
        title: str,
        windows: list[ChunkWindow],
        registry: CharacterRegistry,
    ) -> tuple[CharacterRegistry, list[list[DirectedSegment]]]:
        self.last_chapter_error = None
        self.last_chapter_fallback_used = False
        if self.cast_mode == "solo":
            directed = [
                self._base_solo_segments(window, registry)
                for window in windows
            ]
            return registry, self._add_chapter_performance_direction(directed, windows)
        try:
            attribution = annotate_chapter_speakers(
                ChapterWindow(title=title, chunks=windows),
                registry,
                self.llm,
                self.engine.annotation_prompt_config(),
                self.engine.available_voices(),
            )
            directed: list[list[DirectedSegment]] = []
            for window, spans in zip(windows, attribution.chunk_spans):
                segments = merge_adjacent_segments(
                    group_performance_units(spans, window.text, attribution.registry)
                )
                if "".join(segment.text for segment in segments) != window.text:
                    raise ValueError("segments do not preserve chunk text")
                directed.append(segments)
            if attribution.registry.characters != registry.characters:
                self._write_registry(attribution.registry)
            return attribution.registry, self._add_chapter_performance_direction(directed, windows)
        except Exception as e:
            self.last_chapter_error = f"{type(e).__name__}: {e}"
            self.last_chapter_fallback_used = True
            logger.warning(
                "Chapter-level direction failed for %s; falling back to chunk direction: %s",
                title,
                self.last_chapter_error,
            )
            directed = [
                self.direct_chunk(window, registry)
                for window in windows
            ]
            return registry, directed

    def direct_chunk(
        self,
        window: ChunkWindow,
        registry: CharacterRegistry,
    ) -> list[DirectedSegment]:
        if self.cast_mode == "solo":
            return self._solo_segments(window, registry)
        segments = annotate_with_fallback(
            window,
            registry,
            self.llm,
            self.engine.annotation_prompt_config(),
        )
        return self._add_performance_direction(segments, window)

    def synthesize_chunk(
        self,
        segments: list[DirectedSegment],
        prior_frames: int,
    ) -> tuple[Any, list[WordTimestamp]]:
        from openshelf.config import TTS_SAMPLE_RATE
        from openshelf.pipeline.tts import _synthesize_segments

        sample_rate = self.sample_rate or TTS_SAMPLE_RATE
        return _synthesize_segments(
            self.engine,
            self.aligner,
            segments,
            self.engine.post_processing_config(),
            sample_rate,
            prior_frames,
        )


# --- chapter-NN.voice_direction.json artifact (LLM <-> audio boundary) -------


def directed_segment_to_dict(segment: DirectedSegment) -> dict:
    original_text = segment.original_text if segment.original_text is not None else segment.text
    return {
        "speaker": segment.speaker,
        "voice": asdict(segment.voice),
        "original_text": original_text,
        "synthesis_text": segment.text,
        "emotion": segment.emotion,
        "speed": segment.speed,
        "pause_after_ms": segment.pause_after_ms,
        "delivery_type": segment.delivery_type,
        "voice_policy": segment.voice_policy,
        "join_policy": segment.join_policy,
        "engine_controls": segment.engine_controls,
    }


def voice_spec_from_dict(data: dict) -> VoiceSpec:
    return VoiceSpec(
        id=data["id"],
        preset_name=data.get("preset_name"),
        ref_audio_path=data.get("ref_audio_path"),
        ref_text=data.get("ref_text"),
        style_ref_audio_paths=data.get("style_ref_audio_paths") or {},
        style_ref_texts=data.get("style_ref_texts") or {},
    )


def directed_segment_from_dict(data: dict) -> DirectedSegment:
    return DirectedSegment(
        text=data["synthesis_text"],
        voice=voice_spec_from_dict(data["voice"]),
        speaker=data["speaker"],
        emotion=data.get("emotion"),
        speed=float(data.get("speed", 1.0) or 1.0),
        pause_after_ms=int(data.get("pause_after_ms", 0) or 0),
        original_text=data.get("original_text"),
        delivery_type=data.get("delivery_type", "narration"),
        voice_policy=data.get("voice_policy", "narrator"),
        join_policy=data.get("join_policy", "normal"),
        engine_controls=data.get("engine_controls") or {},
    )


def directed_chunks_from_chapter_direction_artifact(path: str) -> list[list[DirectedSegment]]:
    with open(path, "r", encoding="utf-8") as f:
        payload = json.load(f)
    chapters = payload.get("chapters", [])
    if len(chapters) != 1:
        raise ValueError(f"chapter direction artifact must contain exactly one chapter: {path}")

    chunks = chapters[0].get("chunks", [])
    return [
        [directed_segment_from_dict(segment) for segment in chunk.get("segments", [])]
        for chunk in chunks
    ]


def build_direction_chapter(
    number: int,
    title: str,
    chunk_texts: list[str],
    directed_chunks: list[list[DirectedSegment]],
    fallback_used: bool = False,
    fallback_error: str | None = None,
) -> dict:
    payload = {
        "number": number,
        "title": title,
        "fallback_used": fallback_used,
        "fallback_error": fallback_error,
        "chunks": [],
    }
    for ci, text in enumerate(chunk_texts):
        segments = directed_chunks[ci] if ci < len(directed_chunks) else []
        payload["chunks"].append({
            "index": ci,
            "text": text,
            "segments": [directed_segment_to_dict(segment) for segment in segments],
        })
    return payload


def build_voice_direction_payload(
    rendition: str,
    build_id: str,
    engine_name: str,
    cast_mode: str,
    chapters: list[dict],
) -> dict:
    return {
        "version": 1,
        "rendition": rendition,
        "build": build_id,
        "engine": engine_name,
        "cast_mode": cast_mode,
        "chapters": chapters,
    }


def build_chunk_windows(chunk_texts: list[str]) -> list[ChunkWindow]:
    """Build prev/text/next direction windows for a chapter's chunks.

    Shared by the full DAG runner and the `direct` DAG command.
    """
    return [
        ChunkWindow(
            prev=chunk_texts[i - 1] if i > 0 else "",
            text=text,
            next=chunk_texts[i + 1] if i < len(chunk_texts) - 1 else "",
        )
        for i, text in enumerate(chunk_texts)
    ]
