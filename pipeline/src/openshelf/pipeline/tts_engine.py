"""Shared TTS engine contracts for directed audiobook synthesis."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import numpy as np


@dataclass
class WordTimestamp:
    word: str
    start: float
    end: float


@dataclass
class TTSCapabilities:
    emotion_control: bool
    paralinguistic_markers: bool
    speed_control: bool
    provides_timestamps: bool
    voice_cloning: bool
    performance_direction: bool = False


@dataclass
class VoiceSpec:
    id: str
    preset_name: str | None = None
    ref_audio_path: str | None = None
    ref_text: str | None = None


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


@dataclass
class TTSResult:
    audio: np.ndarray
    sample_rate: int
    words: list[WordTimestamp] | None


@dataclass
class PostProcessingConfig:
    needs_forced_alignment: bool
    voice_transition_silence_ms: int
    normalize_cross_voice: bool
    max_generated_boundary_silence_ms: int | None = None


@dataclass
class RegistryPromptConfig:
    voice_pool_description: str
    schema_extra_fields: dict


@dataclass
class AnnotationPromptConfig:
    speaker_rules: str


@dataclass
class EmotionPromptConfig:
    emotion_vocabulary: list[str]
    marker_format: str | None
    injection_rules: str
    speed_labels: list[str]


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


@runtime_checkable
class WordAligner(Protocol):
    def align(
        self,
        audio: np.ndarray,
        text: str,
        sample_rate: int,
    ) -> list[WordTimestamp]: ...


class NullAligner:
    def align(
        self,
        audio: np.ndarray,
        text: str,
        sample_rate: int,
    ) -> list[WordTimestamp]:
        return []
