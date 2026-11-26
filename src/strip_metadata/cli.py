"""Command-line front end: strip-metadata <folder or file>... [--dry-run] [--yes]."""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from . import __version__, batch
from .cache import CleanCache
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
from .settings import app_dir


def log_dir() -> Path:
    d = app_dir() / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def fmt_time(s: float) -> str:
    s = int(s)
    return f"{s // 3600:d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def main(argv: list[str] | None = None) -> int:
    if sys.stdout is None:  # windowed exe started with --cli: no console attached
        sys.stdout = sys.stderr = open(os.devnull, "w", encoding="utf-8")
    ap = argparse.ArgumentParser(prog="strip-metadata", description="移除相片與影片的 EXIF 等資訊（含所有子資料夾）")
    ap.add_argument("paths", nargs="+", type=Path, help="資料夾或檔案")
    ap.add_argument("--dry-run", action="store_true", help="只檢查，不改檔")
    ap.add_argument("--yes", action="store_true", help="不詢問，直接開始")
    ap.add_argument("--drop-orientation", action="store_true", help="連 JPEG 旋轉方向也移除")
    ap.add_argument("--keep-workflow", action="store_true", help="保留 PNG 裡的 AI 工作流程（ComfyUI 等）")
    ap.add_argument("--copy", action="store_true", help="另存到新資料夾（名稱後加 _已清除），原檔不動")
    ap.add_argument("--new-dates", action="store_true", help="不保留檔案日期")
    ap.add_argument("--recheck", action="store_true", help="全部重新檢查（不略過上次已確認乾淨的檔案）")
    ap.add_argument("--log", type=Path, help="紀錄檔位置")
    ap.add_argument("--version", action="version", version=__version__)
    args = ap.parse_args(argv)

    missing = [p for p in args.paths if not p.exists()]
    if missing:
        print("找不到：" + "、".join(map(str, missing)))
        return 2
    skip = [copy_root(p) for p in args.paths if p.is_dir()] if args.copy else []
    targets, unsupported = scan(args.paths, skip=skip)
    n_vid = sum(p.suffix.lower() in VIDEO_EXT for p in targets)
    print(f"找到圖片 {len(targets) - n_vid} 張、影片 {n_vid} 部" + ("（只檢查，不改檔）" if args.dry_run else ""))
    if n_vid and not find_tool("ffmpeg"):
        print("找不到 ffmpeg，影片會被略過。")
        targets = [p for p in targets if p.suffix.lower() not in VIDEO_EXT]
    if not targets:
        print("沒有要處理的檔案。")
        return 0
    if not args.yes and not args.dry_run:
        try:
            what = "清好的檔案會另存到「_已清除」資料夾" if args.copy else "會直接覆蓋原檔（畫質不變）"
            ans = input(f"{what}。按 Enter 開始，輸入 n 取消：")
        except EOFError:
            ans = "n"
        if ans.lstrip(chr(0xFEFF)).strip().lower() in ("n", "no", "否"):
            print("已取消。")
            return 0

    log_path = args.log or log_dir() / time.strftime("strip_%Y%m%d_%H%M%S.log")
    counts = {CLEANED: 0, CLEAN: 0, "failed": 0}
    opts = Options(keep_orientation=not args.drop_orientation, keep_workflow=args.keep_workflow,
                   keep_dates=not args.new_dates)
    save_copy = args.copy and not args.dry_run
    cache = None if args.recheck else CleanCache(app_dir() / "clean_cache.json")
    t0 = time.time()
    with open(log_path, "w", encoding="utf-8") as log:
        log.write(f"paths={[str(p) for p in args.paths]} dry_run={args.dry_run}\n")
        dest_for = (lambda p: copy_destination(p, args.paths)) if save_copy else None
        for i, r in enumerate(batch.run(targets, opts, args.dry_run, dest_for, cache), 1):
            p = r.path
            if r.error:
                log.write(f"FAILED {p}: {r.error}\n")
                print(f"\n  失敗：{p}（{r.error}）")
            counts[r.status] += 1
            log.write(f"{r.status} {p}\n")
            eta = (time.time() - t0) / i * (len(targets) - i)
            name = p.name if len(p.name) <= 40 else "…" + p.name[-39:]
            bar = "#" * int(30 * i / len(targets))
            sys.stdout.write(f"\r[{bar:<30}] {i}/{len(targets)} {100 * i / len(targets):5.1f}% "
                             f"本檔 {r.took:4.1f}s 剩餘 {fmt_time(eta)}  {name:<40}")
            sys.stdout.flush()
        for p in unsupported:
            log.write(f"unsupported {p}\n")
        log.write(f"summary {counts}\n")
    print()
    verb = "需要清除" if args.dry_run else "已清除"
    print(f"完成，耗時 {fmt_time(time.time() - t0)}：{verb} {counts[CLEANED]} 個、"
          f"本來就乾淨 {counts[CLEAN]} 個、失敗 {counts['failed']} 個。")
    if cache and cache.hits:
        print(f"（其中 {cache.hits} 個上次已確認乾淨、之後沒改過，直接略過）")
    if unsupported:
        print(f"另有 {len(unsupported)} 個不支援的格式（HEIC/TIFF/GIF 等）沒有處理，清單在紀錄檔。")
    print(f"紀錄檔：{log_path}")
    return 1 if counts["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
