"""Unified command line entry point for OpenShelf pipeline workflows."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence


CommandMain = Callable[[Sequence[str] | None], int]


def _build_root_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="openshelf-pipeline",
        description="OpenShelf pipeline command line tools.",
    )
    parser.add_argument(
        "group",
        nargs="?",
        choices=["books", "dag", "ops", "voices", "qa", "profile"],
        help="Command group",
    )
    return parser


def _dispatch_group(
    *,
    prog: str,
    argv: Sequence[str],
    commands: dict[str, tuple[str, CommandMain]],
) -> int:
    parser = argparse.ArgumentParser(prog=prog)
    parser.add_argument("command", nargs="?", choices=sorted(commands))

    args = list(argv)
    if not args:
        parser.print_help()
        return 2
    if args[0] in {"-h", "--help"}:
        parser.print_help()
        return 0
    command = args[0]
    if command not in commands:
        parser.error(f"invalid choice: {command!r} (choose from {', '.join(sorted(commands))})")
    return commands[command][1](args[1:])


def _ops_main(argv: Sequence[str] | None = None) -> int:
    from openshelf.pipeline.ops import catalog, doctor, gpu_preflight

    return _dispatch_group(
        prog="openshelf-pipeline ops",
        argv=list(argv or []),
        commands={
            "gpu-preflight": ("Check GPU readiness", gpu_preflight.main),
            "doctor": ("Inspect a local build", doctor.main),
            "catalog": ("Build/upload catalog.json", catalog.main),
        },
    )


def _voices_main(argv: Sequence[str] | None = None) -> int:
    from openshelf.pipeline import voices

    return _dispatch_group(
        prog="openshelf-pipeline voices",
        argv=list(argv or []),
        commands={
            "bootstrap-f5tts": ("Generate F5-TTS reference clips", voices.main),
        },
    )


def _qa_main(argv: Sequence[str] | None = None) -> int:
    from openshelf.pipeline.qa import audio

    return _dispatch_group(
        prog="openshelf-pipeline qa",
        argv=list(argv or []),
        commands={
            "audio": ("Validate audio quality", audio.main),
        },
    )


def _profile_main(argv: Sequence[str] | None = None) -> int:
    from openshelf.pipeline.qa import direction_profile

    return _dispatch_group(
        prog="openshelf-pipeline profile",
        argv=list(argv or []),
        commands={
            "direction": ("Profile voice direction", direction_profile.main),
        },
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    parser = _build_root_parser()
    if not args:
        parser.print_help()
        return 2
    if args[0] in {"-h", "--help"}:
        parser.print_help()
        return 0

    group = args[0]
    rest = args[1:]
    if group not in {"books", "dag", "ops", "voices", "qa", "profile"}:
        parser.error(
            f"invalid choice: {group!r} "
            "(choose from books, dag, ops, voices, qa, profile)"
        )
    if group == "books":
        from openshelf.pipeline import books

        return books.main(rest)
    if group == "dag":
        from openshelf.pipeline.dag import cli as dag_module

        return dag_module.main(rest)
    if group == "ops":
        return _ops_main(rest)
    if group == "voices":
        return _voices_main(rest)
    if group == "qa":
        return _qa_main(rest)
    if group == "profile":
        return _profile_main(rest)

    parser.error("missing command group")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
