"""Isolate and profile the LLM voice-directing step (no TTS synthesis).

The directing step dominates pipeline runtime: for each chunk the AudioDirector
may issue a speaker-attribution call and (when the engine advertises
performance_direction) an emotion call. With a reasoning model like gpt-5-nano
each call costs tens of seconds, so this script measures the real per-call and
per-chunk latency on a few chunks and projects the cost over the whole book —
without loading the TTS model or generating any audio.

Usage:
    openshelf-pipeline profile direction --epub <path>
    openshelf-pipeline profile direction --epub <path> --chapter 1 --chunks 4
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import time
from pathlib import Path
from typing import Sequence

from ebooklib import epub as _epub_lib

from openshelf.config import CAST_MODE, REGISTRY_OPENING_CHARS, TTS_LANGUAGE
from openshelf.pipeline.engines import create_engine
from openshelf.pipeline.epub_parser import parse_epub
from openshelf.pipeline.llm import LLMClient, OpenAILLM, create_llm
from openshelf.pipeline.text_chunker import chunk_text
from openshelf.pipeline.voice_director import (
    AudioDirector,
    BookContext,
    CharacterProfile,
    CharacterRegistry,
    ChunkWindow,
    SPEED_MAP,
    SpeakerSpan,
    assign_voices,
    annotate_chunk_speakers,
    merge_adjacent_segments,
    narrator_segment,
    needs_annotation,
    spans_to_segments,
    validate_spans,
    validate_span_speakers,
)


def _classify(system: str) -> str:
    if "casting voices" in system:
        return "registry"
    if (
        "labeling who speaks" in system
        or "assigning speakers" in system
        or "audiobook performance direction to pre-extracted" in system
    ):
        return "attribution"
    if "directing an audiobook performance" in system:
        return "emotion"
    if "one full audiobook chapter" in system:
        return "chapter_pass"
    return "other"


class TimingLLM:
    """Wraps an LLMClient and records latency + kind of every call."""

    def __init__(self, wrapped: LLMClient):
        self.wrapped = wrapped
        self.name = f"timing({wrapped.name})"
        self.calls: list[tuple[str, float]] = []

    def complete_json(self, *, system: str, user: str, schema: dict) -> dict:
        kind = _classify(system)
        start = time.monotonic()
        try:
            return self.wrapped.complete_json(system=system, user=user, schema=schema)
        finally:
            elapsed = time.monotonic() - start
            self.calls.append((kind, elapsed))
            print(f"      LLM {kind:<12} {elapsed:6.1f}s", flush=True)


def _opening_text(chunked_chapters: list[dict], max_chars: int) -> str:
    parts: list[str] = []
    total = 0
    for ch_data in chunked_chapters:
        for chunk in ch_data["chunks"]:
            parts.append(chunk.text)
            total += len(chunk.text)
            if total >= max_chars:
                return "\n\n".join(parts)[:max_chars]
    return "\n\n".join(parts)[:max_chars]


CHAPTER_PASS_SCHEMA = {
    "type": "object",
    "properties": {
        "narrator_voice_id": {"type": "string"},
        "characters": {"type": "array"},
        "spans": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "start": {"type": "integer"},
                    "end": {"type": "integer"},
                    "speaker": {"type": "string"},
                    "emotion": {"type": "string"},
                    "speed": {"type": "string"},
                    "synthesis_text": {"type": "string"},
                    "pause_after_ms": {"type": "integer"},
                },
                "required": ["start", "end", "speaker", "emotion", "speed"],
            },
        },
    },
    "required": ["narrator_voice_id", "characters", "spans"],
}


def _voice_from_id(voice_id: str | None, pool):
    by_id = {voice.id: voice for voice in pool}
    by_preset = {
        voice.preset_name: voice for voice in pool
        if voice.preset_name is not None
    }
    if voice_id and voice_id in by_id:
        return by_id[voice_id]
    if voice_id and voice_id in by_preset:
        return by_preset[voice_id]
    return pool[0]


def _chapter_text_and_ranges(chunks) -> tuple[str, list[tuple[int, int]]]:
    parts = []
    ranges = []
    cursor = 0
    for i, chunk in enumerate(chunks):
        if i:
            parts.append("\n\n")
            cursor += 2
        start = cursor
        parts.append(chunk.text)
        cursor += len(chunk.text)
        ranges.append((start, cursor))
    return "".join(parts), ranges


def _slice_spans_for_range(spans: list[SpeakerSpan], start: int, end: int) -> list[SpeakerSpan]:
    sliced = []
    for span in spans:
        overlap_start = max(start, span.start)
        overlap_end = min(end, span.end)
        if overlap_start < overlap_end:
            sliced.append(SpeakerSpan(
                start=overlap_start - start,
                end=overlap_end - start,
                speaker=span.speaker,
            ))
    return sliced


def _run_chapter_pass(llm, engine, chunks, args):
    chapter_text, chunk_ranges = _chapter_text_and_ranges(chunks)
    emotion_cfg = engine.emotion_prompt_config()
    emotions = emotion_cfg.emotion_vocabulary if emotion_cfg else ["neutral"]
    speed_labels = emotion_cfg.speed_labels if emotion_cfg else ["normal"]
    injection_rules = emotion_cfg.injection_rules if emotion_cfg else ""
    voice_pool = engine.available_voices()

    system = (
        "You are casting and directing one full audiobook chapter in a single pass.\n"
        "First identify speaking characters present in this chapter, including aliases "
        "and short descriptions. Then output spans that exactly tile CHAPTER_TEXT "
        "with 0-based character offsets into the unmodified CHAPTER_TEXT.\n"
        "Use narrator for narration and attribution text. Use character speakers only "
        "for their actual spoken words, quoted speech, or explicit spoken thoughts. "
        "Do not use pronouns as aliases.\n"
        "For every span assign one emotion and one speaking pace. "
        f"{injection_rules}\n"
        "synthesis_text may add Kokoro performance cues, punctuation shaping, or SSML-like cues, "
        "but offsets must refer only to the original span text. "
        "pause_after_ms must be between 0 and 2000."
    )
    user = (
        f"Available voices:\n{engine.registry_prompt_config().voice_pool_description}\n\n"
        f"Available emotions: {', '.join(emotions)}\n"
        f"Pace labels: {', '.join(speed_labels)}\n\n"
        f"CHAPTER_TEXT:\n---\n{chapter_text}\n---"
    )
    response = llm.complete_json(system=system, user=user, schema=CHAPTER_PASS_SCHEMA)

    narrator_voice = _voice_from_id(response.get("narrator_voice_id"), voice_pool)
    raw_characters = []
    for item in response.get("characters", []):
        if isinstance(item, str):
            raw_characters.append({
                "canonical": item,
                "aliases": [],
                "description": "",
                "gender": "unknown",
                "age": "unknown",
            })
        elif isinstance(item, dict):
            raw_characters.append(item)
    assigned = assign_voices(raw_characters, voice_pool)
    characters = {}
    for item in raw_characters:
        canonical = str(item.get("canonical", "")).strip()
        if not canonical:
            continue
        characters[canonical] = CharacterProfile(
            canonical=canonical,
            aliases=[str(alias) for alias in item.get("aliases", [])],
            description=str(item.get("description", "")),
            gender=str(item.get("gender", "unknown")),
            age=str(item.get("age", "unknown")),
            persona_of=item.get("persona_of"),
            voice=assigned.get(canonical, narrator_voice),
        )
    registry = CharacterRegistry(narrator_voice=narrator_voice, characters=characters)

    raw_spans = response.get("spans", [])
    spans = [
        SpeakerSpan(
            start=int(item["start"]),
            end=int(item["end"]),
            speaker=str(item["speaker"]),
        )
        for item in raw_spans
    ]
    validate_spans(spans, chapter_text)

    artifact_chunks = []
    for ci, chunk in enumerate(chunks):
        start, end = chunk_ranges[ci]
        chunk_spans = _slice_spans_for_range(spans, start, end)
        validate_spans(chunk_spans, chunk.text)
        segments = spans_to_segments(chunk_spans, chunk.text, registry)
        enriched = []
        for chunk_span, segment in zip(chunk_spans, segments):
            original_start = start + chunk_span.start
            item = None
            for raw in raw_spans:
                if int(raw["start"]) <= original_start < int(raw["end"]):
                    item = raw
                    break
            emotion = str(item.get("emotion", "neutral")) if item else "neutral"
            speed_label = str(item.get("speed", "normal")) if item else "normal"
            enriched.append({
                "text": item.get("synthesis_text") if item and item.get("synthesis_text") else segment.text,
                "original_text": segment.text,
                "speaker": segment.speaker,
                "voice": dataclasses.asdict(segment.voice),
                "emotion": emotion,
                "speed": SPEED_MAP.get(speed_label, 1.0),
                "pause_after_ms": int(item.get("pause_after_ms", 0) or 0) if item else 0,
            })
        artifact_chunks.append({
            "index": ci,
            "text": chunk.text,
            "has_quotes": needs_annotation(chunk.text),
            "segments": enriched,
        })

    return registry, artifact_chunks, chapter_text, raw_spans


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="openshelf-pipeline profile direction",
        description=__doc__,
    )
    ap.add_argument("--epub", required=True, help="Path to EPUB file")
    ap.add_argument("--engine", default="kokoro", help="TTS engine (default: kokoro)")
    ap.add_argument("--chapter", type=int, default=1, help="1-based chapter index to sample")
    ap.add_argument("--chunks", default="4", help="How many chunks to direct, or 'all'")
    ap.add_argument("--model", default=None, help="Override OpenAI model (e.g. gpt-4o-mini)")
    ap.add_argument("--max-output-tokens", type=int, default=None,
                    help="Override OpenAI max output tokens for large chapter-pass outputs")
    ap.add_argument("--artifact", default=None, help="Write registry and directed chunk artifact JSON")
    ap.add_argument("--chapter-pass", action="store_true",
                    help="Experiment: one LLM call annotates/casts the full chapter, then projects to chunks")
    ap.add_argument("--cast-mode", default=CAST_MODE, choices=["solo", "multicast"],
                    help=f"Voice casting mode to profile (default: {CAST_MODE})")
    ap.add_argument("--chunk-attribution", action="store_true",
                    help="Use legacy one attribution call per quote-bearing chunk")
    ap.add_argument("--no-emotion", action="store_true",
                    help="Skip the emotion/performance pass to measure its cost")
    ap.add_argument("--debug-validation", action="store_true",
                    help="Record raw attribution spans and validation errors in the artifact")
    args = ap.parse_args(argv)

    print(f"Parsing {args.epub} ...", flush=True)
    chapters = parse_epub(args.epub)
    epub_meta = _epub_lib.read_epub(args.epub)
    title_meta = epub_meta.get_metadata("DC", "title")
    author_meta = epub_meta.get_metadata("DC", "creator")
    book_title = title_meta[0][0] if title_meta else Path(args.epub).stem
    book_author = author_meta[0][0] if author_meta else "unknown"
    chunked = []
    for ch in chapters:
        spoken_ids = [el.id for el in ch.elements if el.spoken]
        chunked.append({
            "number": ch.number,
            "title": ch.title,
            "chunks": chunk_text(ch.paragraphs, element_ids=spoken_ids),
        })
    total_chunks = sum(len(c["chunks"]) for c in chunked)
    quote_chunks = sum(
        1 for c in chunked for ch in c["chunks"] if needs_annotation(ch.text)
    )
    print(f"Book: {len(chapters)} chapters, {total_chunks} chunks "
          f"({quote_chunks} with quotes)\n", flush=True)

    engine = create_engine(args.engine)
    if args.no_emotion:
        engine.capabilities.performance_direction = False
        engine.capabilities.emotion_control = False

    if args.model or args.max_output_tokens:
        base_llm = OpenAILLM(model=args.model, max_output_tokens=args.max_output_tokens)
    else:
        base_llm = create_llm()
    llm = TimingLLM(base_llm)
    print(f"LLM provider: {base_llm.name} "
          f"model={getattr(base_llm, 'model', '?')}  "
          f"emotion_pass={not args.no_emotion}\n", flush=True)

    director = AudioDirector(engine, llm, aligner=None, cast_mode=args.cast_mode)

    ctx = BookContext(
        title=book_title,
        author=book_author,
        language=TTS_LANGUAGE,
        opening_text=_opening_text(chunked, REGISTRY_OPENING_CHARS),
    )
    print("Building registry ...", flush=True)
    t0 = time.monotonic()
    registry = director.build_registry(ctx)
    print(f"  registry built in {time.monotonic() - t0:.1f}s  "
          f"narrator={registry.narrator_voice.id} "
          f"characters={len(registry.characters)}\n", flush=True)

    ch_idx = max(0, min(args.chapter - 1, len(chunked) - 1))
    chunks = chunked[ch_idx]["chunks"]
    n = len(chunks) if str(args.chunks).lower() == "all" else min(int(args.chunks), len(chunks))
    print(f"Directing chapter {chunked[ch_idx]['number']} "
          f"({chunked[ch_idx]['title']}), {n} of {len(chunks)} chunks:\n", flush=True)

    if args.chapter_pass:
        selected_chunks = chunks[:n]
        t = time.monotonic()
        registry, artifact_chunks, chapter_text, raw_spans = _run_chapter_pass(
            llm,
            engine,
            selected_chunks,
            args,
        )
        elapsed = time.monotonic() - t
        print(f"  chapter-pass directed in {elapsed:.1f}s  "
              f"characters={len(registry.characters)} spans={len(raw_spans)}", flush=True)
        if args.artifact:
            payload = {
                "epub": args.epub,
                "engine": args.engine,
                "mode": "chapter-pass",
                "cast_mode": args.cast_mode,
                "llm_provider": base_llm.name,
                "llm_model": getattr(base_llm, "model", None),
                "emotion_pass": not args.no_emotion,
                "chapter": {
                    "number": chunked[ch_idx]["number"],
                    "title": chunked[ch_idx]["title"],
                    "chunks_directed": n,
                    "chunks_total": len(chunks),
                    "chapter_text_length": len(chapter_text),
                    "raw_span_count": len(raw_spans),
                    "elapsed_seconds": round(elapsed, 3),
                },
                "registry": registry.to_dict(),
                "llm_calls": [
                    {"kind": kind, "elapsed_seconds": round(dt, 3)}
                    for kind, dt in llm.calls
                ],
                "raw_spans": raw_spans,
                "chunks": artifact_chunks,
            }
            artifact_path = Path(args.artifact)
            artifact_path.parent.mkdir(parents=True, exist_ok=True)
            artifact_path.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            print(f"\nArtifact written: {artifact_path}", flush=True)
        return 0

    if not args.chunk_attribution:
        selected_chunks = chunks[:n]
        windows = [
            ChunkWindow(
                prev=selected_chunks[ci - 1].text if ci > 0 else "",
                text=chunk.text,
                next=selected_chunks[ci + 1].text if ci < len(selected_chunks) - 1 else "",
            )
            for ci, chunk in enumerate(selected_chunks)
        ]
        print("  chapter-level attribution:", flush=True)
        t = time.monotonic()
        registry, directed_chunks = director.direct_chapter(
            chunked[ch_idx]["title"],
            windows,
            registry,
        )
        elapsed = time.monotonic() - t
        per_chunk = [elapsed / max(1, len(selected_chunks)) for _ in selected_chunks]
        artifact_chunks = []
        for ci, (chunk, segments) in enumerate(zip(selected_chunks, directed_chunks)):
            artifact_chunks.append({
                "index": ci,
                "text": chunk.text,
                "has_quotes": needs_annotation(chunk.text),
                "elapsed_seconds": round(elapsed, 3) if ci == 0 else 0.0,
                "segments": [
                    {
                        "speaker": segment.speaker,
                        "voice": dataclasses.asdict(segment.voice),
                        "original_text": segment.original_text or segment.text,
                        "synthesis_text": segment.text,
                        "emotion": segment.emotion,
                        "speed": segment.speed,
                        "pause_after_ms": segment.pause_after_ms,
                        "delivery_type": segment.delivery_type,
                        "voice_policy": segment.voice_policy,
                        "join_policy": segment.join_policy,
                    }
                    for segment in segments
                ],
            })
        print(f"    -> chapter directed in {elapsed:.1f}s", flush=True)
        if director.last_chapter_fallback_used:
            print(f"       fallback used: {director.last_chapter_error}", flush=True)
        if args.artifact:
            payload = {
                "epub": args.epub,
                "engine": args.engine,
                "mode": "chapter-attribution",
                "cast_mode": args.cast_mode,
                "llm_provider": base_llm.name,
                "llm_model": getattr(base_llm, "model", None),
                "emotion_pass": not args.no_emotion,
                "chapter": {
                    "number": chunked[ch_idx]["number"],
                    "title": chunked[ch_idx]["title"],
                    "chunks_directed": n,
                    "chunks_total": len(chunks),
                    "elapsed_seconds": round(elapsed, 3),
                    "fallback_used": director.last_chapter_fallback_used,
                    "fallback_error": director.last_chapter_error,
                },
                "registry": registry.to_dict(),
                "llm_calls": [
                    {"kind": kind, "elapsed_seconds": round(dt, 3)}
                    for kind, dt in llm.calls
                ],
                "chunks": artifact_chunks,
            }
            artifact_path = Path(args.artifact)
            artifact_path.parent.mkdir(parents=True, exist_ok=True)
            artifact_path.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            print(f"\nArtifact written: {artifact_path}", flush=True)

        _summary(llm, per_chunk, total_chunks, quote_chunks, args, total_chapters=len(chunked))
        return 0

    per_chunk: list[float] = []
    artifact_chunks: list[dict] = []
    for ci in range(n):
        has_q = needs_annotation(chunks[ci].text)
        print(f"  chunk {ci + 1}/{n}  ({len(chunks[ci].text)} chars, "
              f"quotes={has_q}):", flush=True)
        window = ChunkWindow(
            prev=chunks[ci - 1].text if ci > 0 else "",
            text=chunks[ci].text,
            next=chunks[ci + 1].text if ci < len(chunks) - 1 else "",
        )
        debug_spans = None
        debug_error = None
        t = time.monotonic()
        if args.debug_validation and has_q:
            try:
                spans = annotate_chunk_speakers(
                    window,
                    registry,
                    llm,
                    engine.annotation_prompt_config(),
                )
                debug_spans = [dataclasses.asdict(span) for span in spans]
                validate_spans(spans, window.text)
                validate_span_speakers(spans, registry)
                segments = merge_adjacent_segments(spans_to_segments(spans, window.text, registry))
                if "".join(segment.text for segment in segments) != window.text:
                    raise ValueError("segments do not preserve chunk text")
            except Exception as e:
                debug_error = f"{type(e).__name__}: {e}"
                segments = narrator_segment(window.text, registry)
        else:
            segments = director.direct_chunk(window, registry)
        dt = time.monotonic() - t
        per_chunk.append(dt)
        artifact_chunks.append({
            "index": ci,
            "text": chunks[ci].text,
            "has_quotes": has_q,
            "elapsed_seconds": round(dt, 3),
            "debug_spans": debug_spans,
            "debug_error": debug_error,
            "segments": [
                {
                    "speaker": segment.speaker,
                    "voice": dataclasses.asdict(segment.voice),
                    "original_text": segment.original_text or segment.text,
                    "synthesis_text": segment.text,
                    "emotion": segment.emotion,
                    "speed": segment.speed,
                    "pause_after_ms": segment.pause_after_ms,
                    "delivery_type": segment.delivery_type,
                    "voice_policy": segment.voice_policy,
                    "join_policy": segment.join_policy,
                }
                for segment in segments
            ],
        })
        print(f"    -> chunk directed in {dt:.1f}s", flush=True)

    if args.artifact:
        payload = {
            "epub": args.epub,
            "engine": args.engine,
            "cast_mode": args.cast_mode,
            "llm_provider": base_llm.name,
            "llm_model": getattr(base_llm, "model", None),
            "emotion_pass": not args.no_emotion,
            "chapter": {
                "number": chunked[ch_idx]["number"],
                "title": chunked[ch_idx]["title"],
                "chunks_directed": n,
                "chunks_total": len(chunks),
            },
            "registry": registry.to_dict(),
            "llm_calls": [
                {"kind": kind, "elapsed_seconds": round(elapsed, 3)}
                for kind, elapsed in llm.calls
            ],
            "chunks": artifact_chunks,
        }
        artifact_path = Path(args.artifact)
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"\nArtifact written: {artifact_path}", flush=True)

    _summary(llm, per_chunk, total_chunks, quote_chunks, args, total_chapters=len(chunked))
    return 0


def _summary(llm, per_chunk, total_chunks, quote_chunks, args, total_chapters=None) -> None:
    chunk_calls = [c for c in llm.calls if c[0] != "registry"]
    by_kind: dict[str, list[float]] = {}
    for kind, dt in chunk_calls:
        by_kind.setdefault(kind, []).append(dt)

    print("\n" + "=" * 60)
    print("PER-CALL LATENCY (excluding registry)")
    print("=" * 60)
    for kind, times in by_kind.items():
        avg = sum(times) / len(times)
        print(f"  {kind:<12} n={len(times):<3} avg={avg:6.1f}s "
              f"min={min(times):5.1f}s max={max(times):5.1f}s")

    if per_chunk:
        avg_chunk = sum(per_chunk) / len(per_chunk)
        print(f"\nPER-CHUNK: avg={avg_chunk:.1f}s over {len(per_chunk)} chunks")

        # Project full book. Every chunk gets an emotion call (if enabled);
        # only quote chunks get an attribution call.
        attr_times = by_kind.get("attribution", [])
        emo_times = by_kind.get("emotion", [])
        attr_avg = sum(attr_times) / len(attr_times) if attr_times else 0.0
        emo_avg = sum(emo_times) / len(emo_times) if emo_times else 0.0

        chapter_level = not args.chunk_attribution and not args.chapter_pass
        projected = (total_chapters or 0) * attr_avg if chapter_level else quote_chunks * attr_avg
        if not args.no_emotion:
            projected += total_chunks * emo_avg
        print("\n" + "=" * 60)
        print("PROJECTED FULL-BOOK DIRECTING (serial)")
        print("=" * 60)
        if chapter_level:
            print(f"  attribution: {total_chapters} chapters x {attr_avg:.1f}s "
                  f"= {(total_chapters or 0) * attr_avg / 60:.1f} min")
        else:
            print(f"  attribution: {quote_chunks} quote-chunks x {attr_avg:.1f}s "
                  f"= {quote_chunks * attr_avg / 60:.1f} min")
        if not args.no_emotion:
            print(f"  emotion:     {total_chunks} chunks x {emo_avg:.1f}s "
                  f"= {total_chunks * emo_avg / 60:.1f} min")
        print(f"  TOTAL ~= {projected / 60:.1f} min "
              f"({projected / 3600:.1f} h) for directing alone")


if __name__ == "__main__":
    raise SystemExit(main())
