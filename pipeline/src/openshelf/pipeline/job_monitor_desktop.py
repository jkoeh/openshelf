"""Windows Studio window and ownership of its local server/consumer tree."""
from __future__ import annotations

import ctypes
import hashlib
import os
import tempfile
import threading
from ctypes import wintypes
from urllib.parse import urlsplit

import psutil

from openshelf.config import PROJECT_ROOT
from openshelf.pipeline import job_monitor as monitor
from openshelf.pipeline.job_monitor_web import DashboardServer

TITLE = "OpenShelf Studio"


class BasicLimits(ctypes.Structure):
    _fields_ = [("ProcessTime", ctypes.c_int64), ("JobTime", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSet", ctypes.c_size_t),
                ("MaximumWorkingSet", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD)]


class IOCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint64) for name in
                ("ReadOperations", "WriteOperations", "OtherOperations", "ReadBytes", "WriteBytes", "OtherBytes")]


class ExtendedLimits(ctypes.Structure):
    _fields_ = [("BasicLimits", BasicLimits), ("IOCounters", IOCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]


def kernel_api():
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    for name, args, result in (
        ("CreateJobObjectW", [ctypes.c_void_p, wintypes.LPCWSTR], wintypes.HANDLE),
        ("SetInformationJobObject", [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD], wintypes.BOOL),
        ("AssignProcessToJobObject", [wintypes.HANDLE, wintypes.HANDLE], wintypes.BOOL),
        ("IsProcessInJob", [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)], wintypes.BOOL),
        ("TerminateJobObject", [wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
        ("OpenProcess", [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
        ("CloseHandle", [wintypes.HANDLE], wintypes.BOOL),
        ("CreateMutexW", [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR], wintypes.HANDLE),
    ):
        function = getattr(kernel, name)
        function.argtypes, function.restype = args, result
    return kernel


class ConsumerJob:
    """Non-inheritable groups; Windows kills members when their owner exits."""
    def __init__(self, kernel=None):
        self.kernel = kernel or kernel_api()
        self.extra_handles = []
        self.handle = self._create_group()

    def _create_group(self):
        handle = self.kernel.CreateJobObjectW(None, None)
        if not handle:
            raise monitor.MonitorError("Windows could not create the consumer process group.")
        limits = ExtendedLimits()
        limits.BasicLimits.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.kernel.SetInformationJobObject(handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            self.kernel.CloseHandle(handle)
            raise monitor.MonitorError("Windows could not enable automatic consumer cleanup.")
        return handle

    def attach(self, process):
        # Assign the root first so all future children inherit ownership. Then
        # attach its existing descendants, including a pre-existing synthesis.
        self._attach_pid(process.pid)
        for child in process.children(recursive=True):
            try:
                self._attach_pid(child.pid)
            except psutil.NoSuchProcess:
                pass

    def _attach_pid(self, pid):
        handle = self.kernel.OpenProcess(0x0100 | 0x0001 | 0x1000, False, pid)
        if not handle:
            if not psutil.pid_exists(pid):
                raise psutil.NoSuchProcess(pid)
            raise monitor.MonitorError("Windows denied ownership of the consumer. Close it and launch Studio again.")
        try:
            for group in [self.handle, *self.extra_handles]:
                member = wintypes.BOOL()
                if not self.kernel.IsProcessInJob(handle, group, ctypes.byref(member)):
                    raise monitor.MonitorError("Windows could not check consumer ownership.")
                if member.value:
                    return
            if not self.kernel.AssignProcessToJobObject(self.handle, handle):
                # Windows may forbid merging an existing child's job hierarchy
                # into its parent's new job. Give that child its own kill-on-close
                # group instead; future descendants inherit that ownership.
                group = self._create_group()
                if not self.kernel.AssignProcessToJobObject(group, handle):
                    error = ctypes.get_last_error()
                    self.kernel.CloseHandle(group)
                    raise monitor.MonitorError(f"Windows could not own the consumer process group (error {error}).")
                self.extra_handles.append(group)
        finally:
            self.kernel.CloseHandle(handle)

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None
        for group in self.extra_handles:
            self.kernel.CloseHandle(group)
        self.extra_handles.clear()


class SingleInstance:
    def __init__(self, root=PROJECT_ROOT):
        self.kernel = kernel_api()
        suffix = hashlib.sha256(str(root.resolve()).lower().encode()).hexdigest()[:16]
        self.handle = self.kernel.CreateMutexW(None, False, "Local\\OpenShelfStudio-" + suffix)
        if not self.handle:
            raise monitor.MonitorError("Windows could not initialize the Studio launcher.")
        self.existing = ctypes.get_last_error() == 183

    def focus_existing(self):
        user = ctypes.WinDLL("user32", use_last_error=True)
        user.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
        user.FindWindowW.restype = wintypes.HWND
        user.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        user.SetForegroundWindow.argtypes = [wintypes.HWND]
        window = user.FindWindowW(None, TITLE)
        if window:
            user.ShowWindow(window, 9)
            user.SetForegroundWindow(window)

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


def authenticate_request(request, origin, token):
    """Never forward local authority to another origin or foreign-page fetch."""
    url = urlsplit(request.url)
    local = urlsplit(origin)
    headers = request.headers
    foreign_origin = next((value for key, value in headers.items() if key.lower() == "origin"), None)
    fetch_site = next((value for key, value in headers.items() if key.lower() == "sec-fetch-site"), "none")
    if (url.scheme, url.netloc) == (local.scheme, local.netloc) and url.path.startswith("/api/"):
        if foreign_origin not in (None, origin) or fetch_site not in ("same-origin", "none"):
            return
        for key in list(headers):
            if key.lower() == "x-monitor-token":
                del headers[key]
        headers["X-Monitor-Token"] = token


class Studio:
    def __init__(self, root=PROJECT_ROOT, job=None, server=None):
        self.root = root
        self.job = job or ConsumerJob()
        try:
            self.server = server or DashboardServer(root=root, on_start=self.start_consumer)
        except Exception:
            self.job.close()
            raise
        self.server.on_start = self.start_consumer
        self.thread = None
        self.lock = threading.RLock()
        self.closed = False

    def start_consumer(self):
        with self.lock:
            if self.closed:
                raise monitor.MonitorError("Studio is closing.")
            pid = monitor.start_consumer(self.root)
            process = psutil.Process(pid)
            self.job.attach(process)
            return pid

    def start(self):
        self.start_consumer()
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        with self.lock:
            if self.closed:
                return
            self.closed = True
            # Windows terminates each owned group and its descendants.
            self.job.close()
            if self.thread:
                self.server.shutdown()
            self.server.server_close()
            if self.thread:
                self.thread.join(timeout=3)


def main():
    if os.name != "nt":
        raise monitor.MonitorError("OpenShelf Studio currently requires Windows.")
    instance = SingleInstance()
    if instance.existing:
        instance.focus_existing()
        instance.close()
        return
    studio = None
    try:
        import webview
        from webview.event import Event
        webview.settings["ALLOW_FILE_URLS"] = False
        webview.settings["ALLOW_DOWNLOADS"] = False
        webview.settings["REMOTE_DEBUGGING_PORT"] = None
        studio = Studio()
        # An explicit temporary profile also permits deterministic cleanup tests.
        with tempfile.TemporaryDirectory(prefix="openshelf-studio-") as profile:
            window = webview.create_window(TITLE, studio.server.origin + "/", width=1180,
                                           height=820, min_size=(780, 560),
                                           background_color="#f7f8f2", text_select=True)
            def request_sent(request):
                authenticate_request(request, studio.server.origin, studio.server.token)
            # pywebview 6 dispatches request_sent asynchronously by default;
            # credentials must be attached before its renderer sends the request.
            window.events.request_sent = Event(window, True)
            window.events.request_sent += request_sent
            window.events.closing += studio.close
            studio.server.on_close = window.destroy
            studio.start()
            webview.start(gui="edgechromium", private_mode=True, storage_path=profile, debug=False)
    except Exception as exc:
        if studio:
            studio.close()
            studio = None
        message = str(exc) if isinstance(exc, monitor.MonitorError) else (
            "Studio could not start. Install pywebview>=6,<7 in the root .venv and ensure "
            "Microsoft WebView2 Runtime is installed.")
        ctypes.windll.user32.MessageBoxW(None, message, TITLE, 0x10)
    finally:
        if studio:
            studio.close()
        instance.close()
