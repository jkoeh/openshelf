"""Opt-in real-model TTS comparison; never publishes or changes engine defaults."""

from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import html
import importlib.metadata
import json
from pathlib import Path
import platform
import time


MODEL_ID = "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice"
SAMPLES = {
    "zh": {
        "prose": "雨停以后，林先生推开书房的窗户。远处的山色渐渐清晰，院子里的桂花还带着水珠。他翻开旧书，轻声读了起来。",
        "dialogue": "小梅问：“你明天还来吗？”陈先生笑了笑，说：“当然。我答应过你，要把这个故事讲完。”她低下头，终于松了一口气。",
        "names-numbers": "王安石与苏轼在江宁谈起往事。那是1076年，农历三月初五。账上记着32两银子，另有250文钱。",
        "classical": "夜靜風清，明月照於庭前。客曰：“山川雖遠，故人之情未改。”主人起而相送，行至溪橋，回首但見燈火微明。",
    },
    "en": {
        "prose": "After the rain, Mr. Lin opened the library window. The distant hills emerged from the mist. He turned a yellowed page and began to read, quietly at first.",
        "dialogue": "Will you come back tomorrow? she asked. Of course, he said with a gentle laugh. I promised to finish the story. She looked down, and finally breathed a sigh of relief.",
        "names-numbers": "Dr. Eleanor Hughes arrived in Edinburgh on March fifth, nineteen twenty-six. Her notebook listed thirty-two silver coins, two hundred and fifty pennies, and one unanswered question.",
    },
}
INSTRUCTIONS = {
    "zh": "用自然、温和的普通话朗读，像讲述文学故事。语速平稳，标点处自然停顿，对话有适度的情感，保持同一个叙述者的声音。",
    "en": "Read as a warm, natural literary narrator with steady pacing, clear pronunciation, natural pauses and restrained emotion in dialogue. Keep a consistent narrator voice.",
}


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _versions() -> dict:
    result = {"python": platform.python_version(), "platform": platform.platform()}
    for name in ("qwen-tts", "torch", "torchaudio", "transformers", "kokoro", "misaki", "chatterbox-tts", "whisperx"):
        try:
            result[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            pass
    return result


def _write_report(directory: Path, report: dict) -> None:
    temporary = directory / "report.json.tmp"
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(directory / "report.json")
    cards = []
    for sample in report["samples"]:
        text, cursor, parts = sample["text"], 0, []
        alignment = sample.get("alignment", {})
        for word in alignment.get("words", []):
            start = text.find(word["word"], cursor)
            if start < 0:
                continue
            parts.append(html.escape(text[cursor:start]))
            parts.append(f'<span data-start="{float(word["start"])}" data-end="{float(word["end"])}">{html.escape(word["word"])}</span>')
            cursor = start + len(word["word"])
        parts.append(html.escape(text[cursor:]))
        coverage = alignment.get("coverage_ratio")
        detail = f'{sample["audio_seconds"]:.2f}s audio · {sample["synthesis_seconds"]:.2f}s synthesis · RTF {sample["rtf"]:.2f}'
        if coverage is not None:
            detail += f' · alignment coverage {coverage:.1%}'
        cards.append(f'<article><h2>{html.escape(sample["id"])}</h2><p>{detail}</p><audio controls src="{html.escape(sample["wav"], quote=True)}"></audio><p class="text">{"".join(parts)}</p></article>')
    title = f'{report["engine"]} · {report["voice"]} · {report["language"]}'
    page = '''<!doctype html><html><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>TTS comparison</title>
<style>body{max-width:850px;margin:40px auto;padding:0 20px;font:18px/1.8 system-ui;background:#faf8f2;color:#25241f}article{margin:30px 0;padding:20px;background:white;border:1px solid #ddd}audio{width:100%}.text{white-space:pre-wrap}.active{background:#ffe28a}h2{font-size:20px}</style><body>'''
    page += f'<h1>{html.escape(title)}</h1><p>Local short-sample comparison. RTF below 1 means faster than playback. Forced alignment does not verify pronunciation or narration quality.</p>' + "".join(cards)
    page += '''<script>document.querySelectorAll('article').forEach(card=>{const audio=card.querySelector('audio');audio.addEventListener('timeupdate',()=>card.querySelectorAll('[data-start]').forEach(span=>span.classList.toggle('active',audio.currentTime>=Number(span.dataset.start)&&audio.currentTime<Number(span.dataset.end))));});</script></body></html>'''
    (directory / "index.html").write_text(page, encoding="utf-8")


def _load_runtime(args, torch):
    """Return a synthesis callable and the actual model identity."""
    if args.engine == "qwen3":
        from qwen_tts import Qwen3TTSModel

        kwargs = {
            "device_map": args.device,
            "dtype": torch.bfloat16 if args.device.startswith("cuda") else torch.float32,
            "attn_implementation": "sdpa",
        }
        if args.revision:
            kwargs["revision"] = args.revision
        runtime = Qwen3TTSModel.from_pretrained(MODEL_ID, **kwargs)
        if args.voice.lower() not in {voice.lower() for voice in runtime.get_supported_speakers()}:
            raise ValueError(f"Unsupported Qwen speaker: {args.voice}")
        config = runtime.model.config

        def synthesize(text):
            wavs, rate = runtime.generate_custom_voice(
                text=text, language="Chinese" if args.language == "zh" else "English",
                speaker=args.voice, instruct=INSTRUCTIONS[args.language],
                max_new_tokens=2048,
            )
            return wavs[0], rate

        return synthesize, {
            "id": MODEL_ID,
            "requested_revision": args.revision or "main",
            "resolved_revision": getattr(config, "_commit_hash", None),
            "config": config.to_dict(),
            "dtype": str(kwargs["dtype"]), "attention": "sdpa",
        }

    from openshelf.pipeline.engines import create_engine
    from openshelf.pipeline.tts_engine import DirectedSegment

    engine = create_engine(args.engine, device=args.device, language=args.language)
    voices = {voice.id: voice for voice in engine.available_voices()}
    if args.voice not in voices:
        raise ValueError(f"Voice {args.voice!r} unavailable; choose from {', '.join(voices)}")

    # Load before measuring individual samples, matching the eager Qwen path.
    if args.engine == "kokoro":
        engine._pipeline()
    else:
        engine._runtime()

    def synthesize(text):
        result = engine.synthesize(DirectedSegment(text=text, voice=voices[args.voice], speaker="narrator"))
        return result.audio, result.sample_rate

    return synthesize, {"id": args.engine, "voice": asdict(voices[args.voice])}


def run_benchmark(args) -> dict:
    import numpy as np
    import soundfile as sf
    import torch

    directory = Path(args.output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    if (directory / "report.json").exists() and not args.overwrite:
        raise FileExistsError("Benchmark exists; choose a new output directory or use --overwrite")
    selected = SAMPLES[args.language]
    if args.sample:
        selected = {args.sample: selected[args.sample]}
    if args.device.startswith("cuda") and not torch.cuda.is_available():
        raise RuntimeError("CUDA is unavailable; request --device cpu explicitly if intended")
    cuda = args.device.startswith("cuda")
    if cuda:
        torch.cuda.set_device(args.device)
        torch.cuda.reset_peak_memory_stats()
        torch.cuda.synchronize()
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)
    started = time.perf_counter()
    synthesize, model = _load_runtime(args, torch)
    if cuda:
        torch.cuda.synchronize()
    report = {
        "version": 1, "created_at": datetime.now(timezone.utc).isoformat(),
        "engine": args.engine, "voice": args.voice, "language": args.language,
        "runtime": _versions(), "model": model,
        "settings": {"device": args.device, "seed": args.seed, "batch_size": 1,
                     "instruction": INSTRUCTIONS[args.language] if args.engine == "qwen3" else None,
                     "max_new_tokens": 2048 if args.engine == "qwen3" else None},
        "load_seconds": round(time.perf_counter() - started, 4),
        "gpu": torch.cuda.get_device_name() if cuda else None,
        "peak_cuda_allocated_bytes": torch.cuda.max_memory_allocated() if cuda else 0,
        "samples": [],
    }
    _write_report(directory, report)
    for sample_id, text in selected.items():
        print(f"Synthesizing {args.engine}/{args.voice}: {sample_id}", flush=True)
        torch.manual_seed(args.seed)
        if cuda:
            torch.cuda.synchronize()
        started = time.perf_counter()
        with torch.inference_mode():
            audio, rate = synthesize(text)
        if cuda:
            torch.cuda.synchronize()
        elapsed = time.perf_counter() - started
        audio = np.asarray(audio, dtype=np.float32)
        if audio.ndim != 1 or not audio.size or not np.isfinite(audio).all() or np.max(np.abs(audio)) == 0:
            raise ValueError(f"Invalid/empty/silent audio for {sample_id}")
        if not isinstance(rate, int) or rate <= 0:
            raise ValueError("Invalid sample rate")
        path = directory / f"{sample_id}.wav"
        sf.write(path, audio, rate, subtype="PCM_16")
        duration = len(audio) / rate
        sample = {
            "id": sample_id, "text": text, "text_sha256": _hash(text.encode()),
            "wav": path.name, "wav_sha256": _hash(path.read_bytes()),
            "sample_rate": rate, "audio_seconds": round(duration, 4),
            "synthesis_seconds": round(elapsed, 4), "rtf": round(elapsed / duration, 4),
            "first_sample": not report["samples"],
        }
        report["samples"].append(sample)
        report["peak_cuda_allocated_bytes"] = torch.cuda.max_memory_allocated() if cuda else 0
        _write_report(directory, report)
        print(f"  {duration:.2f}s audio, {elapsed:.2f}s synthesis, RTF={sample['rtf']:.2f}", flush=True)
    return report


def align_report(directory: Path, device: str) -> bool:
    import soundfile as sf
    from openshelf.pipeline.language import unit_count
    from openshelf.pipeline.word_aligner import align_chapter, validate_alignment

    report = json.loads((directory / "report.json").read_text(encoding="utf-8"))
    if report.get("version") != 1 or report.get("language") not in SAMPLES or not report.get("samples"):
        raise ValueError("Unsupported/empty benchmark report")
    success = True
    for sample in report["samples"]:
        path = directory / sample["wav"]
        if Path(sample["wav"]).name != sample["wav"] or path.suffix != ".wav":
            raise ValueError("Benchmark WAV must be a local filename")
        if _hash(path.read_bytes()) != sample["wav_sha256"] or _hash(sample["text"].encode()) != sample["text_sha256"]:
            raise ValueError("Benchmark audio/text changed since synthesis")
        started = time.perf_counter()
        words = align_chapter(str(path), [sample["text"]], [0.0], device=device, language=report["language"])
        duration = sf.info(path).duration
        violations = validate_alignment(words, duration, 1)
        for index, word in enumerate(words):
            if not (0 <= word.start <= word.end <= duration):
                violations.append(f"word[{index}]: invalid timestamp interval")
        if not words:
            violations.append("No aligned words")
        sample["alignment"] = {
            "runtime": _versions(), "device": device,
            "seconds": round(time.perf_counter() - started, 4),
            "words": [asdict(word) for word in words],
            "reader_units": unit_count(sample["text"]),
            "coverage_ratio": round(len(words) / max(1, unit_count(sample["text"])), 4),
            "violations": violations,
        }
        _write_report(directory, report)
        print(f"Aligned {sample['id']}: {len(words)} units, coverage={sample['alignment']['coverage_ratio']:.1%}, violations={len(violations)}", flush=True)
        success = success and not violations
    return success


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", choices=("qwen3", "kokoro", "chatterbox"), default="qwen3")
    parser.add_argument("--language", choices=("zh", "en"), default="zh")
    parser.add_argument("--voice")
    parser.add_argument("--device", choices=("cuda", "cuda:0", "cpu"), default="cuda:0")
    parser.add_argument("--revision")
    parser.add_argument("--seed", type=int, default=1337)
    parser.add_argument("--sample", choices=sorted({key for values in SAMPLES.values() for key in values}))
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--align-only", action="store_true")
    args = parser.parse_args(argv)
    if args.align_only:
        return 0 if align_report(Path(args.output_dir), args.device) else 1
    if args.sample and args.sample not in SAMPLES[args.language]:
        parser.error("Sample is not available in the selected language")
    defaults = {"qwen3": "Ryan", "kokoro": "af_heart", "chatterbox": "chatterbox-af_heart"}
    if args.language == "zh":
        defaults.update(qwen3="Serena", kokoro="zf_xiaobei")
    args.voice = args.voice or defaults[args.engine]
    run_benchmark(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
