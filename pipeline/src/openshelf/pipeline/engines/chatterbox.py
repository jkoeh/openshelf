"""Chatterbox TTSEngine adapter."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np

from openshelf.config import PROJECT_ROOT, TTS_SAMPLE_RATE
from openshelf.pipeline.engines.kokoro import (
    KOKORO_VOICE_POOL_DESCRIPTION,
    KOKORO_VOICES,
)
from openshelf.pipeline.tts_engine import (
    AnnotationPromptConfig,
    DirectedSegment,
    EmotionPromptConfig,
    PostProcessingConfig,
    RegistryPromptConfig,
    TTSCapabilities,
    TTSResult,
    VoiceSpec,
)


CHATTERBOX_EMOTION_VOCABULARY = [
    "neutral",
    "happy",
    "sad",
    "angry",
    "anxious",
    "surprised",
    "amused",
]

DEFAULT_REF_TEXT = (
    "OpenShelf presents this calm reference passage for audiobook narration. "
    "The speaker reads clearly, with steady pacing, natural pauses, and a "
    "neutral expressive tone."
)

_BASE_CONTROLS = {"exaggeration": 0.5, "cfg_weight": 0.5}
_EMOTION_TARGETS = {
    "neutral": {"exaggeration": 0.5, "cfg_weight": 0.5},
    "happy": {"exaggeration": 0.72, "cfg_weight": 0.42},
    "sad": {"exaggeration": 0.38, "cfg_weight": 0.58},
    "angry": {"exaggeration": 0.85, "cfg_weight": 0.34},
    "anxious": {"exaggeration": 0.78, "cfg_weight": 0.36},
    "surprised": {"exaggeration": 0.82, "cfg_weight": 0.38},
    "amused": {"exaggeration": 0.68, "cfg_weight": 0.44},
}


def _chatterbox_voice_id(kokoro_preset: str) -> str:
    return f"chatterbox-{kokoro_preset}"


def _kokoro_preset(voice: VoiceSpec) -> str:
    return voice.preset_name or voice.id


def _voice_pool_description() -> str:
    description = KOKORO_VOICE_POOL_DESCRIPTION.replace(
        "Available Kokoro preset voices.",
        "Kokoro-derived Chatterbox reference voice guidance.",
    )
    for voice in sorted(KOKORO_VOICES, key=lambda item: len(_kokoro_preset(item)), reverse=True):
        preset = _kokoro_preset(voice)
        description = description.replace(preset, _chatterbox_voice_id(preset))
    return (
        "Available Chatterbox reference voices bootstrapped from Kokoro presets. "
        "Choose narrator_voice_id from these chatterbox-* IDs using the same "
        "qualitative guidance as the matching Kokoro preset; Chatterbox will "
        "clone the local reference clip for the selected ID.\n\n"
        f"{description}"
    )


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _interpolate(base: float, target: float, intensity: float) -> float:
    return base + (target - base) * intensity


class ChatterboxAdapter:
    name = "chatterbox"
    capabilities = TTSCapabilities(
        emotion_control=True,
        paralinguistic_markers=False,
        speed_control=False,
        provides_timestamps=False,
        voice_cloning=True,
        performance_direction=True,
    )

    def __init__(
        self,
        voices_dir: str | Path | None = None,
        model: Any | None = None,
        device: str | None = None,
    ):
        base = Path(voices_dir) if voices_dir else PROJECT_ROOT / "pipeline" / "voices"
        self.voice_dir = base / "chatterbox"
        self._model = model
        self.device = device

    def registry_prompt_config(self) -> RegistryPromptConfig:
        return RegistryPromptConfig(
            voice_pool_description=_voice_pool_description(),
            schema_extra_fields={"requires_ref_audio": True},
        )

    def annotation_prompt_config(self) -> AnnotationPromptConfig:
        return AnnotationPromptConfig(
            speaker_rules=(
                "Chatterbox can clone local reference voices. Speaker labels "
                "must still use only registry canonical names or narrator."
            )
        )

    def emotion_prompt_config(self) -> EmotionPromptConfig:
        return EmotionPromptConfig(
            emotion_vocabulary=list(CHATTERBOX_EMOTION_VOCABULARY),
            marker_format=None,
            injection_rules=(
                "Label each speech segment with one emotion and, when useful, "
                "an intensity from 0.0 to 1.0. Do not add paralinguistic tags."
            ),
            speed_labels=["slow", "normal", "fast"],
        )

    def post_processing_config(self) -> PostProcessingConfig:
        return PostProcessingConfig(
            needs_forced_alignment=True,
            voice_transition_silence_ms=0,
            normalize_cross_voice=True,
            max_generated_boundary_silence_ms=25,
        )

    def available_voices(self) -> list[VoiceSpec]:
        voices: list[VoiceSpec] = []
        for kokoro_voice in KOKORO_VOICES:
            preset = _kokoro_preset(kokoro_voice)
            voices.append(VoiceSpec(
                id=_chatterbox_voice_id(preset),
                ref_audio_path=str(self.voice_dir / f"{preset}.wav"),
                ref_text=DEFAULT_REF_TEXT,
            ))
        return voices

    def _runtime(self):
        if self._model is None:
            try:
                from chatterbox.tts import ChatterboxTTS
            except ImportError as exc:
                raise RuntimeError(
                    "Chatterbox support requires the optional chatterbox-tts package. "
                    "Install pipeline dependencies or run: pip install chatterbox-tts"
                ) from exc
            _patch_missing_perth_watermarker()
            self._model = ChatterboxTTS.from_pretrained(device=self.device)
        return self._model

    def _controls_for_segment(self, segment: DirectedSegment) -> dict[str, float]:
        emotion = segment.emotion or "neutral"
        target = _EMOTION_TARGETS.get(emotion, _EMOTION_TARGETS["neutral"])
        raw_intensity = segment.engine_controls.get("intensity", 0.5)
        try:
            intensity = float(raw_intensity)
        except (TypeError, ValueError):
            intensity = 0.5
        intensity = _clamp(intensity, 0.0, 1.0)
        exaggeration = _interpolate(
            _BASE_CONTROLS["exaggeration"],
            target["exaggeration"],
            intensity,
        )
        cfg_weight = _interpolate(
            _BASE_CONTROLS["cfg_weight"],
            target["cfg_weight"],
            intensity,
        )
        return {
            "intensity": round(intensity, 4),
            "exaggeration": round(_clamp(exaggeration, 0.25, 1.2), 4),
            "cfg_weight": round(_clamp(cfg_weight, 0.2, 1.0), 4),
        }

    def apply_performance_controls(self, segment: DirectedSegment) -> DirectedSegment:
        controls = self._controls_for_segment(segment)
        return replace(
            segment,
            engine_controls={
                **segment.engine_controls,
                **controls,
            },
        )

    def synthesize(self, segment: DirectedSegment) -> TTSResult:
        segment = self.apply_performance_controls(segment)
        ref_audio = segment.voice.ref_audio_path
        if not ref_audio:
            raise ValueError(f"Chatterbox voice {segment.voice.id!r} requires reference audio")
        if not Path(ref_audio).exists():
            raise FileNotFoundError(f"Chatterbox reference audio not found: {ref_audio}")

        controls = self._controls_for_segment(segment)
        wav = self._runtime().generate(
            text=segment.text,
            audio_prompt_path=ref_audio,
            exaggeration=controls["exaggeration"],
            cfg_weight=controls["cfg_weight"],
        )
        audio = _to_numpy_audio(wav)
        if audio.size == 0:
            raise RuntimeError(f"Chatterbox returned empty audio for {segment.voice.id}")
        sample_rate = int(getattr(self._runtime(), "sr", TTS_SAMPLE_RATE) or TTS_SAMPLE_RATE)
        return TTSResult(audio=audio, sample_rate=sample_rate, words=None)


def _to_numpy_audio(wav: Any) -> np.ndarray:
    if hasattr(wav, "detach"):
        wav = wav.detach().cpu().numpy()
    audio = np.asarray(wav, dtype=np.float32)
    return np.squeeze(audio)


def _patch_missing_perth_watermarker() -> None:
    try:
        import perth
    except ImportError:
        return
    if getattr(perth, "PerthImplicitWatermarker", None) is None:
        dummy = getattr(perth, "DummyWatermarker", None)
        if dummy is not None:
            perth.PerthImplicitWatermarker = dummy
