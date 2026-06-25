"""Tests for seam pause policy and no-TTS pause repair."""

import os
import sys
import unittest

import numpy as np

# Allow running without pip install
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from openshelf.pipeline.seams import (
    PausePolicy,
    repair_pcm_pauses,
)


class TestPausePolicy(unittest.TestCase):

    def test_classifies_sentence_boundary(self):
        policy = PausePolicy()

        self.assertEqual(policy.classify("painting them red.", "Alice thought"), "sentence")
        self.assertEqual(policy.pause_ms("sentence"), 250)

    def test_classifies_phrase_boundary(self):
        policy = PausePolicy()

        self.assertEqual(policy.classify("she said,", "and waited"), "phrase")
        self.assertEqual(policy.pause_ms("phrase"), 180)

    def test_paragraph_hint_wins(self):
        policy = PausePolicy()

        self.assertEqual(policy.classify("end.", "next", paragraph=True), "paragraph")
        self.assertEqual(policy.pause_ms("paragraph"), 400)


class TestRepairPcmPauses(unittest.TestCase):

    def test_replaces_recorded_pause_and_shifts_sync(self):
        sample_rate = 1000
        audio = np.ones(1000, dtype=np.float32)
        audio[400:520] = 0
        sync_payload = {
            "version": 2,
            "sequence": 1,
            "audio_filename": "section-01.m4a",
            "heading_words": [],
            "chunk_audio_starts": [0.0, 0.6],
            "coverage": {},
            "chunks": [
                {
                    "index": 0,
                    "words": [
                        {"word": "red.", "start": 0.35, "end": 0.4},
                        {"word": "Alice", "start": 0.52, "end": 0.6},
                    ],
                },
                {
                    "index": 1,
                    "words": [
                        {"word": "Later", "start": 0.62, "end": 0.7},
                    ],
                },
            ],
        }
        synthesis_payload = {
            "version": 2,
            "sequence": 1,
            "audio_filename": "section-01.m4a",
            "sample_rate": sample_rate,
            "policy": "old",
            "chunks": [
                {
                    "chunk_index": 0,
                    "start_frame": 0,
                    "end_frame": 1000,
                    "segments": [
                        {
                            "segment_index": 0,
                            "speaker": "narrator",
                            "emotion": "neutral",
                            "start_frame": 0,
                            "end_frame": 1000,
                            "units": [
                                {
                                    "unit_index": 0,
                                    "text": "red.",
                                    "start_frame": 0,
                                    "end_frame": 400,
                                },
                                {
                                    "unit_index": 1,
                                    "text": "Alice",
                                    "start_frame": 520,
                                    "end_frame": 1000,
                                },
                            ],
                        },
                    ],
                    "seams": [
                        {
                            "kind": "engine_unit",
                            "break_type": "sentence",
                            "after_chunk_index": 0,
                            "after_segment_index": 0,
                            "after_unit_index": 0,
                            "pause_start_frame": 400,
                            "pause_end_frame": 520,
                            "pause_ms": 120,
                            "before_text": "red.",
                            "after_text": "Alice",
                        },
                    ],
                },
            ],
        }

        repaired_audio, updated_sync, updated_synthesis, edits = repair_pcm_pauses(
            audio,
            sync_payload,
            synthesis_payload,
            chunk_texts=["red. Alice", "Later"],
        )

        self.assertEqual(len(edits), 1)
        self.assertEqual(len(repaired_audio), 1130)
        self.assertEqual(updated_synthesis["chunks"][0]["seams"][0]["pause_ms"], 250)
        self.assertEqual(updated_synthesis["chunks"][0]["seams"][0]["pause_end_frame"], 650)
        self.assertAlmostEqual(updated_sync["chunk_audio_starts"][1], 0.73)
        self.assertAlmostEqual(updated_sync["chunks"][0]["words"][1]["start"], 0.65)
        self.assertAlmostEqual(updated_sync["chunks"][1]["words"][0]["start"], 0.75)


if __name__ == "__main__":
    unittest.main()
