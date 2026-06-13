"""Safety wrapper around process-books.py."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Sequence

from openshelf.config import CAST_MODE, PERFORMANCE_DIRECTION_MODE, PROJECT_ROOT, TTS_ENGINE
from openshelf.pipeline.gpu_preflight import (
    format_gpu_preflight_report,
    run_gpu_preflight,
)


def build_process_books_command(args: argparse.Namespace, resolved_device: str) -> list[str]:
    script = PROJECT_ROOT / "pipeline" / "scripts" / "process-books.py"
    cmd = [sys.executable, str(script)]
    for flag in ("author", "book", "epub", "source", "output", "engine", "voice", "rendition"):
        value = getattr(args, flag, None)
        if value:
            cmd += [f"--{flag.replace('_', '-')}", str(value)]
    for flag in ("cast_mode", "performance_direction"):
        value = getattr(args, flag, None)
        if value:
            cmd += [f"--{flag.replace('_', '-')}", str(value)]
    if resolved_device:
        cmd += ["--device", resolved_device]
    if getattr(args, "chapters", None):
        cmd += ["--chapters", args.chapters]
    if getattr(args, "dry_run", False):
        cmd.append("--dry-run")
    if getattr(args, "keep_wav", False):
        cmd.append("--keep-wav")
    if getattr(args, "upload", False):
        cmd.append("--upload")
    if getattr(args, "build_id", None):
        cmd += ["--build-id", args.build_id]
    if getattr(args, "resume", False):
        cmd.append("--resume")
    if getattr(args, "force", False):
        cmd.append("--force")
    if getattr(args, "delay", None) is not None:
        cmd += ["--delay", str(args.delay)]
    if getattr(args, "download_dir", None):
        cmd += ["--download-dir", args.download_dir]
    if getattr(args, "log_dir", None):
        cmd += ["--log-dir", args.log_dir]
    return cmd


def run_pipeline(args: argparse.Namespace) -> int:
    if not args.author and not args.book and not args.epub:
        raise ValueError("at least one of --author, --book, or --epub is required")

    resolved_device = args.device
    if not args.skip_preflight:
        report = run_gpu_preflight(
            args.engine,
            args.device,
            load_engine=args.load_engine_preflight,
        )
        print(format_gpu_preflight_report(report))
        if not report.ok:
            return 2
        resolved_device = report.resolved_device
    elif args.device == "auto":
        resolved_device = ""

    cmd = build_process_books_command(args, resolved_device)
    print("Running:", _quote_command(cmd))

    if args.background:
        return _start_background(cmd, args)

    return subprocess.run(cmd).returncode


def _start_background(cmd: list[str], args: argparse.Namespace) -> int:
    log_dir = Path(args.log_dir or "logs")
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    name = args.name or "pipeline-run"
    stdout_path = Path(args.stdout_log or log_dir / f"{stamp}-{name}.out.log")
    stderr_path = Path(args.stderr_log or log_dir / f"{stamp}-{name}.err.log")
    pid_path = Path(args.pid_file or log_dir / f"{stamp}-{name}.pid")

    stdout = stdout_path.open("w", encoding="utf-8")
    stderr = stderr_path.open("w", encoding="utf-8")
    env = os.environ.copy()
    src_dir = str(PROJECT_ROOT / "pipeline" / "src")
    env["PYTHONPATH"] = (
        src_dir if not env.get("PYTHONPATH") else src_dir + os.pathsep + env["PYTHONPATH"]
    )
    process = subprocess.Popen(cmd, stdout=stdout, stderr=stderr, env=env)
    pid_path.write_text(str(process.pid), encoding="utf-8")
    stdout.close()
    stderr.close()
    print(f"Started background pipeline PID {process.pid}")
    print(f"PID file: {pid_path}")
    print(f"stdout:   {stdout_path}")
    print(f"stderr:   {stderr_path}")
    return 0


def _quote_command(cmd: Sequence[str]) -> str:
    return " ".join(f'"{part}"' if " " in part else part for part in cmd)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run process-books.py with GPU preflight and optional background logs"
    )
    parser.add_argument("--author", help="Filter by author name")
    parser.add_argument("--book", help="Filter by book title")
    parser.add_argument("--epub", help="Path to a local EPUB file")
    parser.add_argument("--source", default="all", choices=["gutenberg", "standard-ebooks", "all"])
    parser.add_argument("--output", "-o", default="audio")
    parser.add_argument("--engine", default=TTS_ENGINE)
    parser.add_argument("--voice", default=None)
    parser.add_argument("--rendition", default=None)
    parser.add_argument("--cast-mode", default=CAST_MODE, choices=["solo", "multicast"])
    parser.add_argument(
        "--performance-direction",
        default=PERFORMANCE_DIRECTION_MODE,
        choices=["batched", "chunk", "off"],
    )
    parser.add_argument(
        "--device",
        default="auto",
        choices=["auto", "cuda", "mps", "cpu"],
        help="Device request for preflight and process-books.py (default: auto)",
    )
    parser.add_argument("--chapters", default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--keep-wav", action="store_true")
    parser.add_argument("--upload", action="store_true")
    parser.add_argument("--build-id", default=None)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--delay", type=float, default=2)
    parser.add_argument("--download-dir", default="download/books")
    parser.add_argument("--log-dir", default="logs")
    parser.add_argument("--skip-preflight", action="store_true")
    parser.add_argument("--load-engine-preflight", action="store_true")
    parser.add_argument("--background", action="store_true")
    parser.add_argument("--name", default=None, help="Background log/PID filename stem")
    parser.add_argument("--pid-file", default=None)
    parser.add_argument("--stdout-log", default=None)
    parser.add_argument("--stderr-log", default=None)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        return run_pipeline(args)
    except ValueError as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
