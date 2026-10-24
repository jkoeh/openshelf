"""Pinned local Qwen3 CustomVoice adapter for Mandarin audiobooks."""

from __future__ import annotations

from dataclasses import replace
import hashlib
import importlib.metadata
from typing import Any

import numpy as np

from openshelf.pipeline.language import normalize_language
from openshelf.pipeline.tts_engine import (
    AnnotationPromptConfig, DirectedSegment, PostProcessingConfig,
    RegistryPromptConfig, TTSCapabilities, TTSResult, VoiceSpec,
)

MODEL_ID = "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice"
MODEL_REVISION = "0c0e3051f131929182e2c023b9537f8b1c68adfe"
NARRATION_INSTRUCTION = "用自然、温和的普通话朗读，像讲述文学故事。语速平稳，标点处自然停顿，对话有适度的情感，保持同一个叙述者的声音。"
SPEAKERS = {"qwen3-serena": "Serena", "qwen3-vivian": "Vivian", "qwen3-uncle-fu": "Uncle_Fu"}


class Qwen3Adapter:
    name = "qwen3"
    prefer_packed_synthesis_units = True
    capabilities = TTSCapabilities(
        emotion_control=False, paralinguistic_markers=False, speed_control=False,
        provides_timestamps=False, voice_cloning=False, performance_direction=False,
    )

    def __init__(self, runtime: Any = None, device: str | None = None, language: str = "zh"):
        self.language = normalize_language(language)
        if self.language != "zh":
            raise ValueError("Qwen3 audiobook generation currently requires Chinese")
        self.device = device or "cuda"
        if self.device not in {"cuda", "cuda:0", "cpu"}:
            raise ValueError("Qwen3 supports CUDA or explicit CPU only")
        self.runtime = runtime

    @property
    def synthesis_settings(self) -> dict:
        try:
            package_version = importlib.metadata.version("qwen-tts")
        except importlib.metadata.PackageNotFoundError:
            package_version = "uninstalled"
        return {"model": MODEL_ID, "revision": MODEL_REVISION,
                "instruction_sha256": hashlib.sha256(NARRATION_INSTRUCTION.encode()).hexdigest(),
                "seed": 1337, "max_new_tokens": 2048, "attention": "sdpa",
                "dtype": "float32" if self.device == "cpu" else "bfloat16",
                "qwen_tts_version": package_version}

    def _runtime(self):
        if self.runtime is None:
            try:
                import torch
                from qwen_tts import Qwen3TTSModel
            except ImportError as exc:
                raise RuntimeError("Install pipeline/requirements-mandarin.txt in the isolated Mandarin runtime") from exc
            if self.device.startswith("cuda") and not torch.cuda.is_available():
                raise RuntimeError("Qwen3 requires working CUDA or explicit --device cpu")
            self.runtime = Qwen3TTSModel.from_pretrained(
                MODEL_ID, revision=MODEL_REVISION, device_map=self.device,
                dtype=torch.float32 if self.device == "cpu" else torch.bfloat16,
                attn_implementation="sdpa",
            )
        return self.runtime

    def available_voices(self) -> list[VoiceSpec]:
        return [VoiceSpec(id=key) for key in SPEAKERS]

    def registry_prompt_config(self) -> RegistryPromptConfig:
        return RegistryPromptConfig(voice_pool_description=(
            "Mandarin native speakers: qwen3-serena is warm and gentle; qwen3-vivian is bright; "
            "qwen3-uncle-fu is mature, low and mellow. Keep one narrator voice."
        ), schema_extra_fields={})

    def annotation_prompt_config(self) -> AnnotationPromptConfig:
        return AnnotationPromptConfig(speaker_rules="Preserve original Chinese text and punctuation; use the selected narrator.")

    def emotion_prompt_config(self):
        return None

    def post_processing_config(self) -> PostProcessingConfig:
        return PostProcessingConfig(needs_forced_alignment=True, voice_transition_silence_ms=20,
                                    normalize_cross_voice=False)

    def apply_performance_controls(self, segment: DirectedSegment) -> DirectedSegment:
        if segment.voice.id not in SPEAKERS:
            raise ValueError("Unknown Mandarin Qwen3 voice")
        return replace(segment, engine_controls={**self.synthesis_settings,
                       "speaker": SPEAKERS[segment.voice.id], "instruction": NARRATION_INSTRUCTION})

    def synthesize(self, segment: DirectedSegment) -> TTSResult:
        segment = self.apply_performance_controls(segment)
        if not segment.text.strip():
            raise ValueError("Qwen3 synthesis text is empty")
        import torch
        runtime = self._runtime()
        torch.manual_seed(1337)
        with torch.inference_mode():
            waves, rate = runtime.generate_custom_voice(
                text=segment.text, language="Chinese", speaker=SPEAKERS[segment.voice.id],
                instruct=NARRATION_INSTRUCTION, max_new_tokens=2048,
            )
        if len(waves) != 1 or rate != 24000:
            raise ValueError("Qwen3 returned an unexpected waveform or sample rate")
        audio = np.asarray(waves[0], dtype=np.float32)
        if audio.ndim != 1 or not audio.size or not np.isfinite(audio).all() or not np.any(audio):
            raise ValueError("Qwen3 returned invalid or silent audio")
        return TTSResult(audio=audio, sample_rate=rate, words=None)
