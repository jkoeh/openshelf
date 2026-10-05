"""Native, local-only OpenShelf generation queue window for Windows."""

from __future__ import annotations

import queue
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from openshelf.pipeline.job_monitor import (  # noqa: E402
    REFRESH_SECONDS,
    MonitorError,
    MonitoredJob,
    QueueAPI,
    age,
    find_consumer,
    log_tail,
    start_consumer,
)


class JobMonitor:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.api = QueueAPI()
        self.messages: queue.Queue[tuple[str, object]] = queue.Queue()
        self.active: dict[str, MonitoredJob] = {}
        self.refreshing = False
        self.acting = False
        self.consumer_pid: int | None = None

        root.title("OpenShelf · Job Monitor")
        root.geometry("1080x720")
        root.minsize(780, 560)
        root.configure(bg="#f7f5ef")
        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure("Treeview", font=("Segoe UI", 10), rowheight=29,
                        background="#ffffff", fieldbackground="#ffffff", foreground="#24303a")
        style.configure("Treeview.Heading", font=("Segoe UI Semibold", 10), padding=(8, 8),
                        background="#e9edf0", foreground="#24303a")
        style.configure("TNotebook", background="#f7f5ef", borderwidth=0)
        style.configure("TNotebook.Tab", font=("Segoe UI", 10), padding=(16, 8))

        frame = tk.Frame(root, bg="#f7f5ef", padx=22, pady=18)
        frame.pack(fill="both", expand=True)
        tk.Label(frame, text="OpenShelf", bg="#f7f5ef", fg="#183a43",
                 font=("Georgia", 23, "bold")).pack(anchor="w")
        tk.Label(frame, text="Production job monitor · refreshes every 15 seconds",
                 bg="#f7f5ef", fg="#65737a", font=("Segoe UI", 10)).pack(anchor="w", pady=(1, 14))

        status = tk.Frame(frame, bg="#eaf0ed", padx=14, pady=10)
        status.pack(fill="x")
        self.summary = tk.Label(status, text="Connecting to queue…", bg="#eaf0ed", fg="#183a43",
                                font=("Segoe UI Semibold", 11))
        self.summary.pack(anchor="w")
        self.working = tk.Label(status, text="", bg="#eaf0ed", fg="#4a5b61", font=("Segoe UI", 10))
        self.working.pack(anchor="w", pady=(3, 0))
        self.consumer = tk.Label(status, text="Checking this PC…", bg="#eaf0ed", fg="#4a5b61",
                                 font=("Segoe UI", 10))
        self.consumer.pack(anchor="w", pady=(3, 0))

        actions = tk.Frame(frame, bg="#f7f5ef")
        actions.pack(fill="x", pady=(15, 10))
        self.refresh_button = ttk.Button(actions, text="Refresh now", command=self.refresh)
        self.refresh_button.pack(side="left")
        self.start_button = ttk.Button(actions, text="Start consumer", command=self.start)
        self.start_button.pack(side="left", padx=(8, 0))
        self.high_button = ttk.Button(actions, text="High priority", command=lambda: self.prioritize(True))
        self.high_button.pack(side="right")
        self.normal_button = ttk.Button(actions, text="Normal priority", command=lambda: self.prioritize(False))
        self.normal_button.pack(side="right", padx=(0, 8))
        self.cancel_button = ttk.Button(actions, text="Cancel job", command=self.cancel)
        self.cancel_button.pack(side="right", padx=(0, 8))

        notebook = ttk.Notebook(frame)
        notebook.pack(fill="both", expand=True)
        self.queue_tree = self._table(notebook, "Queue / working", active=True)
        self.history_tree = self._table(notebook, "Recent history", active=False)
        self.queue_tree.bind("<<TreeviewSelect>>", lambda _event: self._selection())

        self.details = tk.Label(frame, text="Select a job for details.", anchor="w", bg="#f7f5ef",
                                fg="#4a5b61", font=("Segoe UI", 10))
        self.details.pack(fill="x", pady=(10, 5))
        tk.Label(frame, text="Consumer log · latest lines", anchor="w", bg="#f7f5ef",
                 fg="#183a43", font=("Segoe UI Semibold", 10)).pack(fill="x")
        self.log = tk.Text(frame, height=7, wrap="word", font=("Consolas", 9),
                           bg="#ffffff", fg="#263238", relief="flat", padx=9, pady=7)
        self.log.pack(fill="x", pady=(5, 0))
        self.log.configure(state="disabled")
        self.error = tk.Label(frame, text="", anchor="w", bg="#f7f5ef", fg="#a13d36",
                              font=("Segoe UI", 10))
        self.error.pack(fill="x", pady=(7, 0))
        self._selection()
        root.after(100, self._tick)
        root.after(200, self._drain)

    def _table(self, notebook: ttk.Notebook, label: str, *, active: bool) -> ttk.Treeview:
        page = tk.Frame(notebook, bg="#ffffff")
        notebook.add(page, text=label)
        columns = ("status", "priority", "book", "mode", "stage", "updated", "attempts")
        tree = ttk.Treeview(page, columns=columns, show="headings", selectmode="browse")
        headings = {"status": "Status", "priority": "Priority", "book": "Book",
                    "mode": "Mode", "stage": "Stage", "updated": "Updated", "attempts": "Tries"}
        widths = {"status": 170, "priority": 75, "book": 350, "mode": 95,
                  "stage": 110, "updated": 80, "attempts": 52}
        for column in columns:
            tree.heading(column, text=headings[column])
            tree.column(column, width=widths[column], minwidth=widths[column] // 2,
                        stretch=column == "book", anchor="w")
        tree.tag_configure("stuck", background="#ffebeb")
        tree.tag_configure("working", background="#edf5fa")
        tree.tag_configure("high", background="#fff6df")
        scroll = ttk.Scrollbar(page, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        return tree

    def _tick(self) -> None:
        self.refresh()
        self.root.after(REFRESH_SECONDS * 1000, self._tick)

    def refresh(self) -> None:
        if self.refreshing:
            return
        self.refreshing = True

        def work() -> None:
            try:
                active, recent = self.api.list_jobs()
                process = find_consumer()
                self.messages.put(("snapshot", (active, recent, process.pid if process else None, log_tail())))
            except Exception as exc:
                self.messages.put(("refresh_error", str(exc) if isinstance(exc, MonitorError) else "Could not refresh the queue."))

        threading.Thread(target=work, daemon=True).start()

    def _drain(self) -> None:
        while True:
            try:
                kind, payload = self.messages.get_nowait()
            except queue.Empty:
                break
            if kind == "snapshot":
                self.refreshing = False
                self._show(*payload)
            elif kind == "refresh_error":
                self.refreshing = False
                self.error.configure(text=str(payload))
                process = find_consumer()
                self.consumer_pid = process.pid if process else None
                self.consumer.configure(text=f"This PC: consumer PID {self.consumer_pid}" if process else "This PC: consumer offline")
                self.start_button.configure(state="disabled" if process else "normal")
            elif kind == "action_done":
                self.acting = False
                self.error.configure(text=str(payload))
                self.refresh()
            elif kind == "action_error":
                self.acting = False
                self.error.configure(text=str(payload))
                messagebox.showerror("OpenShelf", str(payload), parent=self.root)
                self._selection()
                self.refresh()
        self.root.after(200, self._drain)

    def _show(self, active: list[MonitoredJob], recent: list[MonitoredJob],
              consumer_pid: int | None, log: str) -> None:
        selected = self.queue_tree.selection()
        selected_id = selected[0] if selected else None
        self.active = {job.id: job for job in active}
        for tree, jobs in ((self.queue_tree, active), (self.history_tree, recent)):
            tree.delete(*tree.get_children())
            for job in jobs:
                status = job.status()
                tag = "stuck" if status.startswith("Stuck") else "working" if status == "Working" else "high" if job.priority else ""
                tree.insert("", "end", iid=job.id, values=(status, "High" if job.priority else "Normal",
                            f"{job.title} · {job.author}", job.mode.capitalize(), job.stage,
                            age(job.updated_at), job.attempts), tags=(tag,))
        if selected_id in self.active:
            self.queue_tree.selection_set(selected_id)
        running = [job for job in active if job.state == "running"]
        stuck = sum(job.status().startswith("Stuck") for job in running)
        pending = sum(job.state == "queued" for job in active)
        self.summary.configure(text=f"{pending} pending    ·    {len(running) - stuck} working    ·    {stuck} stuck")
        self.working.configure(text=(f"Current: {running[0].title} · {running[0].mode} · {running[0].stage}"
                                     if running else "Current: no job running"))
        self.consumer_pid = consumer_pid
        self.consumer.configure(text=f"This PC: consumer PID {consumer_pid}" if consumer_pid else "This PC: consumer offline")
        self.start_button.configure(state="disabled" if consumer_pid or self.acting else "normal")
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.insert("1.0", log)
        self.log.configure(state="disabled")
        self.error.configure(text="")
        self._selection()

    def _selection(self) -> MonitoredJob | None:
        selection = self.queue_tree.selection()
        job = self.active.get(selection[0]) if selection else None
        queued = bool(job and job.state == "queued" and not self.acting)
        self.high_button.configure(state="normal" if queued and not job.priority else "disabled")
        self.normal_button.configure(state="normal" if queued and job.priority else "disabled")
        self.cancel_button.configure(state="normal" if job and job.state in ("queued", "running") and not self.acting else "disabled")
        self.details.configure(text=(f"{job.source_id}  ·  Job {job.id}  ·  Created {age(job.created_at)} ago  ·  "
                                     f"Last update {age(job.updated_at)} ago  ·  Lease {job.lease_until or '—'}"
                                     if job else "Select a queued or running job for details and controls."))
        return job

    def _act(self, operation, success: str) -> None:
        if self.acting:
            return
        self.acting = True
        self._selection()

        def work() -> None:
            try:
                operation()
                self.messages.put(("action_done", success))
            except Exception as exc:
                self.messages.put(("action_error", str(exc) if isinstance(exc, MonitorError) else "Action failed."))

        threading.Thread(target=work, daemon=True).start()

    def prioritize(self, high: bool) -> None:
        job = self._selection()
        if job and job.state == "queued":
            self._act(lambda: self.api.set_priority(job.id, high), "Priority updated.")

    def cancel(self) -> None:
        job = self._selection()
        if not job or job.state not in ("queued", "running"):
            return
        suffix = " The PC stops its child at the next heartbeat (about 30 seconds)." if job.state == "running" else ""
        if messagebox.askyesno("Cancel generation", f"Cancel {job.title}?{suffix}\n\nThe daily start is not refunded.", parent=self.root):
            self._act(lambda: self.api.cancel(job.id), "Cancellation requested.")

    def start(self) -> None:
        if self.consumer_pid:
            return
        self._act(start_consumer, "Consumer started. It polls every 45 seconds while idle.")


def main() -> None:
    root = tk.Tk()
    JobMonitor(root)
    root.mainloop()


if __name__ == "__main__":
    main()
