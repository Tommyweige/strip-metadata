"""Window app: drop folders/files (or pick them), confirm, watch progress."""
from __future__ import annotations

import os
import queue
import threading
import time
import tkinter as tk
import webbrowser
from collections.abc import Callable
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import __version__, batch, integration, updates
from .cache import CleanCache
from .cli import fmt_time, log_dir
from .core import (
    CLEAN,
    CLEANED,
    VIDEO_EXT,
    Options,
    copy_destination,
    copy_root,
    find_tool,
    scan,
)
from .settings import Settings, app_dir

try:  # drag & drop onto the window; the app still works without it
    from tkinterdnd2 import DND_FILES, TkinterDnD
except Exception:  # pragma: no cover - optional dependency
    TkinterDnD = None
    DND_FILES = None

FONT = "Microsoft JhengHei UI"
ACCENT = "#2563eb"
WARN = "#b45309"
BATCH_WAIT = 0.4  # seconds to collect paths arriving from several right-click launches
CROP_LABELS = {"": "不裁切", "9:16": "直式 9:16", "16:9": "橫式 16:9"}
CROP_VALUES = {label: value for value, label in CROP_LABELS.items()}


class App:
    def __init__(self, root: tk.Tk, confirm: Callable[[str, str], bool] | None = None,
                 settings: Settings | None = None, settings_path: Path | None = None,
                 fetch_update: Callable[[], tuple[str, str] | None] | None = None):
        self.root = root
        self.confirm = confirm or (lambda title, msg: messagebox.askokcancel(title, msg, parent=root))
        self.settings = settings or Settings.load(settings_path)
        self.settings_path = settings_path
        self.fetch_update = fetch_update or updates.fetch_latest
        self.q: queue.Queue = queue.Queue()
        self.stop_event = threading.Event()
        self.state = "idle"  # idle | scanning | running | done
        self.counts = {CLEANED: 0, CLEAN: 0, "failed": 0}
        self.failed: list[Path] = []
        self.roots: list[Path] = []
        self.pending: list[Path] = []
        self.pending_since = 0.0
        self.log_path: Path | None = None
        self.update_info: tuple[str, str] | None = None

        s = self.settings
        self.keep_orientation = tk.BooleanVar(value=s.keep_orientation)
        self.keep_workflow = tk.BooleanVar(value=s.keep_workflow)
        self.save_copy = tk.BooleanVar(value=s.save_copy)
        self.keep_dates = tk.BooleanVar(value=s.keep_dates)
        self.check_updates = tk.BooleanVar(value=s.check_updates)
        self.remember_clean = tk.BooleanVar(value=s.remember_clean)
        self.crop_video = tk.StringVar(value=s.crop_video)
        self.cache_path = (settings_path.parent if settings_path else app_dir()) / "clean_cache.json"
        self.cache: CleanCache | None = None
        self.dry_run = tk.BooleanVar(value=False)
        for name in ("keep_orientation", "keep_workflow", "save_copy", "keep_dates", "check_updates",
                     "remember_clean", "crop_video"):
            getattr(self, name).trace_add("write", lambda *_: self._save_settings())

        self._build()
        self.root.after(50, self._poll)
        self._maybe_check_updates()

    # ------------------------------------------------------------ settings
    def _save_settings(self) -> None:
        s = self.settings
        s.keep_orientation = self.keep_orientation.get()
        s.keep_workflow = self.keep_workflow.get()
        s.save_copy = self.save_copy.get()
        s.keep_dates = self.keep_dates.get()
        s.check_updates = self.check_updates.get()
        s.remember_clean = self.remember_clean.get()
        s.crop_video = self.crop_video.get()
        s.save(self.settings_path)
        self._refresh_copy_banner()

    def options(self) -> Options:
        return Options(keep_orientation=self.keep_orientation.get(), keep_workflow=self.keep_workflow.get(),
                       keep_dates=self.keep_dates.get(), crop=self.crop_video.get())

    # ------------------------------------------------------------ layout
    def _build(self) -> None:
        r = self.root
        r.title(f"清除相片影片資訊 {__version__}")
        r.geometry("620x600")
        r.minsize(500, 520)
        style = ttk.Style(r)
        style.configure(".", font=(FONT, 10))
        style.configure("Title.TLabel", font=(FONT, 14, "bold"))
        style.configure("Muted.TLabel", foreground="#555")
        style.configure("Warn.TLabel", foreground=WARN, font=(FONT, 10, "bold"))
        style.configure("Link.TLabel", foreground=ACCENT, font=(FONT, 10, "underline"))
        style.configure("Accent.TButton", font=(FONT, 10, "bold"))

        main = ttk.Frame(r, padding=16)
        main.pack(fill="both", expand=True)
        # Each optional panel is packed into an empty anchor frame. The anchor must be created
        # BEFORE the panel: a sibling created later is drawn on top and would hide the panel.
        ttk.Label(main, text="清除相片與影片的 EXIF、GPS、AI 來源標記", style="Title.TLabel").pack(anchor="w")
        ttk.Label(main, text="會處理所有子資料夾，畫質不變。", style="Muted.TLabel").pack(anchor="w", pady=(2, 10))
        self.top_anchor = ttk.Frame(main)  # update banner goes here
        self.top_anchor.pack(fill="x")
        self.update_label = ttk.Label(main, style="Link.TLabel", cursor="hand2")
        self.update_label.bind("<Button-1>", lambda _: self._open_update())

        self.drop = tk.Label(main, font=(FONT, 12), fg=ACCENT, bg="#eef4ff", relief="groove", bd=2, height=4)
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

        self.copy_anchor = ttk.Frame(main)
        self.copy_anchor.pack(fill="x")
        self.copy_banner = ttk.Label(main, style="Warn.TLabel")

        btns = ttk.Frame(main)
        btns.pack(fill="x", pady=8)
        self.btn_folder = ttk.Button(btns, text="選擇資料夾…", style="Accent.TButton", command=self._pick_folder)
        self.btn_folder.pack(side="left")
        self.btn_files = ttk.Button(btns, text="選擇檔案…", command=self._pick_files)
        self.btn_files.pack(side="left", padx=8)
        self.btn_stop = ttk.Button(btns, text="停止", command=self.stop, state="disabled")
        self.btn_stop.pack(side="right")
        self.btn_retry = ttk.Button(btns, text="重試失敗的檔案", command=self.retry_failed)

        opts = ttk.Frame(main)
        opts.pack(fill="x")
        ttk.Checkbutton(opts, text="只檢查，不修改", variable=self.dry_run).pack(side="left")
        self.btn_adv = ttk.Button(opts, command=self._toggle_advanced, width=12)
        self.btn_adv.pack(side="right")

        self.adv_anchor = ttk.Frame(main)
        self.adv_anchor.pack(fill="x")
        self.adv = ttk.LabelFrame(main, text="進階選項", padding=(10, 6))
        for text, var in (("保留照片旋轉方向（建議）", self.keep_orientation),
                          ("保留 AI 工作流程（PNG 裡的 ComfyUI 等設定）", self.keep_workflow),
                          ("另存到新資料夾（原資料夾名稱後加「_已清除」，原檔不動）", self.save_copy),
                          ("保留檔案日期", self.keep_dates),
                          ("略過上次已清理、之後沒改過的檔案（加快速度）", self.remember_clean),
                          ("自動檢查新版本（只讀取 GitHub 上的版本號）", self.check_updates)):
            ttk.Checkbutton(self.adv, text=text, variable=var).pack(anchor="w", pady=1)
        crop_row = ttk.Frame(self.adv)
        crop_row.pack(fill="x", pady=(4, 0))
        ttk.Label(crop_row, text="裁切影片：").pack(side="left")
        self.crop_combo = ttk.Combobox(crop_row, state="readonly", width=16,
                                       values=["不裁切", "直式 9:16", "橫式 16:9"])
        self.crop_combo.pack(side="left")
        self.crop_combo.set(CROP_LABELS.get(self.crop_video.get(), "不裁切"))
        self.crop_combo.bind("<<ComboboxSelected>>", self._on_crop_selected)
        menu_row = ttk.Frame(self.adv)
        menu_row.pack(fill="x", pady=(6, 0))
        self.btn_menu = ttk.Button(menu_row, command=self._toggle_menu)
        self.btn_menu.pack(side="left")
        self.menu_status = ttk.Label(menu_row, style="Muted.TLabel")
        self.menu_status.pack(side="left", padx=8)
        self._refresh_menu_button()
        self._show_advanced(self.settings.advanced_open)

        self.bar = ttk.Progressbar(main, mode="determinate", maximum=1)
        self.bar.pack(fill="x", pady=(12, 4))
        self.status = ttk.Label(main, text="等待選擇檔案")
        self.status.pack(anchor="w")
        self.detail = ttk.Label(main, text="", style="Muted.TLabel")
        self.detail.pack(anchor="w")

        self.text = tk.Text(main, height=6, font=(FONT, 9), relief="flat", bg="#f6f6f6", wrap="word")
        self.text.pack(fill="both", expand=True, pady=(8, 8))
        self.text.configure(state="disabled")

        foot = ttk.Frame(main)
        foot.pack(fill="x")
        ffmpeg = "已找到 ffmpeg，可處理影片" if find_tool("ffmpeg") else "找不到 ffmpeg：只處理圖片，影片會略過"
        ttk.Label(foot, text=ffmpeg, style="Muted.TLabel").pack(side="left")
        ttk.Button(foot, text="開啟紀錄資料夾", command=lambda: os.startfile(log_dir())).pack(side="right")
        self._refresh_copy_banner()

    def _show_advanced(self, show: bool) -> None:
        if show:
            self.adv.pack(in_=self.adv_anchor, fill="x", pady=(6, 0))
        else:
            self.adv.pack_forget()
        self.btn_adv.configure(text="進階選項 ▾" if show else "進階選項 ▸")
        self.settings.advanced_open = show

    def _toggle_advanced(self) -> None:
        self._show_advanced(not self.settings.advanced_open)
        self.settings.save(self.settings_path)

    def _on_crop_selected(self, _event=None) -> None:
        self.crop_video.set(CROP_VALUES.get(self.crop_combo.get(), ""))

    def _refresh_copy_banner(self) -> None:
        if self.save_copy.get():
            self.copy_banner.configure(text="目前：另存到新資料夾（名稱後加「_已清除」），原檔不會被修改")
            self.copy_banner.pack(in_=self.copy_anchor, anchor="w", pady=(6, 0))
        else:
            self.copy_banner.pack_forget()

    def _refresh_menu_button(self) -> None:
        try:
            installed = integration.menu_installed()
        except Exception:
            self.btn_menu.configure(text="右鍵選單（不支援）", state="disabled")
            return
        self.btn_menu.configure(text="移除右鍵選單" if installed else "加入右鍵選單")
        self.menu_status.configure(text="檔案總管按右鍵可用「清除相片資訊」" if installed else "")

    def _toggle_menu(self) -> None:
        try:
            if integration.menu_installed():
                integration.uninstall_menu()
            else:
                integration.install_menu()
        except OSError as e:
            self.menu_status.configure(text=f"失敗：{e}")
            return
        self._refresh_menu_button()

    def _write(self, line: str) -> None:
        self.text.configure(state="normal")
        self.text.insert("end", line + "\n")
        self.text.see("end")
        self.text.configure(state="disabled")

    def _set_busy(self, busy: bool) -> None:
        for b in (self.btn_folder, self.btn_files, self.btn_retry):
            b.configure(state="disabled" if busy else "normal")
        self.btn_stop.configure(state="normal" if busy else "disabled")

    # ------------------------------------------------------------ updates
    def _maybe_check_updates(self) -> None:
        if not self.check_updates.get():
            return
        if time.time() - self.settings.last_update_check < updates.CHECK_EVERY:
            return
        self.settings.last_update_check = time.time()
        self.settings.save(self.settings_path)

        def work() -> None:
            found = updates.newer_release(__version__, fetch=self.fetch_update)
            if found:
                self.q.put(("update", *found))
        threading.Thread(target=work, daemon=True).start()

    def _open_update(self) -> None:
        if self.update_info:
            webbrowser.open(self.update_info[1])

    # ------------------------------------------------------------ input
    def add_paths(self, paths: list[str]) -> None:
        """Paths from right-click launches (any thread). Collected briefly, then started together."""
        self.q.put(("paths", paths))

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
    def busy(self) -> bool:
        return self.state in ("scanning", "running")

    def start(self, paths: list[Path]) -> None:
        if self.busy():
            return
        self.roots = [Path(p) for p in paths]
        self.state = "scanning"
        self.stop_event.clear()
        self._set_busy(True)
        self.btn_retry.pack_forget()
        self.status.configure(text="正在搜尋檔案…")
        self.detail.configure(text="")
        self.bar.configure(value=0)
        skip = [copy_root(p) for p in self.roots if p.is_dir()] if self.save_copy.get() else []
        roots = list(self.roots)
        threading.Thread(target=lambda: self.q.put(("scanned", *scan(roots, skip=skip))), daemon=True).start()

    def retry_failed(self) -> None:
        if self.busy() or not self.failed:
            return
        self.state = "scanning"
        self._set_busy(True)
        self.btn_retry.pack_forget()
        self._after_scan(list(self.failed), [], retry=True)

    def stop(self) -> None:
        self.stop_event.set()
        self.status.configure(text="正在停止…")

    def _after_scan(self, targets, unsupported, retry: bool = False) -> None:
        has_ffmpeg = find_tool("ffmpeg") is not None
        skipped_videos = 0 if has_ffmpeg else sum(p.suffix.lower() in VIDEO_EXT for p in targets)
        if not has_ffmpeg:
            targets = [p for p in targets if p.suffix.lower() not in VIDEO_EXT]
        n_vid = sum(p.suffix.lower() in VIDEO_EXT for p in targets)
        if not targets:
            self._finish_idle("沒有找到可處理的相片或影片。")
            return
        what = "重試" if retry else "找到"
        msg = f"{what}圖片 {len(targets) - n_vid} 張、影片 {n_vid} 部。"
        if self.crop_video.get() and n_vid > 0:
            ratio = self.crop_video.get()
            msg += f"\n影片會從中間裁切成 {ratio}（需要重新編碼，畫質會略降，處理較久）。"
        if skipped_videos:
            msg += f"\n（找不到 ffmpeg，{skipped_videos} 部影片會略過）"
        save_copy = self.save_copy.get() and not self.dry_run.get()
        if self.dry_run.get():
            msg += "\n\n只檢查，不會修改任何檔案。要開始嗎？"
        elif save_copy:
            dests = sorted({str(copy_root(r) if r.is_dir() else copy_root(r.parent)) for r in self.roots})
            msg += "\n\n清好的檔案會存到：\n" + "\n".join(dests[:3]) + ("\n…" if len(dests) > 3 else "")
            msg += "\n原檔不會被修改。要開始嗎？"
        else:
            msg += "\n\n會直接覆蓋原檔（畫質不變），無法復原。要開始嗎？"
        if not self.confirm("確認", msg):
            self._finish_idle("已取消。")
            return
        self.state = "running"
        self.counts = {CLEANED: 0, CLEAN: 0, "failed": 0}
        self.failed = []
        self.bar.configure(maximum=len(targets), value=0)
        self.log_path = log_dir() / time.strftime("strip_%Y%m%d_%H%M%S.log")
        self.cache = CleanCache(self.cache_path) if self.remember_clean.get() and not retry else None
        args = (list(self.roots), targets, unsupported, self.options(), self.dry_run.get(), save_copy)
        threading.Thread(target=self._worker, args=args, daemon=True).start()

    def _worker(self, roots, targets, unsupported, options, dry_run, save_copy) -> None:
        t0 = time.time()
        dest_for = (lambda p: copy_destination(p, roots)) if save_copy else None
        with open(self.log_path, "w", encoding="utf-8") as log:
            log.write(f"paths={[str(p) for p in roots]} dry_run={dry_run} save_copy={save_copy} {options}\n")
            results = batch.run(targets, options, dry_run, dest_for, self.cache, self.stop_event)
            for i, r in enumerate(results, 1):
                if r.error:
                    log.write(f"FAILED {r.path}: {r.error}\n")
                log.write(f"{r.status} {r.path}" + (f" -> {r.dest}" if r.dest else "") + "\n")
                eta = (time.time() - t0) / i * (len(targets) - i)
                self.q.put(("progress", i, len(targets), r.path, r.status, r.error, r.took, eta))
            if self.stop_event.is_set():
                log.write("stopped by user\n")
            for p in unsupported:
                log.write(f"unsupported {p}\n")
        self.q.put(("done", time.time() - t0, len(unsupported), dry_run))

    def _poll(self) -> None:
        try:
            while True:
                self._handle(self.q.get_nowait())
        except queue.Empty:
            pass
        if self.pending and not self.busy() and time.time() - self.pending_since >= BATCH_WAIT:
            paths, self.pending = self.pending, []
            self.root.deiconify()
            self.root.lift()
            self.start(paths)
        self.root.after(50, self._poll)

    def _handle(self, msg) -> None:
        kind = msg[0]
        if kind == "paths":
            self.pending += [Path(p) for p in msg[1]]
            self.pending_since = time.time()
        elif kind == "scanned":
            self._after_scan(msg[1], msg[2])
        elif kind == "update":
            self.update_info = (msg[1], msg[2])
            self.update_label.configure(text=f"有新版 v{msg[1]}，按這裡下載")
            self.update_label.pack(in_=self.top_anchor, anchor="w", pady=(0, 8))
        elif kind == "progress":
            _, i, n, p, status, err, took, eta = msg
            self.counts[status] += 1
            self.bar.configure(value=i)
            self.status.configure(text=f"{i}/{n}（{100 * i / n:.0f}%）  剩餘約 {fmt_time(eta)}")
            self.detail.configure(text=f"{p.name}  本檔 {took:.1f} 秒")
            if err:
                self.failed.append(p)
                self._write(f"失敗：{p}（{err}）")
        elif kind == "done":
            _, elapsed, n_unsup, dry_run = msg
            c = self.counts
            verb = "需要清除" if dry_run else "已清除"
            stopped = "（已中途停止）" if self.stop_event.is_set() else ""
            summary = (f"完成{stopped}，耗時 {fmt_time(elapsed)}：{verb} {c[CLEANED]} 個、"
                       f"本來就乾淨 {c[CLEAN]} 個、失敗 {c['failed']} 個。")
            self._write(summary)
            if self.cache and self.cache.hits:
                self._write(f"其中 {self.cache.hits} 個上次已確認乾淨、之後沒改過，直接略過。")
            if n_unsup:
                self._write(f"另有 {n_unsup} 個不支援的格式（HEIC/TIFF/GIF 等）沒有處理。")
            self.status.configure(text=summary)
            self.detail.configure(text="")
            self.state = "done"
            self._set_busy(False)
            if self.failed:
                self.btn_retry.configure(text=f"重試失敗的檔案（{len(self.failed)}）")
                self.btn_retry.pack(side="right", padx=8)

    def _finish_idle(self, text: str) -> None:
        self.status.configure(text=text)
        self.state = "idle"
        self._set_busy(False)
        if self.failed:
            self.btn_retry.pack(side="right", padx=8)


def make_root() -> tk.Tk:
    if TkinterDnD is not None:
        try:
            return TkinterDnD.Tk()
        except Exception:  # tkdnd binary missing: fall back to a plain window
            pass
    return tk.Tk()


def claim_instance(paths: list[str], on_paths: Callable[[list[str]], None]):
    """Hand paths to an open window if there is one; otherwise become the window that receives them.

    Returns (server or None, forwarded: bool).
    """
    for _ in range(15):
        if paths and integration.forward_to_running(paths):
            return None, True
        try:
            return integration.InstanceServer(on_paths), False
        except OSError:
            if not paths:  # plain launch while another window is open: just open another window
                return None, False
            time.sleep(0.2)
    return None, False


def main(paths: list[str] | None = None) -> int:
    paths = list(paths or [])
    holder: dict[str, App] = {}
    early: list[list[str]] = []

    def on_paths(ps: list[str]) -> None:
        if "app" in holder:
            holder["app"].add_paths(ps)
        else:
            early.append(ps)

    server, forwarded = claim_instance(paths, on_paths)
    if forwarded:
        return 0
    try:  # sharp text on high-DPI screens
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    root = make_root()
    app = App(root)
    holder["app"] = app
    if paths:
        app.add_paths(paths)
    for ps in early:
        app.add_paths(ps)
    try:
        root.mainloop()
    finally:
        if server:
            server.close()
    return 0
