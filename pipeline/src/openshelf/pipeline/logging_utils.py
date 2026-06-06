"""Local file logging and heartbeat helpers for long pipeline runs."""

from __future__ import annotations

import logging
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from types import TracebackType


def safe_log_name(name: str) -> str:
    """Return a filesystem-friendly log name fragment."""
    slug = re.sub(r"[^a-zA-Z0-9_.-]+", "-", name.strip()).strip("-")
    return slug.lower() or "pipeline"


def configure_pipeline_logging(
    run_name: str,
    log_dir: str | Path = "logs",
    *,
    level: int = logging.DEBUG,
) -> Path:
    """Configure root logging for one pipeline run and return the log path."""
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = log_path / f"{timestamp}-{safe_log_name(run_name)}.log"

    root = logging.getLogger()
    root.setLevel(level)

    close_pipeline_logging()

    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s [%(name)s] %(message)s"
    ))
    handler._openshelf_pipeline_handler = True  # type: ignore[attr-defined]
    root.addHandler(handler)

    logging.getLogger(__name__).info("Logging to %s", path)
    return path


def close_pipeline_logging() -> None:
    """Close pipeline-owned logging handlers."""
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, "_openshelf_pipeline_handler", False):
            root.removeHandler(handler)
            handler.close()


class Heartbeat:
    """Context manager that logs periodic progress for silent long stages."""

    def __init__(
        self,
        logger: logging.Logger,
        label: str,
        *,
        interval_seconds: float = 30,
    ):
        self.logger = logger
        self.label = label
        self.interval_seconds = interval_seconds
        self._start = 0.0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def __enter__(self) -> Heartbeat:
        self._start = time.monotonic()
        self.logger.info("Started: %s", self.label)
        if self.interval_seconds > 0:
            self._thread = threading.Thread(
                target=self._run,
                name=f"openshelf-heartbeat-{safe_log_name(self.label)}",
                daemon=True,
            )
            self._thread.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1)
        elapsed = time.monotonic() - self._start
        if exc_type is None:
            self.logger.info("Finished: %s (elapsed %.1fs)", self.label, elapsed)
        else:
            self.logger.exception("Failed: %s (elapsed %.1fs)", self.label, elapsed)

    def update(self, label: str) -> None:
        with self._lock:
            self.label = label
        self.logger.info("Progress: %s", label)

    def beat(self, detail: str | None = None) -> None:
        label = self._label()
        elapsed = time.monotonic() - self._start
        if detail:
            self.logger.info("Heartbeat: %s - %s (elapsed %.1fs)", label, detail, elapsed)
        else:
            self.logger.info("Heartbeat: %s still running (elapsed %.1fs)", label, elapsed)

    def _label(self) -> str:
        with self._lock:
            return self.label

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            self.beat()
