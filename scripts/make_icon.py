"""Draw assets/app.png (256px): a photo card with a crossed-out tag. ffmpeg turns it into app.ico."""
import struct
import zlib
from pathlib import Path

N = 256
BLUE, WHITE, SKY, GREEN, RED = (37, 99, 235), (255, 255, 255), (191, 219, 254), (34, 197, 94), (220, 38, 38)
px = [[(0, 0, 0, 0)] * N for _ in range(N)]


def rounded(x0, y0, x1, y1, r, c):
    for y in range(y0, y1):
        for x in range(x0, x1):
            dx = max(x0 + r - x, 0, x - (x1 - 1 - r))
            dy = max(y0 + r - y, 0, y - (y1 - 1 - r))
            if dx * dx + dy * dy <= r * r:
                px[y][x] = (*c, 255)


rounded(8, 8, 248, 248, 48, BLUE)
rounded(44, 56, 212, 200, 16, WHITE)
rounded(60, 72, 196, 168, 8, SKY)
for y in range(110, 168):  # mountain
    half = (y - 110) * 1.1
    for x in range(int(120 - half), int(120 + half)):
        if 60 <= x < 196:
            px[y][x] = (*GREEN, 255)
for y in range(84, 108):  # sun
    for x in range(156, 180):
        if (x - 168) ** 2 + (y - 96) ** 2 <= 120:
            px[y][x] = (250, 204, 21, 255)
for t in range(-11, 12):  # red slash
    for i in range(30, 226):
        x, y = i, 256 - i + t
        if 0 <= y < N:
            px[y][x] = (*RED, 255)

raw = b"".join(b"\0" + bytes(v for p in row for v in p) for row in px)


def chunk(t, d):
    return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))


png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", N, N, 8, 6, 0, 0, 0))
png += chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")
out = Path(__file__).resolve().parent.parent / "assets" / "app.png"
out.parent.mkdir(exist_ok=True)
out.write_bytes(png)
print(out)
