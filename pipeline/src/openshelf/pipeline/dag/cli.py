"""File-to-file pipeline DAG commands."""

from __future__ import annotations

import argparse
import glob
import json
import logging
import os
import re
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Sequence

from openshelf.pipeline.epub_parser import (
    build_book_parse_artifact,
    epub_sha256,
    parse_epub,
    read_book_metadata,
    read_book_parse_artifact,
    write_book_parse_artifact,
)
from openshelf.pipeline.text_chunker import (
    chunk_text,
    read_section_chunks_artifact,
    write_section_chunks_artifact,
)


SECTION_DATA_VERSION = 2
_CHUNKS_RE = re.compile(r"section-(\d+)\.chunks\.json$")
_SYNC_RE = re.compile(r"section-(\d+)\.sync\.json$")
_REGISTRY_OPENING_CHARS = 12000
_PERFORMANCE_DIRECTION_CHOICES = ("batched", "chunk", "off")
logger = logging.getLogger("openshelf.pipeline.dag.cli")


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


def _write_json(path: str, payload: dict) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False, sort_keys=True)


def _display_name_for_voice(voice: str) -> str:
    if "_" in voice:
        return voice.split("_", 1)[1].replace("_", " ").title()
    return voice.title()


def _credit_headings(title: str, author: str, voice_display: str) -> tuple[dict, dict]:
    opening = (
        f"{title}. Written by {author}. Narrated by OpenShelf "
        f"using the {voice_display} voice."
    )
    closing = (
        f"The end of {title}, written by {author}, narrated by OpenShelf "
        f"using the {voice_display} voice."
    )
    return (
        {
            "display_label": "Opening Credits",
            "display_title": "",
            "spoken_text": opening,
            "element_ids": [],
        },
        {
            "display_label": "Closing Credits",
            "display_title": "",
            "spoken_text": closing,
            "element_ids": [],
        },
    )


def _voice_id_for_manifest(voice) -> str:
    return voice.preset_name or voice.id


def _derive_rendition(engine_name: str, voice) -> str:
    voice_id = _voice_id_for_manifest(voice).replace("_", "-")
    prefix = f"{engine_name}-"
    if voice_id.startswith(prefix):
        return voice_id
    return f"{engine_name}-{voice_id}"


def _chapter_direction_path(build_dir: str, chapter_number: int) -> str:
    return os.path.join(build_dir, f"section-{chapter_number:02d}.voice_direction.json")


def _chapter_chunks_path(build_dir: str, chapter_number: int) -> str:
    return os.path.join(build_dir, f"section-{chapter_number:02d}.chunks.json")


def _chapter_sync_path(build_dir: str, chapter_number: int) -> str:
    return os.path.join(build_dir, f"section-{chapter_number:02d}.sync.json")


def _chapter_synthesis_units_path(build_dir: str, chapter_number: int) -> str:
    return os.path.join(build_dir, f"section-{chapter_number:02d}.synthesis_units.json")


def _direction_speed_summary(chapter_payload: dict) -> dict:
    speed_counts: dict[str, int] = {}
    total_segments = 0
    above_1x = 0
    narrator_above_1x = 0
    max_speed = 0.0
    for chunk in chapter_payload.get("chunks", []):
        for segment in chunk.get("segments", []):
            total_segments += 1
            speed = float(segment.get("speed", 1.0) or 1.0)
            speed_key = f"{speed:.2f}"
            speed_counts[speed_key] = speed_counts.get(speed_key, 0) + 1
            max_speed = max(max_speed, speed)
            if speed > 1.0:
                above_1x += 1
                if segment.get("speaker") == "narrator":
                    narrator_above_1x += 1
    return {
        "segments": total_segments,
        "speed_counts": speed_counts,
        "above_1x": above_1x,
        "narrator_above_1x": narrator_above_1x,
        "max_speed": max_speed,
    }


def _chapter_direction_payload_matches(
    payload: dict,
    *,
    chapter_number: int,
    engine_name: str,
    cast_mode: str,
    chunk_texts: list[str],
) -> bool:
    if payload.get("engine") != engine_name:
        return False
    if payload.get("cast_mode") != cast_mode:
        return False
    chapters = payload.get("sections", [])
    if len(chapters) != 1:
        return False
    chapter = chapters[0]
    if int(chapter.get("sequence", -1)) != chapter_number:
        return False
    chunks = chapter.get("chunks", [])
    if len(chunks) != len(chunk_texts):
        return False
    return all((chunk.get("text") or "") == text for chunk, text in zip(chunks, chunk_texts))


def _narrator_voice_dict(narrator_voice: Any) -> dict:
    if isinstance(narrator_voice, dict):
        return dict(narrator_voice)
    return asdict(narrator_voice)


def _remap_direction_payload_for_build(
    payload: dict,
    *,
    rendition: str,
    build_id: str,
    engine_name: str,
    cast_mode: str,
    narrator_voice: Any,
) -> dict:
    remapped = json.loads(json.dumps(payload))
    remapped["rendition"] = rendition
    remapped["build"] = build_id
    remapped["engine"] = engine_name
    remapped["cast_mode"] = cast_mode

    voice = _narrator_voice_dict(narrator_voice)
    for chapter in remapped.get("sections", []):
        for chunk in chapter.get("chunks", []):
            for segment in chunk.get("segments", []):
                if (
                    cast_mode == "solo"
                    or segment.get("speaker") == "narrator"
                    or str(segment.get("voice_policy", "")).startswith("narrator")
                ):
                    segment["voice"] = voice
    return remapped


def _find_cached_chapter_direction(
    *,
    book_dir: str,
    build_dir: str,
    chapter_number: int,
    engine_name: str,
    cast_mode: str,
    chunk_texts: list[str],
) -> tuple[str, dict] | None:
    current_path = Path(_chapter_direction_path(build_dir, chapter_number)).resolve()
    pattern = os.path.join(
        book_dir,
        "audio",
        "*",
        "builds",
        "*",
        f"section-{chapter_number:02d}.voice_direction.json",
    )
    candidates = sorted(
        glob.glob(pattern),
        key=lambda path: os.path.getmtime(path),
        reverse=True,
    )
    for candidate in candidates:
        candidate_path = Path(candidate).resolve()
        if candidate_path == current_path:
            continue
        try:
            payload = _read_json(candidate)
        except (OSError, json.JSONDecodeError):
            logger.warning("Skipping unreadable cached voice direction: %s", candidate)
            continue
        if _chapter_direction_payload_matches(
            payload,
            chapter_number=chapter_number,
            engine_name=engine_name,
            cast_mode=cast_mode,
            chunk_texts=chunk_texts,
        ):
            return candidate, payload
    return None


def _load_or_copy_chapter_direction(
    *,
    book_dir: str,
    build_dir: str,
    chapter_number: int,
    engine_name: str,
    cast_mode: str,
    chunk_texts: list[str],
    rendition: str,
    build_id: str,
    narrator_voice: Any,
) -> tuple[dict, str] | None:
    direction_path = _chapter_direction_path(build_dir, chapter_number)
    if os.path.exists(direction_path):
        payload = _read_json(direction_path)
        if _chapter_direction_payload_matches(
            payload,
            chapter_number=chapter_number,
            engine_name=engine_name,
            cast_mode=cast_mode,
            chunk_texts=chunk_texts,
        ):
            remapped = _remap_direction_payload_for_build(
                payload,
                rendition=rendition,
                build_id=build_id,
                engine_name=engine_name,
                cast_mode=cast_mode,
                narrator_voice=narrator_voice,
            )
            if remapped != payload:
                _write_json(direction_path, remapped)
            return remapped, direction_path
        logger.warning(
            "Existing chapter direction is incompatible and will not be reused: %s",
            direction_path,
        )

    cached = _find_cached_chapter_direction(
        book_dir=book_dir,
        build_dir=build_dir,
        chapter_number=chapter_number,
        engine_name=engine_name,
        cast_mode=cast_mode,
        chunk_texts=chunk_texts,
    )
    if cached is None:
        return None

    source_path, payload = cached
    remapped = _remap_direction_payload_for_build(
        payload,
        rendition=rendition,
        build_id=build_id,
        engine_name=engine_name,
        cast_mode=cast_mode,
        narrator_voice=narrator_voice,
    )
    _write_json(direction_path, remapped)
    logger.info(
        "Copied cached chapter %d direction from %s to %s",
        chapter_number,
        source_path,
        direction_path,
    )
    return remapped, source_path


def _direction_cache_hit_message(source_path: str, target_path: str) -> str:
    if Path(source_path).resolve() == Path(target_path).resolve():
        return f"voice direction cache hit: existing {target_path}"
    return f"voice direction cache hit: copied from {source_path} -> {target_path}"


def _log_torch_device_diagnostics(selected_device: str) -> None:
    try:
        import torch
    except ImportError:
        logger.warning("torch is not installed; selected_device=%s", selected_device)
        return

    cuda_available = torch.cuda.is_available()
    device_names = [
        torch.cuda.get_device_name(i)
        for i in range(torch.cuda.device_count())
    ]
    logger.info(
        "Device selected=%s torch=%s cuda_available=%s cuda_version=%s "
        "cuda_device_count=%d cuda_devices=%s",
        selected_device,
        getattr(torch, "__version__", "unknown"),
        cuda_available,
        getattr(torch.version, "cuda", None),
        torch.cuda.device_count(),
        device_names,
    )
    if selected_device == "cpu" and not cuda_available:
        logger.warning(
            "CUDA is not available to torch. If this machine has an NVIDIA GPU, "
            "the project venv likely has a CPU-only PyTorch wheel."
        )


def _resolve_tts_device(engine_name: str | None, requested_device: str | None) -> str:
    requested = (requested_device or "auto").lower()
    if requested != "auto":
        selected = requested_device
    else:
        from openshelf.pipeline.tts import get_device

        selected = get_device()

    selected = selected or "cpu"

    from openshelf.pipeline.ops.gpu_preflight import accelerator_required_by_default

    if (
        requested == "auto"
        and selected == "cpu"
        and accelerator_required_by_default(engine_name)
    ):
        raise ValueError(
            "Chatterbox auto device resolved to CPU. Install/use a GPU-capable "
            "PyTorch package or pass --device cpu to force the slow path."
        )
    if requested == "cpu" and accelerator_required_by_default(engine_name):
        logger.warning(
            "Chatterbox CPU was explicitly selected; full-book runs are usually very slow."
        )
    return selected


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
        raise ValueError(f"not a section sync artifact: {path}")
    return int(match.group(1))


def _coverage_ratio(aligned_word_count: int, reader_word_count: int) -> float:
    if reader_word_count == 0:
        return 1.0 if aligned_word_count == 0 else 0.0
    return round(aligned_word_count / reader_word_count, 4)


def collect_coverage(build_dir: str) -> dict:
    """Aggregate per-section coverage from section-NN.sync.json.

    Diagnostic only: this reads the coverage block already recorded in each
    sync artifact and rolls it up without judging acceptability.
    """
    sync_paths = sorted(
        glob.glob(os.path.join(build_dir, "section-*.sync.json")),
        key=_chapter_number_from_sync_path,
    )
    if not sync_paths:
        raise FileNotFoundError(f"no section sync artifacts found in {build_dir}")

    chapters: list[dict] = []
    total_reader_words = 0
    total_aligned_words = 0
    first_missing_word_offset: int | None = None

    for sync_path in sync_paths:
        sync_payload = _read_json(sync_path)
        number = sync_payload["sequence"]
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
        "sections": chapters,
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
    for chapter in report["sections"]:
        missing = chapter["first_missing_word_offset"]
        missing_label = "" if missing is None else f"  first missing @ {missing}"
        lines.append(
            f"  Section {chapter['number']:>2}: "
            f"{chapter['aligned_word_count']}/{chapter['reader_word_count']} "
            f"(ratio {chapter['coverage_ratio']}){missing_label}"
        )
    return "\n".join(lines)


def build_section_data_payload(
    build_dir: str,
    rendition: str,
    build_id: str,
    selected_chapters: set[int] | None = None,
) -> dict:
    """Assemble the public section_data.json payload from chunk + sync artifacts.

    Shared by the `assemble` DAG command and the full DAG runner.
    ``selected_chapters`` restricts assembly to a subset (e.g. a local
    --chapters sample run); None assembles every chapter present.
    """
    chunks_paths = sorted(
        glob.glob(os.path.join(build_dir, "section-*.chunks.json")),
        key=_chapter_number_from_chunks_path,
    )
    if not chunks_paths:
        raise FileNotFoundError(f"no chapter chunks artifacts found in {build_dir}")

    payload: dict = {
        "version": SECTION_DATA_VERSION,
        "rendition": rendition,
        "build": build_id,
        "sections": [],
    }

    for chunks_path in chunks_paths:
        number = _chapter_number_from_chunks_path(chunks_path)
        if selected_chapters is not None and number not in selected_chapters:
            continue
        chunk_payload = read_section_chunks_artifact(chunks_path)
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
            "sequence": chunk_payload["sequence"],
            "section_type": chunk_payload["section_type"],
            "ordinal": chunk_payload.get("ordinal"),
            "heading": {
                **chunk_payload["heading"],
                "words": sync_payload.get("heading_words", []),
            },
            "word_count": (
                len(chunk_payload["heading"].get("spoken_text", "").split())
                + sum(len(chunk["text"].split()) for chunk in chunks)
            ),
            "chunks": [],
        }
        for chunk in chunks:
            index = int(chunk["index"])
            entry["chunks"].append({
                "text": chunk["text"],
                "words": sync_chunks.get(index, []),
            })
        payload["sections"].append(entry)

    return payload


def assemble_section_data(
    build_dir: str,
    rendition: str,
    build_id: str,
    output_path: str | None = None,
    selected_chapters: set[int] | None = None,
    force: bool = False,
) -> str:
    payload = build_section_data_payload(
        build_dir, rendition, build_id, selected_chapters=selected_chapters
    )
    destination = output_path or os.path.join(build_dir, "section_data.json")
    return _write_json_idempotent(destination, payload, force=force)


def _parse_chapter_filter(value: str | None) -> set[int] | None:
    """Parse a chapter number/range filter like ``2`` or ``2,4-5`` into a set."""
    if not value:
        return None
    selected: set[int] = set()
    for raw_part in value.split(","):
        part = raw_part.strip()
        if not part:
            continue
        if "-" in part:
            left, right = part.split("-", 1)
            start = int(left.strip())
            end = int(right.strip())
            if start <= 0 or end <= 0 or end < start:
                raise ValueError(f"invalid chapter range: {part}")
            selected.update(range(start, end + 1))
        else:
            number = int(part)
            if number <= 0:
                raise ValueError(f"invalid chapter number: {part}")
            selected.add(number)
    return selected


def parse_book(
    epub_path: str,
    output_path: str,
    source: str = "",
    force: bool = False,
) -> str:
    """Parse an EPUB into the durable ``book_parse.json`` artifact."""
    sections = parse_epub(epub_path)
    if not sections:
        raise ValueError(f"no audiobook sections found in EPUB: {epub_path}")
    metadata = read_book_metadata(epub_path)
    metadata["source"] = source
    payload = build_book_parse_artifact(sections, epub_sha256(epub_path), metadata)
    return write_book_parse_artifact(output_path, payload, force=force)


def chunk_chapters(
    book_parse_path: str,
    build_dir: str,
    selected_chapters: set[int] | None = None,
    force: bool = False,
) -> list[str]:
    """Write ``section-NN.chunks.json`` for selected parsed sections."""
    book_parse = read_book_parse_artifact(book_parse_path)
    rendition, _build_id = _rendition_build_from_build_dir(build_dir)
    voice_token = rendition.split("-", 1)[1] if "-" in rendition else rendition
    voice_display = _display_name_for_voice(voice_token.replace("-", "_"))
    metadata = book_parse.get("metadata", {})
    opening_heading, closing_heading = _credit_headings(
        metadata.get("title", ""),
        metadata.get("author", ""),
        voice_display,
    )
    artifact_sections = [{
        "sequence": 1,
        "section_type": "opening_credits",
        "ordinal": None,
        "heading": opening_heading,
        "elements": [],
    }]
    artifact_sections.extend(
        {**section, "sequence": int(section["sequence"]) + 1}
        for section in book_parse.get("sections", [])
    )
    artifact_sections.append({
        "sequence": len(artifact_sections) + 1,
        "section_type": "closing_credits",
        "ordinal": None,
        "heading": closing_heading,
        "elements": [],
    })
    written: list[str] = []
    for section in artifact_sections:
        number = section["sequence"]
        if selected_chapters is not None and number not in selected_chapters:
            continue
        elements = section.get("elements", [])
        heading_ids = set(section.get("heading", {}).get("element_ids", []))
        body_elements = [
            element
            for element in elements
            if element["spoken"] and element["id"] not in heading_ids
        ]
        paragraphs = [element["text"] for element in body_elements]
        element_ids = [element["id"] for element in body_elements]
        chunks = chunk_text(paragraphs, element_ids=element_ids)
        path = os.path.join(build_dir, f"section-{number:02d}.chunks.json")
        write_section_chunks_artifact(
            path,
            number,
            section["section_type"],
            section.get("ordinal"),
            section["heading"],
            chunks,
            force=force,
        )
        written.append(path)
    if selected_chapters is not None:
        found = {
            section["sequence"]
            for section in artifact_sections
            if section["sequence"] in selected_chapters
        }
        missing = sorted(selected_chapters - found)
        if missing:
            raise ValueError(
                f"--chapters selected missing chapter(s): "
                f"{', '.join(str(n) for n in missing)}"
            )
    return written


def _rendition_build_from_build_dir(build_dir: str) -> tuple[str, str]:
    """Derive (rendition, build) from an ``audio/{rendition}/builds/{build}`` path."""
    build_dir = os.path.normpath(build_dir)
    build_id = os.path.basename(build_dir)
    rendition = os.path.basename(os.path.dirname(os.path.dirname(build_dir)))
    return rendition, build_id


def _book_parse_opening_text(book_parse: dict, max_chars: int = _REGISTRY_OPENING_CHARS) -> str:
    parts: list[str] = []
    total = 0
    for chapter in book_parse.get("sections", []):
        heading_ids = set(chapter.get("heading", {}).get("element_ids", []))
        paragraphs = [
            element.get("text", "")
            for element in chapter.get("elements", [])
            if (
                element.get("spoken")
                and element.get("text")
                and element.get("id") not in heading_ids
            )
        ]
        if not paragraphs:
            continue
        text = "\n\n".join(paragraphs)
        remaining = max_chars - total
        if remaining <= 0:
            break
        parts.append(text[:remaining])
        total += len(parts[-1])
    return "\n\n".join(parts)


def _voice_spec_for_override(engine, voice_id: str):
    from openshelf.pipeline.tts_engine import VoiceSpec

    for voice in engine.available_voices():
        if voice.id == voice_id or voice.preset_name == voice_id:
            return voice
    if getattr(engine, "name", "") == "kokoro":
        return VoiceSpec(id=voice_id, preset_name=voice_id)
    return VoiceSpec(id=voice_id, ref_audio_path=voice_id)


def build_registry(
    book_parse_path: str,
    build_dir: str,
    engine_name: str | None = None,
    voice: str | None = None,
    force: bool = False,
    director=None,
) -> str:
    """Build character_registry.json from book_parse.json without directing audio."""
    from openshelf.pipeline.voice_director import AudioDirector, BookContext

    book_parse = read_book_parse_artifact(book_parse_path)
    metadata = book_parse.get("metadata", {})
    title = metadata.get("title") or ""
    author = metadata.get("author") or ""
    ctx = BookContext(
        title=title,
        author=author,
        language=metadata.get("language") or "en",
        opening_text=_book_parse_opening_text(book_parse),
    )

    if director is None:
        from openshelf.config import TTS_ENGINE
        from openshelf.pipeline.engines import create_engine
        from openshelf.pipeline.llm import create_llm

        engine = create_engine(engine_name or TTS_ENGINE)
        director = AudioDirector(engine, create_llm(), aligner=None)
    else:
        engine = getattr(director, "engine", None)

    narrator_override = (
        _voice_spec_for_override(engine, voice)
        if voice is not None and engine is not None
        else None
    )
    registry = director.build_registry(ctx, narrator_voice_override=narrator_override)
    path = os.path.join(build_dir, "character_registry.json")
    return _write_json_idempotent(path, registry.to_dict(), force=force)


def direct_chapter(
    build_dir: str,
    chapter_number: int,
    engine_name: str | None = None,
    cast_mode: str = "solo",
    performance_direction_mode: str = "batched",
    device: str | None = None,
    force: bool = False,
    director=None,
) -> str:
    """Run LLM voice direction for one chapter and write chapter-NN.voice_direction.json.

    Reads chapter-NN.chunks.json (reader text) and character_registry.json
    (narrator voice). Solo cast mode only — multicast registry repair is out of
    scope (see plans/pipeline-resumable-repair-plan.md). This is the only repair
    stage that may call the LLM; heavy engine/LLM deps import lazily.
    """
    from openshelf.pipeline.voice_director import (
        AudioDirector,
        CharacterRegistry,
        build_chunk_windows,
        build_direction_chapter,
        build_voice_direction_payload,
        voice_spec_from_dict,
    )

    if cast_mode != "solo":
        raise ValueError("direct repair supports solo cast mode only")
    if performance_direction_mode not in _PERFORMANCE_DIRECTION_CHOICES:
        choices = ", ".join(_PERFORMANCE_DIRECTION_CHOICES)
        raise ValueError(f"performance direction must be one of: {choices}")

    chunks_path = os.path.join(build_dir, f"section-{chapter_number:02d}.chunks.json")
    registry_path = os.path.join(build_dir, "character_registry.json")
    direction_path = os.path.join(
        build_dir, f"section-{chapter_number:02d}.voice_direction.json"
    )
    for required in (chunks_path, registry_path):
        if not os.path.exists(required):
            raise FileNotFoundError(f"missing required input for direct: {required}")

    chunk_payload = read_section_chunks_artifact(chunks_path)
    heading = chunk_payload["heading"]
    title = heading.get("display_title") or heading.get("display_label") or ""
    chunks = chunk_payload.get("chunks", [])
    chunk_texts = [chunk["text"] for chunk in chunks]

    registry_dict = _read_json(registry_path)
    registry = CharacterRegistry(
        narrator_voice=voice_spec_from_dict(registry_dict["narrator_voice"]),
        characters={},
    )

    windows = build_chunk_windows(chunk_texts)

    if director is None:
        from openshelf.config import TTS_ENGINE
        from openshelf.pipeline.engines import create_aligner, create_engine
        from openshelf.pipeline.llm import create_llm

        engine = create_engine(engine_name or TTS_ENGINE)
        aligner = create_aligner(engine, device=device or "cpu")
        director = AudioDirector(
            engine,
            create_llm(),
            aligner,
            cast_mode=cast_mode,
            performance_direction_mode=performance_direction_mode,
        )

    registry, directed_chunks = director.direct_chapter(title, windows, registry)
    chapter = build_direction_chapter(
        chunk_payload["sequence"],
        title,
        chunk_texts,
        directed_chunks,
        fallback_used=director.last_chapter_fallback_used,
        fallback_error=director.last_chapter_error,
    )

    rendition, build_id = _rendition_build_from_build_dir(build_dir)
    resolved_engine = engine_name or getattr(getattr(director, "engine", None), "name", "")
    payload = build_voice_direction_payload(
        rendition, build_id, resolved_engine, cast_mode, [chapter]
    )
    return _write_json_idempotent(direction_path, payload, force=force)


def synth_chapter(
    build_dir: str,
    chapter_number: int,
    engine_name: str | None = None,
    device: str | None = None,
    voice: str | None = None,
    keep_wav: bool = False,
    force: bool = False,
    engine=None,
    aligner=None,
) -> str:
    """Synthesize one chapter's audio from its voice direction and write m4a + sync.

    Reads chapter-NN.voice_direction.json (directed segments) and
    chapter-NN.chunks.json (text + paragraph boundaries), runs TTS + alignment,
    encodes to AAC, and writes chapter-NN.sync.json. Covers both fresh synthesis
    and pause/stitch-policy "restitch" repair (there are no durable WAV/unit
    intermediates to restitch from, so audio is regenerated). Heavy engine/TTS
    deps import lazily.

    Idempotency: if both the m4a and sync artifacts already exist, the stage
    skips unless ``force=True`` (TTS is expensive and not deterministic, so the
    file-exists gate stands in for an input fingerprint).
    """
    from openshelf.pipeline.tts import build_chunk_infos, synthesize_chapter_to_files
    from openshelf.pipeline.voice_director import (
        directed_chunks_from_chapter_direction_artifact,
        voice_spec_from_dict,
    )

    chunks_path = os.path.join(build_dir, f"section-{chapter_number:02d}.chunks.json")
    direction_path = os.path.join(
        build_dir, f"section-{chapter_number:02d}.voice_direction.json"
    )
    m4a_path = os.path.join(build_dir, f"section-{chapter_number:02d}.m4a")
    wav_path = os.path.join(build_dir, f"section-{chapter_number:02d}.wav")
    sync_path = os.path.join(build_dir, f"section-{chapter_number:02d}.sync.json")
    synthesis_units_path = _chapter_synthesis_units_path(build_dir, chapter_number)

    for required in (chunks_path, direction_path):
        if not os.path.exists(required):
            raise FileNotFoundError(f"missing required input for synth: {required}")

    if (
        not force
        and os.path.exists(m4a_path)
        and os.path.exists(sync_path)
        and os.path.exists(synthesis_units_path)
    ):
        return sync_path

    chunk_payload = read_section_chunks_artifact(chunks_path)
    chunks = chunk_payload.get("chunks", [])
    directed_chunks = directed_chunks_from_chapter_direction_artifact(direction_path)

    chunk_texts = [chunk["text"] for chunk in chunks]
    ends_paragraph = [
        (ci == len(chunks) - 1) or (chunk["para_end"] != chunks[ci + 1]["para_start"])
        for ci, chunk in enumerate(chunks)
    ]
    chunk_infos = build_chunk_infos(chunk_texts, ends_paragraph, directed_chunks)

    registry_narrator_voice = None
    registry_path = os.path.join(build_dir, "character_registry.json")
    if os.path.exists(registry_path):
        narrator = _read_json(registry_path).get("narrator_voice")
        if narrator:
            registry_narrator_voice = voice_spec_from_dict(narrator)

    if engine is None:
        from openshelf.config import TTS_ENGINE
        from openshelf.pipeline.engines import create_aligner, create_engine
        from openshelf.pipeline.tts import load_pipeline

        device = _resolve_tts_device(engine_name or TTS_ENGINE, device)
        engine = create_engine(engine_name or TTS_ENGINE, device=device)
        if engine.name == "kokoro":
            engine.pipeline = load_pipeline(device=device)
        if aligner is None:
            aligner = create_aligner(engine, device=device)

    narrator_voice = (
        _voice_spec_for_override(engine, voice)
        if voice is not None
        else registry_narrator_voice
    )
    if narrator_voice is None and getattr(engine, "name", "") != "kokoro":
        raise FileNotFoundError(
            "reference-voice synthesis requires character_registry.json with "
            "a complete narrator_voice VoiceSpec"
        )

    synthesize_chapter_to_files(
        engine,
        chunk_infos,
        wav_path,
        m4a_path,
        sync_path,
        synthesis_units_path,
        chapter_number,
        chunk_texts,
        narrator_voice=narrator_voice,
        aligner=aligner,
        keep_wav=keep_wav,
        force=force,
        heading_text=chunk_payload["heading"].get("spoken_text", ""),
    )
    return sync_path


def sync_chapter(
    build_dir: str,
    chapter_number: int,
    device: str = "cpu",
    language: str = "en",
    force: bool = False,
) -> str:
    """Re-run WhisperX alignment for one chapter and rewrite chapter-NN.sync.json.

    Reads the finished m4a + chunks.json (for text) and the prior sync.json (for
    the per-chunk ``chunk_audio_starts``, which are produced at synthesis time and
    cannot be recovered from the m4a alone). Avoids LLM and TTS. Heavy aligner
    deps are imported lazily so the offline commands never require them.
    """
    from openshelf.pipeline.tts_engine import WordTimestamp
    from openshelf.pipeline.word_aligner import (
        align_chapter,
        read_section_sync_artifact,
        write_section_sync_artifact,
    )

    chunks_path = os.path.join(build_dir, f"section-{chapter_number:02d}.chunks.json")
    sync_path = os.path.join(build_dir, f"section-{chapter_number:02d}.sync.json")
    m4a_path = os.path.join(build_dir, f"section-{chapter_number:02d}.m4a")

    for required in (chunks_path, sync_path, m4a_path):
        if not os.path.exists(required):
            raise FileNotFoundError(f"missing required input for sync: {required}")

    chunk_payload = read_section_chunks_artifact(chunks_path)
    chunk_texts = [chunk["text"] for chunk in chunk_payload.get("chunks", [])]

    prior_sync = read_section_sync_artifact(sync_path)
    chunk_audio_starts = prior_sync["chunk_audio_starts"]

    word_entries = align_chapter(
        m4a_path, chunk_texts, chunk_audio_starts, device=device, language=language
    )

    chunk_words: list[list] = [[] for _ in chunk_texts]
    for entry in word_entries:
        if 0 <= entry.chunk_idx < len(chunk_words):
            chunk_words[entry.chunk_idx].append(
                WordTimestamp(word=entry.word, start=entry.start, end=entry.end)
            )

    return write_section_sync_artifact(
        sync_path,
        chapter_number,
        os.path.basename(m4a_path),
        chunk_audio_starts,
        chunk_words,
        chunk_texts=chunk_texts,
        heading_words=[
            WordTimestamp(**word) for word in prior_sync.get("heading_words", [])
        ],
        force=force,
    )


def _refresh_repaired_rendition_manifest(
    build_dir: str,
    chapter_number: int,
    m4a_path: str,
) -> str | None:
    manifest_path = os.path.join(build_dir, "rendition-manifest.json")
    if not os.path.exists(manifest_path):
        return None

    from openshelf.pipeline.encoder import audio_duration

    payload = _read_json(manifest_path)
    sections = payload.get("sections", [])
    duration_seconds = audio_duration(m4a_path)
    updated = False
    for section in sections:
        if int(section.get("sequence", -1)) == chapter_number:
            section["duration_seconds"] = duration_seconds
            updated = True
            break
    if not updated:
        logger.warning(
            "Skipping rendition manifest duration refresh; chapter %d not found in %s",
            chapter_number,
            manifest_path,
        )
        return None

    payload["total_duration_seconds"] = sum(
        float(section.get("duration_seconds", 0.0) or 0.0)
        for section in sections
    )
    _write_json(manifest_path, payload)
    return manifest_path


def _refresh_repaired_section_data(build_dir: str) -> str | None:
    section_data_path = os.path.join(build_dir, "section_data.json")
    if not os.path.exists(section_data_path):
        return None

    rendition, build_id = _rendition_build_from_build_dir(build_dir)
    return assemble_section_data(
        build_dir,
        rendition,
        build_id,
        output_path=section_data_path,
        force=True,
    )


def repair_pauses_chapter(
    build_dir: str,
    chapter_number: int,
    force: bool = False,
) -> str:
    """Repair recorded seam pauses for one synthesized chapter without TTS."""
    if not force:
        raise ValueError("repair-pauses rewrites audio/timing artifacts and requires --force")

    from openshelf.pipeline.seams import repair_chapter_pause_files

    chunks_path = _chapter_chunks_path(build_dir, chapter_number)
    sync_path = _chapter_sync_path(build_dir, chapter_number)
    synthesis_units_path = _chapter_synthesis_units_path(build_dir, chapter_number)
    m4a_path = os.path.join(build_dir, f"section-{chapter_number:02d}.m4a")

    for required in (chunks_path, sync_path, synthesis_units_path, m4a_path):
        if not os.path.exists(required):
            raise FileNotFoundError(f"missing required input for pause repair: {required}")

    chunk_payload = read_section_chunks_artifact(chunks_path)
    chunk_texts = [chunk["text"] for chunk in chunk_payload.get("chunks", [])]
    edits = repair_chapter_pause_files(
        m4a_path=m4a_path,
        sync_path=sync_path,
        synthesis_units_path=synthesis_units_path,
        chunk_texts=chunk_texts,
        force=True,
    )
    refreshed_section_data = None
    refreshed_manifest = None
    if edits:
        refreshed_section_data = _refresh_repaired_section_data(build_dir)
        refreshed_manifest = _refresh_repaired_rendition_manifest(
            build_dir,
            chapter_number,
            m4a_path,
        )
    logger.info(
        (
            "Chapter %d pause repair complete edits=%d m4a=%s sync=%s "
            "synthesis_units=%s section_data=%s rendition_manifest=%s"
        ),
        chapter_number,
        len(edits),
        m4a_path,
        sync_path,
        synthesis_units_path,
        refreshed_section_data or "",
        refreshed_manifest or "",
    )
    return sync_path


def upload_build(
    book_dir: str,
    rendition: str,
    build_id: str,
    bucket: str | None = None,
    source: str | None = None,
    force: bool = False,
    client=None,
) -> list[str]:
    """Publish an existing local build to R2 without rerunning earlier stages.

    Uploads cover/EPUB (when present) and every per-(rendition, build) artifact,
    then updates the mutable book manifest pointer so this build becomes
    ``current_build`` for the rendition (merging with the prior R2 manifest so
    other renditions survive). R2 access is imported lazily so the offline
    commands never require boto3.
    """
    from openshelf.config import R2_BUCKET
    from openshelf.pipeline import r2
    from openshelf.pipeline.manifest import RenditionEntry, merge_book_manifest

    bucket = bucket or R2_BUCKET
    book_dir = os.path.abspath(book_dir)
    title_slug = os.path.basename(book_dir)
    author_slug = os.path.basename(os.path.dirname(book_dir))
    build_dir = os.path.join(book_dir, "audio", rendition, "builds", build_id)

    section_data_path = os.path.join(build_dir, "section_data.json")
    rendition_manifest_path = os.path.join(build_dir, "rendition-manifest.json")
    character_registry_path = os.path.join(build_dir, "character_registry.json")
    voice_direction_path = os.path.join(build_dir, "voice_direction.json")
    run_context_path = os.path.join(build_dir, "run.json")
    book_manifest_path = os.path.join(book_dir, "manifest.json")

    for required in (section_data_path, rendition_manifest_path, book_manifest_path):
        if not os.path.exists(required):
            raise FileNotFoundError(f"missing required artifact for upload: {required}")

    if client is None:
        client = r2.make_client()

    uploaded: list[str] = []

    for ext, content_type in (("jpg", "image/jpeg"), ("png", "image/png")):
        cover_path = os.path.join(book_dir, f"cover.{ext}")
        if os.path.exists(cover_path):
            r2.upload_cover(
                client, bucket, author_slug, title_slug,
                cover_path, content_type, force=force,
            )
            break
    epub_path = os.path.join(book_dir, "book-annotated.epub")
    if os.path.exists(epub_path):
        r2.upload_epub(client, bucket, author_slug, title_slug, epub_path, force=force)

    uploaded += r2.upload_rendition_build(
        client, bucket, author_slug, title_slug, rendition, build_id,
        audio_dir=build_dir,
        section_data_path=section_data_path,
        rendition_manifest_path=rendition_manifest_path,
        character_registry_path=character_registry_path,
        voice_direction_path=voice_direction_path,
        run_context_path=run_context_path,
        force=force,
    )

    with open(book_manifest_path, "r", encoding="utf-8") as f:
        local_manifest = json.load(f)
    local_entry = local_manifest.get("renditions", {}).get(rendition)
    if local_entry is None:
        raise ValueError(
            f"local book manifest has no rendition '{rendition}': {book_manifest_path}"
        )
    new_entry = RenditionEntry(
        voice=local_entry["voice"],
        engine=local_entry["engine"],
        display=local_entry["display"],
        current_build=build_id,
        available_builds=[build_id],
    )
    prior = r2.fetch_prior_book_manifest(client, bucket, author_slug, title_slug)
    legacy_builds: dict[str, list[str]] = {}
    if prior:
        compatible = json.loads(json.dumps(prior))
        compatible["renditions"] = {}
        for prior_rendition, prior_entry in prior.get("renditions", {}).items():
            retained: list[str] = []
            for prior_build in dict.fromkeys(prior_entry.get("available_builds", [])):
                manifest_key = r2.r2_keys.rendition_manifest_key(
                    author_slug,
                    title_slug,
                    prior_rendition,
                    prior_build,
                )
                try:
                    obj = client.get_object(Bucket=bucket, Key=manifest_key)
                    manifest_payload = json.loads(obj["Body"].read().decode("utf-8"))
                except Exception:
                    manifest_payload = {}
                if manifest_payload.get("version") == 2:
                    retained.append(prior_build)
                else:
                    legacy_builds.setdefault(prior_rendition, []).append(prior_build)
            if retained:
                compatible_entry = dict(prior_entry)
                compatible_entry["available_builds"] = retained
                if compatible_entry.get("current_build") not in retained:
                    compatible_entry["current_build"] = retained[0]
                compatible["renditions"][prior_rendition] = compatible_entry
        base = compatible
    else:
        base = local_manifest
    merged = merge_book_manifest(base, rendition, new_entry)
    merged["title"] = local_manifest.get("title", "")
    merged["author"] = local_manifest.get("author", "")
    merged["source"] = source if source is not None else local_manifest.get("source", "")
    with open(book_manifest_path, "w", encoding="utf-8") as f:
        json.dump(merged, f, indent=2, ensure_ascii=False, sort_keys=True)
    uploaded.append(
        r2.upload_book_manifest(client, bucket, author_slug, title_slug, book_manifest_path)
    )
    for legacy_rendition, build_ids in legacy_builds.items():
        uploaded += r2.delete_build_prefixes(
            client,
            bucket,
            author_slug,
            title_slug,
            legacy_rendition,
            build_ids,
        )
    return uploaded


def run_book(args) -> dict:
    """Run the full book pipeline using the DAG artifact boundaries."""
    from openshelf.config import (
        PIPELINE_VERSION,
        R2_BUCKET,
        REGISTRY_OPENING_CHARS,
        TTS_LANGUAGE,
    )
    from openshelf.pipeline.build import new_build_id
    from openshelf.pipeline.engines import create_aligner, create_engine
    from openshelf.pipeline.epub_annotator import annotate_epub
    from openshelf.pipeline.epub_parser import extract_cover_image
    from openshelf.pipeline.llm import create_llm
    from openshelf.pipeline.logging_utils import (
        Heartbeat,
        configure_console_output,
        configure_pipeline_logging,
    )
    from openshelf.pipeline.manifest import (
        SectionMeta,
        generate_book_manifest,
        generate_rendition_entry,
        generate_rendition_manifest,
    )
    from openshelf.pipeline.run_context import (
        make_run_context,
        prepare_run_context,
        run_context_path as build_run_context_path,
        validate_build_id,
    )
    from openshelf.pipeline.tts import (
        build_chunk_infos,
        load_pipeline,
        synthesize_chapter_to_files,
    )
    from openshelf.pipeline.voice_director import (
        AudioDirector,
        BookContext,
        build_chunk_windows,
        build_direction_chapter,
        build_voice_direction_payload,
        directed_chunks_from_chapter_direction_artifact,
    )
    from openshelf.scrapers.http import sanitize

    configure_console_output()

    epub_path = getattr(args, "epub")
    if not os.path.isfile(epub_path):
        raise FileNotFoundError(f"{epub_path} not found")
    if getattr(args, "resume", False) and not getattr(args, "build_id", None):
        raise ValueError("--resume requires --build-id")

    build_id = (
        validate_build_id(args.build_id)
        if getattr(args, "build_id", None)
        else new_build_id()
    )
    epub_stem = sanitize(os.path.splitext(os.path.basename(epub_path))[0])
    log_path = configure_pipeline_logging(
        f"dag-run-{epub_stem}-{build_id}", getattr(args, "log_dir", "logs")
    )
    print(f"Log file: {log_path}")
    logger.info(
        "dag run started epub=%s source=%s engine=%s rendition=%s "
        "performance_direction=%s new_voice_direction=%s upload=%s dry_run=%s",
        epub_path,
        args.source,
        args.engine,
        getattr(args, "rendition", None),
        args.performance_direction,
        getattr(args, "new_voice_direction", False),
        getattr(args, "upload", False),
        getattr(args, "dry_run", False),
    )

    print(f"Parsing {epub_path} ...")
    with Heartbeat(logger, "Parsing EPUB"):
        chapters = parse_epub(epub_path)
    if not chapters:
        raise ValueError(f"no audiobook sections found in EPUB: {epub_path}")

    total_words = sum(ch.word_count for ch in chapters)
    print(f"Found {len(chapters)} source sections ({total_words:,} body words)\n")
    logger.info("Parsed %d source sections (%d body words)", len(chapters), total_words)

    metadata = read_book_metadata(epub_path)
    book_title = metadata.get("title") or sanitize(os.path.splitext(os.path.basename(epub_path))[0])
    epub_parts = os.path.normpath(epub_path).split(os.sep)
    author_slug = sanitize(epub_parts[-2]) if len(epub_parts) >= 2 else "unknown"
    title_slug = sanitize(os.path.splitext(epub_parts[-1])[0])
    book_author = metadata.get("author") or author_slug
    book_dir = os.path.join(args.output, author_slug, title_slug)

    chunked_chapters = [{
        "number": 1,
        "section_type": "opening_credits",
        "ordinal": None,
        "heading": {
            "display_label": "Opening Credits",
            "display_title": "",
            "spoken_text": "",
            "element_ids": [],
        },
        "title": "Opening Credits",
        "word_count": 0,
        "chunks": [],
    }]
    for ch in chapters:
        spoken_ids = [el.id for el in ch.body_elements]
        chunks = chunk_text(ch.paragraphs, element_ids=spoken_ids)
        chunked_chapters.append({
            "number": ch.number + 1,
            "section_type": ch.section_type,
            "ordinal": ch.ordinal,
            "heading": asdict(ch.heading),
            "title": ch.title,
            "word_count": ch.word_count,
            "chunks": chunks,
        })
        print(
            f"  [{ch.number + 1:>2}/{len(chapters) + 2}] {ch.title} - "
            f"{ch.word_count:,} words, {len(chunks)} chunks"
        )
    chunked_chapters.append({
        "number": len(chapters) + 2,
        "section_type": "closing_credits",
        "ordinal": None,
        "heading": {
            "display_label": "Closing Credits",
            "display_title": "",
            "spoken_text": "",
            "element_ids": [],
        },
        "title": "Closing Credits",
        "word_count": 0,
        "chunks": [],
    })

    selected_chapters = _parse_chapter_filter(getattr(args, "chapters", None))
    generation_chapters = chunked_chapters
    if selected_chapters is not None:
        generation_chapters = [
            ch_data for ch_data in chunked_chapters
            if ch_data["number"] in selected_chapters
        ]
        found = {ch_data["number"] for ch_data in generation_chapters}
        missing = sorted(selected_chapters - found)
        if missing:
            raise ValueError(
                "--chapters selected missing chapter(s): "
                + ", ".join(str(n) for n in missing)
            )
        selected_label = ", ".join(str(ch_data["number"]) for ch_data in generation_chapters)
        print(f"\nSection filter: generating {selected_label} of {len(chunked_chapters)} sections")
        logger.info(
            "Section filter active selected=%s total_sections=%d",
            sorted(selected_chapters),
            len(chunked_chapters),
        )

    print(f"\nBook:        {book_author} - {book_title}")
    print(f"Engine:      {args.engine}")
    print(f"Build:       {build_id}  (pipeline_version={PIPELINE_VERSION})")

    if getattr(args, "dry_run", False):
        print("\n[DRY RUN] No audio generated.")
        return {
            "build_id": build_id,
            "book_dir": book_dir,
            "dry_run": True,
            "sections": [ch_data["number"] for ch_data in generation_chapters],
        }

    logger.info("Creating TTS engine and LLM provider")
    selected_device = _resolve_tts_device(args.engine, getattr(args, "device", None))
    _log_torch_device_diagnostics(selected_device)

    engine = create_engine(args.engine, device=selected_device)
    aligner = create_aligner(engine, device=selected_device)
    llm = create_llm()
    director = AudioDirector(
        engine,
        llm,
        aligner,
        cast_mode=args.cast_mode,
        performance_direction_mode=args.performance_direction,
    )

    narrator_override = (
        _voice_spec_for_override(engine, args.voice)
        if getattr(args, "voice", None)
        else None
    )
    opening_parts: list[str] = []
    remaining = REGISTRY_OPENING_CHARS
    for ch_data in chunked_chapters:
        for chunk in ch_data["chunks"]:
            if remaining <= 0:
                break
            text = chunk.text[:remaining]
            opening_parts.append(text)
            remaining -= len(text)
        if remaining <= 0:
            break
    book_ctx = BookContext(
        title=book_title,
        author=book_author,
        language=TTS_LANGUAGE,
        opening_text="\n\n".join(opening_parts),
    )
    registry_label = (
        "Building narrator-only character registry"
        if narrator_override is not None
        else "Building character registry with LLM"
    )
    with Heartbeat(logger, registry_label):
        registry = director.build_registry(
            book_ctx, narrator_voice_override=narrator_override
        )
    narrator_voice = registry.narrator_voice
    narrator_voice_id = _voice_id_for_manifest(narrator_voice)
    narrator_display = _display_name_for_voice(narrator_voice_id)
    opening_credit = (
        f"{book_title}. Written by {book_author}. Narrated by OpenShelf "
        f"using the {narrator_display} voice."
    )
    closing_credit = (
        f"The end of {book_title}, written by {book_author}, narrated by "
        f"OpenShelf using the {narrator_display} voice."
    )
    chunked_chapters[0]["heading"]["spoken_text"] = opening_credit
    chunked_chapters[0]["word_count"] = len(opening_credit.split())
    chunked_chapters[-1]["heading"]["spoken_text"] = closing_credit
    chunked_chapters[-1]["word_count"] = len(closing_credit.split())
    rendition = args.rendition or _derive_rendition(engine.name, narrator_voice)
    build_dir = os.path.join(book_dir, "audio", rendition, "builds", build_id)

    selected_chapter_numbers = [ch_data["number"] for ch_data in generation_chapters]
    run_context = make_run_context(
        build=build_id,
        pipeline_version=PIPELINE_VERSION,
        epub_path=epub_path,
        author_slug=author_slug,
        title_slug=title_slug,
        book_author=book_author,
        book_title=book_title,
        engine=engine.name,
        voice=narrator_voice_id,
        rendition=rendition,
        cast_mode=args.cast_mode,
        performance_direction_mode=args.performance_direction,
        language=TTS_LANGUAGE,
        sections=selected_chapter_numbers,
        new_voice_direction=getattr(args, "new_voice_direction", False),
    )
    run_context_path = build_run_context_path(build_dir)
    prepare_run_context(
        run_context_path,
        run_context,
        resume=getattr(args, "resume", False),
        force=getattr(args, "force", False),
    )
    logger.info(
        "Registry built narrator=%s characters=%d rendition=%s",
        narrator_voice_id,
        len(registry.characters),
        rendition,
    )

    print(f"Rendition:   {rendition}  (narrator={narrator_voice_id})")
    print(f"Cast mode:   {args.cast_mode}")
    print(f"R2 prefix:   books/{author_slug}/{title_slug}/audio/{rendition}/builds/{build_id}/")
    print(f"Local:       {os.path.abspath(build_dir)}/")
    print(f"Run context: {run_context_path}")
    director.build_dir = Path(build_dir)

    os.makedirs(book_dir, exist_ok=True)
    book_parse_path = os.path.join(book_dir, "book_parse.json")
    write_book_parse_artifact(
        book_parse_path,
        build_book_parse_artifact(
            chapters,
            epub_sha256(epub_path),
            {
                "title": book_title,
                "author": book_author,
                "source": args.source,
                "language": metadata.get("language", "en"),
            },
        ),
        force=True,
    )
    logger.info("Book parse artifact written: %s", book_parse_path)

    annotated_epub_path = os.path.join(book_dir, "book-annotated.epub")
    if getattr(args, "force", False) or not os.path.exists(annotated_epub_path):
        annotated_bytes = annotate_epub(epub_path, chapters)
        with open(annotated_epub_path, "wb") as f:
            f.write(annotated_bytes)
        print(f"\nAnnotated EPUB: {annotated_epub_path}")
    else:
        print(f"\nAnnotated EPUB already exists: {annotated_epub_path}")

    cover_path = None
    for ext in ("jpg", "png"):
        candidate = os.path.join(book_dir, f"cover.{ext}")
        if os.path.exists(candidate):
            cover_path = candidate
            break
    if not cover_path:
        cover_result = extract_cover_image(epub_path)
        if cover_result:
            img_data, media_type = cover_result
            cover_ext = "png" if "png" in media_type else "jpg"
            cover_path = os.path.join(book_dir, f"cover.{cover_ext}")
            with open(cover_path, "wb") as f:
                f.write(img_data)
            print(f"Cover extracted: {cover_path}")
        else:
            print("No cover image found in EPUB.")

    os.makedirs(build_dir, exist_ok=True)
    for ch_data in chunked_chapters:
        chunks_path = _chapter_chunks_path(build_dir, ch_data["number"])
        write_section_chunks_artifact(
            chunks_path,
            ch_data["number"],
            ch_data["section_type"],
            ch_data["ordinal"],
            ch_data["heading"],
            ch_data["chunks"],
            force=getattr(args, "force", False),
        )
        ch_data["chunks_artifact_path"] = chunks_path
    print(f"Section chunks: {build_dir}/section-NN.chunks.json")
    logger.info("Section chunk artifacts written under: %s", build_dir)

    character_registry_path = os.path.join(build_dir, "character_registry.json")
    _write_json(character_registry_path, registry.to_dict())
    print(f"Character registry: {character_registry_path}")
    logger.info("Character registry written: %s", character_registry_path)

    voice_direction_path = os.path.join(build_dir, "voice_direction.json")
    voice_direction_payload: dict = build_voice_direction_payload(
        rendition,
        build_id,
        engine.name,
        args.cast_mode,
        [],
    )

    if engine.name == "kokoro":
        print(f"\nLoading TTS pipeline on {selected_device} ...")
        with Heartbeat(logger, f"Loading Kokoro pipeline on {selected_device}"):
            engine.pipeline = load_pipeline(device=selected_device)
        print("Ready.\n")
        logger.info("Kokoro pipeline ready on %s", selected_device)
    else:
        print(f"\nUsing TTS engine: {engine.name}\n")
        logger.info("Using TTS engine: %s", engine.name)

    total_duration = 0.0
    total_skipped = 0
    chapter_durations: dict[int, float] = {}
    start = time.time()

    for ch_data in generation_chapters:
        ch_num = ch_data["number"]
        ch_title = ch_data["title"]
        chunks = ch_data["chunks"]

        print(f"  [{ch_num:>2}/{len(chunked_chapters)}] {ch_title} ({len(chunks)} body chunks)")
        chunk_texts = [c.text for c in chunks]
        chapter_direction_path = _chapter_direction_path(build_dir, ch_num)
        direction_payload = None
        direction_source = None

        if not chunks:
            direction_chapter = build_direction_chapter(
                ch_num,
                ch_title,
                [],
                [],
            )
            direction_payload = build_voice_direction_payload(
                rendition,
                build_id,
                engine.name,
                args.cast_mode,
                [direction_chapter],
            )
            _write_json(chapter_direction_path, direction_payload)
        elif getattr(args, "new_voice_direction", False):
            cache_message = (
                "voice direction cache disabled by --new-voice-direction; "
                "generating fresh direction"
            )
            print(f"    {cache_message}")
            logger.info("Chapter %d %s", ch_num, cache_message)
        elif chunks:
            cached = _load_or_copy_chapter_direction(
                book_dir=book_dir,
                build_dir=build_dir,
                chapter_number=ch_num,
                engine_name=engine.name,
                cast_mode=args.cast_mode,
                chunk_texts=chunk_texts,
                rendition=rendition,
                build_id=build_id,
                narrator_voice=narrator_voice,
            )
            if cached is not None:
                direction_payload, direction_source = cached
                cache_message = _direction_cache_hit_message(
                    direction_source,
                    chapter_direction_path,
                )
                print(f"    {cache_message}")
                logger.info("Chapter %d %s", ch_num, cache_message)
            else:
                cache_message = "voice direction cache miss; generating fresh direction"
                print(f"    {cache_message}")
                logger.info("Chapter %d %s", ch_num, cache_message)

        if direction_payload is None:
            logger.info(
                "Chapter %d/%d direction started: %s (%d chunks)",
                ch_num,
                len(chapters),
                ch_title,
                len(chunks),
            )
            with Heartbeat(logger, f"Directing chapter {ch_num}") as heartbeat:
                heartbeat.update(f"Attributing speakers for chapter {ch_num} ({len(chunks)} chunks)")
                windows = build_chunk_windows(chunk_texts)
                registry, directed_chunks = director.direct_chapter(ch_title, windows, registry)
                heartbeat.update(f"Preparing directed chunk metadata for chapter {ch_num}")
                direction_chapter = build_direction_chapter(
                    ch_data["number"],
                    ch_data["title"],
                    chunk_texts,
                    directed_chunks,
                    fallback_used=director.last_chapter_fallback_used,
                    fallback_error=director.last_chapter_error,
                )
                direction_payload = build_voice_direction_payload(
                    rendition,
                    build_id,
                    engine.name,
                    args.cast_mode,
                    [direction_chapter],
                )
                _write_json(chapter_direction_path, direction_payload)
                logger.info("Chapter %d direction snapshot written: %s", ch_num, chapter_direction_path)
            logger.info("Chapter %d direction complete", ch_num)

        direction_chapter = direction_payload["sections"][0]
        voice_direction_payload["sections"].append(direction_chapter)
        speed_summary = _direction_speed_summary(direction_chapter)
        logger.info(
            "Chapter %d direction speed summary segments=%d speeds=%s "
            "above_1x=%d narrator_above_1x=%d max_speed=%.2f",
            ch_num,
            speed_summary["segments"],
            speed_summary["speed_counts"],
            speed_summary["above_1x"],
            speed_summary["narrator_above_1x"],
            speed_summary["max_speed"],
        )

        directed_chunks_for_synthesis = directed_chunks_from_chapter_direction_artifact(
            chapter_direction_path
        )
        ends_paragraph = [
            (ci == len(chunks) - 1) or (chunks[ci].para_end != chunks[ci + 1].para_start)
            for ci in range(len(chunks))
        ]
        chunk_infos = build_chunk_infos(
            chunk_texts, ends_paragraph, directed_chunks_for_synthesis
        )

        m4a_path = os.path.join(build_dir, f"section-{ch_num:02d}.m4a")
        wav_path = os.path.join(build_dir, f"section-{ch_num:02d}.wav")
        sync_path = _chapter_sync_path(build_dir, ch_num)
        synthesis_units_path = _chapter_synthesis_units_path(build_dir, ch_num)

        print("    synthesizing + aligning ...", end=" ", flush=True)
        with Heartbeat(logger, f"Synthesizing and aligning chapter {ch_num}"):
            result, duration = synthesize_chapter_to_files(
                engine,
                chunk_infos,
                wav_path,
                m4a_path,
                sync_path,
                synthesis_units_path,
                ch_num,
                chunk_texts,
                narrator_voice=narrator_voice,
                aligner=aligner,
                keep_wav=getattr(args, "keep_wav", False),
                force=getattr(args, "force", False),
                heading_text=ch_data["heading"]["spoken_text"],
            )

        chapter_durations[ch_num] = duration
        ch_data["sync_artifact_path"] = sync_path
        total_duration += duration
        total_skipped += result.skipped_chunks

        mins = duration / 60
        skip_note = f", {result.skipped_chunks} skipped" if result.skipped_chunks else ""
        print(f"{mins:.1f} min{skip_note}")
        logger.info(
            "Chapter %d complete duration=%.2fs skipped_chunks=%d m4a=%s",
            ch_num,
            duration,
            result.skipped_chunks,
            m4a_path,
        )
        logger.info("Chapter %d sync artifact written: %s", ch_num, sync_path)
        logger.info(
            "Chapter %d synthesis units artifact written: %s",
            ch_num,
            synthesis_units_path,
        )

    elapsed = time.time() - start
    print(f"\nDone. {total_duration / 60:.1f} min of audio in {elapsed:.0f}s.")
    if total_skipped:
        print(f"Warning: {total_skipped} chunks failed TTS and were skipped.")
        logger.warning("%d chunks failed TTS and were skipped", total_skipped)
    print(f"Output: {os.path.abspath(build_dir)}/")
    logger.info(
        "Audio generation complete duration=%.2fs elapsed=%.2fs output=%s",
        total_duration,
        elapsed,
        build_dir,
    )

    _write_json(character_registry_path, registry.to_dict())
    _write_json(voice_direction_path, voice_direction_payload)
    print(f"Voice direction: {voice_direction_path}")
    logger.info("Character registry finalized: %s", character_registry_path)
    logger.info("Voice direction written: %s", voice_direction_path)

    chapter_metas = [
        SectionMeta(
            sequence=ch_data["number"],
            section_type=ch_data["section_type"],
            ordinal=ch_data["ordinal"],
            display_label=ch_data["heading"]["display_label"],
            display_title=ch_data["heading"]["display_title"],
            filename=f"section-{ch_data['number']:02d}.m4a",
            duration_seconds=chapter_durations.get(ch_data["number"], 0.0),
            word_count=(
                len(ch_data["heading"].get("spoken_text", "").split())
                + sum(len(chunk.text.split()) for chunk in ch_data["chunks"])
            ),
        )
        for ch_data in generation_chapters
        if ch_data["number"] in chapter_durations
    ]

    rendition_manifest_path = os.path.join(build_dir, "rendition-manifest.json")
    generate_rendition_manifest(
        rendition=rendition,
        build_id=build_id,
        voice=narrator_voice_id,
        engine=engine.name,
        pipeline_version=PIPELINE_VERSION,
        sections=chapter_metas,
        output_dir=build_dir,
    )
    print(f"Rendition manifest: {rendition_manifest_path}")
    logger.info("Rendition manifest written: %s", rendition_manifest_path)

    section_data_path = assemble_section_data(
        build_dir,
        rendition,
        build_id,
        selected_chapters={ch_data["number"] for ch_data in generation_chapters},
        force=True,
    )
    print(f"Section data: {section_data_path}")
    logger.info("Section data written: %s", section_data_path)

    new_entry = generate_rendition_entry(
        voice=narrator_voice_id,
        engine=engine.name,
        display=_display_name_for_voice(narrator_voice_id),
        build_id=build_id,
    )
    book_manifest_path = generate_book_manifest(
        author=book_author,
        title=book_title,
        source=args.source,
        renditions={rendition: new_entry},
        output_dir=book_dir,
    )
    print(f"Book manifest (local): {book_manifest_path}")
    logger.info("Book manifest written: %s", book_manifest_path)

    uploaded: list[str] = []
    if getattr(args, "upload", False):
        print("\nUploading to R2 ...")
        logger.info(
            "Uploading to R2 bucket=%s prefix=books/%s/%s/",
            R2_BUCKET,
            author_slug,
            title_slug,
        )
        with Heartbeat(logger, "Uploading build to R2"):
            uploaded = upload_build(
                book_dir=book_dir,
                rendition=rendition,
                build_id=build_id,
                source=args.source,
                force=getattr(args, "force", False),
            )
        print("Upload complete.")
        print(f"R2 prefix: books/{author_slug}/{title_slug}/")
        logger.info("Upload complete: books/%s/%s/", author_slug, title_slug)

    return {
        "build_id": build_id,
        "book_dir": book_dir,
        "build_dir": build_dir,
        "rendition": rendition,
        "voice": narrator_voice_id,
        "section_data_path": section_data_path,
        "voice_direction_path": voice_direction_path,
        "uploaded": uploaded,
    }


def add_run_arguments(parser: argparse.ArgumentParser, *, positional_epub: bool = False) -> None:
    from openshelf.config import CAST_MODE, PERFORMANCE_DIRECTION_MODE, TTS_ENGINE

    if positional_epub:
        parser.add_argument("epub", help="Path to EPUB file")
    else:
        parser.add_argument("--epub", required=True, help="Path to EPUB file")
    parser.add_argument("--output", "-o", default="audio", help="Output directory (default: audio/)")
    parser.add_argument("--source", default="gutenberg", help="Book source (gutenberg, standard-ebooks)")
    parser.add_argument("--engine", default=TTS_ENGINE, help=f"TTS engine (default: {TTS_ENGINE})")
    parser.add_argument("--voice", default=None, help="Narrator voice override; skips LLM narrator selection")
    parser.add_argument(
        "--cast-mode",
        default=CAST_MODE,
        choices=["solo", "multicast"],
        help=f"Voice casting mode: solo narrator or experimental multicast (default: {CAST_MODE})",
    )
    parser.add_argument(
        "--performance-direction",
        default=PERFORMANCE_DIRECTION_MODE,
        choices=list(_PERFORMANCE_DIRECTION_CHOICES),
        help=(
            "Performance/emotion direction mode for engines that support it "
            f"(default: {PERFORMANCE_DIRECTION_MODE})"
        ),
    )
    parser.add_argument("--rendition", default=None, help="Rendition slug (default: derived from engine + narrator)")
    parser.add_argument("--device", default=None, help="Device: cuda, mps, cpu (default: auto)")
    parser.add_argument(
        "--sections",
        "--chapters",
        dest="chapters",
        default=None,
        help="Local sample filter: section sequence/ranges, e.g. 2 or 2,4-5",
    )
    parser.add_argument("--dry-run", action="store_true", help="Parse and chunk only, no audio")
    parser.add_argument("--keep-wav", action="store_true", help="Keep WAV files after encoding")
    parser.add_argument("--upload", action="store_true", help="Upload to Cloudflare R2 after conversion")
    parser.add_argument(
        "--new-voice-direction",
        action="store_true",
        help="Force fresh per-chapter voice direction instead of reusing local cached direction",
    )
    parser.add_argument("--log-dir", default="logs", help="Local log directory (default: logs/)")
    parser.add_argument("--build-id", default=None, help="Target 16-hex build ID for resume/force runs")
    parser.add_argument("--resume", action="store_true", help="Resume an existing build only if run.json matches")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite artifacts in the selected build prefix",
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="openshelf-pipeline dag")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run = subparsers.add_parser("run")
    add_run_arguments(run)

    parse = subparsers.add_parser("parse")
    parse.add_argument("--epub", required=True)
    parse.add_argument("--out", required=True)
    parse.add_argument("--source", default="")
    parse.add_argument("--force", action="store_true")

    chunk = subparsers.add_parser("chunk")
    chunk.add_argument("--book-parse", required=True)
    chunk.add_argument("--build-dir", required=True)
    chunk.add_argument("--sections", "--chapters", dest="chapters", default=None)
    chunk.add_argument("--force", action="store_true")

    registry = subparsers.add_parser("registry")
    registry.add_argument("--book-parse", required=True)
    registry.add_argument("--build-dir", required=True)
    registry.add_argument("--engine", default=None)
    registry.add_argument("--voice", default=None)
    registry.add_argument("--force", action="store_true")

    assemble = subparsers.add_parser("assemble")
    assemble.add_argument("--build-dir", required=True)
    assemble.add_argument("--rendition", required=True)
    assemble.add_argument("--build-id", required=True)
    assemble.add_argument("--output")
    assemble.add_argument("--force", action="store_true")

    coverage = subparsers.add_parser("coverage")
    coverage.add_argument("--build-dir", required=True)
    coverage.add_argument("--json", action="store_true")

    direct = subparsers.add_parser("direct")
    direct.add_argument("--build-dir", required=True)
    direct.add_argument("--chapter", type=int, required=True)
    direct.add_argument("--engine", default=None)
    direct.add_argument("--cast-mode", default="solo", choices=["solo", "multicast"])
    direct.add_argument(
        "--performance-direction",
        default="batched",
        choices=_PERFORMANCE_DIRECTION_CHOICES,
    )
    direct.add_argument("--device", default=None)
    direct.add_argument("--force", action="store_true")

    synth = subparsers.add_parser("synth")
    synth.add_argument("--build-dir", required=True)
    synth.add_argument("--chapter", type=int, required=True)
    synth.add_argument("--engine", default=None)
    synth.add_argument("--device", default=None)
    synth.add_argument("--voice", default=None)
    synth.add_argument("--keep-wav", action="store_true")
    synth.add_argument("--force", action="store_true")

    sync = subparsers.add_parser("sync")
    sync.add_argument("--build-dir", required=True)
    sync.add_argument("--chapter", type=int, required=True)
    sync.add_argument("--device", default="cpu")
    sync.add_argument("--language", default="en")
    sync.add_argument("--force", action="store_true")

    repair_pauses = subparsers.add_parser("repair-pauses")
    repair_pauses.add_argument("--build-dir", required=True)
    repair_pauses.add_argument("--chapter", type=int, required=True)
    repair_pauses.add_argument("--force", action="store_true")

    upload = subparsers.add_parser("upload")
    upload.add_argument("--book-dir", required=True)
    upload.add_argument("--rendition", required=True)
    upload.add_argument("--build-id", required=True)
    upload.add_argument("--source", default=None)
    upload.add_argument("--force", action="store_true")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "parse":
        path = parse_book(
            epub_path=args.epub,
            output_path=args.out,
            source=args.source,
            force=args.force,
        )
        print(path)
        return 0

    if args.command == "run":
        run_book(args)
        return 0

    if args.command == "chunk":
        try:
            selected = _parse_chapter_filter(args.chapters)
        except ValueError as e:
            parser.error(f"invalid --chapters value: {e}")
        written = chunk_chapters(
            book_parse_path=args.book_parse,
            build_dir=args.build_dir,
            selected_chapters=selected,
            force=args.force,
        )
        for path in written:
            print(path)
        return 0

    if args.command == "registry":
        path = build_registry(
            book_parse_path=args.book_parse,
            build_dir=args.build_dir,
            engine_name=args.engine,
            voice=args.voice,
            force=args.force,
        )
        print(path)
        return 0

    if args.command == "assemble":
        path = assemble_section_data(
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

    if args.command == "direct":
        path = direct_chapter(
            build_dir=args.build_dir,
            chapter_number=args.chapter,
            engine_name=args.engine,
            cast_mode=args.cast_mode,
            performance_direction_mode=args.performance_direction,
            device=args.device,
            force=args.force,
        )
        print(path)
        return 0

    if args.command == "synth":
        path = synth_chapter(
            build_dir=args.build_dir,
            chapter_number=args.chapter,
            engine_name=args.engine,
            device=args.device,
            voice=args.voice,
            keep_wav=args.keep_wav,
            force=args.force,
        )
        print(path)
        return 0

    if args.command == "sync":
        path = sync_chapter(
            build_dir=args.build_dir,
            chapter_number=args.chapter,
            device=args.device,
            language=args.language,
            force=args.force,
        )
        print(path)
        return 0

    if args.command == "repair-pauses":
        path = repair_pauses_chapter(
            build_dir=args.build_dir,
            chapter_number=args.chapter,
            force=args.force,
        )
        print(path)
        return 0

    if args.command == "upload":
        keys = upload_build(
            book_dir=args.book_dir,
            rendition=args.rendition,
            build_id=args.build_id,
            source=args.source,
            force=args.force,
        )
        for key in keys:
            print(key)
        return 0

    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
