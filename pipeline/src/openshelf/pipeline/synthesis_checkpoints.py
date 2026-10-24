"""Validated local-only checkpoints for expensive synthesis regions."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import math
from pathlib import Path
import re
import uuid
from typing import Any

import numpy as np
import soundfile as sf

from openshelf.pipeline.seams import (
    ChunkSynthesisAudit, SeamAudit, SegmentSynthesisAudit, SynthesisUnitAudit,
)
from openshelf.pipeline.tts_engine import WordTimestamp


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_completion(receipt: str | Path, fingerprint: str, files: list[str],
                     result: dict, duration: float) -> None:
    """Commit a local success receipt only after all section artifacts exist."""
    path = Path(receipt)
    payload = {"version": 1, "fingerprint": fingerprint,
               "files": {Path(file).name: file_sha256(file) for file in files},
               "result": result, "duration": duration}
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=False, allow_nan=False), encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def read_completion(receipt: str | Path, fingerprint: str, files: list[str]) -> dict | None:
    try:
        path = Path(receipt)
        if path.stat().st_size > 64 * 1024 * 1024:
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        if (payload["version"] != 1 or payload["fingerprint"] != fingerprint
                or not math.isfinite(payload["duration"]) or payload["duration"] <= 0
                or payload["result"]["skipped_chunks"] != 0
                or set(payload["files"]) != {Path(file).name for file in files}):
            return None
        if any(Path(file).stat().st_size == 0 or payload["files"][Path(file).name] != file_sha256(file)
               for file in files):
            return None
        return payload
    except (OSError, ValueError, TypeError, KeyError, OverflowError):
        return None


def input_fingerprint(payload: Any) -> str:
    """Hash caller-supplied immutable synthesis and alignment inputs."""
    return hashlib.sha256(json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")).hexdigest()


@dataclass
class RegionCheckpoint:
    audio: np.ndarray
    words: list[WordTimestamp]
    audit: ChunkSynthesisAudit
    context_after: str = ""


class SynthesisCheckpoints:
    """A WAV is committed before its hash-bearing JSON, never via pickle."""

    def __init__(self, directory: str | Path, sample_rate: int):
        self.directory = Path(directory)
        self.sample_rate = sample_rate

    def _paths(self, key: str) -> tuple[Path, Path]:
        if not re.fullmatch(r"heading|chunk-\d{4,8}", key):
            raise ValueError("Invalid synthesis checkpoint key")
        return self.directory / f"{key}.wav", self.directory / f"{key}.json"

    @staticmethod
    def _audit(payload: dict) -> ChunkSynthesisAudit:
        raw = dict(payload)
        raw["segments"] = [SegmentSynthesisAudit(
            **{**segment, "units": [SynthesisUnitAudit(**unit)
                                      for unit in segment["units"]]},
        ) for segment in raw["segments"]]
        raw["seams"] = [SeamAudit(**seam) for seam in raw["seams"]]
        return ChunkSynthesisAudit(**raw)

    def load(self, key: str, fingerprint: str) -> RegionCheckpoint | None:
        wav, metadata = self._paths(key)
        try:
            if metadata.stat().st_size > 8 * 1024 * 1024 or wav.stat().st_size > 64 * 1024 * 1024:
                return None
            payload = json.loads(metadata.read_text(encoding="utf-8"))
            if (payload["version"] != 1 or payload["fingerprint"] != fingerprint
                    or payload["sample_rate"] != self.sample_rate
                    or payload["wav_sha256"] != hashlib.sha256(wav.read_bytes()).hexdigest()):
                return None
            audio, rate = sf.read(wav, dtype="float32")
            if (rate != self.sample_rate or audio.ndim != 1 or not audio.size
                    or audio.size != payload["frames"] or not np.isfinite(audio).all()):
                return None
            words = [WordTimestamp(**word) for word in payload["words"]]
            if any(not isinstance(word.word, str) or not math.isfinite(word.start)
                   or not math.isfinite(word.end) or word.start < 0 or word.end < word.start
                   for word in words):
                return None
            if not isinstance(payload.get("context_after", ""), str):
                return None
            return RegionCheckpoint(audio, words, self._audit(payload["audit"]), payload.get("context_after", ""))
        except (OSError, RuntimeError, ValueError, TypeError, KeyError, OverflowError):
            return None

    def save(self, key: str, fingerprint: str, audio: np.ndarray,
             words: list[WordTimestamp], audit: ChunkSynthesisAudit,
             context_after: str = "") -> None:
        wav, metadata = self._paths(key)
        if (audio.ndim != 1 or not audio.size or not np.isfinite(audio).all()):
            raise ValueError("Invalid synthesis checkpoint waveform")
        self.directory.mkdir(parents=True, exist_ok=True)
        suffix = "." + uuid.uuid4().hex + ".tmp"
        wav_tmp = wav.with_name(wav.name + suffix)
        metadata_tmp = metadata.with_name(metadata.name + suffix)
        try:
            sf.write(wav_tmp, audio, self.sample_rate, format="WAV", subtype="FLOAT")
            payload = {
                "version": 1, "fingerprint": fingerprint,
                "sample_rate": self.sample_rate, "frames": int(audio.size),
                "wav_sha256": hashlib.sha256(wav_tmp.read_bytes()).hexdigest(),
                "words": [asdict(word) for word in words], "audit": asdict(audit),
                "context_after": context_after,
            }
            metadata_tmp.write_text(json.dumps(payload, ensure_ascii=False,
                                              allow_nan=False), encoding="utf-8")
            wav_tmp.replace(wav)
            metadata_tmp.replace(metadata)
        finally:
            wav_tmp.unlink(missing_ok=True)
            metadata_tmp.unlink(missing_ok=True)
