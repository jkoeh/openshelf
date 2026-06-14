"""GPU preflight checks for local pipeline runs."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Sequence


_ACCELERATOR_REQUIRED_ENGINES = {"chatterbox", "chatterboxtts"}


@dataclass
class PreflightMessage:
    level: str
    message: str


@dataclass
class TorchStatus:
    imported: bool
    version: str | None = None
    cuda_version: str | None = None
    cuda_available: bool = False
    cuda_device_count: int = 0
    cuda_devices: list[str] = field(default_factory=list)
    mps_available: bool = False
    error: str | None = None


@dataclass
class GpuPreflightReport:
    engine: str
    requested_device: str
    resolved_device: str
    accelerator_required: bool
    torch: TorchStatus
    ok: bool
    messages: list[PreflightMessage] = field(default_factory=list)
    model_devices: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def normalize_engine_name(engine: str | None) -> str:
    return (engine or "kokoro").lower().replace("-", "").replace("_", "")


def accelerator_required_by_default(engine: str | None) -> bool:
    return normalize_engine_name(engine) in _ACCELERATOR_REQUIRED_ENGINES


def collect_torch_status(torch_module: Any | None = None) -> TorchStatus:
    if torch_module is None:
        try:
            import torch as torch_module  # type: ignore[no-redef]
        except Exception as exc:  # pragma: no cover - exercised via injection
            return TorchStatus(imported=False, error=f"{type(exc).__name__}: {exc}")

    cuda_available = bool(torch_module.cuda.is_available())
    cuda_count = int(torch_module.cuda.device_count()) if cuda_available else 0
    cuda_devices: list[str] = []
    for idx in range(cuda_count):
        try:
            cuda_devices.append(str(torch_module.cuda.get_device_name(idx)))
        except Exception:
            cuda_devices.append(f"cuda:{idx}")

    mps_available = False
    backends = getattr(torch_module, "backends", None)
    mps_backend = getattr(backends, "mps", None)
    if mps_backend is not None:
        try:
            mps_available = bool(mps_backend.is_available())
        except Exception:
            mps_available = False

    return TorchStatus(
        imported=True,
        version=str(getattr(torch_module, "__version__", "unknown")),
        cuda_version=str(getattr(getattr(torch_module, "version", None), "cuda", None)),
        cuda_available=cuda_available,
        cuda_device_count=cuda_count,
        cuda_devices=cuda_devices,
        mps_available=mps_available,
    )


def resolve_device(requested_device: str | None, torch_status: TorchStatus) -> str:
    requested = (requested_device or "auto").lower()
    if requested not in {"auto", "cuda", "mps", "cpu"}:
        raise ValueError("--device must be one of auto, cuda, mps, cpu")
    if requested != "auto":
        return requested
    if torch_status.cuda_available:
        return "cuda"
    if torch_status.mps_available:
        return "mps"
    return "cpu"


def run_gpu_preflight(
    engine: str | None,
    requested_device: str | None = "auto",
    *,
    load_engine: bool = False,
    torch_module: Any | None = None,
    engine_factory: Callable[..., Any] | None = None,
) -> GpuPreflightReport:
    requested = (requested_device or "auto").lower()
    torch_status = collect_torch_status(torch_module)
    resolved = resolve_device(requested, torch_status)
    requires_accelerator = accelerator_required_by_default(engine)
    messages: list[PreflightMessage] = []
    ok = True

    if not torch_status.imported:
        if requested == "cpu":
            messages.append(PreflightMessage(
                "warning",
                f"PyTorch could not be imported during CPU preflight: {torch_status.error}",
            ))
        else:
            ok = False
            messages.append(PreflightMessage(
                "error",
                f"PyTorch could not be imported: {torch_status.error}",
            ))

    if requested == "cuda" and not torch_status.cuda_available:
        ok = False
        messages.append(PreflightMessage(
            "error",
            "CUDA was requested, but torch.cuda.is_available() is false.",
        ))
    if requested == "mps" and not torch_status.mps_available:
        ok = False
        messages.append(PreflightMessage(
            "error",
            "MPS was requested, but the PyTorch MPS backend is unavailable.",
        ))
    if requested == "auto" and requires_accelerator and resolved == "cpu":
        ok = False
        messages.append(PreflightMessage(
            "error",
            (
                "Chatterbox auto device resolved to CPU. Install/use a GPU-capable "
                "PyTorch package or force the slow path with --device cpu."
            ),
        ))
    if requested == "cpu" and requires_accelerator:
        messages.append(PreflightMessage(
            "warning",
            "Chatterbox CPU was explicitly selected; full-book runs are usually very slow.",
        ))

    model_devices: list[str] = []
    if load_engine and ok:
        try:
            if engine_factory is None:
                from openshelf.pipeline.engines import create_engine

                engine_factory = create_engine
            engine_obj = engine_factory(engine, device=resolved)
            runtime_loader = getattr(engine_obj, "_runtime", None)
            runtime = runtime_loader() if callable(runtime_loader) else engine_obj
            model_devices = sorted(_collect_model_devices(runtime))
            if resolved in {"cuda", "mps"}:
                matching = [dev for dev in model_devices if dev.startswith(resolved)]
                if model_devices and not matching:
                    ok = False
                    messages.append(PreflightMessage(
                        "error",
                        (
                            f"Loaded engine runtime, but observed model devices "
                            f"{model_devices}; expected {resolved}."
                        ),
                    ))
                if not model_devices:
                    messages.append(PreflightMessage(
                        "warning",
                        "Loaded engine runtime, but no model device could be inferred.",
                    ))
        except Exception as exc:
            ok = False
            messages.append(PreflightMessage(
                "error",
                f"Engine load preflight failed: {type(exc).__name__}: {exc}",
            ))

    if ok and not messages:
        messages.append(PreflightMessage(
            "info",
            f"Resolved device {resolved} is usable for {engine or 'default engine'}.",
        ))

    return GpuPreflightReport(
        engine=normalize_engine_name(engine),
        requested_device=requested,
        resolved_device=resolved,
        accelerator_required=requires_accelerator,
        torch=torch_status,
        ok=ok,
        messages=messages,
        model_devices=model_devices,
    )


def _collect_model_devices(obj: Any, *, max_depth: int = 3) -> set[str]:
    devices: set[str] = set()
    seen: set[int] = set()

    def visit(value: Any, depth: int) -> None:
        if value is None or id(value) in seen or depth > max_depth:
            return
        seen.add(id(value))
        direct_device = getattr(value, "device", None)
        if direct_device is not None and not callable(direct_device):
            devices.add(str(direct_device))

        parameters = getattr(value, "parameters", None)
        if callable(parameters):
            try:
                for idx, param in enumerate(parameters()):
                    param_device = getattr(param, "device", None)
                    if param_device is not None:
                        devices.add(str(param_device))
                    if idx >= 7:
                        break
            except Exception:
                pass

        if depth == max_depth:
            return
        members = getattr(value, "__dict__", {})
        if isinstance(members, dict):
            for child in members.values():
                if isinstance(child, (str, bytes, int, float, bool)):
                    continue
                visit(child, depth + 1)
        if isinstance(value, dict):
            for child in value.values():
                visit(child, depth + 1)
        elif isinstance(value, (list, tuple, set)):
            for child in value:
                visit(child, depth + 1)

    visit(obj, 0)
    return devices


def format_gpu_preflight_report(report: GpuPreflightReport) -> str:
    status = "OK" if report.ok else "FAIL"
    torch_status = report.torch
    lines = [
        f"GPU preflight: {status}",
        f"  engine: {report.engine}",
        f"  requested device: {report.requested_device}",
        f"  resolved device: {report.resolved_device}",
        f"  accelerator required by default: {report.accelerator_required}",
    ]
    if torch_status.imported:
        lines.extend([
            f"  torch: {torch_status.version}",
            f"  torch cuda build: {torch_status.cuda_version}",
            f"  cuda available: {torch_status.cuda_available}",
            f"  cuda devices: {torch_status.cuda_devices or []}",
            f"  mps available: {torch_status.mps_available}",
        ])
    else:
        lines.append(f"  torch import error: {torch_status.error}")
    if report.model_devices:
        lines.append(f"  model devices: {report.model_devices}")
    for message in report.messages:
        lines.append(f"  {message.level}: {message.message}")
    return "\n".join(lines)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="openshelf-pipeline ops gpu-preflight",
        description="Check GPU readiness for OpenShelf TTS",
    )
    parser.add_argument("--engine", default="chatterbox", help="TTS engine to check")
    parser.add_argument(
        "--device",
        default="auto",
        choices=["auto", "cuda", "mps", "cpu"],
        help="Requested device (default: auto)",
    )
    parser.add_argument(
        "--load-engine",
        action="store_true",
        help="Also construct and load the engine runtime to verify model placement",
    )
    parser.add_argument("--json", action="store_true", help="Print JSON report")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    report = run_gpu_preflight(
        args.engine,
        args.device,
        load_engine=args.load_engine,
    )
    if args.json:
        print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
    else:
        print(format_gpu_preflight_report(report))
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
