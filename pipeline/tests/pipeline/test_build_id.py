"""Tests for compute_build_id — the per-(rendition, voice, pipeline-version, config) hash
that identifies a coherent snapshot of pipeline output. See pipeline/docs/step6-r2.md.

The function is a pure transform: same inputs → same hash, any meaningful input
change → different hash. Output is the first 7 hex characters of a sha256, in the
spirit of `git rev-parse --short`.
"""

import os
import re
import sys
import unittest
from unittest.mock import patch

# Allow running without pip install
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from openshelf.pipeline.build import compute_build_id


SEVEN_HEX = re.compile(r"^[0-9a-f]{7}$")


class TestComputeBuildIdShape(unittest.TestCase):

    def test_output_is_seven_lowercase_hex_chars(self):
        h = compute_build_id(rendition="kokoro-af-heart", voice="af_heart")
        self.assertRegex(h, SEVEN_HEX)


class TestComputeBuildIdDeterminism(unittest.TestCase):

    def test_same_inputs_same_hash(self):
        a = compute_build_id(rendition="kokoro-af-heart", voice="af_heart")
        b = compute_build_id(rendition="kokoro-af-heart", voice="af_heart")
        self.assertEqual(a, b)


class TestComputeBuildIdSensitivity(unittest.TestCase):

    def test_different_rendition_different_hash(self):
        a = compute_build_id(rendition="kokoro-af-heart", voice="af_heart")
        b = compute_build_id(rendition="kokoro-bf-emma", voice="af_heart")
        self.assertNotEqual(a, b)

    def test_different_voice_different_hash(self):
        a = compute_build_id(rendition="kokoro-af-heart", voice="af_heart")
        b = compute_build_id(rendition="kokoro-af-heart", voice="bf_emma")
        self.assertNotEqual(a, b)

    def test_different_pipeline_version_different_hash(self):
        with patch("openshelf.pipeline.build.PIPELINE_VERSION", "1"):
            v1 = compute_build_id(rendition="kokoro-af-heart", voice="af_heart")
        with patch("openshelf.pipeline.build.PIPELINE_VERSION", "2"):
            v2 = compute_build_id(rendition="kokoro-af-heart", voice="af_heart")
        self.assertNotEqual(v1, v2)

    def test_different_chunk_max_words_different_hash(self):
        with patch("openshelf.pipeline.build.CHUNK_MAX_WORDS", 200):
            a = compute_build_id(rendition="kokoro-af-heart", voice="af_heart")
        with patch("openshelf.pipeline.build.CHUNK_MAX_WORDS", 150):
            b = compute_build_id(rendition="kokoro-af-heart", voice="af_heart")
        self.assertNotEqual(a, b)

    def test_different_lead_in_silence_different_hash(self):
        with patch("openshelf.pipeline.build.LEAD_IN_SILENCE_MS", 50):
            a = compute_build_id(rendition="kokoro-af-heart", voice="af_heart")
        with patch("openshelf.pipeline.build.LEAD_IN_SILENCE_MS", 80):
            b = compute_build_id(rendition="kokoro-af-heart", voice="af_heart")
        self.assertNotEqual(a, b)


if __name__ == "__main__":
    unittest.main()
