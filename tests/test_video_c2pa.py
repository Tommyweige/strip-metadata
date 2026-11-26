import struct
import subprocess

import pytest
from conftest import HAS_FFMPEG, SECRET

from strip_metadata.core import C2PA_UUID, CLEAN, CLEANED, bmff_has_c2pa, strip_file, video_has_metadata


def box(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", 8 + len(payload)) + kind + payload


def c2pa_box(payload: bytes = b"\0\0\0\0manifest\0" + b"jumb SECRET c2pa.actions OpenAI Sora") -> bytes:
    return box(b"uuid", C2PA_UUID + payload)


def test_bmff_scan_finds_c2pa_uuid(tmp_path):
    p = tmp_path / "x.mp4"
    p.write_bytes(box(b"ftyp", b"isom\0\0\0\0") + box(b"free", b"") + c2pa_box())
    assert bmff_has_c2pa(p)


def test_bmff_scan_ignores_other_uuid_and_handles_largesize(tmp_path):
    other = box(b"uuid", b"\x11" * 16 + b"data")
    large = struct.pack(">I", 1) + b"mdat" + struct.pack(">Q", 16 + 4) + b"abcd"
    p = tmp_path / "x.mp4"
    p.write_bytes(box(b"ftyp", b"isom\0\0\0\0") + other + large)
    assert not bmff_has_c2pa(p)
    p.write_bytes(p.read_bytes() + c2pa_box())  # after a 64-bit sized box
    assert bmff_has_c2pa(p)


def test_bmff_scan_survives_garbage(tmp_path):
    p = tmp_path / "x.mp4"
    p.write_bytes(b"\0\0\0\x03junk")
    assert not bmff_has_c2pa(p)


@pytest.mark.skipif(not HAS_FFMPEG, reason="needs ffmpeg")
def test_video_whose_only_metadata_is_c2pa_gets_cleaned(tmp_path):
    p = tmp_path / "sora.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=64x48:rate=10", "-t", "1",
                    "-c:v", "mpeg4", "-map_metadata", "-1", "-fflags", "+bitexact", "-flags:v", "+bitexact",
                    str(p)], check=True)
    p.write_bytes(p.read_bytes() + c2pa_box())
    assert video_has_metadata(p)
    assert strip_file(p) == CLEANED
    out = p.read_bytes()
    assert SECRET not in out and C2PA_UUID not in out and not bmff_has_c2pa(p)
    assert strip_file(p) == CLEAN
