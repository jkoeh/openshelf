#!/usr/bin/env python3
"""Generate golden test fixtures for word alignment integration tests.

Run this script once to create the baseline fixtures. Re-run when:
- WhisperX or Kokoro model versions change
- Alignment logic changes intentionally

Requires: kokoro, whisperx, torch, soundfile, numpy

Usage:
    python tests/fixtures/golden_alignment/generate_golden.py
"""

import json
import os
import sys

import numpy as np
import soundfile as sf

# Allow running from project root without pip install
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "src"))

FIXTURE_DIR = os.path.dirname(__file__)
AUDIO_PATH = os.path.join(FIXTURE_DIR, "golden_audio.wav")
ALIGNMENT_PATH = os.path.join(FIXTURE_DIR, "golden_alignment.json")
METADATA_PATH = os.path.join(FIXTURE_DIR, "golden_metadata.json")

# Short known text — two chunks, simple sentences for reliable alignment
CHUNK_TEXTS = [
    "The morning sun cast long shadows across the quiet village square.",
    "Birds sang from the rooftops as the baker opened his shop.",
]
CHUNK_AUDIO_STARTS_PLACEHOLDER = []  # filled after synthesis


def generate_audio():
    """Synthesize golden audio using Kokoro TTS."""
    from openshelf.pipeline.tts import load_pipeline, _generate_silence, _normalize
    from openshelf.config import TTS_SAMPLE_RATE, SILENCE_BETWEEN_CHUNKS_MS

    print("Loading TTS pipeline ...")
    pipeline = load_pipeline()

    silence = _generate_silence(TTS_SAMPLE_RATE, SILENCE_BETWEEN_CHUNKS_MS)
    segments: list[np.ndarray] = []
    chunk_audio_starts: list[float] = []
    frames_so_far = 0

    for i, text in enumerate(CHUNK_TEXTS):
        print(f"  Synthesizing chunk {i}: {text[:50]}...")
        results = list(pipeline(text, voice="af_heart"))
        chunk_audio = np.concatenate([r[2] for r in results])
        chunk_audio = _normalize(chunk_audio)

        if segments:
            frames_so_far += len(silence)
            segments.append(silence)

        chunk_audio_starts.append(frames_so_far / TTS_SAMPLE_RATE)
        segments.append(chunk_audio)
        frames_so_far += len(chunk_audio)

    full_audio = np.concatenate(segments)
    sf.write(AUDIO_PATH, full_audio, TTS_SAMPLE_RATE)
    duration = len(full_audio) / TTS_SAMPLE_RATE
    print(f"  Audio written: {AUDIO_PATH} ({duration:.2f}s)")
    return chunk_audio_starts, duration


def generate_alignment(chunk_audio_starts: list[float]):
    """Run WhisperX alignment on the golden audio."""
    from openshelf.pipeline.word_aligner import align_chapter, validate_alignment
    import dataclasses

    print("Running WhisperX alignment ...")
    words = align_chapter(
        AUDIO_PATH,
        CHUNK_TEXTS,
        chunk_audio_starts,
        device="cpu",
        language="en",
    )
    print(f"  Aligned {len(words)} words")

    violations = validate_alignment(words, 30.0, len(CHUNK_TEXTS))
    if violations:
        print("  WARNING: Alignment has violations:")
        for v in violations:
            print(f"    - {v}")

    return [dataclasses.asdict(w) for w in words]


def main():
    print("=== Generating Golden Alignment Fixtures ===\n")

    chunk_audio_starts, duration = generate_audio()

    alignment_words = generate_alignment(chunk_audio_starts)

    # Save alignment
    with open(ALIGNMENT_PATH, "w") as f:
        json.dump(alignment_words, f, indent=2)
    print(f"\nAlignment written: {ALIGNMENT_PATH}")

    # Save metadata for the test to use
    metadata = {
        "chunk_texts": CHUNK_TEXTS,
        "chunk_audio_starts": chunk_audio_starts,
        "audio_duration": duration,
        "num_chunks": len(CHUNK_TEXTS),
        "num_words": len(alignment_words),
    }
    with open(METADATA_PATH, "w") as f:
        json.dump(metadata, f, indent=2)
    print(f"Metadata written: {METADATA_PATH}")

    print(f"\nDone. {len(alignment_words)} words aligned across {len(CHUNK_TEXTS)} chunks.")
    print("Commit all files in tests/fixtures/golden_alignment/ to version control.")


if __name__ == "__main__":
    main()
