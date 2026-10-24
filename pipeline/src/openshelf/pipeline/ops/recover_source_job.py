"""Supervise one long standard Mandarin job without changing queue invariants."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import asdict, replace
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

import psutil

from openshelf.config import PROJECT_ROOT, TTS_SAMPLE_RATE
from openshelf.pipeline.build_recovery import adopt_local_build
from openshelf.pipeline.run_context import RunContext, load_run_context, validate_resume_context
from openshelf.pipeline.synthesis_checkpoints import SynthesisCheckpoints, read_completion, write_completion


class RecoveryStopped(RuntimeError):
    """A controlled, credential-free explanation safe for operator logs."""


def save_state(path: Path, state: dict) -> None:
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix(path.suffix + ".tmp")
    pending.write_text(json.dumps(state, indent=2), encoding="utf-8")
    pending.replace(path)


@contextmanager
def recovery_lock(state_file: Path):
    lock = state_file.with_suffix(state_file.suffix + ".lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    identity = {"pid": os.getpid(), "created": psutil.Process().create_time()}
    for attempt in range(2):
        try:
            stream = lock.open("x", encoding="utf-8")
            stream.write(json.dumps(identity))
            stream.close()
            break
        except FileExistsError:
            previous = json.loads(lock.read_text(encoding="utf-8"))
            try:
                alive = psutil.Process(previous["pid"]).create_time() == previous["created"]
            except psutil.NoSuchProcess:
                alive = False
            if alive or attempt:
                raise RecoveryStopped("A recovery supervisor already owns this state")
            lock.unlink()
    try:
        yield
    finally:
        if lock.exists() and json.loads(lock.read_text(encoding="utf-8")) == identity:
            lock.unlink()


def network_retry(operation):
    from openshelf.pipeline.job_monitor import MonitorError
    while True:
        try:
            return operation()
        except urllib.error.HTTPError as error:
            if error.code not in (429, 500, 502, 503, 504):
                raise
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            pass
        except MonitorError as error:
            if not any(text in str(error) for text in (
                "Could not reach", "temporarily unavailable", "rate limit", "HTTP 502", "HTTP 504",
            )):
                raise
        print("Production API unavailable; waiting before retry.", flush=True)
        time.sleep(30)


def has_targeted_claim_schema(document: dict) -> bool:
    def supports(schema):
        if "$ref" in schema:
            value = document
            for part in schema["$ref"].removeprefix("#/").split("/"):
                value = value[part]
            return supports(value)
        return "job_id" in schema.get("properties", {}) or any(supports(part) for part in schema.get("allOf", []))
    for path, operations in document.get("paths", {}).items():
        if path.endswith("/internal/generation-jobs/claim"):
            return supports(operations["post"]["requestBody"]["content"]["application/json"]["schema"])
    return False


def wait_for_targeted_claim(api_base: str) -> None:
    def read_spec():
        request = urllib.request.Request(api_base + "/openapi.json", headers={
            "User-Agent": "OpenShelf/1.0 (+https://github.com/jkoeh/openshelf)", "Cache-Control": "no-store",
        })
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    while not has_targeted_claim_schema(network_retry(read_spec)):
        print("Waiting for the deployed exact-job claim capability; no job was created.", flush=True)
        time.sleep(30)


def matching_producer(process: psutil.Process, build: str) -> bool:
    try:
        args = process.cmdline()
        return ("--build-id" in args and args[args.index("--build-id") + 1] == build
                and "--engine" in args and args[args.index("--engine") + 1] == "qwen3"
                and "openshelf.pipeline.dag.cli" in args and "run" in args)
    except (psutil.Error, IndexError):
        return False


def certify_completed_section(build: Path, sequence: int) -> dict:
    """Operator certification of a stable legacy section, in its exact runtime."""
    from openshelf.pipeline.voice_director import directed_chunks_from_chapter_direction_artifact
    from openshelf.pipeline.encoder import audio_duration
    from openshelf.pipeline.engines import create_aligner, create_engine
    from openshelf.pipeline.language import unit_count
    from openshelf.pipeline.tts import ChunkInfo, SynthesisResult, _section_synthesis_fingerprint
    from openshelf.pipeline.tts_engine import VoiceSpec, WordTimestamp

    context = load_run_context(build / "run.json")
    engine = create_engine(context["engine"], device="cuda", language="zh")
    if context["engine_settings"] != engine.synthesis_settings:
        raise ValueError("Legacy section runtime settings do not match")
    aligner = create_aligner(engine, device="cuda", language="zh")
    stem = f"section-{sequence:02d}"
    chunks = json.loads((build / f"{stem}.chunks.json").read_text(encoding="utf-8"))
    registry = json.loads((build / "character_registry.json").read_text(encoding="utf-8"))
    narrator = VoiceSpec(**registry["narrator_voice"])
    directed = directed_chunks_from_chapter_direction_artifact(str(build / f"{stem}.voice_direction.json"))
    infos = [ChunkInfo(chunk["text"],
                       index == len(chunks["chunks"]) - 1 or chunk["para_end"] != chunks["chunks"][index + 1]["para_start"],
                       directed[index]) for index, chunk in enumerate(chunks["chunks"])]
    heading = chunks["heading"]["spoken_text"]
    fingerprint = _section_synthesis_fingerprint(engine, aligner, infos, narrator, heading, TTS_SAMPLE_RATE)
    files = [str(build / f"{stem}{suffix}") for suffix in (".m4a", ".sync.json", ".synthesis_units.json")]
    existing = read_completion(build / f"{stem}.complete.json", fingerprint, files)
    if existing:
        return existing
    sync = json.loads(Path(files[1]).read_text(encoding="utf-8"))
    audit = json.loads(Path(files[2]).read_text(encoding="utf-8"))
    duration = audio_duration(files[0])
    # Coverage in the section sync artifact describes body chunks; headings
    # have their own independently aligned region.
    expected_count = sum(unit_count(info.text) for info in infos)
    if (sync["version"] != 2 or sync["sequence"] != sequence
            or sync["audio_filename"] != Path(files[0]).name
            or len(sync["chunks"]) != len(infos) or len(sync["chunk_audio_starts"]) != len(infos)
            or any(start < 0 or not math.isfinite(start) for start in sync["chunk_audio_starts"])
            or sync["coverage"]["reader_word_count"] != expected_count
            or sync["coverage"]["coverage_ratio"] < .98
            or audit["sequence"] != sequence or audit["sample_rate"] != TTS_SAMPLE_RATE
            or not math.isfinite(duration) or duration <= 0):
        raise ValueError("Legacy section is incomplete or has incompatible sync")
    words = [WordTimestamp(**word) for word in sync["heading_words"]]
    body = [[WordTimestamp(**word) for word in chunk["words"]] for chunk in sync["chunks"]]
    if (heading.strip() and not words) or any(info.text.strip() and not aligned for info, aligned in zip(infos, body)):
        raise ValueError("Legacy section has an unaligned spoken region")
    for word in [*words, *(word for chunk in body for word in chunk)]:
        if (not math.isfinite(word.start) or not math.isfinite(word.end)
                or word.start < 0 or word.end < word.start or word.end > duration + .1):
            raise ValueError("Legacy section timing exceeds audio")
    for chunk in audit["chunks"]:
        if chunk["start_frame"] < 0 or chunk["end_frame"] > (duration + .1) * TTS_SAMPLE_RATE:
            raise ValueError("Legacy section seam audit exceeds audio")
    subprocess.run(["ffmpeg", "-v", "error", "-i", files[0], "-f", "null", "-"],
                   check=True, timeout=300, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    result = SynthesisResult(duration, 0, sync["chunk_audio_starts"], body,
                             [SynthesisCheckpoints._audit(chunk) for chunk in audit["chunks"]], words)
    write_completion(build / f"{stem}.complete.json", fingerprint, files, asdict(result), duration)
    return {"duration": duration}


def stop_idle_consumer(owner, own_job_id=None) -> None:
    from openshelf.pipeline.job_consumer import terminate_child_tree
    from openshelf.pipeline.job_monitor import find_consumer
    active, _ = owner.list_jobs()
    if any(job.state == "running" and job.id != own_job_id for job in active):
        raise RecoveryStopped("Another managed audio job is running")
    consumer = find_consumer()
    if consumer:
        for process in [consumer, *consumer.children(recursive=True)]:
            try:
                if "consume-jobs" not in process.cmdline() and process.name().lower() != "conhost.exe":
                    raise RecoveryStopped("Consumer has an active child; recovery will not stop it")
            except psutil.NoSuchProcess:
                continue
        terminate_child_tree(consumer)


def wait_for_legacy_boundary(args, state, state_file: Path, runtime: str) -> None:
    from openshelf.pipeline.job_consumer import terminate_child_tree
    source = Path(args.source_build).resolve()
    if not args.wait_pid:
        return
    try:
        process = psutil.Process(args.wait_pid)
    except psutil.NoSuchProcess:
        return
    if not matching_producer(process, source.name):
        raise RecoveryStopped("Wait PID is not the exact source-build producer")
    created = process.create_time()
    if state.get("wait_created_at") not in (None, created):
        raise RecoveryStopped("Wait PID was reused by a different process")
    state["wait_created_at"] = created
    state["phase"] = "waiting_for_section_boundary"
    save_state(state_file, state)
    path = source / f"section-{args.after_section:02d}.m4a"
    previous = None
    stable_since = time.monotonic()
    while process.is_running():
        if not matching_producer(process, source.name):
            raise RecoveryStopped("Producer identity changed while waiting")
        try:
            snapshot = (path.stat().st_mtime, path.stat().st_size)
            encoding = any("ffmpeg" in child.name().lower() for child in process.children(recursive=True))
            if snapshot != previous or encoding:
                previous, stable_since = snapshot, time.monotonic()
            if (snapshot[0] >= created and snapshot[1] > 0 and not encoding
                    and time.monotonic() - stable_since >= 60):
                env = os.environ.copy()
                env["PYTHONPATH"] = str(PROJECT_ROOT / "pipeline" / "src")
                subprocess.run([runtime, "-m", "openshelf.pipeline.ops.recover_source_job", "--source-build", str(source),
                                "--certify-section", str(args.after_section)],
                               env=env, cwd=PROJECT_ROOT, check=True, timeout=360)
                outermost = process
                for parent in process.parents():
                    if matching_producer(parent, source.name):
                        outermost = parent
                    else:
                        break
                terminate_child_tree(outermost)
                print(f"Preserved completed section {args.after_section}; old producer stopped.", flush=True)
                return
        except FileNotFoundError:
            pass
        time.sleep(30)


class AdoptionLease:
    def __init__(self, api, job):
        self.api, self.job = api, job
        self.stop = threading.Event()
        self.error = None

    def __enter__(self):
        def renew():
            while not self.stop.wait(20):
                try:
                    self.api.post(f"/internal/generation-jobs/{self.job['id']}/heartbeat",
                                  {"lease_token": self.job["lease_token"]})
                except Exception as error:
                    self.error = error
                    return
        self.thread = threading.Thread(target=renew, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.stop.set()
        self.thread.join(timeout=35)
        if self.error:
            raise RecoveryStopped("Lease was lost during local artifact adoption") from self.error


def supervise(args) -> int:
    from openshelf.pipeline.job_consumer import JobAPI, mandarin_runtime, process_job
    from openshelf.pipeline.job_monitor import API_BASE, QueueAPI

    source = Path(args.source_build).resolve()
    context = RunContext(**load_run_context(source / "run.json"))
    if context.engine != "qwen3" or context.language != "zh" or context.voice != "qwen3-serena":
        raise ValueError("Recovery currently supports standard Mandarin Serena only")
    state_file = Path(args.state_file).resolve()
    state = json.loads(state_file.read_text(encoding="utf-8")) if state_file.exists() else {
        "version": 1, "source_id": args.source_id, "initial_build": str(source), "fresh_jobs": 0,
    }
    if state["source_id"] != args.source_id or state["initial_build"] != str(source):
        raise ValueError("Recovery state belongs to another source/build")
    if state.get("phase") == "completed":
        print("Recovery already completed.", flush=True)
        return 0
    state["phase"] = "starting"
    save_state(state_file, state)
    runtime = mandarin_runtime("cuda")
    if not runtime:
        raise RecoveryStopped("Pinned Mandarin CUDA runtime is not ready")
    class RetryingQueueAPI(QueueAPI):
        def _request(self, path, payload=None):
            return network_retry(lambda: super(RetryingQueueAPI, self)._request(path, payload))
    owner = RetryingQueueAPI()
    api = JobAPI(API_BASE, (PROJECT_ROOT / "worker/.secrets/prod-pc-token").read_text(encoding="utf-8").strip())
    stop_idle_consumer(owner, state.get("job_id"))
    if not state.get("boundary_done"):
        wait_for_legacy_boundary(args, state, state_file, runtime)
        state["boundary_done"] = True
        save_state(state_file, state)
    wait_for_targeted_claim(API_BASE)
    os.environ["OPENSHELF_REQUIRE_COMPLETE_AUDIO"] = "1"
    last_build = Path(state.get("current_build", str(source)))

    while True:
        active, recent = owner.list_jobs()
        managed = next((job for job in [*active, *recent] if job.id == state.get("job_id")), None)
        if managed and managed.state == "completed":
            state["phase"] = "completed"
            state["author_slug"], state["title_slug"] = context.author_slug, context.title_slug
            save_state(state_file, state)
            print(f"Published {args.source_id} through normal job completion.", flush=True)
            return 0
        if managed and managed.state == "canceled":
            state["phase"] = "canceled"
            save_state(state_file, state)
            return 1
        if managed and managed.state == "failed" and managed.attempts < 3:
            owner.retry(managed.id)
            print("Reserved a normal owner retry for the incomplete job.", flush=True)
        elif not managed or managed.state == "failed":
            if state["fresh_jobs"] >= args.max_new_jobs:
                raise RecoveryStopped("Recovery fresh-job limit reached; saved work remains intact")
            created = owner._request("/generation-jobs", {"source_id": args.source_id, "mode": "standard"})
            state["job_id"] = created["id"]
            state["fresh_jobs"] += 1
            state["phase"] = "queued"
            save_state(state_file, state)
            print(f"Created ordinary capped recovery job {created['id']}.", flush=True)
        job = network_retry(lambda: api.post("/internal/generation-jobs/claim", {
            "job_id": state["job_id"], "mandarin": True, "expressive": False, "documents": False,
        }))["job"]
        if not job:
            time.sleep(30)
            continue
        if job["id"] != state["job_id"] or job["source_id"] != args.source_id:
            raise RecoveryStopped("Worker did not honor the exact-job recovery claim")
        if job["language"] != "zh" or job["epub_sha256"] != context.epub_sha256:
            raise ValueError("Claimed source does not match the original validated EPUB")
        target = source.parent / job["build_id"]
        expected = replace(context, build=job["build_id"])
        try:
            with AdoptionLease(api, job):
                if not (target / "run.json").exists():
                    audit = adopt_local_build(last_build, target, expected)
                    print(f"Adopted {len(audit['sections'])} validated local sections/checkpoint groups.", flush=True)
                else:
                    validate_resume_context(load_run_context(target / "run.json"), expected)
        except (OSError, RuntimeError, ValueError) as error:
            state["last_error_class"] = type(error).__name__
            save_state(state_file, state)
            print(f"Adoption stopped ({type(error).__name__}); waiting for the normal lease to expire.", flush=True)
            time.sleep(30)
            continue
        last_build = target
        state["current_build"] = str(target)
        state["phase"] = "running"
        save_state(state_file, state)
        try:
            process_job(api, job, download_root=PROJECT_ROOT / "download/books",
                        audio_root=PROJECT_ROOT / "audio", device="cuda", mandarin_python=runtime)
        except (OSError, RuntimeError, ValueError) as error:
            state["last_error_class"] = type(error).__name__
            save_state(state_file, state)
            print(f"Generation stopped ({type(error).__name__}); validated checkpoints retained.", flush=True)
            time.sleep(30)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="openshelf-pipeline ops recover-source-job")
    parser.add_argument("--source-build", required=True)
    parser.add_argument("--source-id")
    parser.add_argument("--wait-pid", type=int)
    parser.add_argument("--after-section", type=int, default=2)
    parser.add_argument("--state-file")
    parser.add_argument("--max-new-jobs", type=int, default=3, choices=range(1, 6))
    parser.add_argument("--certify-section", type=int, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.certify_section is not None:
        certify_completed_section(Path(args.source_build).resolve(), args.certify_section)
        print(f"Certified completed section {args.certify_section}.", flush=True)
        return 0
    if not args.source_id or not args.state_file:
        parser.error("--source-id and --state-file are required for recovery")
    try:
        ownership = None
        if os.name == "nt":
            from openshelf.pipeline.job_monitor_desktop import ConsumerJob
            ownership = ConsumerJob()
            ownership.attach(psutil.Process())
        # Keep the non-inheritable Windows handle alive until interpreter exit;
        # the OS then kills any remaining child even after a supervisor crash.
        with recovery_lock(Path(args.state_file).resolve()):
            return supervise(args)
    except Exception as error:
        reason = str(error) if isinstance(error, RecoveryStopped) else type(error).__name__
        print(f"Recovery stopped safely: {reason}. Existing audio/checkpoints retained.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
