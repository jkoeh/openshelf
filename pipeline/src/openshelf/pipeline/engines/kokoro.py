"""Kokoro TTSEngine adapter."""

from __future__ import annotations

from typing import Any

import numpy as np

from openshelf.config import TTS_SAMPLE_RATE
from openshelf.pipeline.tts_engine import (
    AnnotationPromptConfig,
    DirectedSegment,
    PostProcessingConfig,
    RegistryPromptConfig,
    TTSCapabilities,
    TTSResult,
    VoiceSpec,
)


KOKORO_VOICES = [
    VoiceSpec("af_heart", preset_name="af_heart"),
    VoiceSpec("af_bella", preset_name="af_bella"),
    VoiceSpec("af_jessica", preset_name="af_jessica"),
    VoiceSpec("af_nicole", preset_name="af_nicole"),
    VoiceSpec("af_sarah", preset_name="af_sarah"),
    VoiceSpec("af_sky", preset_name="af_sky"),
    VoiceSpec("am_adam", preset_name="am_adam"),
    VoiceSpec("am_echo", preset_name="am_echo"),
    VoiceSpec("am_eric", preset_name="am_eric"),
    VoiceSpec("am_michael", preset_name="am_michael"),
    VoiceSpec("am_liam", preset_name="am_liam"),
    VoiceSpec("bf_emma", preset_name="bf_emma"),
    VoiceSpec("bf_alice", preset_name="bf_alice"),
    VoiceSpec("bf_lily", preset_name="bf_lily"),
    VoiceSpec("bm_george", preset_name="bm_george"),
    VoiceSpec("bm_daniel", preset_name="bm_daniel"),
    VoiceSpec("bm_fable", preset_name="bm_fable"),
]


KOKORO_VOICE_POOL_DESCRIPTION = """Available Kokoro preset voices.
Choose narrator_voice_id for the book's genre, audience, and prose tone. For
children's fantasy, whimsy, comedy, or a young protagonist, prefer a warm,
approachable, expressive narrator such as af_heart, bf_alice, af_sky, or
bf_lily. Avoid very deep, grave, or authoritative voices for children's books
unless the text is solemn or explicitly adult. Use stronger/deeper male voices
more often for character roles or serious adult narration.

Narrator-suitable voices:
af_heart: warm, friendly, balanced adult female; good default for broad literary narration and children's classics.
bf_alice: bright British female; good for whimsical British children's fiction and young protagonists.
af_sky: airy, youthful female; good for light, curious, dreamy narration.
bf_lily: bright British female; good for playful, lyrical, energetic narration.
af_nicole: soft, intimate female; good for gentle or reflective narration.
bf_emma: composed British female; good for classic literary narration.
am_michael: calm male; good for neutral adult narration when a male voice fits.
bm_fable: expressive British male; good for theatrical fantasy narration when a male voice is desired.

Character/role voices:
af_bella: bright female; good for lively female characters.
af_jessica: clear female; good for direct or formal female characters.
af_sarah: neutral female; good for secondary female roles.
am_adam: deep male; good for stern, large, or ominous male characters.
am_echo: warm male; good for anxious or lively male characters.
am_eric: clear male; good for precise or officious male characters.
am_liam: young male; good for youthful male characters.
bm_george: authoritative deep British male; best for solemn adult narration, kings, judges, or commanding roles, not the default for whimsical children's fiction.
bm_daniel: warm British male; good for genial adult male characters.
"""


class KokoroAdapter:
    name = "kokoro"
    capabilities = TTSCapabilities(
        emotion_control=False,
        paralinguistic_markers=False,
        speed_control=True,
        provides_timestamps=False,
        voice_cloning=False,
        performance_direction=False,
    )

    def __init__(self, pipeline: Any | None = None, device: str | None = None):
        self.pipeline = pipeline
        self.device = device

    def _pipeline(self):
        if self.pipeline is None:
            from openshelf.pipeline.tts import load_pipeline

            self.pipeline = load_pipeline(device=self.device)
        return self.pipeline

    def registry_prompt_config(self) -> RegistryPromptConfig:
        return RegistryPromptConfig(
            voice_pool_description=KOKORO_VOICE_POOL_DESCRIPTION,
            schema_extra_fields={},
        )

    def annotation_prompt_config(self) -> AnnotationPromptConfig:
        return AnnotationPromptConfig(
            speaker_rules=(
                "Kokoro uses preset voices only. Prefer stable, recurring speakers "
                "from the registry and use narrator for attribution tags."
            )
        )

    def emotion_prompt_config(self):
        return None

    def post_processing_config(self) -> PostProcessingConfig:
        return PostProcessingConfig(
            needs_forced_alignment=True,
            voice_transition_silence_ms=20,
            normalize_cross_voice=False,
        )

    def available_voices(self) -> list[VoiceSpec]:
        return list(KOKORO_VOICES)

    def synthesize(self, segment: DirectedSegment) -> TTSResult:
        from openshelf.pipeline.tts import _extract_words

        voice = segment.voice.preset_name or segment.voice.id
        pipeline = self._pipeline()
        try:
            if segment.speed != 1.0:
                results = list(pipeline(segment.text, voice=voice, speed=segment.speed))
            else:
                results = list(pipeline(segment.text, voice=voice))
        except TypeError:
            results = list(pipeline(segment.text, voice=voice))

        if not results:
            raise RuntimeError("No audio returned")

        audio = np.concatenate([
            np.asarray(r.audio if hasattr(r, "audio") else r[2], dtype=np.float32)
            for r in results
        ])
        return TTSResult(
            audio=audio,
            sample_rate=TTS_SAMPLE_RATE,
            words=None,
        )
