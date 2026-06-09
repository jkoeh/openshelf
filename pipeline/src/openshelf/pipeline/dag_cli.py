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

    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
