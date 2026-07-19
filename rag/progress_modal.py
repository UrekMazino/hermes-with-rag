"""
Reusable GUI progress modal for long-running Project Y RAG jobs.

Pops a small always-on-top window with a determinate progress bar, a live
percentage, an ETA, and the current item. Used by triage, OCR, and indexing
so you get visible feedback on multi-thousand-file / multi-hour runs.

Headless-safe: if no display is available (e.g. a hidden/background launch),
it silently degrades to periodic console prints instead of crashing.

Usage:
    from progress_modal import ProgressModal
    pm = ProgressModal("PDF Triage", total=8823, subtitle="Classifying...")
    for i, item in enumerate(items, 1):
        ...work...
        pm.update(i, status=item.name)
        if pm.cancelled:        # user closed the window
            break
    pm.close("Done")
"""
from __future__ import annotations
import time


class ProgressModal:
    def __init__(self, title: str = "Working...", total: int = 100, subtitle: str = ""):
        self.total = max(1, int(total))
        self.cancelled = False
        self.ok = False
        self._start = time.time()
        self._last_console = 0
        try:
            import tkinter as tk
            from tkinter import ttk
            self.root = tk.Tk()
            self.root.title(title)
            self.root.geometry("540x150")
            self.root.resizable(False, False)
            self.root.attributes("-topmost", True)
            self.root.protocol("WM_DELETE_WINDOW", self._on_close)
            self._pct = tk.StringVar(value=f"0.0%    0 / {self.total}")
            self._status = tk.StringVar(value=subtitle or "Starting...")
            self._eta = tk.StringVar(value="ETA: --")
            tk.Label(self.root, text=title, font=("Segoe UI", 11, "bold")).pack(pady=(12, 2))
            self.bar = ttk.Progressbar(self.root, length=500, maximum=self.total, mode="determinate")
            self.bar.pack(pady=4)
            row = tk.Frame(self.root); row.pack(fill="x", padx=20)
            tk.Label(row, textvariable=self._pct, font=("Segoe UI", 10)).pack(side="left")
            tk.Label(row, textvariable=self._eta, font=("Segoe UI", 10), fg="#555").pack(side="right")
            tk.Label(self.root, textvariable=self._status, font=("Segoe UI", 8),
                     fg="#555", wraplength=500, anchor="w", justify="left").pack(pady=(2, 8), padx=20, fill="x")
            self.root.update()
            self.ok = True
        except Exception as e:
            print(f"[progress] GUI unavailable ({type(e).__name__}); using console output.")
            self.ok = False

    def _on_close(self):
        self.cancelled = True

    def _eta_str(self, done: int) -> str:
        if done <= 0:
            return "ETA: --"
        elapsed = time.time() - self._start
        rate = done / elapsed if elapsed > 0 else 0
        if rate <= 0:
            return "ETA: --"
        remaining = (self.total - done) / rate
        m, s = divmod(int(remaining), 60)
        h, m = divmod(m, 60)
        return f"ETA: {h:d}h{m:02d}m" if h else f"ETA: {m:d}m{s:02d}s"

    def update(self, done: int, status: str = ""):
        pct = 100.0 * done / self.total
        if self.ok:
            try:
                self.bar["value"] = min(done, self.total)
                self._pct.set(f"{pct:5.1f}%    {done} / {self.total}")
                self._eta.set(self._eta_str(done))
                if status:
                    self._status.set(status)
                self.root.update()
                return
            except Exception:
                self.ok = False
        # console fallback: print at most ~every 2s
        now = time.time()
        if now - self._last_console >= 2 or done >= self.total:
            self._last_console = now
            print(f"  {pct:5.1f}%  {done}/{self.total}  {self._eta_str(done)}  {status}", flush=True)

    def close(self, final: str = "Done"):
        if self.ok:
            try:
                self.bar["value"] = self.total
                self._pct.set(f"100.0%    {self.total} / {self.total}")
                self._eta.set("Done")
                self._status.set(final)
                self.root.update()
                time.sleep(0.8)
                self.root.destroy()
            except Exception:
                pass
        else:
            print(f"  {final} ({self.total}/{self.total})", flush=True)
