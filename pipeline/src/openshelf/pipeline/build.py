"""Compute the per-(rendition, voice, pipeline-version, config) build hash.

A "build" identifies a coherent snapshot of pipeline output bytes. Same inputs
produce the same hash; any change to inputs that would change audio bytes or
chapter_data produces a different hash, which becomes a new R2 path prefix and
a new immutable URL — see pipeline/docs/step6-r2.md and the rendition-vs-build
section in the root CLAUDE.md.

The function is a pure transform over (rendition, voice, PIPELINE_VERSION, and
the TTS/encoder/chunker config constants that influence output). The constants
are imported by name so unit tests can patch them in place.
"""

from __future__ import annotations

import hashlib

from openshelf.config import (
    AAC_BITRATE,
    CHUNK_MAX_WORDS,
    CROSSFADE_MS,
    LEAD_IN_SILENCE_MS,
    PIPELINE_VERSION,
    SILENCE_MID_PARAGRAPH_MS,
    SILENCE_PARAGRAPH_BREAK_MS,
    TTS_LANGUAGE,
    TTS_SAMPLE_RATE,
)


_HASH_LEN = 7


def compute_build_id(rendition: str, voice: str) -> str:
    """Return the first 7 hex characters of a sha256 over pipeline-affecting inputs."""
    h = hashlib.sha256()
    parts = [
        ("pipeline_version", PIPELINE_VERSION),
        ("rendition", rendition),
        ("voice", voice),
        ("tts_language", TTS_LANGUAGE),
        ("tts_sample_rate", TTS_SAMPLE_RATE),
        ("chunk_max_words", CHUNK_MAX_WORDS),
        ("silence_paragraph_break_ms", SILENCE_PARAGRAPH_BREAK_MS),
        ("silence_mid_paragraph_ms", SILENCE_MID_PARAGRAPH_MS),
        ("crossfade_ms", CROSSFADE_MS),
        ("lead_in_silence_ms", LEAD_IN_SILENCE_MS),
        ("aac_bitrate", AAC_BITRATE),
    ]
    for key, value in parts:
        h.update(f"{key}={value}\n".encode("utf-8"))
    return h.hexdigest()[:_HASH_LEN]
