"""Draws the dashboard app's icons (dashboard/icons/*.png). Plain Python, no packages.

A sun over three lines of news, on a dark blue rounded square.
Run it again only if you change the design: py scripts/make_dashboard_icons.py
"""

import struct
import zlib
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "dashboard" / "icons"
TOP, BOTTOM = (52, 76, 178), (22, 32, 80)
SUN = (255, 194, 71)
LINES = [(0.56, 0.80), (0.68, 0.70), (0.80, 0.56)]  # (y, right end) of each news line


def png(path, size, pixel):
    rows = []
    for y in range(size):
        row = bytearray([0])
        for x in range(size):
            # 3x3 samples per pixel for smooth edges
            total = [0.0, 0.0, 0.0, 0.0]
            for sy in range(3):
                for sx in range(3):
                    r, g, b, a = pixel((x + (sx + 0.5) / 3) / size, (y + (sy + 0.5) / 3) / size)
                    total = [total[0] + r * a, total[1] + g * a, total[2] + b * a, total[3] + a]
            alpha = total[3] / 9
            color = [round(c / total[3]) if total[3] else 0 for c in total[:3]]
            row += bytes(color + [round(alpha * 255)])
        rows.append(bytes(row))
    raw = zlib.compress(b"".join(rows), 9)

    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))

    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
                     + chunk(b"IDAT", raw) + chunk(b"IEND", b""))


def rounded_square(u, v, radius):
    dx, dy = max(radius - u, 0, u - (1 - radius)), max(radius - v, 0, v - (1 - radius))
    return dx * dx + dy * dy <= radius * radius


def artwork(u, v):
    """The sun and the lines at (u, v) in 0..1, or None for the background."""
    if (u - 0.68) ** 2 + (v - 0.32) ** 2 <= 0.15 ** 2:
        return SUN
    for y, right in LINES:
        half = 0.035
        if abs(v - y) <= half:
            end = min(max(u, 0.2 + half), right - half)
            if (u - end) ** 2 + (v - y) ** 2 <= half * half:
                return (255, 255, 255)
    return None


def background(u, v):
    t = (u + v) / 2
    return tuple(TOP[i] + (BOTTOM[i] - TOP[i]) * t for i in range(3))


def icon(u, v):
    if not rounded_square(u, v, 0.22):
        return (0, 0, 0, 0)
    return (*(artwork(u, v) or background(u, v)), 1)


def maskable(u, v):
    # Full-bleed background; the artwork shrunk into the 80% safe area
    inner = artwork(0.5 + (u - 0.5) / 0.8, 0.5 + (v - 0.5) / 0.8)
    return (*(inner or background(u, v)), 1)


def badge(u, v):
    return (255, 255, 255, 1) if artwork(u, v) else (0, 0, 0, 0)


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    for name, size, draw in (("icon-192.png", 192, icon), ("icon-512.png", 512, icon),
                             ("maskable-512.png", 512, maskable), ("badge-96.png", 96, badge)):
        png(OUT / name, size, draw)
        print("Made", name)
