import json
import os
import subprocess
from pathlib import Path

import pytest
from conftest import HAS_FFMPEG, SECRET, make_jpeg, make_mp4, make_png, make_webp, png_chunk, seg

from strip_metadata.core import (
    CLEAN,
    CLEANED,
    StripError,
    _jpeg_orientation,
    scan,
    strip_file,
    strip_jpeg,
    strip_png,
    strip_webp,
)


def jpeg_scan_data(d: bytes) -> bytes:
    return d[d.index(b"\xff\xda"):]


def jpeg_segments(d: bytes) -> list[tuple[int, bytes]]:
    out, pos = [], 2
    while d[pos + 1] != 0xDA:
        n = int.from_bytes(d[pos + 2:pos + 4], "big")
        out.append((d[pos + 1], d[pos + 4:pos + 2 + n]))
        pos += 2 + n
    return out


def png_chunks(d: bytes) -> list[tuple[bytes, bytes]]:
    out, pos = [], 8
    while pos < len(d):
        n = int.from_bytes(d[pos:pos + 4], "big")
        out.append((d[pos + 4:pos + 8], d[pos + 8:pos + 8 + n]))
        pos += 12 + n
    return out


# ---------------------------------------------------------------- JPEG
def test_jpeg_removes_metadata_keeps_pixels_and_orientation(tmp_path):
    src = make_jpeg(tmp_path / "x.jpg", orientation=6).read_bytes()
    out = strip_jpeg(src)
    assert SECRET not in out
    assert jpeg_scan_data(out) == jpeg_scan_data(src)
    app1 = [p for m, p in jpeg_segments(out) if m == 0xE1]
    assert len(app1) == 1 and _jpeg_orientation(app1[0]) == 6
    assert strip_jpeg(out) is None  # second run: nothing left to do


def test_jpeg_drop_orientation(tmp_path):
    src = make_jpeg(tmp_path / "x.jpg", orientation=6).read_bytes()
    out = strip_jpeg(src, keep_orientation=False)
    assert all(m != 0xE1 for m, _ in jpeg_segments(out))


def test_jpeg_upright_photo_gets_no_exif(tmp_path):
    out = strip_jpeg(make_jpeg(tmp_path / "x.jpg", orientation=1).read_bytes())
    assert all(m != 0xE1 for m, _ in jpeg_segments(out))


def test_jpeg_keeps_icc_and_jfif(tmp_path):
    src = make_jpeg(tmp_path / "x.jpg").read_bytes()
    icc = seg(0xE2, b"ICC_PROFILE\0\x01\x01fakeprofile")
    src = src[:2] + icc + src[2:]
    out = strip_jpeg(src)
    markers = [m for m, _ in jpeg_segments(out)]
    assert 0xE2 in markers and 0xE0 in markers


def test_jpeg_garbage_raises():
    with pytest.raises(StripError):
        strip_jpeg(b"not a jpeg")
    with pytest.raises(StripError):
        strip_jpeg(b"\xff\xd8\xff\xe1\x00")  # truncated


# ---------------------------------------------------------------- PNG
def test_png_removes_text_and_c2pa(tmp_path):
    src = make_png(tmp_path / "x.png").read_bytes()
    out = strip_png(src)
    types = [t for t, _ in png_chunks(out)]
    assert SECRET not in out and b"caBX" not in types and b"tEXt" not in types
    assert types[0] == b"IHDR" and types[-1] == b"IEND"
    idat = lambda d: b"".join(c for t, c in png_chunks(d) if t == b"IDAT")  # noqa: E731
    assert idat(out) == idat(src)
    assert strip_png(out) is None


def test_png_keeps_color_profile(tmp_path):
    src = make_png(tmp_path / "x.png").read_bytes()
    src = src[:33] + png_chunk(b"iCCP", b"icc\0\0fake") + src[33:]
    assert b"iCCP" in [t for t, _ in png_chunks(strip_png(src))]


def test_png_garbage_raises():
    with pytest.raises(StripError):
        strip_png(b"\x89PNG\r\n\x1a\n\x00\x00")


# ---------------------------------------------------------------- WebP
def test_webp_removes_exif_and_fixes_header(tmp_path):
    src = make_webp(tmp_path / "x.webp").read_bytes()
    out = strip_webp(src)
    assert SECRET not in out and b"EXIF" not in out
    assert int.from_bytes(out[4:8], "little") == len(out) - 8
    vp8x = out.index(b"VP8X")
    assert out[vp8x + 8] & 0x08 == 0
    assert strip_webp(out) is None


# ---------------------------------------------------------------- driver
def test_strip_file_in_place_keeps_mtime(tmp_path):
    p = make_png(tmp_path / "x.png")
    os.utime(p, (1_600_000_000, 1_600_000_000))
    assert strip_file(p) == CLEANED
    assert SECRET not in p.read_bytes()
    assert int(p.stat().st_mtime) == 1_600_000_000
    assert strip_file(p) == CLEAN
    assert list(tmp_path.iterdir()) == [p]  # no temp files left


def test_dry_run_changes_nothing(tmp_path):
    p = make_jpeg(tmp_path / "x.jpg")
    before = p.read_bytes()
    assert strip_file(p, dry_run=True) == CLEANED
    assert p.read_bytes() == before


def test_broken_file_left_untouched(tmp_path):
    p = tmp_path / "broken.jpg"
    p.write_bytes(b"\xff\xd8\xff\xe1\x00\x10abc")
    with pytest.raises(StripError):
        strip_file(p)
    assert p.read_bytes() == b"\xff\xd8\xff\xe1\x00\x10abc"


def test_scan_recurses_and_reports_unsupported(media_dir):
    (media_dir / ".git").mkdir()
    (media_dir / ".git" / "skip.png").write_bytes(b"x")
    targets, unsupported = scan([media_dir])
    names = {p.name for p in targets}
    assert {"photo.jpg", "ai.png", "pic.webp"} <= names
    assert "skip.png" not in names and "notes.txt" not in names
    assert [p.name for p in unsupported] == ["phone.heic"]


def test_scan_single_file_and_duplicates(tmp_path):
    p = make_png(tmp_path / "x.png")
    targets, _ = scan([p, tmp_path])
    assert targets == [p]


# ---------------------------------------------------------------- video
@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")
def test_video_metadata_removed_rotation_kept(tmp_path):
    p = make_mp4(tmp_path / "clip.mp4", rotation=90)
    assert strip_file(p) == CLEANED
    info = json.loads(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format_tags:stream_tags:stream_side_data=rotation",
         "-of", "json", str(p)], capture_output=True, text=True, check=True).stdout)
    text = json.dumps(info)
    assert "SECRET" not in text and "121.56" not in text and "2024-01-01" not in text
    rot = [sd.get("rotation") for s in info["streams"] for sd in s.get("side_data_list", [])]
    assert rot and abs(int(rot[0])) == 90
    assert strip_file(p) == CLEAN


def test_real_world_png_with_c2pa(tmp_path):
    """Regression: PNG whose only metadata is an OpenAI C2PA caBX chunk."""
    base = (Path(__file__).parent / "fixtures" / "base.png").read_bytes()
    p = tmp_path / "image.png"
    p.write_bytes(base[:33] + png_chunk(b"caBX", b"jumb OpenAI Media Service c2pa.actions.v2") + base[33:])
    assert strip_file(p) == CLEANED
    assert b"OpenAI" not in p.read_bytes()
