"""Step 3: Generate audio from text chunks using Kokoro TTS."""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, replace
from typing import Any

import numpy as np
import soundfile as sf

from openshelf.config import (
    TTS_VOICE,
    TTS_LANGUAGE,
    TTS_SAMPLE_RATE,
    CROSSFADE_MS,
    LEAD_IN_SILENCE_MS,
)
from openshelf.pipeline.seams import (
    ChunkSynthesisAudit,
    PausePolicy,
    SeamAudit,
    SegmentSynthesisAudit,
    SynthesisUnitAudit,
    TextUnit,
    pause_frames,
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
_PARAGRAPH_BREAK_RE = re.compile(r"\n\s*\n+")
_SENTENCE_END_RE = re.compile(r"[.!?][\"'\u201d\u2019)\]]*\s*$")
_CHAPTER_LABEL_RE = re.compile(r"^(?:chapter\s+)?(?:[ivxlcdm]+|\d+)\.?$", re.IGNORECASE)

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
    synthesis_units: list[ChunkSynthesisAudit]


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
    return re.sub(r"\s+", " ", without_tags).strip()


def _is_true_internal_paragraph_break(left: str, right: str) -> bool:
    left_clean = _sanitize_synthesis_text(left)
    right_clean = _sanitize_synthesis_text(right)
    if not left_clean or not right_clean:
        return False
    if _CHAPTER_LABEL_RE.fullmatch(left_clean):
        return False
    if not _SENTENCE_END_RE.search(left_clean):
        return False
    # Short title/subtitle fragments should flow into the following prose.
    if _word_count(right_clean) < 4 and not right_clean.startswith(('"', "\u201c")):
        return False
    return True


def _join_synthesis_fragments(left: str, right: str) -> str:
    left_clean = _sanitize_synthesis_text(left)
    right_clean = _sanitize_synthesis_text(right)
    if not left_clean:
        return right_clean
    if not right_clean:
        return left_clean
    if (
        not _SENTENCE_END_RE.search(left_clean)
        and _word_count(left_clean) <= 8
        and not left_clean.endswith((",", ";", ":", "-", "\u2014"))
    ):
        left_clean = f"{left_clean}."
    return f"{left_clean} {right_clean}".strip()


def _split_synthesis_units(text: str) -> list[TextUnit]:
    parts = [part.strip() for part in _PARAGRAPH_BREAK_RE.split(text) if part.strip()]
    if len(parts) <= 1:
        return [TextUnit(text)]
    units: list[TextUnit] = []
    current = parts[0]
    for part in parts[1:]:
        if _is_true_internal_paragraph_break(current, part):
            units.append(TextUnit(current, "paragraph"))
            current = part
        else:
            current = _join_synthesis_fragments(current, part)
    units.append(TextUnit(current))
    return units


def _word_count(text: str) -> int:
    return len(re.findall(r"\b[\w']+\b", text))


def _last_sentence(text: str, max_words: int = 30) -> str:
    clean = _sanitize_synthesis_text(text)
    if not clean:
        return ""
    sentences = re.findall(r"[^.!?]+[.!?][\"'\u201d\u2019]*|[^.!?]+$", clean)
    if not sentences:
        return ""
    sentence = sentences[-1].strip()
    words = sentence.split()
    if len(words) > max_words:
        sentence = " ".join(words[-max_words:])
    return sentence


def _enforce_monotonic_word_order(words: list[WordTimestamp]) -> list[WordTimestamp]:
    adjusted: list[WordTimestamp] = []
    cursor = 0.0
    epsilon = 0.001
    for word in words:
        start = max(float(word.start), cursor)
        end = max(float(word.end), start + epsilon)
        start = round(start, 4)
        end = round(end, 4)
        adjusted.append(WordTimestamp(word=word.word, start=start, end=end))
        cursor = end
    return adjusted


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


def _can_use_rolling_context(aligner: WordAligner, post_cfg: PostProcessingConfig) -> bool:
    return (
        os.environ.get("OPENSHELF_TTS_ROLLING_CONTEXT") == "1"
        and post_cfg.needs_forced_alignment
        and not isinstance(aligner, NullAligner)
        and bool(getattr(aligner, "supports_context_trim", False))
    )


def _trim_context_audio(
    audio: np.ndarray,
    sample_rate: int,
    aligner: WordAligner,
    context_text: str,
    current_text: str,
) -> np.ndarray | None:
    context_words = _word_count(context_text)
    if context_words <= 0:
        return audio
    aligned = aligner.align(audio, f"{context_text} {current_text}", sample_rate)
    if len(aligned) <= context_words:
        return None
    start_s = aligned[context_words].start
    if start_s <= 0:
        return None
    trim_at = min(len(audio), int(start_s * sample_rate))
    trimmed = audio[trim_at:]
    if len(trimmed) == 0:
        return None
    return trimmed


def _synthesize_with_rolling_context(
    engine: Any,
    aligner: WordAligner,
    segment: DirectedSegment,
    post_cfg: PostProcessingConfig,
    context_text: str,
) -> Any:
    if not context_text or not _can_use_rolling_context(aligner, post_cfg):
        return _synthesize_segment_with_fallback(engine, segment)

    contextual_text = f"{context_text} {segment.text}".strip()
    contextual_segment = replace(segment, text=contextual_text)
    try:
        result = _synthesize_segment_with_fallback(engine, contextual_segment)
        audio = np.asarray(result.audio, dtype=np.float32)
        trimmed = _trim_context_audio(
            audio,
            int(result.sample_rate),
            aligner,
            context_text,
            segment.text,
        )
        if trimmed is None:
            raise RuntimeError("rolling context trim boundary not found")
        return replace(result, audio=trimmed)
    except Exception:
        logger.warning(
            "Rolling TTS context failed for %s; retrying without context",
            segment.speaker,
            exc_info=True,
        )
        return _synthesize_segment_with_fallback(engine, segment)


@dataclass(frozen=True)
class _RenderUnit:
    segment: DirectedSegment
    segment_index: int
    unit_index: int
    text: str
    break_type_after: str | None = None


def _as_text_unit(value: Any) -> TextUnit:
    if isinstance(value, TextUnit):
        return value
    if isinstance(value, (list, tuple)) and value:
        break_type = value[1] if len(value) > 1 and isinstance(value[1], str) else None
        return TextUnit(str(value[0]), break_type)
    return TextUnit(str(value))


def _engine_text_units(engine: Any, text: str) -> list[TextUnit]:
    split_units = getattr(engine, "split_synthesis_units", None)
    if not callable(split_units):
        return [TextUnit(text)]
    raw_units = split_units(text)
    if not raw_units:
        return []
    return [_as_text_unit(unit) for unit in raw_units]


def _render_units_for_segments(
    engine: Any,
    segments: list[DirectedSegment],
    policy: PausePolicy,
) -> list[_RenderUnit]:
    render_units: list[_RenderUnit] = []
    for segment_index, segment in enumerate(segments):
        base_units = (
            [TextUnit(segment.text)]
            if getattr(engine, "prefer_packed_synthesis_units", False)
            else _split_synthesis_units(segment.text)
        )
        unit_index = 0
        for base_unit in base_units:
            engine_units = _engine_text_units(engine, base_unit.text)
            if not engine_units:
                continue
            for split_index, engine_unit in enumerate(engine_units):
                is_last_engine_unit = split_index == len(engine_units) - 1
                if is_last_engine_unit:
                    break_type_after = engine_unit.break_type_after or base_unit.break_type_after
                else:
                    next_unit = engine_units[split_index + 1]
                    break_type_after = engine_unit.break_type_after or policy.classify(
                        engine_unit.text,
                        next_unit.text,
                    )
                render_units.append(_RenderUnit(
                    segment=segment,
                    segment_index=segment_index,
                    unit_index=unit_index,
                    text=engine_unit.text,
                    break_type_after=break_type_after,
                ))
                unit_index += 1
    return render_units


def _synthesize_segments(
    engine: Any,
    aligner: WordAligner,
    segments: list[DirectedSegment],
    post_cfg: PostProcessingConfig,
    sample_rate: int,
    prior_audio_frames: int,
    rolling_context: list[str] | None = None,
    chunk_index: int = 0,
    return_audit: bool = False,
) -> tuple[np.ndarray, list[WordTimestamp]] | tuple[np.ndarray, list[WordTimestamp], ChunkSynthesisAudit]:
    """Synthesize directed segments. Returns (audio, absolute word timestamps)."""
    if not segments:
        raise RuntimeError("No directed segments to synthesize")

    audio_parts: list[np.ndarray] = []
    all_words: list[WordTimestamp] = []
    forced_align_texts: list[str] = []
    forced_align_starts: list[float] = []
    frames_so_far = 0
    prev_voice_id: str | None = None
    policy = PausePolicy()
    render_units = _render_units_for_segments(engine, segments, policy)
    segment_audits: dict[int, SegmentSynthesisAudit] = {}
    seams: list[SeamAudit] = []

    for render_index, render_unit in enumerate(render_units):
        segment = render_unit.segment
        unit_segment = replace(segment, text=render_unit.text)
        synth_segment = _segment_for_synthesis(unit_segment)
        apply_controls = getattr(engine, "apply_performance_controls", None)
        if callable(apply_controls):
            synth_segment = apply_controls(synth_segment)
        if not _has_spoken_content(synth_segment.text):
            if segment.pause_after_ms > 0:
                pause = _generate_silence(sample_rate, segment.pause_after_ms)
                audio_parts.append(pause)
                frames_so_far += len(pause)
            continue

        voice_id = segment.voice.id
        if prev_voice_id is not None and voice_id != prev_voice_id:
            transition_ms = 0 if segment.join_policy == "tight" else post_cfg.voice_transition_silence_ms
            if transition_ms > 0:
                silence = _generate_silence(sample_rate, transition_ms)
                audio_parts.append(silence)
                frames_so_far += len(silence)

        context_text = rolling_context[0] if rolling_context else ""
        result = _synthesize_with_rolling_context(
            engine,
            aligner,
            synth_segment,
            post_cfg,
            context_text,
        )
        audio = np.asarray(result.audio, dtype=np.float32)
        audio = _limit_boundary_silence(
            audio,
            sample_rate,
            post_cfg.max_generated_boundary_silence_ms,
        )
        audio = _normalize(audio, cross_voice=post_cfg.normalize_cross_voice)
        audio = _apply_boundary_fades(audio, sample_rate)

        unit_start = prior_audio_frames + frames_so_far
        if post_cfg.needs_forced_alignment:
            forced_align_texts.append(synth_segment.text)
            forced_align_starts.append(frames_so_far / sample_rate)
        else:
            raw_words = result.words
            if raw_words is None:
                raw_words = aligner.align(audio, synth_segment.text, sample_rate)

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
        unit_end = prior_audio_frames + frames_so_far
        prev_voice_id = voice_id
        if rolling_context is not None:
            rolling_context[0] = _last_sentence(synth_segment.original_text or synth_segment.text)

        segment_audit = segment_audits.get(render_unit.segment_index)
        if segment_audit is None:
            segment_audit = SegmentSynthesisAudit(
                segment_index=render_unit.segment_index,
                speaker=segment.speaker,
                emotion=segment.emotion,
                start_frame=unit_start,
                end_frame=unit_end,
            )
            segment_audits[render_unit.segment_index] = segment_audit
        segment_audit.end_frame = unit_end
        segment_audit.units.append(SynthesisUnitAudit(
            unit_index=render_unit.unit_index,
            text=synth_segment.text,
            start_frame=unit_start,
            end_frame=unit_end,
        ))

        if render_index + 1 >= len(render_units):
            continue
        next_unit = render_units[render_index + 1]
        kind = (
            "engine_unit"
            if next_unit.segment_index == render_unit.segment_index
            else "directed_segment"
        )
        if kind == "directed_segment" and next_unit.segment.join_policy == "tight":
            pause_ms = 0
            break_type = "word"
        else:
            break_type = render_unit.break_type_after or policy.classify(
                synth_segment.text,
                next_unit.text,
                current_delivery_type=segment.delivery_type,
                next_delivery_type=next_unit.segment.delivery_type,
            )
            pause_ms = policy.pause_ms(break_type)
        pause_start = prior_audio_frames + frames_so_far
        if pause_ms > 0:
            pause = _generate_silence(sample_rate, pause_ms)
            audio_parts.append(pause)
            frames_so_far += len(pause)
        pause_end = prior_audio_frames + frames_so_far
        seams.append(SeamAudit(
            kind=kind,
            break_type=break_type,
            after_chunk_index=chunk_index,
            after_segment_index=render_unit.segment_index,
            after_unit_index=render_unit.unit_index,
            pause_start_frame=pause_start,
            pause_end_frame=pause_end,
            pause_ms=pause_ms,
            before_text=synth_segment.text[-120:],
            after_text=next_unit.text[:120],
        ))

    if not audio_parts:
        silent = _generate_silence(sample_rate, 80)
        audit = ChunkSynthesisAudit(
            chunk_index=chunk_index,
            start_frame=prior_audio_frames,
            end_frame=prior_audio_frames + len(silent),
        )
        if return_audit:
            return silent, all_words, audit
        return silent, all_words
    chunk_audio = np.concatenate(audio_parts)
    if post_cfg.needs_forced_alignment and forced_align_texts:
        if hasattr(aligner, "align_segments"):
            raw_words = aligner.align_segments(
                chunk_audio,
                forced_align_texts,
                forced_align_starts,
                sample_rate,
            )
        else:
            raw_words = aligner.align(
                chunk_audio,
                " ".join(forced_align_texts),
                sample_rate,
            )
        offset_s = prior_audio_frames / sample_rate
        all_words = [
            WordTimestamp(
                word=w.word,
                start=round(w.start + offset_s, 4),
                end=round(w.end + offset_s, 4),
            )
            for w in raw_words
        ]
    audit = ChunkSynthesisAudit(
        chunk_index=chunk_index,
        start_frame=prior_audio_frames,
        end_frame=prior_audio_frames + len(chunk_audio),
        segments=[segment_audits[index] for index in sorted(segment_audits)],
        seams=seams,
    )
    if return_audit:
        return chunk_audio, all_words, audit
    return chunk_audio, all_words


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
    synthesis_units: list[ChunkSynthesisAudit] = []
    prior_chunk_emitted = False
    policy = PausePolicy()

    if isinstance(getattr(pipeline, "capabilities", None), TTSCapabilities):
        engine = pipeline
    else:
        from openshelf.pipeline.engines.kokoro import KokoroAdapter

        engine = KokoroAdapter(pipeline=pipeline)

    post_cfg = engine.post_processing_config()
    if aligner is None:
        aligner = NullAligner()
    rolling_context = [""]

    for i, chunk_info in enumerate(chunks):
        try:
            pending_gap: np.ndarray | None = None
            pending_gap_seam: SeamAudit | None = None
            chunk_start_frames = frames_so_far

            if prior_chunk_emitted:
                break_type = policy.classify(
                    chunks[i - 1].text,
                    chunk_info.text,
                    paragraph=chunks[i - 1].ends_paragraph,
                )
                gap_ms = policy.pause_ms(break_type)
                pending_gap = _generate_silence(sample_rate, gap_ms)
                pending_gap_seam = SeamAudit(
                    kind="chunk",
                    break_type=break_type,
                    after_chunk_index=i - 1,
                    after_segment_index=None,
                    after_unit_index=None,
                    pause_start_frame=frames_so_far,
                    pause_end_frame=frames_so_far + len(pending_gap),
                    pause_ms=gap_ms,
                    before_text=chunks[i - 1].text[-120:],
                    after_text=chunk_info.text[:120],
                )
                chunk_start_frames += len(pending_gap)

            segments = chunk_info.directed_segments
            if segments is None:
                segments = [DirectedSegment(
                    text=chunk_info.text,
                    voice=VoiceSpec(id=voice, preset_name=voice),
                    speaker="narrator",
                )]

            chunk_audio, words, chunk_audit = _synthesize_segments(
                engine=engine,
                aligner=aligner,
                segments=segments,
                post_cfg=post_cfg,
                sample_rate=sample_rate,
                prior_audio_frames=chunk_start_frames,
                rolling_context=rolling_context,
                chunk_index=i,
                return_audit=True,
            )

            if pending_gap is not None:
                if pending_gap_seam is not None and synthesis_units:
                    synthesis_units[-1].seams.append(pending_gap_seam)
                    synthesis_units[-1].end_frame += len(pending_gap)
                frames_so_far += len(pending_gap)
                audio_segments.append(pending_gap)

            chunk_start = frames_so_far / sample_rate
            if post_cfg.needs_forced_alignment and not words:
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
            chunk_words.append(_enforce_monotonic_word_order(words))

            audio_segments.append(chunk_audio)
            frames_so_far += len(chunk_audio)
            synthesis_units.append(chunk_audit)
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
        synthesis_units=synthesis_units,
    )


def build_chunk_infos(
    chunk_texts: list[str],
    ends_paragraph: list[bool],
    directed_chunks: list[list[DirectedSegment]] | None = None,
) -> list[ChunkInfo]:
    """Assemble the per-chunk ChunkInfo list for synthesis.

    Shared by the full DAG runner and the `synth` DAG command so both
    build synthesis input the same way.
    """
    infos: list[ChunkInfo] = []
    for i, text in enumerate(chunk_texts):
        directed = None
        if directed_chunks is not None and i < len(directed_chunks):
            directed = directed_chunks[i]
        infos.append(ChunkInfo(
            text=text,
            ends_paragraph=ends_paragraph[i],
            directed_segments=directed,
        ))
    return infos


def synthesize_chapter_to_files(
    engine: Any,
    chunk_infos: list[ChunkInfo],
    wav_path: str,
    m4a_path: str,
    sync_path: str,
    synthesis_units_path: str | None,
    chapter_number: int,
    chunk_texts: list[str],
    voice: str | None = None,
    aligner: Any = None,
    keep_wav: bool = False,
    force: bool = False,
) -> tuple[SynthesisResult, float]:
    """Synthesize one chapter to its m4a + sync artifacts. Returns (result, encoded_duration).

    This is the single audio-generation path shared by the full DAG
    orchestrator and the `synth` DAG command: remove any stale m4a, synthesize +
    align, encode to AAC, and write chapter-NN.sync.json. The encoded duration is
    returned for manifest accounting.
    """
    from openshelf.pipeline.encoder import encode_to_aac
    from openshelf.pipeline.seams import write_synthesis_units_artifact
    from openshelf.pipeline.word_aligner import write_chapter_sync_artifact

    # Any existing m4a is leftover from an aborted attempt; regenerate cleanly so
    # stale audio is never paired with freshly-generated word timestamps.
    if os.path.exists(m4a_path):
        os.remove(m4a_path)

    synth_kwargs: dict = {"aligner": aligner}
    if voice is not None:
        synth_kwargs["voice"] = voice
    result = synthesize_chapter(engine, chunk_infos, wav_path, **synth_kwargs)
    duration = encode_to_aac(wav_path, m4a_path, delete_wav=not keep_wav)

    write_chapter_sync_artifact(
        sync_path,
        chapter_number,
        os.path.basename(m4a_path),
        result.chunk_audio_starts,
        result.chunk_words,
        chunk_texts=chunk_texts,
        force=force,
    )
    if synthesis_units_path is not None:
        write_synthesis_units_artifact(
            synthesis_units_path,
            chapter_number,
            os.path.basename(m4a_path),
            TTS_SAMPLE_RATE,
            result.synthesis_units,
            force=force,
        )
    return result, duration
