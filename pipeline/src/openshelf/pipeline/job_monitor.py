"""Local-only data and process helpers for the Windows generation monitor."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import psutil

from openshelf.config import PROJECT_ROOT, R2_ACCESS_KEY, R2_ACCOUNT_ID, R2_SECRET_KEY


API_BASE = "https://openshelf-api.johnkoeh.workers.dev/api/v1"
REFRESH_SECONDS = 15


class MonitorError(Exception):
    """A short error safe to show in the local window."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        raise MonitorError("Queue API redirected the request; owner credentials were not forwarded.")


def _time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def age(value: str | None, current: datetime | None = None) -> str:
    timestamp = _time(value)
    if timestamp is None:
        return "—"
    seconds = max(0, int(((current or datetime.now(timezone.utc)) - timestamp).total_seconds()))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


@dataclass(frozen=True)
class MonitoredJob:
    id: str
    source_id: str
    title: str
    author: str
    mode: str
    state: str
    stage: str
    priority: int
    attempts: int
    created_at: str
    updated_at: str
    lease_until: str | None
    error_code: str | None

    @classmethod
    def from_api(cls, data: dict) -> MonitoredJob:
        return cls(**{key: data[key] for key in cls.__dataclass_fields__})

    def status(self, current: datetime | None = None) -> str:
        if self.state == "queued":
            return "Pending"
        if self.state == "running":
            expiry = _time(self.lease_until)
            if expiry is None or expiry <= (current or datetime.now(timezone.utc)):
                return "Stuck · lease expired"
            return "Working"
        return self.state.capitalize()


class QueueAPI:
    def __init__(self, root: Path = PROJECT_ROOT):
        self.owner_token_file = root / "worker" / ".secrets" / "prod-owner-token"

    def _request(self, path: str, payload: dict | None = None) -> dict:
        try:
            token = self.owner_token_file.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise MonitorError("Production owner token is missing from worker/.secrets.") from exc
        if len(token) < 24:
            raise MonitorError("Production owner token is invalid.")
        request = urllib.request.Request(
            API_BASE + path,
            data=json.dumps(payload).encode("utf-8") if payload is not None else None,
            method="POST" if payload is not None else "GET",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json",
                     "Cache-Control": "no-store",
                     "User-Agent": "OpenShelf/1.0 (+https://github.com/jkoeh/openshelf)"},
        )
        try:
            with urllib.request.build_opener(_NoRedirect()).open(request, timeout=10) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            message = {
                401: "Production owner token was rejected.",
                404: "The queue API is not deployed yet.",
                429: "Queue API rate limit reached; refresh again shortly.",
                503: "The production queue is temporarily unavailable.",
            }.get(exc.code, f"Queue API returned HTTP {exc.code}.")
            raise MonitorError(message) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise MonitorError("Could not reach the production queue API.") from exc

    def list_jobs(self) -> tuple[list[MonitoredJob], list[MonitoredJob]]:
        result = self._request("/admin/generation-jobs")
        try:
            return ([MonitoredJob.from_api(item) for item in result["active"]],
                    [MonitoredJob.from_api(item) for item in result["recent"]])
        except (KeyError, TypeError, ValueError) as exc:
            raise MonitorError("Queue API returned an unexpected response.") from exc

    def set_priority(self, job_id: str, high: bool) -> None:
        self._request(f"/admin/generation-jobs/{job_id}/priority",
                      {"priority": "high" if high else "normal"})

    def cancel(self, job_id: str) -> None:
        self._request(f"/generation-jobs/{job_id}/cancel", {})

    def retry(self, job_id: str) -> None:
        self._request(f"/generation-jobs/{job_id}/retry", {})


def find_consumer(root: Path = PROJECT_ROOT) -> psutil.Process | None:
    """Find the production consumer, including one started outside the window."""
    script = (root / "pipeline" / "scripts" / "openshelf-pipeline.py").resolve()
    for process in psutil.process_iter(["cmdline"]):
        try:
            args = process.info["cmdline"] or []
            lowered = [str(arg).lower() for arg in args]
            if "consume-jobs" not in lowered or API_BASE.lower() not in lowered:
                continue
            if any((Path(process.cwd(), arg) if not Path(arg).is_absolute() else Path(arg)).resolve() == script
                   for arg in args if Path(arg).name.lower() == "openshelf-pipeline.py"):
                return process
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            continue
    return None


def start_consumer(root: Path = PROJECT_ROOT) -> int:
    existing = find_consumer(root)
    if existing:
        return existing.pid
    executable = root / ".venv" / "Scripts" / "python.exe" if os.name == "nt" else root / ".venv" / "bin" / "python"
    if not executable.is_file():
        raise MonitorError("Root Python .venv is missing. Run the pipeline setup first.")
    if not shutil.which("ffmpeg"):
        raise MonitorError("FFmpeg is not on PATH. Install it before starting the consumer.")
    if not all((R2_ACCOUNT_ID, R2_ACCESS_KEY, R2_SECRET_KEY)):
        raise MonitorError("R2 upload credentials are missing from pipeline/.env.")
    try:
        pc_token = (root / "worker" / ".secrets" / "prod-pc-token").read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise MonitorError("Production PC token is missing from worker/.secrets.") from exc
    if len(pc_token) < 24:
        raise MonitorError("Production PC token is invalid.")
    log_dir = root / "audio"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"consumer-production-{datetime.now().strftime('%Y%m%d-%H%M%S')}.log"
    child_env = os.environ.copy()
    child_env.update({"OPENSHELF_PC_TOKEN": pc_token, "R2_BUCKET": "openshelf", "PYTHONUNBUFFERED": "1"})
    command = [str(executable), "-u", str(root / "pipeline" / "scripts" / "openshelf-pipeline.py"),
               "books", "consume-jobs", "--api-base", API_BASE, "--device", "cuda"]
    with log_path.open("a", encoding="utf-8") as log:
        process = subprocess.Popen(command, cwd=root, env=child_env, stdout=log,
                                   stderr=subprocess.STDOUT,
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    (log_dir / "consumer-production.pid").write_text(str(process.pid), encoding="ascii")
    return process.pid


def latest_log(root: Path = PROJECT_ROOT) -> Path | None:
    files = [*root.glob("audio/consumer-production-*.log"),
             *root.glob("audio/consumer-production-*.out.log"),
             *root.glob("audio/consumer-production-*.err.log")]
    return max(files, key=lambda path: (path.stat().st_mtime, path.stat().st_size > 0)) if files else None


def log_tail(root: Path = PROJECT_ROOT, lines: int = 24) -> str:
    path = latest_log(root)
    if not path:
        return "No consumer log yet."
    try:
        with path.open("rb") as handle:
            handle.seek(0, 2)
            handle.seek(max(0, handle.tell() - 32768))
            text = handle.read().decode("utf-8", errors="replace")
        return "\n".join(text.splitlines()[-lines:]) or "Consumer is idle; no log output yet."
    except OSError:
        return "Consumer log is unavailable."
