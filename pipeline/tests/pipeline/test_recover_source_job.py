import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from openshelf.pipeline.ops.recover_source_job import certify_completed_section, has_targeted_claim_schema, matching_producer, recovery_lock, stop_idle_consumer, supervise
from openshelf.pipeline.tts_engine import PostProcessingConfig
from openshelf.pipeline.run_context import RunContext, write_run_context


class RecoverySupervisorTests(unittest.TestCase):
    def test_idle_consumer_console_host_is_owned_but_synthesis_child_is_not_stopped(self):
        console = SimpleNamespace(cmdline=lambda: ["conhost.exe", "0x4"], name=lambda: "conhost.exe")
        consumer = SimpleNamespace(cmdline=lambda: ["python", "consume-jobs"],
                                   children=lambda **kwargs: [console])
        owner = SimpleNamespace(list_jobs=lambda: ([], []))
        with patch("openshelf.pipeline.job_monitor.find_consumer", return_value=consumer), \
                patch("openshelf.pipeline.job_consumer.terminate_child_tree") as stop:
            stop_idle_consumer(owner)
            stop.assert_called_once_with(consumer)
            console.name = lambda: "python.exe"
            with self.assertRaises(RuntimeError):
                stop_idle_consumer(owner)
            self.assertEqual(stop.call_count, 1)

    def test_requires_deployed_exact_job_claim_schema(self):
        spec = {"paths": {"/api/v1/internal/generation-jobs/claim": {"post": {
            "requestBody": {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/Claim"}}}},
        }}}, "components": {"schemas": {"Claim": {"properties": {"mandarin": {"type": "boolean"}}}}}}
        self.assertFalse(has_targeted_claim_schema(spec))
        spec["components"]["schemas"]["Claim"]["properties"]["job_id"] = {"type": "string", "format": "uuid"}
        self.assertTrue(has_targeted_claim_schema(spec))

    def test_legacy_certification_keeps_heading_out_of_body_coverage(self):
        with tempfile.TemporaryDirectory() as directory:
            build = Path(directory)
            (build / "run.json").write_text(json.dumps({"engine": "qwen3", "engine_settings": {"model": "pinned"}}), encoding="utf-8")
            (build / "character_registry.json").write_text(json.dumps({"narrator_voice": {"id": "qwen3-serena"}}), encoding="utf-8")
            (build / "section-01.chunks.json").write_text(json.dumps({"heading": {"spoken_text": "紅。"}, "chunks": []}), encoding="utf-8")
            (build / "section-01.voice_direction.json").write_text(json.dumps({"sections": [{"sequence": 1, "title": "片頭", "chunks": []}]}), encoding="utf-8")
            (build / "section-01.m4a").write_bytes(b"encoded fixture")
            (build / "section-01.sync.json").write_text(json.dumps({
                "version": 2, "sequence": 1, "audio_filename": "section-01.m4a", "chunk_audio_starts": [],
                "heading_words": [{"word": "紅", "start": .1, "end": .4}], "chunks": [],
                "coverage": {"reader_word_count": 0, "coverage_ratio": 1.0},
            }), encoding="utf-8")
            (build / "section-01.synthesis_units.json").write_text(json.dumps({
                "sequence": 1, "sample_rate": 24000, "chunks": [
                    {"chunk_index": -1, "start_frame": 1200, "end_frame": 12000, "segments": [], "seams": []},
                ],
            }), encoding="utf-8")
            engine = SimpleNamespace(name="fixture", synthesis_settings={"model": "pinned"},
                                     post_processing_config=lambda: PostProcessingConfig(True, 0, False))
            with patch("openshelf.pipeline.engines.create_engine", return_value=engine), \
                    patch("openshelf.pipeline.engines.create_aligner", return_value=object()), \
                    patch("openshelf.pipeline.encoder.audio_duration", return_value=1.0), \
                    patch("openshelf.pipeline.ops.recover_source_job.subprocess.run") as decode:
                result = certify_completed_section(build, 1)
            self.assertEqual(result["duration"], 1.0)
            self.assertTrue((build / "section-01.complete.json").exists())
            self.assertEqual(decode.call_args.args[0][0], "ffmpeg")

    def test_process_identity_requires_exact_dag_engine_and_build(self):
        process = SimpleNamespace(cmdline=lambda: ["python", "-m", "openshelf.pipeline.dag.cli", "run",
                                                  "--engine", "qwen3", "--build-id", "a" * 16])
        self.assertTrue(matching_producer(process, "a" * 16))
        self.assertFalse(matching_producer(process, "b" * 16))
        process.cmdline = lambda: ["python", "unrelated", "--build-id", "a" * 16]
        self.assertFalse(matching_producer(process, "a" * 16))

    def test_second_supervisor_cannot_own_live_state(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state.json"
            with recovery_lock(state):
                with self.assertRaises(RuntimeError):
                    with recovery_lock(state):
                        self.fail("duplicate supervisor entered")
            self.assertFalse(state.with_suffix(".json.lock").exists())

    def test_fresh_capped_job_adopts_then_uses_normal_process_and_finish(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "audio/author/title/audio/qwen3-serena/builds" / ("a" * 16)
            source.mkdir(parents=True)
            context = RunContext("a" * 16, "2", "book.epub", "sha256", "author", "title", "作者", "紅樓夢",
                                 "qwen3", "qwen3-serena", "qwen3-serena", "solo", "batched", "zh", [1],
                                 engine_settings={"model": "pinned"})
            write_run_context(source / "run.json", context)
            token_file = root / "worker/.secrets/prod-pc-token"
            token_file.parent.mkdir(parents=True)
            token_file.write_text("offline fixture credential", encoding="utf-8")
            store = {"state": "none", "create_count": 0, "claim_count": 0}
            identifier = "00000000-0000-4000-8000-000000000001"

            class Queue:
                def _request(self, path, payload=None):
                    self_path = "/generation-jobs"
                    if path != self_path or payload != {"source_id": "gutenberg:24264", "mode": "standard"}:
                        raise AssertionError("Unexpected owner mutation")
                    store["create_count"] += 1
                    store["state"] = "queued"
                    return {"id": identifier}

                def list_jobs(self):
                    job = SimpleNamespace(id=identifier, state=store["state"], attempts=1)
                    return ([], [] if store["state"] == "none" else [job])

            class API:
                def __init__(self, base, token):
                    pass

                def post(self, path, payload):
                    if path != "/internal/generation-jobs/claim" or payload["job_id"] != identifier:
                        raise AssertionError("Recovery claimed an unrelated job")
                    store["claim_count"] += 1
                    store["state"] = "running"
                    return {"job": {"id": identifier, "source_id": "gutenberg:24264", "language": "zh",
                                    "epub_sha256": "sha256", "build_id": "b" * 16, "lease_token": "lease"}}

            def finish(api, job, **kwargs):
                expected = source.parent / ("b" * 16)
                self.assertTrue((expected / "run.json").exists())
                self.assertTrue((expected / "recovery.json").exists())
                self.assertEqual(kwargs["mandarin_python"], "pinned-python")
                store["state"] = "completed"

            args = SimpleNamespace(source_build=str(source), source_id="gutenberg:24264",
                                   state_file=str(root / "audio/recovery.json"), wait_pid=None,
                                   after_section=2, max_new_jobs=3)
            with patch("openshelf.pipeline.ops.recover_source_job.PROJECT_ROOT", root), \
                    patch("openshelf.pipeline.ops.recover_source_job.stop_idle_consumer"), \
                    patch("openshelf.pipeline.ops.recover_source_job.wait_for_targeted_claim"), \
                    patch("openshelf.pipeline.job_monitor.QueueAPI", Queue), \
                    patch("openshelf.pipeline.job_consumer.JobAPI", API), \
                    patch("openshelf.pipeline.job_consumer.mandarin_runtime", return_value="pinned-python"), \
                    patch("openshelf.pipeline.job_consumer.process_job", side_effect=finish) as process, \
                    patch.dict("os.environ", {}, clear=False):
                self.assertEqual(supervise(args), 0)
            self.assertEqual(store["create_count"], 1)
            self.assertEqual(store["claim_count"], 1)
            process.assert_called_once()
            state = json.loads(Path(args.state_file).read_text(encoding="utf-8"))
            self.assertEqual(state["phase"], "completed")
            self.assertNotIn("lease_token", state)
