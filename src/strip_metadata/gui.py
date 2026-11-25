"""Window app: drop folders/files (or pick them), confirm, watch progress."""
from __future__ import annotations

import os
import queue
import threading
import time
import tkinter as tk
from collections.abc import Callable
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import __version__
from .cli import fmt_time, log_dir
from .core import CLEAN, CLEANED, VIDEO_EXT, StripError, find_tool, scan, strip_file

try:  # drag & drop onto the window; the app still works without it
    from tkinterdnd2 import DND_FILES, TkinterDnD
except Exception:  # pragma: no cover - optional dependency
    TkinterDnD = None
    DND_FILES = None

FONT = "Microsoft JhengHei UI"
ACCENT = "#2563eb"


class App:
    def __init__(self, root: tk.Tk, confirm: Callable[[str, str], bool] | None = None):
        self.root = root
        self.confirm = confirm or (lambda title, msg: messagebox.askokcancel(title, msg, parent=root))
        self.q: queue.Queue = queue.Queue()
        self.stop_event = threading.Event()
        self.state = "idle"  # idle | scanning | running | done
        self.counts = {CLEANED: 0, CLEAN: 0, "failed": 0}
        self.log_path: Path | None = None
        self.keep_orientation = tk.BooleanVar(value=True)
        self.dry_run = tk.BooleanVar(value=False)
        self._build()
        self.root.after(50, self._poll)

    # ------------------------------------------------------------ layout
    def _build(self) -> None:
        r = self.root
        r.title(f"清除相片影片資訊 {__version__}")
        r.geometry("600x520")
        r.minsize(480, 460)
        style = ttk.Style(r)
        style.configure(".", font=(FONT, 10))
        style.configure("Title.TLabel", font=(FONT, 14, "bold"))
        style.configure("Muted.TLabel", foreground="#555")
        style.configure("Accent.TButton", font=(FONT, 10, "bold"))

        main = ttk.Frame(r, padding=16)
        main.pack(fill="both", expand=True)
        ttk.Label(main, text="清除相片與影片的 EXIF、GPS、AI 來源標記", style="Title.TLabel").pack(anchor="w")
        ttk.Label(main, text="會處理所有子資料夾，直接覆蓋原檔，畫質不變。",
                  style="Muted.TLabel").pack(anchor="w", pady=(2, 12))

        self.drop = tk.Label(main, font=(FONT, 12), fg=ACCENT, bg="#eef4ff", relief="groove", bd=2, height=5)
        self.drop.pack(fill="x")
        self.dnd_enabled = False
        if TkinterDnD is not None:
            try:  # only works when the root window was created by TkinterDnD
                self.drop.drop_target_register(DND_FILES)
                self.drop.dnd_bind("<<Drop>>", self._on_drop)
                self.dnd_enabled = True
            except (tk.TclError, AttributeError):
                pass
        self.drop.configure(text="把資料夾或檔案拖到這裡" if self.dnd_enabled else "按下方按鈕選擇資料夾或檔案")

        btns = ttk.Frame(main)
        btns.pack(fill="x", pady=10)
        self.btn_folder = ttk.Button(btns, text="選擇資料夾…", style="Accent.TButton", command=self._pick_folder)
        self.btn_folder.pack(side="left")
        self.btn_files = ttk.Button(btns, text="選擇檔案…", command=self._pick_files)
        self.btn_files.pack(side="left", padx=8)
        self.btn_stop = ttk.Button(btns, text="停止", command=self.stop, state="disabled")
        self.btn_stop.pack(side="right")

        opts = ttk.Frame(main)
        opts.pack(fill="x")
        ttk.Checkbutton(opts, text="保留照片旋轉方向（建議）", variable=self.keep_orientation).pack(side="left")
        ttk.Checkbutton(opts, text="只檢查，不修改", variable=self.dry_run).pack(side="left", padx=16)

        self.bar = ttk.Progressbar(main, mode="determinate", maximum=1)
        self.bar.pack(fill="x", pady=(14, 4))
        self.status = ttk.Label(main, text="等待選擇檔案")
        self.status.pack(anchor="w")
        self.detail = ttk.Label(main, text="", style="Muted.TLabel")
        self.detail.pack(anchor="w")

        self.text = tk.Text(main, height=8, font=(FONT, 9), relief="flat", bg="#f6f6f6", wrap="word")
        self.text.pack(fill="both", expand=True, pady=(8, 8))
        self.text.configure(state="disabled")

        foot = ttk.Frame(main)
        foot.pack(fill="x")
        ffmpeg = "已找到 ffmpeg，可處理影片" if find_tool("ffmpeg") else "找不到 ffmpeg：只處理圖片，影片會略過"
        ttk.Label(foot, text=ffmpeg, style="Muted.TLabel").pack(side="left")
        ttk.Button(foot, text="開啟紀錄資料夾", command=lambda: os.startfile(log_dir())).pack(side="right")

    def _write(self, line: str) -> None:
        self.text.configure(state="normal")
        self.text.insert("end", line + "\n")
        self.text.see("end")
        self.text.configure(state="disabled")

    def _set_busy(self, busy: bool) -> None:
        for b in (self.btn_folder, self.btn_files):
            b.configure(state="disabled" if busy else "normal")
        self.btn_stop.configure(state="normal" if busy else "disabled")

    # ------------------------------------------------------------ input
    def _on_drop(self, event) -> None:
        self.start([Path(p) for p in self.root.tk.splitlist(event.data)])

    def _pick_folder(self) -> None:
        d = filedialog.askdirectory(parent=self.root, title="選擇要清理的資料夾（含子資料夾）")
        if d:
            self.start([Path(d)])

    def _pick_files(self) -> None:
        fs = filedialog.askopenfilenames(parent=self.root, title="選擇檔案", filetypes=[
            ("相片與影片", "*.jpg *.jpeg *.png *.webp *.mp4 *.mov *.m4v *.mkv *.webm *.avi *.3gp"),
            ("所有檔案", "*.*")])
        if fs:
            self.start([Path(f) for f in fs])

    # ------------------------------------------------------------ work
    def start(self, paths: list[Path]) -> None:
        if self.state in ("scanning", "running"):
            return
        self.state = "scanning"
        self.stop_event.clear()
        self._set_busy(True)
        self.status.configure(text="正在搜尋檔案…")
        self.detail.configure(text="")
        self.bar.configure(value=0)
        threading.Thread(target=lambda: self.q.put(("scanned", paths, *scan(paths))), daemon=True).start()

    def stop(self) -> None:
        self.stop_event.set()
        self.status.configure(text="正在停止…")

    def _after_scan(self, paths, targets, unsupported) -> None:
        has_ffmpeg = find_tool("ffmpeg") is not None
        skipped_videos = 0 if has_ffmpeg else sum(p.suffix.lower() in VIDEO_EXT for p in targets)
        if not has_ffmpeg:
            targets = [p for p in targets if p.suffix.lower() not in VIDEO_EXT]
        n_vid = sum(p.suffix.lower() in VIDEO_EXT for p in targets)
        if not targets:
            self._finish_idle("沒有找到可處理的相片或影片。")
            return
        msg = f"找到圖片 {len(targets) - n_vid} 張、影片 {n_vid} 部。"
        if skipped_videos:
            msg += f"\n（找不到 ffmpeg，{skipped_videos} 部影片會略過）"
        if self.dry_run.get():
            msg += "\n\n只檢查，不會修改任何檔案。要開始嗎？"
        else:
            msg += "\n\n會直接覆蓋原檔（畫質不變），無法復原。要開始嗎？"
        if not self.confirm("確認", msg):
            self._finish_idle("已取消。")
            return
        self.state = "running"
        self.counts = {CLEANED: 0, CLEAN: 0, "failed": 0}
        self.bar.configure(maximum=len(targets), value=0)
        self.log_path = log_dir() / time.strftime("strip_%Y%m%d_%H%M%S.log")
        args = (paths, targets, unsupported, self.keep_orientation.get(), self.dry_run.get())
        threading.Thread(target=self._worker, args=args, daemon=True).start()

    def _worker(self, paths, targets, unsupported, keep_orientation, dry_run) -> None:
        t0 = time.time()
        with open(self.log_path, "w", encoding="utf-8") as log:
            log.write(f"paths={[str(p) for p in paths]} dry_run={dry_run}\n")
            for i, p in enumerate(targets, 1):
                if self.stop_event.is_set():
                    log.write("stopped by user\n")
                    break
                t1 = time.time()
                err = None
                try:
                    status = strip_file(p, keep_orientation, dry_run)
                except StripError as e:
                    status, err = "failed", str(e)
                    log.write(f"FAILED {p}: {e}\n")
                log.write(f"{status} {p}\n")
                eta = (time.time() - t0) / i * (len(targets) - i)
                self.q.put(("progress", i, len(targets), p, status, err, time.time() - t1, eta))
            for p in unsupported:
                log.write(f"unsupported {p}\n")
        self.q.put(("done", time.time() - t0, len(unsupported), dry_run))

    def _poll(self) -> None:
        try:
            while True:
                msg = self.q.get_nowait()
                kind = msg[0]
                if kind == "scanned":
                    self._after_scan(*msg[1:])
                elif kind == "progress":
                    _, i, n, p, status, err, took, eta = msg
                    self.counts[status] += 1
                    self.bar.configure(value=i)
                    self.status.configure(text=f"{i}/{n}（{100 * i / n:.0f}%）  剩餘約 {fmt_time(eta)}")
                    self.detail.configure(text=f"{p.name}  本檔 {took:.1f} 秒")
                    if err:
                        self._write(f"失敗：{p}（{err}）")
                elif kind == "done":
                    _, elapsed, n_unsup, dry_run = msg
                    c = self.counts
                    verb = "需要清除" if dry_run else "已清除"
                    stopped = "（已中途停止）" if self.stop_event.is_set() else ""
                    summary = (f"完成{stopped}，耗時 {fmt_time(elapsed)}：{verb} {c[CLEANED]} 個、"
                               f"本來就乾淨 {c[CLEAN]} 個、失敗 {c['failed']} 個。")
                    self._write(summary)
                    if n_unsup:
                        self._write(f"另有 {n_unsup} 個不支援的格式（HEIC/TIFF/GIF 等）沒有處理。")
                    self.status.configure(text=summary)
                    self.detail.configure(text="")
                    self.state = "done"
                    self._set_busy(False)
        except queue.Empty:
            pass
        self.root.after(50, self._poll)

    def _finish_idle(self, text: str) -> None:
        self.status.configure(text=text)
        self.state = "idle"
        self._set_busy(False)


def make_root() -> tk.Tk:
    if TkinterDnD is not None:
        try:
            return TkinterDnD.Tk()
        except Exception:  # tkdnd binary missing: fall back to a plain window
            pass
    return tk.Tk()


def main(paths: list[str] | None = None) -> int:
    try:  # sharp text on high-DPI screens
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    root = make_root()
    app = App(root)
    if paths:  # files dropped on the .exe icon
        root.after(200, lambda: app.start([Path(p) for p in paths]))
    root.mainloop()
    return 0
