"""Lossless metadata removal for images and videos.

Images (JPEG / PNG / WebP) are rewritten in pure Python: only metadata blocks
are dropped, pixel data is copied byte for byte. Videos are remuxed with
ffmpeg (``-c copy``, no re-encode) without metadata, chapters or data streams.

Kept on purpose, because removing them changes how the file looks:
  * JPEG orientation (a tiny EXIF holding only the Orientation tag)
  * ICC color profiles
  * video rotation (display matrix)
"""
from __future__ import annotations

import json
import os
import shutil
import struct
import subprocess
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

JPEG_EXT = {".jpg", ".jpeg", ".jpe", ".jfif"}
PNG_EXT = {".png"}
WEBP_EXT = {".webp"}
IMAGE_EXT = JPEG_EXT | PNG_EXT | WEBP_EXT
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi", ".3gp"}
UNSUPPORTED_EXT = {".heic", ".heif", ".tif", ".tiff", ".gif", ".avif", ".dng", ".cr2", ".nef", ".arw"}
SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv"}
TMP_MARK = ".__stripping__"

# caBX = C2PA "Content Credentials" (signed provenance, e.g. from AI generators)
PNG_DROP = {b"tEXt", b"zTXt", b"iTXt", b"eXIf", b"tIME", b"caBX"}
WEBP_DROP = {b"EXIF", b"XMP ", b"C2PA"}
# PNG text keywords that hold AI generation workflows (ComfyUI, AUTOMATIC1111 / Forge)
WORKFLOW_KEYWORDS = {b"prompt", b"workflow", b"parameters"}
COPY_SUFFIX = "_已清除"
# Tags ffprobe always reports that are not personal metadata.
HARMLESS_VIDEO_TAGS = {"major_brand", "minor_version", "compatible_brands",
                       "handler_name", "vendor_id", "language", "encoder", "duration"}

CLEANED, CLEAN = "cleaned", "clean"
CROP_CHOICES = ("", "9:16", "16:9")


class StripError(Exception):
    """The file could not be processed; the original is left untouched."""


# ---------------------------------------------------------------- tools
def find_tool(name: str) -> str | None:
    """Look for ffmpeg/ffprobe next to the app first, then on PATH."""
    exe = name + (".exe" if os.name == "nt" else "")
    base = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
    for cand in (base / exe, base / "ffmpeg" / exe):
        if cand.is_file():
            return str(cand)
    return shutil.which(name)


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0  # no console flashes in the GUI
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", creationflags=flags)


# ---------------------------------------------------------------- JPEG
def _jpeg_orientation(app1: bytes) -> int:
    """Orientation (0x0112) from an APP1 Exif payload; 1 if absent."""
    if not app1.startswith(b"Exif\0\0"):
        return 1
    t = app1[6:]
    if len(t) < 8 or t[:2] not in (b"II", b"MM"):
        return 1
    e = "<" if t[:2] == b"II" else ">"
    try:
        ifd = struct.unpack(e + "I", t[4:8])[0]
        n = struct.unpack(e + "H", t[ifd:ifd + 2])[0]
        for i in range(n):
            p = ifd + 2 + i * 12
            tag, typ = struct.unpack(e + "HH", t[p:p + 4])
            if tag == 0x0112 and typ == 3:
                v = struct.unpack(e + "H", t[p + 8:p + 10])[0]
                return v if 1 <= v <= 8 else 1
    except struct.error:
        pass
    return 1


def orientation_app1(v: int) -> bytes:
    """A minimal APP1 segment holding only the Orientation tag."""
    tiff = b"MM\0*" + struct.pack(">I", 8) + struct.pack(">H", 1)
    tiff += struct.pack(">HHI", 0x0112, 3, 1) + struct.pack(">HH", v, 0)
    tiff += struct.pack(">I", 0)
    payload = b"Exif\0\0" + tiff
    return b"\xff\xe1" + struct.pack(">H", len(payload) + 2) + payload


def strip_jpeg(data: bytes, keep_orientation: bool = True) -> bytes | None:
    """Return the cleaned bytes, or None when there is nothing to remove."""
    if data[:2] != b"\xff\xd8":
        raise StripError("不是有效的 JPEG")
    out = [b"\xff\xd8"]
    pos, orientation, removed = 2, 1, False
    try:
        while pos < len(data):
            if data[pos] != 0xFF:
                raise StripError("JPEG 結構異常")
            marker = data[pos + 1]
            if marker == 0xFF:  # fill byte
                pos += 1
                continue
            if marker in (0x01, *range(0xD0, 0xD8)):
                out.append(data[pos:pos + 2])
                pos += 2
                continue
            if marker in (0xDA, 0xD9):  # start of scan / end: copy the rest verbatim
                out.append(data[pos:])
                break
            seg_len = struct.unpack(">H", data[pos + 2:pos + 4])[0]
            if seg_len < 2 or pos + 2 + seg_len > len(data):
                raise StripError("JPEG 檔案不完整")
            seg = data[pos:pos + 2 + seg_len]
            payload = seg[4:]
            pos += 2 + seg_len
            keep = True
            if marker == 0xE1:
                own = _jpeg_orientation(payload)
                if orientation == 1:
                    orientation = own
                # our own orientation-only block from an earlier run is not metadata to remove
                keep = keep_orientation and seg == orientation_app1(own)
                if keep:
                    orientation = 1
            elif marker == 0xE0:
                keep = payload.startswith((b"JFIF\0", b"JFXX\0"))
            elif marker == 0xE2:
                keep = payload.startswith(b"ICC_PROFILE\0")
            elif marker == 0xEE:
                keep = payload.startswith(b"Adobe")
            elif 0xE3 <= marker <= 0xEF or marker == 0xFE:  # incl. APP11 = C2PA, COM
                keep = False
            if keep:
                out.append(seg)
            else:
                removed = True
        else:
            raise StripError("JPEG 檔案不完整（沒有影像資料）")
    except (IndexError, struct.error) as e:
        raise StripError("JPEG 檔案不完整") from e
    if not removed:
        return None
    if keep_orientation and orientation != 1:
        idx = 2 if len(out) > 1 and out[1][:2] == b"\xff\xe0" else 1
        out.insert(idx, orientation_app1(orientation))
    return b"".join(out)


# ---------------------------------------------------------------- PNG
PNG_SIG = b"\x89PNG\r\n\x1a\n"


def is_workflow_chunk(ctype: bytes, body: bytes) -> bool:
    """A PNG text chunk holding an AI workflow (ComfyUI prompt/workflow, A1111 parameters)."""
    if ctype not in (b"tEXt", b"zTXt", b"iTXt"):
        return False
    return body.split(b"\0", 1)[0] in WORKFLOW_KEYWORDS


def strip_png(data: bytes, keep_workflow: bool = False) -> bytes | None:
    if not data.startswith(PNG_SIG):
        raise StripError("不是有效的 PNG")
    out, pos, removed = [PNG_SIG], 8, False
    while pos < len(data):
        if pos + 12 > len(data):
            raise StripError("PNG 檔案不完整")
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        ctype = data[pos + 4:pos + 8]
        chunk = data[pos:pos + 12 + length]
        pos += 12 + length
        if keep_workflow and is_workflow_chunk(ctype, chunk[8:-4]):
            out.append(chunk)
        elif ctype in PNG_DROP:
            removed = True
        else:
            out.append(chunk)
        if ctype == b"IEND":
            break
    return b"".join(out) if removed else None


# ---------------------------------------------------------------- WebP
def strip_webp(data: bytes) -> bytes | None:
    if data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        raise StripError("不是有效的 WebP")
    chunks, pos, removed = [], 12, False
    while pos + 8 <= len(data):
        fourcc = data[pos:pos + 4]
        size = struct.unpack("<I", data[pos + 4:pos + 8])[0]
        full = data[pos:pos + 8 + size + (size & 1)]
        pos += 8 + size + (size & 1)
        if fourcc in WEBP_DROP:
            removed = True
            continue
        chunks.append(bytearray(full))
    if not removed:
        return None
    for c in chunks:
        if c[:4] == b"VP8X":
            c[8] &= ~(0x08 | 0x04) & 0xFF  # clear the EXIF and XMP flags
    body = b"WEBP" + b"".join(chunks)
    return b"RIFF" + struct.pack("<I", len(body)) + body


# ---------------------------------------------------------------- video
def video_has_metadata(path: Path) -> bool:
    ffprobe = find_tool("ffprobe")
    if not ffprobe:
        return True  # cannot tell, try anyway
    r = _run([ffprobe, "-v", "error", "-show_entries", "format_tags:stream_tags:chapter",
              "-of", "json", str(path)])
    if r.returncode != 0:
        return True
    info = json.loads(r.stdout or "{}")
    if info.get("chapters"):
        return True
    tags = dict(info.get("format", {}).get("tags", {}))
    for s in info.get("streams", []):
        tags.update(s.get("tags", {}))
    return any(k.lower() not in HARMLESS_VIDEO_TAGS for k in tags)


def video_display_size(path: Path) -> tuple[int, int] | None:
    """Width and height as seen during playback, after any rotation metadata is applied."""
    ffprobe = find_tool("ffprobe")
    if not ffprobe:
        return None
    r = _run([ffprobe, "-v", "error", "-select_streams", "v:0",
              "-show_entries", "stream=width,height:stream_side_data=rotation:stream_tags=rotate",
              "-of", "json", str(path)])
    if r.returncode != 0:
        return None
    try:
        info = json.loads(r.stdout or "{}")
    except ValueError:
        return None
    streams = info.get("streams") or []
    if not streams:
        return None
    stream = streams[0]
    w, h = stream.get("width"), stream.get("height")
    if not w or not h:
        return None
    angle = 0
    for side_data in stream.get("side_data_list") or []:
        if "rotation" in side_data:
            angle = side_data["rotation"]
            break
    else:
        angle = stream.get("tags", {}).get("rotate", 0)
    try:
        angle = abs(int(float(angle)))
    except (TypeError, ValueError):
        angle = 0
    if angle % 180 == 90:
        w, h = h, w
    return int(w), int(h)


def crop_box(w: int, h: int, ratio: str) -> tuple[int, int, int, int] | None:
    """Centred, largest crop rectangle (cw, ch, x, y) for `ratio`; None when no crop is needed."""
    if not ratio:
        return None
    a, b = ratio.split(":")
    r = int(a) / int(b)
    if w / h > r:
        ch = h
        cw = round(h * r)
    else:
        cw = w
        ch = round(w / r)
    cw -= cw % 2
    ch -= ch % 2
    x = (w - cw) // 2
    y = (h - ch) // 2
    x -= x % 2
    y -= y % 2
    if cw >= w - 2 and ch >= h - 2:
        return None
    return cw, ch, x, y


def strip_video(path: Path, tmp: Path, crop_to: tuple[int, int, int, int] | None = None) -> None:
    ffmpeg = find_tool("ffmpeg")
    if not ffmpeg:
        raise StripError("找不到 ffmpeg，無法處理影片")
    cmd = [ffmpeg, "-v", "error", "-y", "-i", str(path)]
    if crop_to is None:
        cmd += ["-map", "0:v", "-map", "0:a?",  # data streams (e.g. GPS tracks) are dropped
                "-map_metadata", "-1", "-map_metadata:s:v", "-1", "-map_metadata:s:a", "-1",
                "-map_chapters", "-1", "-c", "copy",
                "-fflags", "+bitexact", "-flags:v", "+bitexact", "-flags:a", "+bitexact",
                str(tmp)]
    else:
        cw, ch, x, y = crop_to
        if path.suffix.lower() == ".webm":
            codec = ["-c:v", "libvpx-vp9", "-crf", "30", "-b:v", "0", "-pix_fmt", "yuv420p"]
        else:
            codec = ["-c:v", "libx264", "-crf", "18", "-preset", "medium", "-pix_fmt", "yuv420p"]
        cmd += ["-map", "0:v:0", "-map", "0:a?",
                "-vf", f"crop={cw}:{ch}:{x}:{y}",
                "-map_metadata", "-1", "-map_metadata:s:v", "-1", "-map_metadata:s:a", "-1",
                "-map_chapters", "-1", *codec, "-c:a", "copy",
                "-fflags", "+bitexact", "-flags:v", "+bitexact", "-flags:a", "+bitexact",
                str(tmp)]
    r = _run(cmd)
    if r.returncode != 0 or not tmp.exists() or tmp.stat().st_size == 0:
        last = (r.stderr.strip().splitlines() or ["未知錯誤"])[-1]
        raise StripError("ffmpeg 失敗：" + last)


# ---------------------------------------------------------------- driver
@dataclass(frozen=True)
class Options:
    keep_orientation: bool = True
    keep_workflow: bool = False
    keep_dates: bool = True
    crop: str = ""


def copy_root(folder: Path) -> Path:
    """Where a copy of `folder` goes when saving to a new folder: a sibling named <name>_已清除."""
    folder = Path(folder)
    if not folder.name:  # a drive root such as D:\
        return folder / "已清除"
    return folder.with_name(folder.name + COPY_SUFFIX)


def copy_destination(path: Path, roots: Iterable[Path]) -> Path:
    """Destination of `path` in save-copy mode, keeping the sub-folder layout under its root."""
    path = Path(path)
    for root in roots:
        root = Path(root)
        if root != path:  # a picked file has no layout to keep
            try:
                rel = path.relative_to(root)
            except ValueError:
                continue
            return copy_root(root) / rel
    return copy_root(path.parent) / path.name


def _finish(tmp: Path, dest: Path, src_stat: os.stat_result, keep_dates: bool) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    os.replace(tmp, dest)
    if keep_dates:
        os.utime(dest, (src_stat.st_atime, src_stat.st_mtime))


def strip_file(path: Path, options: Options | None = None, dry_run: bool = False,
               dest: Path | None = None, cache=None) -> str:
    """Clean one file. Overwrites it, or writes the result to `dest` (save-copy mode).

    Returns CLEANED or CLEAN; raises StripError on failure, leaving the original untouched.
    In save-copy mode a CLEAN file is still copied to `dest`, so the new folder is complete.
    `cache` (a CleanCache) lets unchanged files that were already clean be skipped unread.
    """
    opts = options or Options()
    path = Path(path)
    target = Path(dest) if dest else path
    ext = path.suffix.lower()
    tmp = target.with_name(target.stem + TMP_MARK + target.suffix)
    try:
        st = path.stat()
        fp = cache.fingerprint(opts) if cache is not None else ""
        if cache is not None and not dest and cache.is_clean(path, st, fp):
            return CLEAN
        status = _strip(path, ext, st, opts, dry_run, dest, target, tmp)
        if cache is not None and not dest and (status == CLEAN or not dry_run):
            cache.mark_clean(path, fp)
        return status
    except PermissionError as e:
        raise StripError("檔案正被其他程式開啟（例如瀏覽器或播放器正在播放、預覽），或是唯讀檔；"
                         "關掉那個程式後再試一次") from e
    except OSError as e:
        raise StripError(f"讀寫失敗：{e.strerror or e}") from e
    finally:
        tmp.unlink(missing_ok=True)


def _strip(path, ext, st, opts, dry_run, dest, target, tmp) -> str:
    if ext in VIDEO_EXT:
        box = crop_box(*size, opts.crop) if opts.crop and (size := video_display_size(path)) else None
        dirty = box is not None or video_has_metadata(path)
        if not dry_run:
            if dirty:
                tmp.parent.mkdir(parents=True, exist_ok=True)
                strip_video(path, tmp, box)
                _finish(tmp, target, st, opts.keep_dates)
            elif dest:
                _copy(path, target, opts.keep_dates)
        return CLEANED if dirty else CLEAN
    data = path.read_bytes()
    if ext in JPEG_EXT:
        new = strip_jpeg(data, opts.keep_orientation)
    elif ext in PNG_EXT:
        new = strip_png(data, opts.keep_workflow)
    elif ext in WEBP_EXT:
        new = strip_webp(data)
    else:
        raise StripError("不支援的格式")
    if not dry_run:
        if new is not None:
            tmp.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_bytes(new)
            _finish(tmp, target, st, opts.keep_dates)
        elif dest:
            _copy(path, target, opts.keep_dates)
    return CLEAN if new is None else CLEANED


def _copy(src: Path, dest: Path, keep_dates: bool) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    (shutil.copy2 if keep_dates else shutil.copyfile)(src, dest)


def scan(paths: Iterable[Path], skip: Iterable[Path] = ()) -> tuple[list[Path], list[Path]]:
    """Expand files and folders (recursively) into (supported, unsupported) lists.

    Folders in `skip` (e.g. the save-copy output folders) are not entered.
    """
    targets: list[Path] = []
    unsupported: list[Path] = []
    skip_set = {Path(p).resolve() for p in skip}

    def add(p: Path) -> None:
        if TMP_MARK in p.name:
            return
        ext = p.suffix.lower()
        if ext in IMAGE_EXT | VIDEO_EXT:
            targets.append(p)
        elif ext in UNSUPPORTED_EXT:
            unsupported.append(p)

    for root in paths:
        root = Path(root)
        if root.is_file():
            add(root)
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS
                                 and (Path(dirpath) / d).resolve() not in skip_set)
            for f in sorted(filenames):
                add(Path(dirpath) / f)
    seen: set[Path] = set()
    targets = [p for p in targets if not (p in seen or seen.add(p))]
    return targets, unsupported
