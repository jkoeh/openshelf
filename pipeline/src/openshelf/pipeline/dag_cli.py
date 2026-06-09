"""File-to-file pipeline DAG commands."""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
from typing import Sequence

from openshelf.pipeline.text_chunker import read_chapter_chunks_artifact


CHAPTER_DATA_VERSION = 1
_CHUNKS_RE = re.compile(r"chapter-(\d+)\.chunks\.json$")
_SYNC_RE = re.compile(r"chapter-(\d+)\.sync\.json$")


def _read_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _write_json_idempotent(path: str, payload: dict, force: bool = False) -> str:
    if os.path.exists(path) and not force:
        existing = _read_json(path)
        if existing == payload:
            return path
        raise FileExistsError(f"output already exists with different payload: {path}")

    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
        f.write("\n")
    return path


def _chapter_number_from_chunks_path(path: str) -> int:
    match = _CHUNKS_RE.search(os.path.basename(path))
    if not match:
        raise ValueError(f"not a chapter chunks artifact: {path}")
    return int(match.group(1))


def _sync_path_for_chunks_path(chunks_path: str) -> str:
    return chunks_path.replace(".chunks.json", ".sync.json")


def _chapter_number_from_sync_path(path: str) -> int:
    match = _SYNC_RE.search(os.path.basename(path))
    if not match:
        raise ValueError(f"not a chapter sync artifact: {path}")
    return int(match.group(1))


def _coverage_ratio(aligned_word_count: int, reader_word_count: int) -> float:
    if reader_word_count == 0:
        return 1.0 if aligned_word_count == 0 else 0.0
    return round(aligned_word_count / reader_word_count, 4)


def collect_coverage(build_dir: str) -> dict:
    """Aggregate per-chapter coverage from chapter-NN.sync.json into a book report.

    Diagnostic only: this reads the `coverage` block already recorded in each sync
    artifact and rolls it up. It does not judge whether coverage is acceptable.
    """
    sync_paths = sorted(
        glob.glob(os.path.join(build_dir, "chapter-*.sync.json")),
        key=_chapter_number_from_sync_path,
    )
    if not sync_paths:
        raise FileNotFoundError(f"no chapter sync artifacts found in {build_dir}")

    chapters: list[dict] = []
    total_reader_words = 0
    total_aligned_words = 0
    first_missing_word_offset: int | None = None

    for sync_path in sync_paths:
        sync_payload = _read_json(sync_path)
        number = sync_payload["number"]
        coverage = sync_payload.get("coverage", {})
        reader_words = coverage.get("reader_word_count", 0)
        aligned_words = coverage.get("aligned_word_count", 0)
        chapter_missing = coverage.get("first_missing_word_offset")

        if first_missing_word_offset is None and chapter_missing is not None:
            first_missing_word_offset = total_reader_words + chapter_missing

        chapters.append({
            "number": number,
            "reader_word_count": reader_words,
            "aligned_word_count": aligned_words,
            "coverage_ratio": coverage.get(
                "coverage_ratio", _coverage_ratio(aligned_words, reader_words)
            ),
            "first_missing_word_offset": chapter_missing,
        })
        total_reader_words += reader_words
        total_aligned_words += aligned_words

    return {
        "build_dir": build_dir,
        "reader_word_count": total_reader_words,
        "aligned_word_count": total_aligned_words,
        "coverage_ratio": _coverage_ratio(total_aligned_words, total_reader_words),
        "first_missing_word_offset": first_missing_word_offset,
        "chapters": chapters,
    }


def _format_coverage_report(report: dict) -> str:
    lines = [f"Coverage report for {report['build_dir']}"]
    lines.append(
        f"  Book total: {report['aligned_word_count']}/{report['reader_word_count']} "
        f"words aligned (ratio {report['coverage_ratio']})"
    )
    if report["first_missing_word_offset"] is not None:
        lines.append(
            f"  First missing word offset: {report['first_missing_word_offset']}"
        )
    for chapter in report["chapters"]:
        missing = chapter["first_missing_word_offset"]
        missing_label = "" if missing is None else f"  first missing @ {missing}"
        lines.append(
            f"  Chapter {chapter['number']:>2}: "
            f"{chapter['aligned_word_count']}/{chapter['reader_word_count']} "
            f"(ratio {chapter['coverage_ratio']}){missing_label}"
        )
    return "\n".join(lines)


def assemble_chapter_data(
    build_dir: str,
    rendition: str,
    build_id: str,
    output_path: str | None = None,
    force: bool = False,
) -> str:
    chunks_paths = sorted(
        glob.glob(os.path.join(build_dir, "chapter-*.chunks.json")),
        key=_chapter_number_from_chunks_path,
    )
    if not chunks_paths:
        raise FileNotFoundError(f"no chapter chunks artifacts found in {build_dir}")

    payload: dict = {
        "version": CHAPTER_DATA_VERSION,
        "rendition": rendition,
        "build": build_id,
        "chapters": [],
    }

    for chunks_path in chunks_paths:
        chunk_payload = read_chapter_chunks_artifact(chunks_path)
        sync_path = _sync_path_for_chunks_path(chunks_path)
        if not os.path.exists(sync_path):
            raise FileNotFoundError(f"missing sync artifact for {chunks_path}: {sync_path}")
        sync_payload = _read_json(sync_path)
        sync_chunks = {
            int(chunk["index"]): chunk.get("words", [])
            for chunk in sync_payload.get("chunks", [])
        }
        chunks = chunk_payload.get("chunks", [])
        entry = {
            "number": chunk_payload["number"],
            "title": chunk_payload["title"],
            "word_count": sum(len(chunk["text"].split()) for chunk in chunks),
            "chunks": [],
        }
        for chunk in chunks:
            index = int(chunk["index"])
            entry["chunks"].append({
                "text": chunk["text"],
                "words": sync_chunks.get(index, []),
            })
        payload["chapters"].append(entry)

    destination = output_path or os.path.join(build_dir, "chapter_data.json")
    return _write_json_idempotent(destination, payload, force=force)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pipeline")
    subparsers = parser.add_subparsers(dest="command", required=True)

    assemble = subparsers.add_parser("assemble")
    assemble.add_argument("--build-dir", required=True)
    assemble.add_argument("--rendition", required=True)
    assemble.add_argument("--build-id", required=True)
    assemble.add_argument("--output")
    assemble.add_argument("--force", action="store_true")

    coverage = subparsers.add_parser("coverage")
    coverage.add_argument("--build-dir", required=True)
    coverage.add_argument("--json", action="store_true")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "assemble":
        path = assemble_chapter_data(
            build_dir=args.build_dir,
            rendition=args.rendition,
            build_id=args.build_id,
            output_path=args.output,
            force=args.force,
        )
        print(path)
        return 0

    if args.command == "coverage":
        report = collect_coverage(build_dir=args.build_dir)
        if args.json:
            print(json.dumps(report, indent=2, ensure_ascii=False))
        else:
            print(_format_coverage_report(report))
        return 0

    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
