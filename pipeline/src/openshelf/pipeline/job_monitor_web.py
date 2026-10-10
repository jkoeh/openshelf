"""Authenticated loopback dashboard. No account credentials cross this boundary."""
from __future__ import annotations

import json
import os
import re
import secrets
import threading
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import psutil

from openshelf.config import PROJECT_ROOT
from openshelf.pipeline import job_monitor as monitor

ASSETS = Path(__file__).with_name("monitor")
FILES = {"/": ("index.html", "text/html"), "/dashboard.css": ("dashboard.css", "text/css"),
         "/dashboard.js": ("dashboard.js", "text/javascript")}
JOB_ID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z")


def redact(text: str, root: Path) -> str:
    values = [value for key, value in os.environ.items()
              if any(word in key.upper() for word in ("TOKEN", "SECRET", "API_KEY", "ACCESS_KEY"))]
    for filename in ("prod-owner-token", "prod-pc-token"):
        try:
            values.append((root / "worker" / ".secrets" / filename).read_text().strip())
        except OSError:
            pass
    for value in sorted(set(values), key=len, reverse=True):
        if len(value) >= 8:
            text = text.replace(value, "[redacted]")
    return text


def stop_idle_consumer(api, root: Path) -> None:
    process = monitor.find_consumer(root)
    if not process:
        return
    # Freeze the exact consumer tree before inspecting it so it cannot spawn
    # synthesis between inspection and termination. Fail closed on unknown children.
    suspended = []
    try:
        processes = [process, *process.children(recursive=True)]
        for item in processes:
            args = [str(arg).lower() for arg in item.cmdline()]
            if "consume-jobs" not in args or monitor.API_BASE.lower() not in args:
                raise monitor.MonitorError("Consumer is processing a book. Cancel its job first.")
        for item in processes:
            item.suspend()
            suspended.append(item)
        allowed_pids = {item.pid for item in processes}
        if any(child.pid not in allowed_pids for item in processes for child in item.children()):
            raise monitor.MonitorError("Consumer is processing a book. Cancel its job first.")
        active, _ = api.list_jobs()
        if any(job.state == "running" for job in active):
            raise monitor.MonitorError("A job is running. Cancel it and wait for the consumer to become idle.")
        for item in reversed(processes):
            item.terminate()
    except (psutil.AccessDenied, psutil.NoSuchProcess) as exc:
        raise monitor.MonitorError("Could not stop the consumer. Refresh its status and try again.") from exc
    finally:
        for item in suspended:
            try:
                item.resume()
            except (psutil.AccessDenied, psutil.NoSuchProcess):
                pass


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, api=None, root: Path = PROJECT_ROOT, *, on_close=None, on_start=None):
        self.root = root
        self.api = api or monitor.QueueAPI(root)
        self.token = secrets.token_urlsafe(32)
        self.control_lock = threading.Lock()
        self.on_close = on_close
        self.on_start = on_start
        super().__init__(("127.0.0.1", 0), Handler)
        self.origin = f"http://127.0.0.1:{self.server_port}"

    def snapshot(self):
        active, recent = self.api.list_jobs()
        process = monitor.find_consumer(self.root)
        def row(job):
            return {**asdict(job), "status": job.status(), "age": monitor.age(job.created_at),
                    "heartbeat_age": monitor.age(job.updated_at)}
        return {"active": [row(job) for job in active], "recent": [row(job) for job in recent],
                "consumer": {"running": process is not None, "pid": process.pid if process else None},
                "log": redact(monitor.log_tail(self.root, lines=70), self.root),
                "desktop": self.on_close is not None,
                "refresh_seconds": monitor.REFRESH_SECONDS}


class Handler(BaseHTTPRequestHandler):
    server: DashboardServer

    def setup(self):
        super().setup()
        self.connection.settimeout(15)

    def log_message(self, *_):
        pass  # Never log request URLs, headers, or tokens.

    def send(self, status, body, content_type="application/json"):
        data = json.dumps(body).encode() if content_type == "application/json" else body
        self.send_response(status)
        self.send_header("Content-Type", content_type + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; "
                         "style-src 'self'; connect-src 'self'; img-src 'self' data:; "
                         "frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
        self.end_headers()
        self.wfile.write(data)

    def boundary(self, api=False):
        if self.headers.get("Host") != self.server.origin.removeprefix("http://"):
            self.send(403, {"error": "Invalid local host."})
            return False
        origin = self.headers.get("Origin")
        if ((origin is not None and origin != self.server.origin)
                or self.headers.get("Sec-Fetch-Site", "none") not in {"same-origin", "none"}):
            self.send(403, {"error": "Only this dashboard may access the monitor."})
            return False
        if api and not secrets.compare_digest(self.headers.get("X-Monitor-Token", "").encode(), self.server.token.encode()):
            self.send(401, {"error": "Session expired. Launch the monitor again with open-job-monitor.cmd."})
            return False
        return True

    def do_GET(self):
        if not self.boundary(api=self.path.startswith("/api/")):
            return
        if self.path in FILES:
            filename, content_type = FILES[self.path]
            self.send(200, (ASSETS / filename).read_bytes(), content_type)
        elif self.path == "/api/status":
            try:
                with self.server.control_lock:
                    self.send(200, self.server.snapshot())
            except monitor.MonitorError as exc:
                self.send(502, {"error": str(exc)})
            except Exception:
                self.send(500, {"error": "Could not load the monitor. Try refreshing."})
        else:
            self.send(404, {"error": "Not found."})

    def do_POST(self):
        if not self.boundary(api=True):
            return
        if (self.headers.get("Origin") != self.server.origin
                or self.headers.get("Content-Type", "").split(";")[0] != "application/json"
                or self.headers.get("Transfer-Encoding")):
            self.send(403, {"error": "Same-origin JSON requests are required."})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 4096:
                raise ValueError
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError
        except (ValueError, OSError):
            self.send(400, {"error": "Invalid request."})
            return
        try:
            with self.server.control_lock:
                if self.path == "/api/close" and not data:
                    self.send(200, {"ok": True})
                    threading.Thread(target=self.server.on_close or self.server.shutdown, daemon=True).start()
                    return
                if self.path == "/api/consumer/start" and not data:
                    if self.server.on_start:
                        self.server.on_start()
                    else:
                        monitor.start_consumer(self.server.root)
                elif self.path == "/api/consumer/stop" and not data:
                    stop_idle_consumer(self.server.api, self.server.root)
                elif self.path == "/api/job" and set(data) == {"id", "action"}:
                    job_id, action = data["id"], data["action"]
                    if not isinstance(job_id, str) or not JOB_ID.fullmatch(job_id):
                        raise ValueError
                    active, recent = self.server.api.list_jobs()
                    job = next((job for job in [*active, *recent] if job.id == job_id), None)
                    if job is None:
                        raise monitor.MonitorError("Job no longer appears in the queue. Refresh and try again.")
                    if action in {"high", "normal"} and job.state == "queued":
                        self.server.api.set_priority(job_id, action == "high")
                    elif action == "cancel" and job.state in {"queued", "running"}:
                        self.server.api.cancel(job_id)
                    elif action == "retry" and job.state in {"failed", "canceled"}:
                        self.server.api.retry(job_id)
                    else:
                        raise monitor.MonitorError("That action is unavailable for this job's current state.")
                else:
                    raise ValueError
                self.send(200, {"ok": True})
        except ValueError:
            self.send(400, {"error": "Invalid action."})
        except monitor.MonitorError as exc:
            self.send(409, {"error": str(exc)})
        except Exception:
            self.send(500, {"error": "Action failed. Refresh the dashboard and try again."})

    def do_OPTIONS(self):
        self.send(403, {"error": "Cross-origin access is disabled."})


def main():
    from openshelf.pipeline.job_monitor_desktop import main as desktop_main
    desktop_main()
