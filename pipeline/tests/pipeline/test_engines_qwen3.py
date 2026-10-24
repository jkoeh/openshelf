"""Offline Mandarin adapter and immutable model identity checks."""

from pathlib import Path
import os
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))
from openshelf.pipeline.engines import create_engine, create_aligner
from openshelf.pipeline.engines.qwen3 import Qwen3Adapter, MODEL_ID, MODEL_REVISION, NARRATION_INSTRUCTION
from openshelf.pipeline.tts_engine import DirectedSegment, VoiceSpec
from openshelf.pipeline import job_consumer, books
from openshelf.pipeline.run_context import make_run_context, validate_resume_context, RunContextError


class Qwen3Tests(unittest.TestCase):
    def test_mandarin_factory_and_voice_policy(self):
        engine = create_engine("qwen3", language="zh-Hant")
        self.assertEqual(create_aligner(engine, language="zh").language, "zh")
        self.assertIn("qwen3-serena", [v.id for v in engine.available_voices()])
        with self.assertRaisesRegex(ValueError, "Chinese"):
            create_engine("qwen3", language="en")
        with self.assertRaisesRegex(ValueError, "voice"):
            engine.synthesize(DirectedSegment("中文", VoiceSpec("af_heart"), "narrator"))

    def test_original_text_pinned_instruction_and_real_audio_contract(self):
        runtime = MagicMock()
        runtime.generate_custom_voice.return_value = ([np.ones(100, dtype=np.float32)], 24000)
        engine = Qwen3Adapter(runtime=runtime, device="cpu")
        segment = DirectedSegment("中文，原樣保留。", VoiceSpec("qwen3-serena"), "narrator", engine_controls={"instruction": "untrusted"})
        with patch.dict(sys.modules, {"torch": MagicMock()}):
            result = engine.synthesize(segment)
        self.assertIsNone(result.words)
        self.assertEqual(result.sample_rate, 24000)
        self.assertEqual(runtime.generate_custom_voice.call_args.kwargs, {
            "text": segment.text, "language": "Chinese", "speaker": "Serena",
            "instruct": NARRATION_INSTRUCTION, "max_new_tokens": 2048,
        })
        self.assertEqual(engine.apply_performance_controls(segment).engine_controls["revision"], MODEL_REVISION)
        for waves, rate in [([np.array([np.nan])], 24000), ([np.zeros(100)], 24000), ([np.ones(100)], 22050)]:
            runtime.generate_custom_voice.return_value = (waves, rate)
            with patch.dict(sys.modules, {"torch": MagicMock()}), self.assertRaises(ValueError):
                engine.synthesize(segment)

    def test_lazy_runtime_load_pins_checkpoint(self):
        fake = MagicMock()
        with patch.dict(sys.modules, {"qwen_tts": fake, "torch": MagicMock()}):
            engine = Qwen3Adapter(device="cuda")
            fake.Qwen3TTSModel.from_pretrained.assert_not_called()
            engine._runtime()
        call = fake.Qwen3TTSModel.from_pretrained.call_args
        self.assertEqual(call.args, (MODEL_ID,))
        self.assertEqual(call.kwargs["revision"], MODEL_REVISION)
        self.assertEqual(call.kwargs["attn_implementation"], "sdpa")

    def test_resume_rejects_model_setting_changes_and_accepts_legacy_empty_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "book.epub"
            path.write_bytes(b"epub")
            fields = dict(build="1234567890abcdef", pipeline_version="2", epub_path=str(path),
                          author_slug="author", title_slug="title", book_author="作者", book_title="書", engine="qwen3",
                          voice="qwen3-serena", rendition="qwen3-serena", cast_mode="solo", performance_direction_mode="off",
                          language="zh", sections=[1])
            expected = make_run_context(**fields, engine_settings={"revision": MODEL_REVISION})
            previous = expected.to_dict()
            previous["engine_settings"] = {"revision": "different"}
            with self.assertRaisesRegex(RunContextError, "engine_settings"):
                validate_resume_context(previous, expected)
            legacy = make_run_context(**fields)
            previous = legacy.to_dict()
            del previous["engine_settings"]
            validate_resume_context(previous, legacy)

    def test_runtime_capability_probe_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "python.exe"
            executable.touch()
            with patch.dict(os.environ, {"OPENSHELF_MANDARIN_PYTHON": str(executable)}), \
                    patch.object(job_consumer.subprocess, "run", return_value=SimpleNamespace(returncode=0)) as run:
                self.assertEqual(job_consumer.mandarin_runtime("auto"), str(executable))
                self.assertEqual(run.call_args.kwargs["timeout"], 30)
                run.return_value.returncode = 1
                self.assertIsNone(job_consumer.mandarin_runtime("auto"))

    def test_catalog_wrapper_forwards_documented_language_and_editions(self):
        with patch("openshelf.pipeline.source_catalog.main", return_value=0) as sync:
            self.assertEqual(books.main(["sync-catalog", "--source", "wikisource", "--language", "zh",
                                        "--editions", "editions.json", "--api-base", "https://example.com/api/v1", "--dry-run"]), 0)
        passed = sync.call_args.args[0]
        self.assertEqual(passed[passed.index("--language") + 1], "zh")
        self.assertEqual(passed[passed.index("--editions") + 1], "editions.json")

    def test_reading_download_identifies_pc_without_public_credentials(self):
        opener = MagicMock()
        opener.open.return_value.__enter__.return_value.read.return_value = b"PK\x03\x04test"
        api = job_consumer.JobAPI("https://example.com/api/v1", "s" * 32)
        with patch.object(job_consumer.urllib.request, "build_opener", return_value=opener), \
                patch.object(job_consumer, "_store_epub"):
            self.assertTrue(api.download_source_epub("gutenberg:24264", Path("test.epub")))
        request = opener.open.call_args.args[0]
        self.assertIn("OpenShelf", request.get_header("User-agent"))
        self.assertIsNone(request.get_header("Authorization"))
