import shutil
import struct
import subprocess
import zlib
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
SECRET = b"SECRET"


def exif_app1(orientation: int) -> bytes:
    """APP1 with Orientation plus a Make tag that must disappear."""
    make = b"SECRETCAM\0"
    entries = [(0x0112, 3, 1, struct.pack(">HH", orientation, 0)),
               (0x010F, 2, len(make), struct.pack(">I", 8 + 2 + 2 * 12 + 4))]
    tiff = b"MM\0*" + struct.pack(">I", 8) + struct.pack(">H", len(entries))
    for tag, typ, cnt, val in entries:
        tiff += struct.pack(">HHI", tag, typ, cnt) + val
    tiff += struct.pack(">I", 0) + make
    p = b"Exif\0\0" + tiff
    return b"\xff\xe1" + struct.pack(">H", len(p) + 2) + p


def seg(marker: int, payload: bytes) -> bytes:
    return bytes([0xFF, marker]) + struct.pack(">H", len(payload) + 2) + payload


def png_chunk(ctype: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + ctype + data + struct.pack(">I", zlib.crc32(ctype + data))


def make_jpeg(path: Path, orientation: int = 6) -> Path:
    d = (FIXTURES / "base.jpg").read_bytes()
    inj = (exif_app1(orientation)
           + seg(0xE1, b"http://ns.adobe.com/xap/1.0/\0<x:xmpmeta>SECRET GPS</x:xmpmeta>")
           + seg(0xEB, b"JP\0\x01SECRET c2pa manifest")
           + seg(0xFE, b"SECRET COMMENT"))
    # after SOI and the JFIF APP0
    app0_end = 2 + 2 + struct.unpack(">H", d[4:6])[0] if d[2:4] == b"\xff\xe0" else 2
    path.write_bytes(d[:app0_end] + inj + d[app0_end:])
    return path


def make_png(path: Path) -> Path:
    d = (FIXTURES / "base.png").read_bytes()
    extra = png_chunk(b"tEXt", b"prompt\0{\"SECRET\": 1}") + png_chunk(b"caBX", b"jumbSECRET OpenAI c2pa")
    path.write_bytes(d[:33] + extra + d[33:])  # after IHDR
    return path


def make_webp(path: Path) -> Path:
    d = (FIXTURES / "base.webp").read_bytes()
    body = d[12:]
    exif = b"MM\0*\0\0\0\x08\0\0\0\0\0\0SECRETEXIF"
    vp8x = b"VP8X" + struct.pack("<I", 10) + bytes([0x08, 0, 0, 0]) + (15).to_bytes(3, "little") * 2
    ex = b"EXIF" + struct.pack("<I", len(exif)) + exif + (b"\0" if len(exif) & 1 else b"")
    nb = b"WEBP" + vp8x + body + ex
    path.write_bytes(b"RIFF" + struct.pack("<I", len(nb)) + nb)
    return path


def make_mp4(path: Path, rotation: int = 90) -> Path:
    plain = path.with_name("plain_" + path.name)
    ff = ["ffmpeg", "-v", "error", "-y"]
    subprocess.run(ff + ["-f", "lavfi", "-i", "testsrc=size=64x48:rate=10", "-f", "lavfi", "-i", "sine=d=1",
                         "-t", "1", "-c:v", "mpeg4", "-c:a", "aac", str(plain)], check=True)
    subprocess.run(ff + ["-display_rotation", str(rotation), "-i", str(plain), "-c", "copy",
                         "-metadata", "title=SECRET TITLE", "-metadata", "location=+25.03+121.56/",
                         "-metadata:s:v", "creation_time=2024-01-01T00:00:00Z", str(path)], check=True)
    plain.unlink()
    return path


@pytest.fixture
def media_dir(tmp_path: Path) -> Path:
    (tmp_path / "a" / "b").mkdir(parents=True)
    make_jpeg(tmp_path / "photo.jpg")
    make_png(tmp_path / "a" / "ai.png")
    make_webp(tmp_path / "a" / "b" / "pic.webp")
    (tmp_path / "a" / "notes.txt").write_text("not media")
    (tmp_path / "a" / "phone.heic").write_bytes(b"\0" * 10)
    if HAS_FFMPEG:
        make_mp4(tmp_path / "a" / "b" / "clip.mp4")
    return tmp_path


@pytest.fixture(autouse=True)
def isolated_app_dir(tmp_path_factory, monkeypatch):
    """Keep settings, logs and the clean-file cache of test runs out of the real user folder."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path_factory.mktemp("localappdata")))
