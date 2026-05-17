"""Tests for new_build_id — the per-pipeline-run identifier for an R2 build prefix.

Each pipeline run mints a fresh random 16-hex string. No content addressing,
no input dependence — see pipeline/docs/step6-r2.md and the rendition-vs-build
section in the root CLAUDE.md.
"""

import os
import re
import sys
import unittest

# Allow running without pip install
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from openshelf.pipeline.build import new_build_id


SIXTEEN_HEX = re.compile(r"^[0-9a-f]{16}$")


class TestNewBuildIdShape(unittest.TestCase):

    def test_output_is_sixteen_lowercase_hex_chars(self):
        self.assertRegex(new_build_id(), SIXTEEN_HEX)


class TestNewBuildIdUniqueness(unittest.TestCase):

    def test_two_calls_return_different_ids(self):
        self.assertNotEqual(new_build_id(), new_build_id())

    def test_one_thousand_calls_have_no_collisions(self):
        ids = {new_build_id() for _ in range(1000)}
        self.assertEqual(len(ids), 1000)


class TestNewBuildIdSignature(unittest.TestCase):

    def test_takes_no_arguments(self):
        # If someone tries to thread (rendition, voice) back in, the signature
        # should reject it — the whole point of this slice is that build_id is
        # independent of any input.
        with self.assertRaises(TypeError):
            new_build_id("kokoro-af-heart", "af_heart")  # type: ignore[call-arg]


if __name__ == "__main__":
    unittest.main()
