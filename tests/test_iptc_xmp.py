"""IPTC, XMP and other non-image metadata in PNG, WebP, JPEG and MP4."""
import struct
import subprocess

import pytest
from conftest import FIXTURES, HAS_FFMPEG, SECRET, png_chunk

from strip_metadata.core import (
    CLEAN,
    CLEANED,
    XMP_UUID,
    bmff_metadata,
    strip_file,
    strip_jpeg,
    strip_png,
    strip_webp,
    video_has_metadata,
)

XMP = b'<x:xmpmeta xmlns:x="adobe:ns:meta/">SECRET dc:creator</x:xmpmeta>'


def png_with(*chunks: bytes) -> bytes:
    d = (FIXTURES / "base.png").read_bytes()
    return d[:33] + b"".join(chunks) + d[33:]


def chunk_types(png: bytes) -> list[bytes]:
    out, pos = [], 8
    while pos < len(png):
        n = struct.unpack(">I", png[pos:pos + 4])[0]
        out.append(png[pos + 4:pos + 8])
        pos += 12 + n
    return out


def test_png_drops_xmp_iptc_and_private_chunks_but_keeps_colour():
    data = png_with(png_chunk(b"iTXt", b"XML:com.adobe.xmp\0\0\0\0\0" + XMP),
                    png_chunk(b"zTXt", b"Raw profile type iptc\0\0" + b"x\x9c" + SECRET),
                    png_chunk(b"iDOT", SECRET), png_chunk(b"vpAg", SECRET), png_chunk(b"prVW", SECRET),
                    png_chunk(b"gAMA", struct.pack(">I", 45455)), png_chunk(b"pHYs", b"\0\0\x0b\x13" * 2 + b"\x01"))
    out = strip_png(data)
    types = chunk_types(out)
    assert SECRET not in out
    assert b"gAMA" in types and b"pHYs" in types and types[0] == b"IHDR" and types[-1] == b"IEND"
    assert strip_png(out) is None


def webp(*chunks: tuple[bytes, bytes], flags: int = 0x0C) -> bytes:
    vp8x = b"VP8X" + struct.pack("<I", 10) + bytes([flags, 0, 0, 0]) + b"\x00" * 6
    body = b"WEBP" + vp8x + b"".join(c + struct.pack("<I", len(d)) + d + b"\0" * (len(d) & 1) for c, d in chunks)
    return b"RIFF" + struct.pack("<I", len(body)) + body


def test_webp_drops_unknown_chunks_too():
    data = webp((b"VP8L", b"\x2f" + b"\0" * 9), (b"XMP ", XMP), (b"IPTC", SECRET), (b"ICCP", b"icc!"))
    out = strip_webp(data)
    assert SECRET not in out and b"ICCP" in out and b"VP8L" in out
    assert out[20] & 0x0C == 0  # EXIF/XMP flags cleared
    assert strip_webp(out) is None


def test_jpeg_drops_xmp_extended_xmp_and_iptc():
    base = (FIXTURES / "base.jpg").read_bytes()

    def seg(marker, payload):
        return bytes([0xFF, marker]) + struct.pack(">H", len(payload) + 2) + payload

    data = (base[:2] + seg(0xE1, b"http://ns.adobe.com/xap/1.0/\0" + XMP)
            + seg(0xE1, b"http://ns.adobe.com/xmp/extension/\0" + SECRET)
            + seg(0xED, b"Photoshop 3.0\08BIM\x04\x04\0\0\0\0\0\x08\x1c\x02\x50\0\x03SECRET") + base[2:])
    out = strip_jpeg(data)
    assert SECRET not in out and out.endswith(base[-2:])


def box(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", 8 + len(payload)) + kind + payload


def test_bmff_finds_xmp_uuid_and_udta_xmp(tmp_path):
    p = tmp_path / "a.mp4"
    p.write_bytes(box(b"ftyp", b"isom\0\0\0\0") + box(b"uuid", XMP_UUID + XMP))
    assert bmff_metadata(p) == {"XMP"}
    nested = box(b"moov", box(b"mvhd", b"\0" * 100) + box(b"udta", box(b"XMP_", XMP)))
    p.write_bytes(box(b"ftyp", b"isom\0\0\0\0") + nested)
    assert bmff_metadata(p) == {"XMP"}
    p.write_bytes(box(b"ftyp", b"isom\0\0\0\0") + box(b"moov", box(b"mvhd", b"\0" * 100)))
    assert bmff_metadata(p) == set()


@pytest.mark.skipif(not HAS_FFMPEG, reason="needs ffmpeg")
@pytest.mark.parametrize("where", ["top", "udta"])
def test_video_with_only_xmp_gets_cleaned(tmp_path, where):
    p = tmp_path / "x.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=64x48:rate=10", "-t", "1",
                    "-c:v", "mpeg4", "-map_metadata", "-1", "-fflags", "+bitexact", "-flags:v", "+bitexact",
                    str(p)], check=True)
    data = p.read_bytes()
    if where == "top":
        data += box(b"uuid", XMP_UUID + XMP)
    else:  # moov is the last box: grow it by one more udta child holding XMP_
        i = data.rfind(b"moov") - 4
        assert i + struct.unpack(">I", data[i:i + 4])[0] == len(data)
        extra = box(b"udta", box(b"XMP_", XMP))
        data = data[:i] + struct.pack(">I", len(data) - i + len(extra)) + data[i + 4:] + extra
    p.write_bytes(data)
    assert video_has_metadata(p)
    assert strip_file(p) == CLEANED
    assert SECRET not in p.read_bytes() and bmff_metadata(p) == set()
    assert strip_file(p) == CLEAN
