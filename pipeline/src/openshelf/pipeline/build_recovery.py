"""Local, audited artifact adoption into an ordinary fresh job's build."""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import re
import shutil

from openshelf.pipeline.run_context import RunContext, load_run_context, validate_resume_context, write_run_context
from openshelf.pipeline.synthesis_checkpoints import (
    SynthesisCheckpoints, file_sha256, read_completion,
)


def adopt_local_build(source: str | Path, target: str | Path, expected: RunContext) -> dict:
    """Copy proven work, never a public manifest or mutable job/publication state.

    The caller owns process coordination and must stop the old writer first.
    ``expected`` comes from the new job's exact source and invocation settings.
    """
    source = Path(source).resolve(strict=True)
    target = Path(target).resolve()
    original = load_run_context(source / "run.json")
    if source == target or target.name != expected.build or source.name != original.get("build"):
        raise ValueError("Recovery requires distinct exact source and target build directories")
    validate_resume_context(original, replace(expected, build=original["build"]))
    if target.exists() and any(target.iterdir()):
        raise FileExistsError("Recovery target must be empty")
    target.mkdir(parents=True, exist_ok=True)
    write_run_context(target / "run.json", expected)
    adopted = []

    for number in expected.sections:
        stem = f"section-{number:02d}"
        receipt = source / f"{stem}.complete.json"
        files = [source / f"{stem}{suffix}" for suffix in
                 (".m4a", ".sync.json", ".synthesis_units.json")]
        completed = None
        try:
            fingerprint = json.loads(receipt.read_text(encoding="utf-8"))["fingerprint"]
            completed = read_completion(receipt, fingerprint, [str(file) for file in files])
        except (OSError, ValueError, KeyError, TypeError):
            pass
        checkpoints = source / f"{stem}.checkpoints"
        valid_regions = []
        if checkpoints.is_dir() and not checkpoints.is_symlink():
            for metadata in sorted(checkpoints.glob("*.json")):
                if not re.fullmatch(r"heading|chunk-\d{4,8}", metadata.stem):
                    continue
                try:
                    payload = json.loads(metadata.read_text(encoding="utf-8"))
                    old_cache = SynthesisCheckpoints(checkpoints, payload["sample_rate"])
                    region = old_cache.load(metadata.stem, payload["fingerprint"])
                    if region:
                        new_cache = SynthesisCheckpoints(target / checkpoints.name, payload["sample_rate"])
                        new_cache.save(metadata.stem, payload["fingerprint"], region.audio,
                                       region.words, region.audit, region.context_after)
                        valid_regions.append(metadata.stem)
                except (OSError, ValueError, KeyError, TypeError, RuntimeError):
                    continue
        if not completed and not valid_regions:
            continue
        source_hashes = {}
        target_hashes = {}
        selected = [*files, receipt] if completed else []
        selected += [source / f"{stem}.chunks.json", source / f"{stem}.voice_direction.json"]
        for file in selected:
            if file.is_symlink() or not file.is_file():
                raise ValueError("Recovery requires ordinary local section files")
            destination = target / file.name
            source_hashes[file.name] = file_sha256(file)
            if file.name.endswith(".voice_direction.json"):
                payload = json.loads(file.read_text(encoding="utf-8"))
                if payload.get("build") != original["build"]:
                    raise ValueError("Direction artifact belongs to a different build")
                payload["build"] = expected.build
                destination.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            else:
                shutil.copyfile(file, destination)
            target_hashes[file.name] = file_sha256(destination)
        adopted.append({"sequence": number, "completed": bool(completed),
                        "regions": valid_regions, "source_sha256": source_hashes,
                        "target_sha256": target_hashes})
    audit = {"version": 1, "source_build": original["build"], "target_build": expected.build,
             "epub_sha256": expected.epub_sha256, "sections": adopted}
    (target / "recovery.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    return audit
