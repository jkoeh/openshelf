"""Step 3: Generate audio from text chunks using Kokoro TTS."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, replace
from typing import Any

import numpy as np
import soundfile as sf

from openshelf.config import (
    TTS_VOICE,
    TTS_LANGUAGE,
    TTS_SAMPLE_RATE,
    SILENCE_PARAGRAPH_BREAK_MS,
    SILENCE_MID_PARAGRAPH_MS,
    CROSSFADE_MS,
    LEAD_IN_SILENCE_MS,
)
from openshelf.pipeline.tts_engine import (
    DirectedSegment,
    NullAligner,
    PostProcessingConfig,
    TTSCapabilities,
    VoiceSpec,
    WordAligner,
    WordTimestamp,
)

logger = logging.getLogger(__name__)
_BRACKET_CUE_WORDS = (
    "anxious",
    "anxiously",
    "breathy",
    "bright",
    "brightly",
    "calm",
    "curious",
    "excited",
    "gentle",
    "neutral",
    "sigh",
    "singing",
    "sings",
    "soft",
    "softly",
    "somber",
    "tense",
    "warm",
    "warmly",
    "weary",
    "whisper",
)
_BRACKET_CUE_RE = re.compile(
    r"\[\s*(?:"
    + "|".join(re.escape(word) for word in _BRACKET_CUE_WORDS)
    + r")(?:\s*,\s*(?:"
    + "|".join(re.escape(word) for word in _BRACKET_CUE_WORDS)
    + r"))*\s*\]",
    re.IGNORECASE,
)
_TAG_RE = re.compile(r"<[^>]+>")

# Lazy-loaded at first use — torch and kokoro are heavy deps
torch: Any = None
KPipeline: Any = None


def _import_torch() -> Any:
    global torch  # noqa: PLW0603
    if torch is None:
        import torch as _torch
        torch = _torch
    return torch


def _import_kpipeline() -> Any:
    global KPipeline  # noqa: PLW0603
    if KPipeline is None:
        from kokoro import KPipeline as _KP
        KPipeline = _KP
    return KPipeline


@dataclass
class ChunkInfo:
    text: str
    ends_paragraph: bool = True
    directed_segments: list[DirectedSegment] | None = None


@dataclass
class ChapterAudio:
    chapter_number: int
    title: str
    wav_path: str
    duration_seconds: float
    word_count: int


@dataclass
class SynthesisResult:
    duration_seconds: float
    skipped_chunks: int
    chunk_audio_starts: list[float]   # len == len(input chunks); -1.0 for skipped chunks
    chunk_words: list[list[WordTimestamp]]  # per-chunk word timestamps (empty list for skipped)


def get_device() -> str:
    t = _import_torch()
    if t.cuda.is_available():
        return "cuda"
    if t.backends.mps.is_available():
        return "mps"
    return "cpu"


def load_pipeline(device: str | None = None):
    kp = _import_kpipeline()
    if device is None:
        device = get_device()
    logger.info("Loading Kokoro pipeline on %s", device)
    return kp(lang_code=TTS_LANGUAGE, device=device)


def _generate_silence(sample_rate: int, duration_ms: int) -> np.ndarray:
    n_samples = int(sample_rate * duration_ms / 1000)
    return np.zeros(n_samples, dtype=np.float32)


def _normalize(
    audio: np.ndarray,
    target_peak: float = 0.89,
    cross_voice: bool = False,
) -> np.ndarray:
    if len(audio) == 0:
        return audio
    peak = np.abs(audio).max()
    if peak > 0:
        audio = audio * (target_peak / peak)
    return audio


def _apply_boundary_fades(
    audio: np.ndarray, sample_rate: int, fade_ms: int = CROSSFADE_MS
) -> np.ndarray:
    """Apply fade-in at start and fade-out at end to smooth chunk boundaries."""
    fade_samples = int(sample_rate * fade_ms / 1000)
    if len(audio) < 2 * fade_samples:
        return audio
    audio = audio.copy()
    audio[:fade_samples] *= np.linspace(0, 1, fade_samples, dtype=np.float32)
    audio[-fade_samples:] *= np.linspace(1, 0, fade_samples, dtype=np.float32)
    return audio


def _limit_boundary_silence(
    audio: np.ndarray,
    sample_rate: int,
    max_silence_ms: int | None,
) -> np.ndarray:
    if max_silence_ms is None or max_silence_ms < 0 or len(audio) == 0:
        return audio

    max_samples = int(sample_rate * max_silence_ms / 1000)
    peak = float(np.abs(audio).max())
    if peak <= 0.0:
        return audio[:max_samples]

    threshold = max(0.005, peak * 0.02)
    voiced = np.flatnonzero(np.abs(audio) >= threshold)
    if voiced.size == 0:
        return audio[:max_samples]

    start = max(0, int(voiced[0]) - max_samples)
    end = min(len(audio), int(voiced[-1]) + max_samples + 1)
    return audio[start:end]


def _extract_words(results: list, sample_rate: int = TTS_SAMPLE_RATE) -> list[WordTimestamp]:
    """Extract word timestamps from Kokoro Result objects (chunk-relative).

    Misaki MTokens are sub-word units (a contraction like "don't" can split
    into several tokens). The token's `whitespace` attribute holds the
    whitespace that follows it, so a non-empty `whitespace` marks a word
    boundary. We accumulate consecutive tokens into a single word, taking
    `start_ts` from the first token in the run and `end_ts` from the last.

    A single `pipeline(text)` call can yield multiple `Result` objects when
    Kokoro segments the input internally. Token timestamps are reported
    relative to *each Result's own audio*, so we accumulate audio durations
    across Results and add the running offset to every token timestamp.
    """
    words: list[WordTimestamp] = []
    offset = 0.0  # seconds of audio from prior Results in this synth call

    def _flush(buf: list, off: float) -> None:
        if not buf:
            return
        text = "".join(getattr(t, "text", "") for t in buf).strip()
        if not text:
            return
        words.append(WordTimestamp(
            word=text,
            start=round(buf[0].start_ts + off, 4),
            end=round(buf[-1].end_ts + off, 4),
        ))

    for r in results:
        tokens = getattr(r, "tokens", None) or []
        buf: list = []
        for tok in tokens:
            text = getattr(tok, "text", "")
            start_ts = getattr(tok, "start_ts", None)
            end_ts = getattr(tok, "end_ts", None)
            whitespace = getattr(tok, "whitespace", "")

            if text and not text.isspace() and start_ts is not None and end_ts is not None:
                buf.append(tok)

            if whitespace:
                _flush(buf, offset)
                buf = []
        _flush(buf, offset)

        # Advance the running offset by this Result's audio duration so the
        # next Result's tokens land on the correct global timeline.
        audio_attr = r.audio if hasattr(r, "audio") else (r[2] if len(r) > 2 else None)
        if audio_attr is not None:
            arr = np.asarray(audio_attr, dtype=np.float32)
            offset += len(arr) / sample_rate

    return words


def _synthesize_single_chunk(
    pipeline: Any,
    chunk_info: ChunkInfo,
    voice: str,
    sample_rate: int,
) -> tuple[np.ndarray, list[WordTimestamp]]:
    """Synthesize a single chunk. Returns (audio, word_timestamps)."""
    from openshelf.pipeline.engines.kokoro import KokoroAdapter

    engine = KokoroAdapter(pipeline=pipeline)
    segment = DirectedSegment(
        text=chunk_info.text,
        voice=VoiceSpec(id=voice, preset_name=voice),
        speaker="narrator",
    )
    return _synthesize_segments(
        engine=engine,
        aligner=NullAligner(),
        segments=[segment],
        post_cfg=engine.post_processing_config(),
        sample_rate=sample_rate,
        prior_audio_frames=0,
    )


def _spoken_probe_text(text: str) -> str:
    """Remove synthesis cues before deciding if text has pronounceable content."""
    return _sanitize_synthesis_text(text)


def _has_spoken_content(text: str) -> bool:
    return any(ch.isalnum() for ch in _spoken_probe_text(text))


def _sanitize_synthesis_text(text: str) -> str:
    """Strip audit-only steering markup that engines may speak literally."""
    clean = text.replace("\ufeff", "")
    without_cues = _BRACKET_CUE_RE.sub("", clean)
    without_tags = _TAG_RE.sub(" ", without_cues)
    # Keep normal spaces stable without collapsing paragraph breaks that may
    # still help Kokoro phrase longer passages.
    without_tags = re.sub(r"[ \t\f\v]+", " ", without_tags)
    without_tags = re.sub(r" *\n *", "\n", without_tags)
    return without_tags.strip()


def _segment_for_synthesis(segment: DirectedSegment) -> DirectedSegment:
    clean_text = _sanitize_synthesis_text(segment.text)
    if clean_text == segment.text:
        return segment
    return replace(segment, text=clean_text)


def _synthesize_segment_with_fallback(engine: Any, segment: DirectedSegment) -> Any:
    segment = _segment_for_synthesis(segment)
    try:
        return engine.synthesize(segment)
    except Exception:
        original = segment.original_text or segment.text
        if original == segment.text:
            raise
        if not _has_spoken_content(original):
            raise
        retry = replace(
            segment,
            text=original,
            emotion=None,
            speed=1.0,
            original_text=original,
        )
        logger.warning(
            "Steered TTS failed for %s; retrying original text",
            segment.speaker,
            exc_info=True,
        )
        return engine.synthesize(retry)


def _synthesize_segments(
    engine: Any,
    aligner: WordAligner,
    segments: list[DirectedSegment],
    post_cfg: PostProcessingConfig,
    sample_rate: int,
    prior_audio_frames: int,
) -> tuple[np.ndarray, list[WordTimestamp]]:
    """Synthesize directed segments. Returns (audio, absolute word timestamps)."""
    if not segments:
        raise RuntimeError("No directed segments to synthesize")

    audio_parts: list[np.ndarray] = []
    all_words: list[WordTimestamp] = []
    frames_so_far = 0
    prev_voice_id: str | None = None

    for segment in segments:
        synth_segment = _segment_for_synthesis(segment)
        if not _has_spoken_content(synth_segment.text):
            if segment.pause_after_ms > 0:
                pause = _generate_silence(sample_rate, segment.pause_after_ms)
                audio_parts.append(pause)
                frames_so_far += len(pause)
            continue

        result = _synthesize_segment_with_fallback(engine, synth_segment)
        raw_words = [] if post_cfg.needs_forced_alignment else result.words
        if raw_words is None:
            raw_words = aligner.align(
                result.audio,
                synth_segment.text,
                result.sample_rate,
            )

        audio = np.asarray(result.audio, dtype=np.float32)
        audio = _limit_boundary_silence(
            audio,
            sample_rate,
            post_cfg.max_generated_boundary_silence_ms,
        )
        audio = _normalize(audio, cross_voice=post_cfg.normalize_cross_voice)
        audio = _apply_boundary_fades(audio, sample_rate)

        voice_id = segment.voice.id
        if prev_voice_id is not None and voice_id != prev_voice_id:
            transition_ms = 0 if segment.join_policy == "tight" else post_cfg.voice_transition_silence_ms
            if transition_ms > 0:
                silence = _generate_silence(sample_rate, transition_ms)
                audio_parts.append(silence)
                frames_so_far += len(silence)

        offset_s = (prior_audio_frames + frames_so_far) / sample_rate
        all_words.extend([
            WordTimestamp(
                word=w.word,
                start=round(w.start + offset_s, 4),
                end=round(w.end + offset_s, 4),
            )
            for w in raw_words
        ])

        audio_parts.append(audio)
        frames_so_far += len(audio)
        prev_voice_id = voice_id

        if segment.pause_after_ms > 0:
            pause = _generate_silence(sample_rate, segment.pause_after_ms)
            audio_parts.append(pause)
            frames_so_far += len(pause)

    if not audio_parts:
        return _generate_silence(sample_rate, 80), all_words
    return np.concatenate(audio_parts), all_words


def synthesize_chapter(
    pipeline: Any,
    chunks: list[ChunkInfo],
    output_path: str,
    voice: str = TTS_VOICE,
    sample_rate: int = TTS_SAMPLE_RATE,
    aligner: WordAligner | None = None,
) -> SynthesisResult:
    if not chunks:
        raise ValueError("chunks list is empty")

    # Lead-in silence absorbs the AAC encoder priming samples and Kokoro's
    # first-token onset transient so the file doesn't start with a click.
    lead_in = _generate_silence(sample_rate, LEAD_IN_SILENCE_MS)
    audio_segments: list[np.ndarray] = [lead_in]
    skipped = 0
    frames_so_far = len(lead_in)
    chunk_audio_starts: list[float] = []
    chunk_words: list[list[WordTimestamp]] = []
    prior_chunk_emitted = False

    if isinstance(getattr(pipeline, "capabilities", None), TTSCapabilities):
        engine = pipeline
    else:
        from openshelf.pipeline.engines.kokoro import KokoroAdapter

        engine = KokoroAdapter(pipeline=pipeline)

    post_cfg = engine.post_processing_config()
    if aligner is None:
        aligner = NullAligner()

    for i, chunk_info in enumerate(chunks):
        try:
            pending_gap: np.ndarray | None = None
            chunk_start_frames = frames_so_far

            if prior_chunk_emitted:
                # Variable silence: longer at paragraph breaks, shorter mid-paragraph
                if chunks[i - 1].ends_paragraph:
                    gap_ms = SILENCE_PARAGRAPH_BREAK_MS
                else:
                    gap_ms = SILENCE_MID_PARAGRAPH_MS
                pending_gap = _generate_silence(sample_rate, gap_ms)
                chunk_start_frames += len(pending_gap)

            segments = chunk_info.directed_segments
            if segments is None:
                segments = [DirectedSegment(
                    text=chunk_info.text,
                    voice=VoiceSpec(id=voice, preset_name=voice),
                    speaker="narrator",
                )]

            chunk_audio, words = _synthesize_segments(
                engine=engine,
                aligner=aligner,
                segments=segments,
                post_cfg=post_cfg,
                sample_rate=sample_rate,
                prior_audio_frames=chunk_start_frames,
            )

            if pending_gap is not None:
                frames_so_far += len(pending_gap)
                audio_segments.append(pending_gap)

            chunk_start = frames_so_far / sample_rate
            if post_cfg.needs_forced_alignment:
                chunk_relative_words = aligner.align(chunk_audio, chunk_info.text, sample_rate)
                words = [
                    WordTimestamp(
                        word=w.word,
                        start=round(w.start + chunk_start, 4),
                        end=round(w.end + chunk_start, 4),
                    )
                    for w in chunk_relative_words
                ]
            chunk_audio_starts.append(chunk_start)
            chunk_words.append(words)

            audio_segments.append(chunk_audio)
            frames_so_far += len(chunk_audio)
            prior_chunk_emitted = True
        except Exception:
            logger.warning("Chunk %d failed, skipping: %.40s...", i, chunk_info.text, exc_info=True)
            chunk_audio_starts.append(-1.0)
            chunk_words.append([])
            skipped += 1

    if not prior_chunk_emitted:
        raise RuntimeError("All chunks failed TTS synthesis")

    full_audio = np.concatenate(audio_segments)
    sf.write(output_path, full_audio, sample_rate)

    duration = len(full_audio) / sample_rate
    return SynthesisResult(
        duration_seconds=duration,
        skipped_chunks=skipped,
        chunk_audio_starts=chunk_audio_starts,
        chunk_words=chunk_words,
    )
