"""Local build diagnostics for OpenShelf pipeline outputs."""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Sequence


_CHAPTER_FILE_RE = re.compile(r"chapter-(\d+)\.(chunks|voice_direction|sync)\.json$")
_M4A_RE = re.compile(r"chapter-(\d+)\.m4a$")


@dataclass
class DoctorIssue:
    level: str
    message: str


@dataclass
class DoctorReport:
    build_dir: str
    status: str
    counts: dict[str, int]
    chapters: dict[str, list[int]]
    issues: list[DoctorIssue] = field(default_factory=list)
    artifacts: dict[str, bool] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status != "fail"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def diagnose_build(build_dir: str, log_path: str | None = None) -> DoctorReport:
    root = Path(build_dir)
    issues: list[DoctorIssue] = []
    artifacts: dict[str, bool] = {}
    chapters: dict[str, list[int]] = {
        "chunks": [],
        "voice_direction": [],
        "m4a": [],
        "sync": [],
        "chapter_data": [],
        "rendition_manifest": [],
    }

    if not root.exists() or not root.is_dir():
        return DoctorReport(
            build_dir=str(root),
            status="fail",
            counts={},
            chapters=chapters,
            issues=[DoctorIssue("error", f"build directory not found: {root}")],
            artifacts={},
        )

    for name in (
        "run.json",
        "character_registry.json",
        "voice_direction.json",
        "rendition-manifest.json",
        "chapter_data.json",
    ):
        artifacts[name] = (root / name).exists()

    file_lists = {
        "chunks": _chapter_json_numbers(root, "chunks"),
        "voice_direction": _chapter_json_numbers(root, "voice_direction"),
        "sync": _chapter_json_numbers(root, "sync"),
        "m4a": _m4a_numbers(root),
    }
    for key, values in file_lists.items():
        chapters[key] = sorted(values)

    expected = set(chapters["chunks"]) or set(chapters["m4a"]) or set(chapters["sync"])
    if expected:
        for key in ("voice_direction", "m4a", "sync"):
            missing = sorted(expected - set(chapters[key]))
            if missing:
                issues.append(DoctorIssue(
                    "error",
                    f"missing {key} artifact(s) for chapter(s): {_format_numbers(missing)}",
                ))

    if not artifacts.get("chapter_data.json", False):
        issues.append(DoctorIssue("error", "missing chapter_data.json"))
    else:
        payload = _read_json(root / "chapter_data.json", issues)
        data_numbers = [
            int(ch["number"])
            for ch in payload.get("chapters", [])
            if isinstance(ch, dict) and "number" in ch
        ]
        chapters["chapter_data"] = sorted(data_numbers)
        missing = sorted(expected - set(data_numbers))
        if expected and missing:
            issues.append(DoctorIssue(
                "error",
                f"chapter_data.json missing chapter(s): {_format_numbers(missing)}",
            ))

    if not artifacts.get("rendition-manifest.json", False):
        issues.append(DoctorIssue("error", "missing rendition-manifest.json"))
    else:
        payload = _read_json(root / "rendition-manifest.json", issues)
        manifest_numbers = [
            int(ch["number"])
            for ch in payload.get("chapters", [])
            if isinstance(ch, dict) and "number" in ch
        ]
        chapters["rendition_manifest"] = sorted(manifest_numbers)
        missing = sorted(expected - set(manifest_numbers))
        if expected and missing:
            issues.append(DoctorIssue(
                "error",
                f"rendition-manifest.json missing chapter(s): {_format_numbers(missing)}",
            ))

    if not artifacts.get("run.json", False):
        issues.append(DoctorIssue("warning", "run.json is missing; resume safety is unavailable."))
    if not artifacts.get("character_registry.json", False):
        issues.append(DoctorIssue("warning", "character_registry.json is missing."))
    if not artifacts.get("voice_direction.json", False):
        issues.append(DoctorIssue("warning", "book-level voice_direction.json is missing."))

    _inspect_sync_artifacts(root, issues)
    if log_path:
        _inspect_log(Path(log_path), issues)

    status = "ok"
    if any(issue.level == "error" for issue in issues):
        status = "fail"
    elif issues:
        status = "warn"

    counts = {
        "chunks": len(chapters["chunks"]),
        "voice_direction": len(chapters["voice_direction"]),
        "m4a": len(chapters["m4a"]),
        "sync": len(chapters["sync"]),
        "chapter_data": len(chapters["chapter_data"]),
        "rendition_manifest": len(chapters["rendition_manifest"]),
    }
    return DoctorReport(
        build_dir=str(root),
        status=status,
        counts=counts,
        chapters=chapters,
        issues=issues,
        artifacts=artifacts,
    )


def _chapter_json_numbers(root: Path, kind: str) -> list[int]:
    suffix = f".{kind}.json"
    return sorted(
        _chapter_number(path)
        for path in root.glob(f"chapter-*{suffix}")
        if _chapter_number(path) is not None
    )


def _m4a_numbers(root: Path) -> list[int]:
    return sorted(
        number
        for path in root.glob("chapter-*.m4a")
        for number in [_chapter_number(path)]
        if number is not None
    )


def _chapter_number(path: Path) -> int | None:
    name = path.name
    match = _CHAPTER_FILE_RE.match(name) or _M4A_RE.match(name)
    if not match:
        return None
    return int(match.group(1))


def _read_json(path: Path, issues: list[DoctorIssue]) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as f:
            payload = json.load(f)
        return payload if isinstance(payload, dict) else {}
    except Exception as exc:
        issues.append(DoctorIssue(
            "error",
            f"could not read {path.name}: {type(exc).__name__}: {exc}",
        ))
        return {}


def _inspect_sync_artifacts(root: Path, issues: list[DoctorIssue]) -> None:
    for path in sorted(glob.glob(str(root / "chapter-*.sync.json"))):
        payload = _read_json(Path(path), issues)
        number = payload.get("number", Path(path).stem)
        starts = payload.get("chunk_audio_starts", [])
        skipped = sum(1 for item in starts if item == -1.0)
        if skipped:
            issues.append(DoctorIssue(
                "warning",
                f"chapter {number} has {skipped} skipped chunk marker(s).",
            ))
        coverage = payload.get("coverage", {})
        ratio = coverage.get("coverage_ratio")
        if isinstance(ratio, (int, float)) and ratio < 0.98:
            issues.append(DoctorIssue(
                "warning",
                f"chapter {number} has low sync coverage ratio {ratio}.",
            ))


def _inspect_log(log_path: Path, issues: list[DoctorIssue]) -> None:
    if not log_path.exists():
        issues.append(DoctorIssue("warning", f"log file not found: {log_path}"))
        return
    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception as exc:
        issues.append(DoctorIssue(
            "warning",
            f"could not read log {log_path}: {type(exc).__name__}: {exc}",
        ))
        return

    known_ffmpeg_probe = False
    for idx, line in enumerate(lines):
        context = "\n".join(lines[max(0, idx - 4): idx + 4])
        if "Failed to load FFmpeg" in context and "torio._extension.utils" in context:
            known_ffmpeg_probe = True
            continue
        if " ERROR " in line or "Failed:" in line:
            issues.append(DoctorIssue("error", f"log reports failure: {line.strip()}"))
        elif "Traceback (most recent call last):" in line:
            issues.append(DoctorIssue(
                "error",
                f"log contains traceback near line {idx + 1}",
            ))
    if known_ffmpeg_probe:
        issues.append(DoctorIssue(
            "warning",
            "log contains torio FFmpeg extension probe tracebacks; treated as known debug noise.",
        ))


def _format_numbers(numbers: list[int]) -> str:
    return ", ".join(str(number) for number in numbers)


def format_doctor_report(report: DoctorReport) -> str:
    lines = [
        f"Pipeline doctor: {report.status.upper()}",
        f"  build dir: {report.build_dir}",
        (
            "  counts: "
            f"chunks={report.counts.get('chunks', 0)}, "
            f"direction={report.counts.get('voice_direction', 0)}, "
            f"m4a={report.counts.get('m4a', 0)}, "
            f"sync={report.counts.get('sync', 0)}, "
            f"chapter_data={report.counts.get('chapter_data', 0)}, "
            f"rendition_manifest={report.counts.get('rendition_manifest', 0)}"
        ),
    ]
    for name, present in sorted(report.artifacts.items()):
        lines.append(f"  artifact {name}: {'yes' if present else 'no'}")
    if report.issues:
        lines.append("  issues:")
        for issue in report.issues:
            lines.append(f"    {issue.level}: {issue.message}")
    return "\n".join(lines)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inspect an OpenShelf local build")
    parser.add_argument("--build-dir", required=True)
    parser.add_argument("--log", default=None, help="Optional pipeline log to scan")
    parser.add_argument("--json", action="store_true", help="Print JSON report")
    parser.add_argument(
        "--fail-on-warning",
        action="store_true",
        help="Exit non-zero when warnings are present",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    report = diagnose_build(args.build_dir, args.log)
    if args.json:
        print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
    else:
        print(format_doctor_report(report))
    if report.status == "fail":
        return 1
    if report.status == "warn" and args.fail_on_warning:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
