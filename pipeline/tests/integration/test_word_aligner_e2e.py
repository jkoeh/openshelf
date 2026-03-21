"""E2E golden snapshot test for WhisperX word alignment.

Requires:
- WhisperX installed
- Golden fixtures generated (run tests/fixtures/golden_alignment/generate_golden.py)
- Set RUN_INTEGRATION=1 to enable

Usage:
    RUN_INTEGRATION=1 python -m unittest tests.integration.test_word_aligner_e2e -v
"""

import json
import os
import sys
import unittest

# Allow running without pip install
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "..", "fixtures", "golden_alignment")
AUDIO_PATH = os.path.join(FIXTURE_DIR, "golden_audio.wav")
ALIGNMENT_PATH = os.path.join(FIXTURE_DIR, "golden_alignment.json")
METADATA_PATH = os.path.join(FIXTURE_DIR, "golden_metadata.json")

SKIP_REASON = None
if not os.environ.get("RUN_INTEGRATION"):
    SKIP_REASON = "RUN_INTEGRATION not set"
elif not os.path.exists(AUDIO_PATH):
    SKIP_REASON = f"Golden fixtures not found — run generate_golden.py first"


@unittest.skipIf(SKIP_REASON, SKIP_REASON or "")
class TestWordAlignerE2E(unittest.TestCase):
    """Compare live WhisperX alignment against golden snapshot."""

    @classmethod
    def setUpClass(cls):
        with open(METADATA_PATH) as f:
            cls.metadata = json.load(f)
        with open(ALIGNMENT_PATH) as f:
            cls.golden_words = json.load(f)

        from openshelf.pipeline.word_aligner import align_chapter
        cls.live_words = align_chapter(
            AUDIO_PATH,
            cls.metadata["chunk_texts"],
            cls.metadata["chunk_audio_starts"],
            device="cpu",
            language="en",
        )

    def test_produces_words(self):
        """Alignment returns a non-empty word list."""
        self.assertGreater(len(self.live_words), 0)

    def test_word_count_within_tolerance(self):
        """Word count is within 20% of golden snapshot."""
        golden_count = len(self.golden_words)
        live_count = len(self.live_words)
        tolerance = 0.2
        self.assertGreater(live_count, golden_count * (1 - tolerance),
                           f"Too few words: {live_count} vs golden {golden_count}")
        self.assertLess(live_count, golden_count * (1 + tolerance),
                        f"Too many words: {live_count} vs golden {golden_count}")

    def test_timestamps_monotonically_non_decreasing(self):
        """Word start times never go backwards."""
        for i in range(1, len(self.live_words)):
            self.assertGreaterEqual(
                self.live_words[i].start,
                self.live_words[i - 1].start,
                f"word[{i}] '{self.live_words[i].word}' start went backwards",
            )

    def test_timestamps_within_audio_bounds(self):
        """All timestamps fall within [0, audio_duration]."""
        duration = self.metadata["audio_duration"]
        for i, w in enumerate(self.live_words):
            self.assertGreaterEqual(w.start, 0.0, f"word[{i}] negative start")
            self.assertLessEqual(w.start, duration,
                                 f"word[{i}] start exceeds duration {duration}")

    def test_chunk_idx_in_range(self):
        """All chunk_idx values are valid."""
        num_chunks = self.metadata["num_chunks"]
        for i, w in enumerate(self.live_words):
            self.assertGreaterEqual(w.chunk_idx, 0, f"word[{i}] negative chunk_idx")
            self.assertLess(w.chunk_idx, num_chunks,
                            f"word[{i}] chunk_idx {w.chunk_idx} >= {num_chunks}")

    def test_both_chunks_represented(self):
        """Words are assigned to every chunk (no chunk is empty)."""
        chunk_indices = {w.chunk_idx for w in self.live_words}
        for i in range(self.metadata["num_chunks"]):
            self.assertIn(i, chunk_indices, f"Chunk {i} has no aligned words")

    def test_timestamp_drift_within_tolerance(self):
        """Timestamps don't drift more than 100ms from golden snapshot.

        Uses a best-effort word matching strategy: for each golden word,
        find the closest live word by text and check timestamp proximity.
        We use 100ms tolerance (generous) to account for model version variance.
        """
        TOLERANCE_S = 0.1  # 100ms

        # Build a lookup of live words by text (lowercased)
        live_by_text: dict[str, list] = {}
        for w in self.live_words:
            key = w.word.strip().lower()
            live_by_text.setdefault(key, []).append(w)

        matched = 0
        drifted = 0
        for gw in self.golden_words:
            key = gw["word"].strip().lower()
            candidates = live_by_text.get(key, [])
            if not candidates:
                continue
            # Find candidate closest in time
            best = min(candidates, key=lambda c: abs(c.start - gw["start"]))
            matched += 1
            if abs(best.start - gw["start"]) > TOLERANCE_S:
                drifted += 1

        # At least 80% of matched words should be within tolerance
        if matched > 0:
            drift_rate = drifted / matched
            self.assertLess(
                drift_rate, 0.2,
                f"{drifted}/{matched} words drifted > {TOLERANCE_S}s from golden",
            )

    def test_validate_alignment_passes(self):
        """The validate_alignment function finds no violations."""
        from openshelf.pipeline.word_aligner import validate_alignment
        violations = validate_alignment(
            self.live_words,
            self.metadata["audio_duration"],
            self.metadata["num_chunks"],
        )
        self.assertEqual(violations, [], f"Validation violations: {violations}")


if __name__ == "__main__":
    unittest.main()
