"""Chatterbox TTSEngine adapter."""

from __future__ import annotations

import logging
import os
import re
import time
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
DEFAULT_MAX_SYNTHESIS_CHARS = 320
DEFAULT_SPLIT_PAUSE_MS = 80
_SENTENCE_RE = re.compile(r".+?(?:[.!?][\"'\u201d\u2019)\]]*(?=\s+|$)|$)", re.DOTALL)

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

logger = logging.getLogger(__name__)


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
        self._prepared_ref_audio: str | None = None

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
            _patch_chatterbox_backend_forward()
            self._model = ChatterboxTTS.from_pretrained(device=self.device)
        return self._model

    def _prepare_reference_voice(
        self,
        runtime: Any,
        ref_audio: str,
        exaggeration: float,
    ) -> bool:
        ref_key = str(Path(ref_audio).resolve())
        if self._prepared_ref_audio == ref_key:
            return True
        prepare = getattr(runtime, "prepare_conditionals", None)
        if not callable(prepare):
            return False

        started = time.perf_counter()
        prepare(ref_audio, exaggeration=exaggeration)
        elapsed = time.perf_counter() - started
        self._prepared_ref_audio = ref_key
        logger.info("Chatterbox prepared reference voice %s in %.2fs", ref_key, elapsed)
        return True

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

    def split_synthesis_units(self, text: str) -> list[tuple[str, int]]:
        max_chars = _int_env("OPENSHELF_CHATTERBOX_MAX_CHARS", DEFAULT_MAX_SYNTHESIS_CHARS)
        pause_ms = _int_env("OPENSHELF_CHATTERBOX_SPLIT_PAUSE_MS", DEFAULT_SPLIT_PAUSE_MS)
        if len(text) <= max_chars:
            return [(text, 0)]

        sentences = _sentence_units(text)
        packed: list[str] = []
        current = ""
        for sentence in sentences:
            if len(sentence) > max_chars:
                if current:
                    packed.append(current)
                    current = ""
                packed.extend(_word_pack(sentence, max_chars))
                continue
            candidate = f"{current} {sentence}".strip() if current else sentence
            if current and len(candidate) > max_chars:
                packed.append(current)
                current = sentence
            else:
                current = candidate
        if current:
            packed.append(current)

        if len(packed) <= 1:
            return [(text, 0)]
        logger.info(
            "Chatterbox split long synthesis unit chars=%d units=%d max_chars=%d",
            len(text),
            len(packed),
            max_chars,
        )
        return [
            (unit, pause_ms if index < len(packed) - 1 else 0)
            for index, unit in enumerate(packed)
        ]

    def synthesize(self, segment: DirectedSegment) -> TTSResult:
        segment = self.apply_performance_controls(segment)
        ref_audio = segment.voice.ref_audio_path
        if not ref_audio:
            raise ValueError(f"Chatterbox voice {segment.voice.id!r} requires reference audio")
        if not Path(ref_audio).exists():
            raise FileNotFoundError(f"Chatterbox reference audio not found: {ref_audio}")

        controls = self._controls_for_segment(segment)
        _patch_chatterbox_progress_bars()
        runtime = self._runtime()
        prepared = self._prepare_reference_voice(runtime, ref_audio, controls["exaggeration"])
        generate_kwargs = {
            "text": segment.text,
            "exaggeration": controls["exaggeration"],
            "cfg_weight": controls["cfg_weight"],
        }
        if not prepared:
            generate_kwargs["audio_prompt_path"] = ref_audio
        started = time.perf_counter()
        wav = runtime.generate(**generate_kwargs)
        elapsed = time.perf_counter() - started
        logger.info(
            "Chatterbox generated speaker=%s voice=%s chars=%d cfg_weight=%.4f "
            "exaggeration=%.4f prepared_ref=%s elapsed=%.2fs",
            segment.speaker,
            segment.voice.id,
            len(segment.text),
            controls["cfg_weight"],
            controls["exaggeration"],
            prepared,
            elapsed,
        )
        audio = _to_numpy_audio(wav)
        if audio.size == 0:
            raise RuntimeError(f"Chatterbox returned empty audio for {segment.voice.id}")
        sample_rate = int(getattr(runtime, "sr", TTS_SAMPLE_RATE) or TTS_SAMPLE_RATE)
        return TTSResult(audio=audio, sample_rate=sample_rate, words=None)


def _to_numpy_audio(wav: Any) -> np.ndarray:
    if hasattr(wav, "detach"):
        wav = wav.detach().cpu().numpy()
    audio = np.asarray(wav, dtype=np.float32)
    return np.squeeze(audio)


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(1, value)


def _sentence_units(text: str) -> list[str]:
    clean = re.sub(r"\s+", " ", text).strip()
    return [match.group(0).strip() for match in _SENTENCE_RE.finditer(clean) if match.group(0).strip()]


def _word_pack(text: str, max_chars: int) -> list[str]:
    words = text.split()
    units: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip() if current else word
        if current and len(candidate) > max_chars:
            units.append(current)
            current = word
        else:
            current = candidate
    if current:
        units.append(current)
    return units


def _quiet_tqdm(iterable: Any = None, *args: Any, **kwargs: Any) -> Any:
    return iterable if iterable is not None else ()


def _patch_chatterbox_progress_bars() -> None:
    module_names = [
        "chatterbox.models.t3.t3",
        "chatterbox.models.s3gen.flow_matching",
    ]
    for module_name in module_names:
        try:
            module = __import__(module_name, fromlist=["tqdm"])
        except ImportError:
            continue
        if hasattr(module, "tqdm"):
            module.tqdm = _quiet_tqdm


def _patch_chatterbox_backend_forward() -> None:
    try:
        from chatterbox.models.t3.inference.t3_hf_backend import T3HuggingfaceBackend
        from transformers.modeling_outputs import CausalLMOutputWithCrossAttentions
    except ImportError:
        return
    if getattr(T3HuggingfaceBackend, "_openshelf_fast_forward", False):
        return

    def _fast_forward(
        self: Any,
        inputs_embeds: Any,
        past_key_values: Any = None,
        use_cache: bool = True,
        output_attentions: bool = False,
        output_hidden_states: bool = True,
        return_dict: bool = True,
    ) -> Any:
        is_large_input = inputs_embeds.size(1) != 1
        has_cache = past_key_values is not None and len(past_key_values) > 0
        assert not (is_large_input and has_cache)
        assert return_dict

        tfmr_out = self.model(
            inputs_embeds=inputs_embeds,
            past_key_values=past_key_values,
            use_cache=use_cache,
            output_attentions=False,
            output_hidden_states=False,
            return_dict=True,
        )
        hidden_states = getattr(tfmr_out, "last_hidden_state", None)
        if hidden_states is None:
            hidden_states = tfmr_out.hidden_states[-1]
        logits = self.speech_head(hidden_states)
        return CausalLMOutputWithCrossAttentions(
            logits=logits,
            past_key_values=tfmr_out.past_key_values,
            hidden_states=(hidden_states,) if output_hidden_states else None,
            attentions=None,
        )

    T3HuggingfaceBackend.forward = _fast_forward
    T3HuggingfaceBackend._openshelf_fast_forward = True
    logger.info("Patched Chatterbox T3 backend forward for OpenShelf inference")


def _patch_missing_perth_watermarker() -> None:
    try:
        import perth
    except ImportError:
        return
    if getattr(perth, "PerthImplicitWatermarker", None) is None:
        dummy = getattr(perth, "DummyWatermarker", None)
        if dummy is not None:
            perth.PerthImplicitWatermarker = dummy
