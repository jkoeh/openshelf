"""User-facing book workflows for the OpenShelf pipeline CLI."""

from __future__ import annotations

import argparse
import glob
import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Sequence

from openshelf.config import CAST_MODE, PERFORMANCE_DIRECTION_MODE, PROJECT_ROOT, TTS_ENGINE
from openshelf.pipeline.logging_utils import (
    Heartbeat,
    configure_console_output,
    configure_pipeline_logging,
)
from openshelf.pipeline.ops.gpu_preflight import (
    format_gpu_preflight_report,
    run_gpu_preflight,
)
from openshelf.scrapers.gutenberg import gutenberg_search
from openshelf.scrapers.http import download_book, matches_filter, sanitize
from openshelf.scrapers.standard_ebooks import se_search


logger = logging.getLogger("openshelf.pipeline.books")
DEFAULT_DOWNLOAD_DIR = str(PROJECT_ROOT / "download" / "books")


def run_gutenberg(args: argparse.Namespace) -> None:
    """Download from Project Gutenberg."""
    source_dir = os.path.join(args.output, "gutenberg")
    total = 0
    failed = 0

    for author_name, title, epub_url in gutenberg_search(
        author=args.author,
        language=args.language,
        subject=args.subject,
        delay=args.delay,
    ):
        total += 1
        ok = download_book(
            author_name,
            title,
            epub_url,
            source_dir,
            delay=args.delay,
            dry_run=args.dry_run,
        )
        if not ok and not args.dry_run:
            failed += 1

    print(f"\n  Gutenberg: {total} books found", end="")
    if not args.dry_run:
        print(f", {failed} failed", end="")
    print()


def run_standard_ebooks(args: argparse.Namespace) -> None:
    """Download from Standard Ebooks."""
    source_dir = os.path.join(args.output, "standard-ebooks")
    total = 0
    failed = 0

    for author_name, title, epub_url in se_search(
        author=args.author,
        language=args.language,
        delay=args.delay,
    ):
        total += 1
        ok = download_book(
            author_name,
            title,
            epub_url,
            source_dir,
            delay=args.delay,
            dry_run=args.dry_run,
        )
        if not ok and not args.dry_run:
            failed += 1

    print(f"\n  Standard Ebooks: {total} books found", end="")
    if not args.dry_run:
        print(f", {failed} failed", end="")
    print()


def download_books(args: argparse.Namespace) -> int:
    """Scraper-only download workflow."""
    if args.source in ("gutenberg", "all"):
        print("=== Project Gutenberg ===")
        run_gutenberg(args)

    if args.source in ("standard-ebooks", "all"):
        print("\n=== Standard Ebooks ===")
        run_standard_ebooks(args)

    print("\nDone.")
    return 0


def search_and_download(args: argparse.Namespace) -> list[tuple[str, str]]:
    """Search sources and download matching EPUBs.

    Returns a list of ``(epub_path, source_name)`` pairs.
    """
    downloaded: list[tuple[str, str]] = []
    query = args.author or args.book
    logger.info("Starting search source=%s query=%r", args.source, query)

    if args.source in ("gutenberg", "all"):
        source_name = "gutenberg"
        source_dir = os.path.join(args.download_dir, source_name)
        print("=== Project Gutenberg ===")
        with Heartbeat(logger, "Searching Project Gutenberg"):
            for author_name, title, epub_url in gutenberg_search(author=query, delay=args.delay):
                if not matches_filter(args.book, title):
                    logger.debug("Skipping Gutenberg title filter: %s", title)
                    continue
                if not matches_filter(args.author, author_name):
                    logger.debug("Skipping Gutenberg author filter: %s", author_name)
                    continue
                logger.info("Matched Gutenberg book: %s - %s", author_name, title)
                print(f"  {author_name} - {title}")
                ok = download_book(author_name, title, epub_url, source_dir, delay=args.delay)
                if ok:
                    epub_path = _downloaded_epub_path(source_dir, author_name, title)
                    if os.path.isfile(epub_path):
                        downloaded.append((epub_path, source_name))

    if args.source in ("standard-ebooks", "all"):
        source_name = "standard-ebooks"
        source_dir = os.path.join(args.download_dir, source_name)
        print("\n=== Standard Ebooks ===")
        with Heartbeat(logger, "Searching Standard Ebooks"):
            for author_name, title, epub_url in se_search(author=query, delay=args.delay):
                if not matches_filter(args.book, title):
                    logger.debug("Skipping Standard Ebooks title filter: %s", title)
                    continue
                if not matches_filter(args.author, author_name):
                    logger.debug("Skipping Standard Ebooks author filter: %s", author_name)
                    continue
                logger.info("Matched Standard Ebooks book: %s - %s", author_name, title)
                print(f"  {author_name} - {title}")
                ok = download_book(author_name, title, epub_url, source_dir, delay=args.delay)
                if ok:
                    epub_path = _downloaded_epub_path(source_dir, author_name, title)
                    if os.path.isfile(epub_path):
                        downloaded.append((epub_path, source_name))

    logger.info("Search/download complete: %d book(s)", len(downloaded))
    return downloaded


def _downloaded_epub_path(source_dir: str, author_name: str, title: str) -> str:
    author_slug = sanitize(author_name)
    title_slug = sanitize(title) or "untitled"
    return os.path.join(source_dir, author_slug, title_slug + ".epub")


def convert_book(epub_path: str, source_name: str, args: argparse.Namespace) -> int:
    """Run the DAG full-book runner on a single EPUB."""
    src_dir = str(PROJECT_ROOT / "pipeline" / "src")
    cmd = [
        sys.executable,
        "-m",
        "openshelf.pipeline.dag.cli",
        "run",
        "--epub",
        epub_path,
        "--source",
        source_name,
        "--output",
        args.output,
    ]

    _append_optional(cmd, "--engine", getattr(args, "engine", None))
    _append_optional(cmd, "--voice", getattr(args, "voice", None))
    _append_optional(cmd, "--rendition", getattr(args, "rendition", None))
    _append_optional(cmd, "--cast-mode", getattr(args, "cast_mode", None))
    _append_optional(cmd, "--performance-direction", getattr(args, "performance_direction", None))
    _append_optional(cmd, "--device", getattr(args, "device", None))
    _append_optional(cmd, "--chapters", getattr(args, "chapters", None))
    _append_optional(cmd, "--build-id", getattr(args, "build_id", None))

    if args.keep_wav:
        cmd.append("--keep-wav")
    if args.upload:
        cmd.append("--upload")
    if args.dry_run:
        cmd.append("--dry-run")
    if getattr(args, "resume", False):
        cmd.append("--resume")
    if getattr(args, "new_voice_direction", False):
        cmd.append("--new-voice-direction")
    if args.force:
        cmd.append("--force")
    cmd += ["--log-dir", args.log_dir]

    env = os.environ.copy()
    prior_pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        src_dir if not prior_pythonpath else src_dir + os.pathsep + prior_pythonpath
    )
    logger.info("Starting DAG runner subprocess: %s", _quote_command(cmd))
    return subprocess.run(cmd, env=env).returncode


def refresh_catalog(args: argparse.Namespace | None = None) -> int:
    """Refresh catalog.json after successful uploads."""
    from openshelf.pipeline.ops.catalog import build_and_upload_catalog

    build_and_upload_catalog(dry_run=False)
    return 0


def find_local_epub(author: str, title: str, download_dir: str) -> str | None:
    """Return the local EPUB path for ``author`` and ``title`` if present."""
    author_slug = sanitize(author)
    title_slug = sanitize(title)

    all_epubs = glob.glob(os.path.join(download_dir, "*", "*", "*.epub"))
    matches = [
        p for p in all_epubs
        if author_slug in os.path.basename(os.path.dirname(p)).lower()
        and title_slug in os.path.basename(p).lower()
    ]
    return matches[0] if matches else None


def process_books(args: argparse.Namespace, parser: argparse.ArgumentParser | None = None) -> int:
    """Download/process one or more books through the DAG runner."""
    if not args.author and not args.book and not args.epub:
        if parser:
            parser.error("at least one of --author, --book, or --epub is required")
        raise ValueError("at least one of --author, --book, or --epub is required")

    if args.background:
        resolved_device = _resolve_process_device(args)
        cmd = build_process_command(args, resolved_device, skip_preflight=True)
        print("Running:", _quote_command(cmd))
        return _start_background(cmd, args)

    configure_console_output()
    run_name = "books-process"
    if args.author:
        run_name += f"-{sanitize(args.author)}"
    if args.book:
        run_name += f"-{sanitize(args.book)}"
    log_path = configure_pipeline_logging(run_name, args.log_dir)
    print(f"Log file: {log_path}")
    logger.info("books process started")

    args.device = _resolve_process_device(args)

    if args.epub:
        epub_path = os.path.abspath(args.epub)
        if not os.path.isfile(epub_path):
            print(f"Error: file not found: {epub_path}")
            logger.error("Local EPUB not found: %s", epub_path)
            return 1
        source_name = "local"
        for src in ("gutenberg", "standard-ebooks"):
            if src in epub_path:
                source_name = src
                break
        downloaded = [(epub_path, source_name)]
        print(f"Using local EPUB: {epub_path}\n")
        logger.info("Using local EPUB: %s source=%s", epub_path, source_name)
    else:
        print("Phase 1: Downloading books ...\n")
        downloaded = search_and_download(args)
        if not downloaded:
            print("\nNo books downloaded.")
            logger.warning("No books downloaded")
            return 1
        print(f"\n{len(downloaded)} book(s) downloaded.\n")

    print("Phase 2: Converting books ...\n")
    succeeded = 0
    failed = 0

    for i, (epub_path, source_name) in enumerate(downloaded, 1):
        print(f"\n{'=' * 60}")
        print(f"[{i}/{len(downloaded)}] {epub_path}")
        print(f"{'=' * 60}\n")

        rc = convert_book(epub_path, source_name, args)
        if rc == 0:
            succeeded += 1
            logger.info("DAG run succeeded: %s", epub_path)
        else:
            failed += 1
            logger.error("DAG run failed rc=%s: %s", rc, epub_path)
            print(f"\nError: DAG runner exited with code {rc}")

    print(f"\n{'=' * 60}")
    print(f"Done. {succeeded} succeeded, {failed} failed out of {len(downloaded)} book(s).")
    logger.info("books process complete: %d succeeded, %d failed", succeeded, failed)
    if failed:
        return 1

    if args.upload and succeeded:
        print("\nRefreshing catalog.json ...")
        try:
            rc = refresh_catalog(args)
        except Exception as exc:
            logger.exception("catalog refresh failed")
            print(f"Error: catalog refresh failed: {type(exc).__name__}: {exc}")
            return 1
        if rc != 0:
            logger.error("catalog refresh failed rc=%s", rc)
            print(f"Error: catalog refresh exited with code {rc}")
            return rc
        logger.info("catalog refresh succeeded")
        print("Catalog refreshed.")

    return 0


def reprocess_book(args: argparse.Namespace, parser: argparse.ArgumentParser | None = None) -> int:
    """Find an already-downloaded EPUB and process it with upload + force."""
    epub_path = find_local_epub(args.author, args.title, args.download_dir)
    if not epub_path:
        print(
            f"Error: no local EPUB found for {args.author} / {args.title} under {args.download_dir}",
            file=sys.stderr,
        )
        print(
            "Hint: run openshelf-pipeline books process to download it first.",
            file=sys.stderr,
        )
        return 1

    print(f"Reprocessing local EPUB: {epub_path}\n")
    process_args = argparse.Namespace(
        author=None,
        book=None,
        epub=epub_path,
        source="all",
        output=args.output,
        engine=args.engine,
        voice=args.voice,
        rendition=args.rendition,
        cast_mode=args.cast_mode,
        performance_direction=args.performance_direction,
        device=args.device,
        chapters=args.chapters,
        dry_run=False,
        keep_wav=args.keep_wav,
        upload=True,
        build_id=args.build_id,
        resume=False,
        force=True,
        new_voice_direction=args.new_voice_direction,
        delay=args.delay,
        download_dir=args.download_dir,
        log_dir=args.log_dir,
        skip_preflight=args.skip_preflight,
        load_engine_preflight=args.load_engine_preflight,
        background=args.background,
        name=args.name,
        pid_file=args.pid_file,
        stdout_log=args.stdout_log,
        stderr_log=args.stderr_log,
    )
    return process_books(process_args, parser)


def build_process_command(
    args: argparse.Namespace,
    resolved_device: str | None,
    *,
    skip_preflight: bool,
) -> list[str]:
    """Build a re-runnable ``books process`` command for background execution."""
    cmd = [
        sys.executable,
        "-m",
        "openshelf.pipeline.cli",
        "books",
        "process",
    ]
    for flag in ("author", "book", "epub", "source", "output", "engine", "voice", "rendition"):
        _append_optional(cmd, f"--{flag.replace('_', '-')}", getattr(args, flag, None))
    for flag in ("cast_mode", "performance_direction"):
        _append_optional(cmd, f"--{flag.replace('_', '-')}", getattr(args, flag, None))
    _append_optional(cmd, "--device", resolved_device)
    _append_optional(cmd, "--chapters", getattr(args, "chapters", None))
    _append_optional(cmd, "--build-id", getattr(args, "build_id", None))

    for flag in ("dry_run", "keep_wav", "upload", "resume", "force", "new_voice_direction"):
        if getattr(args, flag, False):
            cmd.append(f"--{flag.replace('_', '-')}")
    if skip_preflight:
        cmd.append("--skip-preflight")
    if getattr(args, "load_engine_preflight", False):
        cmd.append("--load-engine-preflight")
    if getattr(args, "delay", None) is not None:
        cmd += ["--delay", str(args.delay)]
    _append_optional(cmd, "--download-dir", getattr(args, "download_dir", None))
    _append_optional(cmd, "--log-dir", getattr(args, "log_dir", None))
    return cmd


def _resolve_process_device(args: argparse.Namespace) -> str | None:
    requested = getattr(args, "device", None) or "auto"
    if args.dry_run:
        return None if requested == "auto" else requested
    if not args.skip_preflight:
        report = run_gpu_preflight(
            args.engine,
            requested,
            load_engine=args.load_engine_preflight,
        )
        print(format_gpu_preflight_report(report))
        if not report.ok:
            raise SystemExit(2)
        return report.resolved_device
    if requested == "auto":
        return None
    return requested


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


def _append_optional(cmd: list[str], flag: str, value: object | None) -> None:
    if value is not None and value != "":
        cmd += [flag, str(value)]


def _quote_command(cmd: Sequence[str]) -> str:
    return " ".join(f'"{part}"' if " " in part else part for part in cmd)


def _add_processing_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--output", "-o", default="audio", help="Audio output directory")
    parser.add_argument("--engine", default=TTS_ENGINE, help="TTS engine")
    parser.add_argument("--voice", default=None, help="Narrator voice override")
    parser.add_argument("--rendition", default=None, help="Rendition slug override")
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
        help="Device request (default: auto)",
    )
    parser.add_argument("--chapters", default=None, help="Chapter number/ranges, e.g. 2 or 2,4-5")
    parser.add_argument("--keep-wav", action="store_true")
    parser.add_argument(
        "--new-voice-direction",
        action="store_true",
        help="Force fresh per-chapter voice direction instead of reusing local cached direction",
    )
    parser.add_argument("--build-id", default=None)
    parser.add_argument("--delay", type=float, default=2)
    parser.add_argument("--download-dir", default=DEFAULT_DOWNLOAD_DIR)
    parser.add_argument("--log-dir", default="logs")
    parser.add_argument("--skip-preflight", action="store_true")
    parser.add_argument("--load-engine-preflight", action="store_true")
    parser.add_argument("--background", action="store_true")
    parser.add_argument("--name", default=None, help="Background log/PID filename stem")
    parser.add_argument("--pid-file", default=None)
    parser.add_argument("--stdout-log", default=None)
    parser.add_argument("--stderr-log", default=None)


def _build_download_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="openshelf-pipeline books download",
        description="Download EPUB books from Project Gutenberg and Standard Ebooks.",
    )
    parser.add_argument(
        "--source",
        default="all",
        choices=["gutenberg", "standard-ebooks", "all"],
    )
    parser.add_argument("--author", default=None)
    parser.add_argument("--language", default=None)
    parser.add_argument("--subject", default=None)
    parser.add_argument("--output", default=DEFAULT_DOWNLOAD_DIR)
    parser.add_argument("--delay", type=float, default=2)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def _build_process_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="openshelf-pipeline books process",
        description="Download and convert books to audio.",
    )
    parser.add_argument("--author", help="Filter by author name")
    parser.add_argument("--book", help="Filter by book title")
    parser.add_argument("--epub", help="Path to a local EPUB file")
    parser.add_argument(
        "--source",
        default="all",
        choices=["gutenberg", "standard-ebooks", "all"],
    )
    _add_processing_options(parser)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--upload", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--force", action="store_true")
    return parser


def _build_reprocess_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="openshelf-pipeline books reprocess",
        description="Reprocess a downloaded book under a fresh build.",
    )
    parser.add_argument("author")
    parser.add_argument("title")
    _add_processing_options(parser)
    return parser


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="openshelf-pipeline books",
        description="Book search, download, process, and reprocess workflows.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    download = subparsers.add_parser("download", help="Download EPUBs only")
    download.add_argument("--source", default="all", choices=["gutenberg", "standard-ebooks", "all"])
    download.add_argument("--author", default=None)
    download.add_argument("--language", default=None)
    download.add_argument("--subject", default=None)
    download.add_argument("--output", default=DEFAULT_DOWNLOAD_DIR)
    download.add_argument("--delay", type=float, default=2)
    download.add_argument("--dry-run", action="store_true")

    process = subparsers.add_parser("process", help="Download/process/upload books")
    process.add_argument("--author", help="Filter by author name")
    process.add_argument("--book", help="Filter by book title")
    process.add_argument("--epub", help="Path to a local EPUB file")
    process.add_argument("--source", default="all", choices=["gutenberg", "standard-ebooks", "all"])
    _add_processing_options(process)
    process.add_argument("--dry-run", action="store_true")
    process.add_argument("--upload", action="store_true")
    process.add_argument("--resume", action="store_true")
    process.add_argument("--force", action="store_true")

    reprocess = subparsers.add_parser("reprocess", help="Reprocess a downloaded EPUB")
    reprocess.add_argument("author")
    reprocess.add_argument("title")
    _add_processing_options(reprocess)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "download":
            return download_books(args)
        if args.command == "process":
            return process_books(args, parser)
        if args.command == "reprocess":
            return reprocess_book(args, parser)
    except ValueError as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
