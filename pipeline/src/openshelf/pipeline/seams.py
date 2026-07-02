"""Seam pause policy, synthesis-unit audit artifacts, and pause repair."""

from __future__ import annotations

import copy
import json
import os
import subprocess
import tempfile
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

import numpy as np
import soundfile as sf

from openshelf.config import AAC_BITRATE
from openshelf.pipeline.tts_engine import WordTimestamp
from openshelf.pipeline.word_aligner import build_section_sync_artifact


PauseBreakType = Literal[
    "paragraph",
    "sentence",
    "phrase",
    "quote",
    "inner_thought",
    "word",
    "technical",
]
SeamKind = Literal["chunk", "directed_segment", "engine_unit"]

AUDIOBOOK_PAUSE_POLICY = "audiobook-v2"


@dataclass(frozen=True)
class TextUnit:
    text: str
    break_type_after: PauseBreakType | None = None


@dataclass
class SynthesisUnitAudit:
    unit_index: int
    text: str
    start_frame: int
    end_frame: int


@dataclass
class SegmentSynthesisAudit:
    segment_index: int
    speaker: str
    emotion: str | None
    start_frame: int
    end_frame: int
    units: list[SynthesisUnitAudit] = field(default_factory=list)


@dataclass
class SeamAudit:
    kind: SeamKind
    break_type: PauseBreakType
    after_chunk_index: int
    after_segment_index: int | None
    after_unit_index: int | None
    pause_start_frame: int
    pause_end_frame: int
    pause_ms: int
    before_text: str
    after_text: str


@dataclass
class ChunkSynthesisAudit:
    chunk_index: int
    start_frame: int
    end_frame: int
    segments: list[SegmentSynthesisAudit] = field(default_factory=list)
    seams: list[SeamAudit] = field(default_factory=list)


@dataclass(frozen=True)
class PauseEdit:
    old_start_frame: int
    old_end_frame: int
    new_end_frame: int

    @property
    def delta_frames(self) -> int:
        return self.new_end_frame - self.old_end_frame


class PausePolicy:
    """Single owner for audiobook seam pause durations."""

    name = AUDIOBOOK_PAUSE_POLICY
    durations_ms: dict[PauseBreakType, int] = {
        "paragraph": 600,
        "sentence": 350,
        "phrase": 180,
        "quote": 180,
        "inner_thought": 180,
        "word": 120,
        "technical": 0,
    }

    _sentence_end = (".", "?", "!")
    _phrase_end = (",", ";", ":", "-", "\u2013", "\u2014")
    _quote_chars = ('"', "'", "\u201c", "\u201d", "\u2018", "\u2019")

    def pause_ms(self, break_type: PauseBreakType) -> int:
        return self.durations_ms.get(break_type, self.durations_ms["word"])

    def classify(
        self,
        before_text: str,
        after_text: str,
        *,
        paragraph: bool = False,
        next_delivery_type: str | None = None,
        current_delivery_type: str | None = None,
    ) -> PauseBreakType:
        if paragraph or "\n\n" in before_text[-4:] or after_text[:2] == "\n\n":
            return "paragraph"
        if next_delivery_type == "inner_thought" or current_delivery_type == "inner_thought":
            return "inner_thought"

        before = before_text.rstrip()
        after = after_text.lstrip()
        if not before or not after:
            return "word"

        before_unquoted = before.rstrip("".join(self._quote_chars) + ")]}")
        last = before_unquoted[-1:] or before[-1:]
        if last in self._sentence_end:
            return "sentence"
        if last in self._phrase_end:
            return "phrase"
        if before[-1:] in self._quote_chars or after[:1] in self._quote_chars:
            return "quote"
        return "word"


def pause_frames(policy: PausePolicy, break_type: PauseBreakType, sample_rate: int) -> int:
    return int(round(sample_rate * policy.pause_ms(break_type) / 1000))


def build_synthesis_units_artifact(
    sequence: int,
    audio_filename: str,
    sample_rate: int,
    chunks: list[ChunkSynthesisAudit],
    policy_name: str = AUDIOBOOK_PAUSE_POLICY,
) -> dict[str, Any]:
    return {
        "version": 2,
        "sequence": sequence,
        "audio_filename": audio_filename,
        "sample_rate": sample_rate,
        "policy": policy_name,
        "chunks": [asdict(chunk) for chunk in chunks],
    }


def read_synthesis_units_artifact(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_synthesis_units_artifact(
    path: str,
    sequence: int,
    audio_filename: str,
    sample_rate: int,
    chunks: list[ChunkSynthesisAudit],
    *,
    force: bool = False,
) -> str:
    payload = build_synthesis_units_artifact(
        sequence,
        audio_filename,
        sample_rate,
        chunks,
    )
    if os.path.exists(path) and not force:
        existing = read_synthesis_units_artifact(path)
        if existing == payload:
            return path
        raise FileExistsError(f"synthesis units artifact already exists with different payload: {path}")

    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False, sort_keys=True)
    return path


def _all_seams(payload: dict[str, Any]) -> list[dict[str, Any]]:
    seams: list[dict[str, Any]] = []
    for chunk in payload.get("chunks", []):
        seams.extend(chunk.get("seams", []))
    return seams


def _shift_for_frame(frame: int, edits: list[PauseEdit]) -> int:
    shift = 0
    for edit in edits:
        if frame >= edit.old_end_frame:
            shift += edit.delta_frames
    return shift


def _shift_time(value: float, sample_rate: int, edits: list[PauseEdit]) -> float:
    frame = int(round(value * sample_rate))
    return round((frame + _shift_for_frame(frame, edits)) / sample_rate, 4)


def _shift_frame_value(value: Any, edits: list[PauseEdit]) -> Any:
    if not isinstance(value, int):
        return value
    return value + _shift_for_frame(value, edits)


def _shift_synthesis_payload(
    payload: dict[str, Any],
    edits: list[PauseEdit],
    policy: PausePolicy,
) -> dict[str, Any]:
    updated = copy.deepcopy(payload)

    def shift_obj_frames(obj: dict[str, Any]) -> None:
        for key, value in list(obj.items()):
            if key.endswith("_frame") and isinstance(value, int):
                obj[key] = _shift_frame_value(value, edits)

    for chunk in updated.get("chunks", []):
        shift_obj_frames(chunk)
        for segment in chunk.get("segments", []):
            shift_obj_frames(segment)
            for unit in segment.get("units", []):
                shift_obj_frames(unit)
        for seam in chunk.get("seams", []):
            shift_obj_frames(seam)

    for chunk in updated.get("chunks", []):
        for seam in chunk.get("seams", []):
            break_type = seam.get("break_type", "word")
            target_ms = policy.pause_ms(break_type)
            start = int(seam["pause_start_frame"])
            new_len = int(round(int(updated["sample_rate"]) * target_ms / 1000))
            seam["pause_ms"] = target_ms
            seam["pause_end_frame"] = start + new_len

    updated["policy"] = policy.name
    return updated


def _shift_sync_payload(
    sync_payload: dict[str, Any],
    sample_rate: int,
    edits: list[PauseEdit],
    chunk_texts: list[str] | None = None,
) -> dict[str, Any]:
    chunk_audio_starts = [
        -1.0 if float(start) < 0 else _shift_time(float(start), sample_rate, edits)
        for start in sync_payload.get("chunk_audio_starts", [])
    ]

    chunk_words: list[list[WordTimestamp]] = []
    for chunk in sync_payload.get("chunks", []):
        words = []
        for word in chunk.get("words", []):
            words.append(WordTimestamp(
                word=word["word"],
                start=_shift_time(float(word["start"]), sample_rate, edits),
                end=_shift_time(float(word["end"]), sample_rate, edits),
            ))
        chunk_words.append(words)

    heading_words = [
        WordTimestamp(
            word=word["word"],
            start=_shift_time(float(word["start"]), sample_rate, edits),
            end=_shift_time(float(word["end"]), sample_rate, edits),
        )
        for word in sync_payload.get("heading_words", [])
    ]

    return build_section_sync_artifact(
        int(sync_payload["sequence"]),
        sync_payload["audio_filename"],
        chunk_audio_starts,
        chunk_words,
        chunk_texts=chunk_texts,
        heading_words=heading_words,
    )


def repair_pcm_pauses(
    audio: np.ndarray,
    sync_payload: dict[str, Any],
    synthesis_payload: dict[str, Any],
    *,
    chunk_texts: list[str] | None = None,
    policy: PausePolicy | None = None,
) -> tuple[np.ndarray, dict[str, Any], dict[str, Any], list[PauseEdit]]:
    policy = policy or PausePolicy()
    sample_rate = int(synthesis_payload["sample_rate"])
    edits: list[PauseEdit] = []

    for seam in sorted(_all_seams(synthesis_payload), key=lambda s: int(s["pause_start_frame"]), reverse=True):
        start = int(seam["pause_start_frame"])
        end = int(seam["pause_end_frame"])
        if end < start:
            raise ValueError("seam pause_end_frame precedes pause_start_frame")
        if start > len(audio) or end > len(audio):
            raise ValueError("seam pause range is outside audio")
        break_type = seam.get("break_type", "word")
        target_ms = policy.pause_ms(break_type)
        target_frames = int(round(sample_rate * target_ms / 1000))
        old_frames = end - start
        if target_frames == old_frames:
            continue
        replacement = np.zeros(target_frames, dtype=np.float32)
        audio = np.concatenate([audio[:start], replacement, audio[end:]])
        edits.append(PauseEdit(
            old_start_frame=start,
            old_end_frame=end,
            new_end_frame=start + target_frames,
        ))

    edits = sorted(edits, key=lambda item: item.old_start_frame)
    if not edits:
        return audio, sync_payload, synthesis_payload, []

    updated_sync = _shift_sync_payload(sync_payload, sample_rate, edits, chunk_texts=chunk_texts)
    updated_synthesis = _shift_synthesis_payload(synthesis_payload, edits, policy)
    return audio, updated_sync, updated_synthesis, edits


def _decode_to_wav(m4a_path: str, wav_path: str, sample_rate: int) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            m4a_path,
            "-ar",
            str(sample_rate),
            "-ac",
            "1",
            wav_path,
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _encode_wav_to_m4a(wav_path: str, m4a_path: str) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-i", wav_path, "-c:a", "aac", "-b:a", AAC_BITRATE, "-f", "mp4", m4a_path],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def repair_chapter_pause_files(
    *,
    m4a_path: str,
    sync_path: str,
    synthesis_units_path: str,
    chunk_texts: list[str] | None = None,
    force: bool = False,
) -> list[PauseEdit]:
    if not force:
        raise ValueError("pause repair rewrites artifacts and requires force=True")
    if not os.path.exists(m4a_path):
        raise FileNotFoundError(m4a_path)
    if not os.path.exists(sync_path):
        raise FileNotFoundError(sync_path)
    if not os.path.exists(synthesis_units_path):
        raise FileNotFoundError(synthesis_units_path)

    with open(sync_path, "r", encoding="utf-8") as f:
        sync_payload = json.load(f)
    synthesis_payload = read_synthesis_units_artifact(synthesis_units_path)
    sample_rate = int(synthesis_payload["sample_rate"])

    with tempfile.TemporaryDirectory() as tmp:
        decoded_wav = os.path.join(tmp, "decoded.wav")
        repaired_wav = os.path.join(tmp, "repaired.wav")
        repaired_m4a = os.path.join(tmp, "repaired.m4a")
        _decode_to_wav(m4a_path, decoded_wav, sample_rate)
        audio, read_sample_rate = sf.read(decoded_wav, dtype="float32")
        if audio.ndim > 1:
            audio = audio[:, 0]
        if int(read_sample_rate) != sample_rate:
            raise ValueError(f"decoded sample rate {read_sample_rate} != artifact sample rate {sample_rate}")

        repaired_audio, updated_sync, updated_synthesis, edits = repair_pcm_pauses(
            audio,
            sync_payload,
            synthesis_payload,
            chunk_texts=chunk_texts,
        )
        if not edits:
            return []

        sf.write(repaired_wav, repaired_audio, sample_rate)
        _encode_wav_to_m4a(repaired_wav, repaired_m4a)

        os.replace(repaired_m4a, m4a_path)
        with open(sync_path, "w", encoding="utf-8") as f:
            json.dump(updated_sync, f, indent=2, ensure_ascii=False, sort_keys=True)
        with open(synthesis_units_path, "w", encoding="utf-8") as f:
            json.dump(updated_synthesis, f, indent=2, ensure_ascii=False, sort_keys=True)

    return edits
