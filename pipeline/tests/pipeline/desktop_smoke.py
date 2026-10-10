"""Opt-in Windows/WebView2 smoke test; no production queue or synthesis."""
from __future__ import annotations
import json
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import Mock, patch

import psutil
import webview
from webview.event import Event

from openshelf.pipeline import job_monitor as monitor
from openshelf.pipeline.job_monitor_desktop import ConsumerJob, Studio, authenticate_request
from openshelf.pipeline.job_monitor_web import DashboardServer
from pipeline.tests.pipeline.test_job_monitor import job


def smoke():
    fake_api = Mock()
    fake_api.list_jobs.return_value = ([monitor.MonitoredJob.from_api(job(title="Lifecycle test book"))], [])
    with tempfile.TemporaryDirectory(prefix="openshelf-smoke-") as folder:
        tree_file = Path(folder) / "child.pid"
        command = (
            "import subprocess,sys,time; from pathlib import Path; "
            "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(120)']); "
            f"Path({str(tree_file)!r}).write_text(str(p.pid)); time.sleep(120)"
        )
        process = subprocess.Popen([sys.executable, "-c", command], creationflags=subprocess.CREATE_NO_WINDOW)
        consumer = psutil.Process(process.pid)
        deadline = time.monotonic() + 10
        while not tree_file.exists() and time.monotonic() < deadline:
            time.sleep(.1)
        assert tree_file.exists(), "Inert child did not start"
        child_pid = int(tree_file.read_text())
        server = DashboardServer(fake_api)
        studio = Studio(server=server)
        metrics, errors = {}, []
        with patch.object(monitor, "start_consumer", return_value=process.pid), patch.object(
                monitor, "find_consumer", return_value=consumer), patch.object(monitor, "log_tail", return_value="Test journal"):
            window = webview.create_window("OpenShelf lifecycle test", server.origin + "/", width=1100, height=760)
            window.events.request_sent = Event(window, True)
            def attach_header(request):
                authenticate_request(request, server.origin, server.token)
            window.events.request_sent += attach_header
            window.events.closing += studio.close
            server.on_close = window.destroy
            status_received = threading.Event()
            responses = []
            def response_received(response):
                if response.url.endswith("/api/status"):
                    responses.append(response.status_code)
                    status_received.set()
            window.events.response_received += response_received
            studio.start()
            def check():
                try:
                    for index in range(2):
                        assert status_received.wait(20), "Dashboard status did not arrive"
                        deadline = time.monotonic() + 10
                        while time.monotonic() < deadline:
                            text = window.evaluate_js("document.body.innerText")
                            if "Lifecycle test book" in text:
                                break
                            time.sleep(.1)
                        assert "Lifecycle test book" in text, text
                        assert "Session expired" not in text
                        assert window.evaluate_js("sessionStorage.getItem('monitor-session')") is None
                        assert "#" not in window.get_current_url()
                        if index == 0:
                            status_received.clear()
                            window.run_js("window.location.reload()")
                    metrics.update(authentication="native headers", refresh="passed", status_codes=responses)
                except Exception as exc:
                    errors.append(str(exc))
                finally:
                    window.destroy()
            try:
                webview.settings["ALLOW_FILE_URLS"] = False
                webview.start(check, gui="edgechromium", private_mode=True, storage_path=folder, debug=False)
            finally:
                studio.close()
                if process.poll() is None:
                    process.kill()
            process.wait(timeout=10)
            psutil.wait_procs([psutil.Process(child_pid)] if psutil.pid_exists(child_pid) else [], timeout=10)
            assert not psutil.pid_exists(child_pid), "Consumer descendant survived close"
            with socket.socket() as sock:
                assert sock.connect_ex(server.server_address) != 0, "Server listener survived close"
            metrics.update(window_close="server and tree stopped", children_stopped=True)
            if errors:
                raise AssertionError(errors)

    # A separate owner exits without calling close: its OS handle must still
    # kill the adopted subprocess. No application cleanup callback is involved.
    script = (
        "import subprocess,sys,time,os; import psutil; "
        "from openshelf.pipeline.job_monitor_desktop import ConsumerJob; "
        "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(120)']); "
        "j=ConsumerJob(); j.attach(psutil.Process(p.pid)); "
        "print(p.pid,flush=True); os._exit(0)"
    )
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    pid = int(result.stdout.strip())
    if psutil.pid_exists(pid):
        psutil.wait_procs([psutil.Process(pid)], timeout=10)
    assert not psutil.pid_exists(pid), "Consumer survived abrupt owner exit"
    metrics["abrupt_exit"] = "consumer stopped"
    print(json.dumps(metrics))


if __name__ == "__main__":
    smoke()
