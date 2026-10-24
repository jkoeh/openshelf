"""Offline checks of benchmark measurements and real alignment failure handling."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))
from openshelf.pipeline.qa import tts_benchmark as benchmark
from openshelf.pipeline.word_aligner import WordEntry


def args(directory, **overrides):
    values = dict(output_dir=directory, engine="qwen3", language="zh", voice="Serena",
                  sample="prose", device="cpu", seed=1337, revision="abc123", overwrite=False)
    values.update(overrides)
    return argparse.Namespace(**values)


class BenchmarkTests(unittest.TestCase):
    def make_report(self, directory):
        with patch.object(benchmark, "_load_runtime", return_value=(lambda _: (np.ones(2400) * 0.1, 24000), {"id": "fake"})):
            return benchmark.run_benchmark(args(directory))

    def test_measurements_hashes_and_refusal_to_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            report = self.make_report(directory)
            sample = report["samples"][0]
            self.assertEqual(sample["text"], benchmark.SAMPLES["zh"]["prose"])
            self.assertEqual(sample["audio_seconds"], 0.1)
            self.assertEqual(sample["wav_sha256"], benchmark._hash((Path(directory) / sample["wav"]).read_bytes()))
            self.assertGreaterEqual(sample["rtf"], 0)
            with self.assertRaises(FileExistsError):
                benchmark.run_benchmark(args(directory))

    def test_invalid_audio_is_not_published_as_success(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(benchmark, "_load_runtime", return_value=(lambda _: (np.array([np.nan]), 24000), {})):
                with self.assertRaisesRegex(ValueError, "Invalid"):
                    benchmark.run_benchmark(args(directory))
            report = json.loads((Path(directory) / "report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["samples"], [])

    def test_empty_alignment_fails_and_tampering_is_detected(self):
        with tempfile.TemporaryDirectory() as directory:
            self.make_report(directory)
            with patch("openshelf.pipeline.word_aligner.align_chapter", return_value=[]):
                self.assertFalse(benchmark.align_report(Path(directory), "cpu"))
            report_path = Path(directory) / "report.json"
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertIn("No aligned words", report["samples"][0]["alignment"]["violations"])
            report["samples"][0]["text"] += "changed"
            report_path.write_text(json.dumps(report), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "changed"):
                benchmark.align_report(Path(directory), "cpu")

    def test_original_text_and_gaps_survive_highlighting(self):
        with tempfile.TemporaryDirectory() as directory:
            self.make_report(directory)
            with patch("openshelf.pipeline.word_aligner.align_chapter", return_value=[WordEntry("雨", 0, 0.05, 0)]):
                self.assertTrue(benchmark.align_report(Path(directory), "cpu"))
            page = (Path(directory) / "index.html").read_text(encoding="utf-8")
            self.assertIn('>雨</span>停以后，林先生', page)
            self.assertNotIn('>雨</span> 停', page)

    def test_qwen_uses_customvoice_checkpoint_and_explicit_language(self):
        torch = MagicMock()
        qwen = MagicMock()
        runtime = qwen.Qwen3TTSModel.from_pretrained.return_value
        runtime.get_supported_speakers.return_value = ["serena"]
        runtime.model.config._commit_hash = "immutable-commit"
        runtime.model.config.to_dict.return_value = {}
        runtime.generate_custom_voice.return_value = ([np.ones(100)], 24000)
        with patch.dict(sys.modules, {"qwen_tts": qwen}):
            synthesize, model = benchmark._load_runtime(args("unused", device="cuda:0"), torch)
            synthesize("中文，原样保留。")
        kwargs = qwen.Qwen3TTSModel.from_pretrained.call_args.kwargs
        self.assertEqual(kwargs["attn_implementation"], "sdpa")
        self.assertEqual(kwargs["revision"], "abc123")
        self.assertEqual(model["resolved_revision"], "immutable-commit")
        generated = runtime.generate_custom_voice.call_args.kwargs
        self.assertEqual(generated["language"], "Chinese")
        self.assertEqual(generated["text"], "中文，原样保留。")


if __name__ == "__main__":
    unittest.main()
