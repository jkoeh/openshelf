"""F5-TTS adapter shell.

F5-TTS has no built-in preset catalog for OpenShelf, so its initial voice pool
is bootstrapped from local Kokoro-generated reference clips. The clips are
auditable local build inputs, not public R2 artifacts.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
import re

from openshelf.config import PROJECT_ROOT, TTS_SAMPLE_RATE
from openshelf.pipeline.engines.kokoro import (
    KOKORO_VOICE_POOL_DESCRIPTION,
    KOKORO_VOICES,
    KokoroAdapter,
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


DEFAULT_REF_TEXT = (
    "OpenShelf presents this calm reference passage for audiobook narration. "
    "The speaker reads clearly, with steady pacing, natural pauses, and a "
    "neutral expressive tone."
)

F5_EMOTION_VOCABULARY = [
    "neutral",
    "happy",
    "sad",
    "angry",
    "anxious",
    "surprised",
    "amused",
]

STYLE_REF_TEXTS = {
    "neutral": DEFAULT_REF_TEXT,
    "happy": "OpenShelf presents this bright reference passage with a warm smile and buoyant energy.",
    "sad": "OpenShelf presents this quiet reference passage with restrained sorrow and reflective pauses.",
    "angry": "OpenShelf presents this tense reference passage with controlled force and clipped emphasis.",
    "anxious": "OpenShelf presents this uneasy reference passage with nervous urgency and careful breaths.",
    "surprised": "OpenShelf presents this startled reference passage with lifted energy and alert phrasing.",
    "amused": "OpenShelf presents this amused reference passage with playful warmth and a light smile.",
}


@dataclass(frozen=True)
class BootstrapVoiceResult:
    voice_id: str
    kokoro_preset: str
    path: str
    status: str


def _kokoro_preset(voice: VoiceSpec) -> str:
    return voice.preset_name or voice.id


def _f5_voice_id(kokoro_preset: str) -> str:
    return f"f5tts-{kokoro_preset}"


def _f5_voice_pool_description() -> str:
    description = KOKORO_VOICE_POOL_DESCRIPTION.replace(
        "Available Kokoro preset voices.",
        "Kokoro-derived F5 reference voice guidance.",
    )
    for voice in sorted(KOKORO_VOICES, key=lambda item: len(_kokoro_preset(item)), reverse=True):
        preset = _kokoro_preset(voice)
        description = re.sub(rf"\b{re.escape(preset)}\b", _f5_voice_id(preset), description)
    return (
        "Available F5-TTS reference voices bootstrapped from Kokoro presets. "
        "Choose narrator_voice_id from these f5tts-* IDs using the same "
        "qualitative guidance as the matching Kokoro preset; F5 will clone the "
        "local reference clip for the selected ID.\n\n"
        f"{description}"
    )


def _select_kokoro_voices(voice_ids: list[str] | None) -> list[VoiceSpec]:
    if not voice_ids:
        return list(KOKORO_VOICES)

    by_id: dict[str, VoiceSpec] = {}
    for voice in KOKORO_VOICES:
        preset = _kokoro_preset(voice)
        by_id[preset] = voice
        by_id[_f5_voice_id(preset)] = voice

    selected: list[VoiceSpec] = []
    unknown: list[str] = []
    for voice_id in voice_ids:
        voice = by_id.get(voice_id)
        if voice is None:
            unknown.append(voice_id)
            continue
        if voice not in selected:
            selected.append(voice)

    if unknown:
        known = ", ".join(_kokoro_preset(voice) for voice in KOKORO_VOICES)
        raise ValueError(f"Unknown Kokoro/F5 voice id(s): {', '.join(unknown)}. Known: {known}")
    return selected


def bootstrap_kokoro_reference_voices(
    voices_dir: str | Path | None = None,
    voice_ids: list[str] | None = None,
    overwrite: bool = False,
    dry_run: bool = False,
    device: str | None = None,
    kokoro_pipeline=None,
) -> list[BootstrapVoiceResult]:
    """Render local F5 reference WAVs from Kokoro presets."""
    adapter = F5TTSAdapter(voices_dir=voices_dir)
    selected = _select_kokoro_voices(voice_ids)
    results: list[BootstrapVoiceResult] = []

    if not dry_run:
        adapter.voice_dir.mkdir(parents=True, exist_ok=True)

    kokoro = None
    for kokoro_voice in selected:
        preset = _kokoro_preset(kokoro_voice)
        path = adapter.voice_dir / f"{preset}.wav"
        voice_id = _f5_voice_id(preset)

        if path.exists() and not overwrite:
            results.append(BootstrapVoiceResult(voice_id, preset, str(path), "exists"))
            continue

        if dry_run:
            status = "would_overwrite" if path.exists() else "would_write"
            results.append(BootstrapVoiceResult(voice_id, preset, str(path), status))
            continue

        if kokoro is None:
            kokoro = KokoroAdapter(pipeline=kokoro_pipeline, device=device)

        segment = DirectedSegment(
            text=DEFAULT_REF_TEXT,
            voice=VoiceSpec(id=preset, preset_name=preset),
            speaker="narrator",
            original_text=DEFAULT_REF_TEXT,
        )
        result = kokoro.synthesize(segment)
        if result.audio.size == 0:
            raise RuntimeError(f"Kokoro returned empty audio for {preset}")

        import soundfile as sf

        sf.write(str(path), result.audio, result.sample_rate or TTS_SAMPLE_RATE)
        status = "overwritten" if overwrite else "written"
        results.append(BootstrapVoiceResult(voice_id, preset, str(path), status))

    return results


class F5TTSAdapter:
    name = "f5tts"
    capabilities = TTSCapabilities(
        emotion_control=True,
        paralinguistic_markers=False,
        speed_control=True,
        provides_timestamps=False,
        voice_cloning=True,
        performance_direction=True,
    )

    def __init__(
        self,
        voices_dir: str | Path | None = None,
        f5tts=None,
        device: str | None = None,
        model: str = "F5TTS_v1_Base",
        seed: int | None = None,
        remove_silence: bool = False,
    ):
        base = Path(voices_dir) if voices_dir else PROJECT_ROOT / "pipeline" / "voices"
        self.voice_dir = base / "f5tts"
        self._f5tts = f5tts
        self.device = device
        self.model = model
        self.seed = seed
        self.remove_silence = remove_silence

    def registry_prompt_config(self) -> RegistryPromptConfig:
        return RegistryPromptConfig(
            voice_pool_description=_f5_voice_pool_description(),
            schema_extra_fields={"requires_ref_audio": True},
        )

    def annotation_prompt_config(self) -> AnnotationPromptConfig:
        return AnnotationPromptConfig(
            speaker_rules=(
                "F5-TTS can clone local reference voices. Speaker labels must "
                "still use only registry canonical names or narrator."
            )
        )

    def emotion_prompt_config(self) -> EmotionPromptConfig:
        return EmotionPromptConfig(
            emotion_vocabulary=list(F5_EMOTION_VOCABULARY),
            marker_format=None,
            injection_rules=(
                "Label each speech segment with one emotion. Narrator is "
                "always neutral unless the prose clearly demands otherwise."
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
            style_paths = self._style_ref_audio_paths(preset)
            voices.append(VoiceSpec(
                id=_f5_voice_id(preset),
                ref_audio_path=str(self.voice_dir / f"{preset}.wav"),
                ref_text=DEFAULT_REF_TEXT,
                style_ref_audio_paths=style_paths,
                style_ref_texts={
                    style: STYLE_REF_TEXTS[style]
                    for style in style_paths
                    if style in STYLE_REF_TEXTS
                },
            ))
        return voices

    def _style_ref_audio_paths(self, preset: str) -> dict[str, str]:
        refs: dict[str, str] = {"neutral": str(self.voice_dir / f"{preset}.wav")}
        for emotion in F5_EMOTION_VOCABULARY:
            nested = self.voice_dir / preset / f"{emotion}.wav"
            flat = self.voice_dir / f"{preset}-{emotion}.wav"
            if nested.exists():
                refs[emotion] = str(nested)
            elif flat.exists():
                refs[emotion] = str(flat)
        return refs

    def _runtime(self):
        if self._f5tts is None:
            try:
                from f5_tts.api import F5TTS
            except ImportError as exc:
                raise RuntimeError(
                    "F5-TTS support requires the optional f5-tts package. "
                    "Install pipeline dependencies or run: pip install f5-tts"
                ) from exc
            self._f5tts = F5TTS(model=self.model, device=self.device)
        return self._f5tts

    def _styled_voice(self, segment: DirectedSegment) -> tuple[VoiceSpec, str]:
        emotion = segment.emotion or "neutral"
        if emotion not in F5_EMOTION_VOCABULARY:
            emotion = "neutral"
        audio_paths = segment.voice.style_ref_audio_paths or {}
        text_by_style = segment.voice.style_ref_texts or {}
        style = emotion if emotion in audio_paths and Path(audio_paths[emotion]).exists() else "neutral"
        ref_audio = audio_paths.get(style) or segment.voice.ref_audio_path
        ref_text = text_by_style.get(style) or segment.voice.ref_text
        return replace(segment.voice, ref_audio_path=ref_audio, ref_text=ref_text), style

    def apply_performance_controls(self, segment: DirectedSegment) -> DirectedSegment:
        _, style = self._styled_voice(segment)
        return replace(
            segment,
            engine_controls={
                **segment.engine_controls,
                "style_ref": style,
            },
        )

    def synthesize(self, segment: DirectedSegment) -> TTSResult:
        segment = self.apply_performance_controls(segment)
        voice, _style = self._styled_voice(segment)
        ref_audio = voice.ref_audio_path
        if not ref_audio:
            raise ValueError(f"F5-TTS voice {segment.voice.id!r} requires reference audio")
        if not Path(ref_audio).exists():
            raise FileNotFoundError(f"F5-TTS reference audio not found: {ref_audio}")

        ref_text = voice.ref_text
        if not ref_text:
            raise ValueError(f"F5-TTS voice {segment.voice.id!r} requires reference text")

        wav, sample_rate, _ = self._runtime().infer(
            ref_file=ref_audio,
            ref_text=ref_text,
            gen_text=segment.text,
            speed=segment.speed,
            seed=self.seed,
            remove_silence=self.remove_silence,
        )
        import numpy as np

        audio = np.asarray(wav, dtype=np.float32)
        if audio.size == 0:
            raise RuntimeError(f"F5-TTS returned empty audio for {segment.voice.id}")
        return TTSResult(audio=audio, sample_rate=int(sample_rate), words=None)
