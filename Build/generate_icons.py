#!/usr/bin/env python3
from __future__ import annotations

import struct
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD_ASSETS = ROOT / "Build" / "assets"
RESOURCES = ROOT / "resources"


def _rounded_rect_mask(size: int, margin: int, radius: int, x: int, y: int) -> bool:
    left = margin
    top = margin
    right = size - margin - 1
    bottom = size - margin - 1
    if left <= x <= right and top + radius <= y <= bottom - radius:
        return True
    if top <= y <= bottom and left + radius <= x <= right - radius:
        return True

    corners = [
        (left + radius, top + radius),
        (right - radius, top + radius),
        (left + radius, bottom - radius),
        (right - radius, bottom - radius),
    ]
    for cx, cy in corners:
        if (x - cx) ** 2 + (y - cy) ** 2 <= radius**2:
            return True
    return False


def _draw_icon(size: int) -> bytes:
    bg = (10, 18, 38, 255)
    inner = (23, 36, 72, 255)
    border = (87, 188, 255, 255)
    white = (255, 255, 255, 255)

    margin = max(8, size // 10)
    radius = max(12, size // 5)
    border_w = max(2, size // 40)

    pixels = [[bg for _ in range(size)] for _ in range(size)]

    for y in range(size):
        for x in range(size):
            inside = _rounded_rect_mask(size, margin, radius, x, y)
            if not inside:
                continue
            border_zone = not _rounded_rect_mask(size, margin + border_w, max(1, radius - border_w), x, y)
            pixels[y][x] = border if border_zone else inner

    bar_x = size // 3
    bar_w = max(8, size // 9)
    top = size // 4
    bottom = size - top
    for y in range(top, bottom):
        for x in range(bar_x - bar_w // 2, bar_x + bar_w // 2):
            if 0 <= x < size:
                pixels[y][x] = border

    for i in range(size // 2):
        x = bar_x + i
        y_up = size // 2 - int(i * 0.75)
        y_dn = size // 2 + int(i * 0.75)
        for thickness in range(-max(2, size // 50), max(2, size // 50) + 1):
            if 0 <= x < size and 0 <= y_up + thickness < size:
                pixels[y_up + thickness][x] = white
            if 0 <= x < size and 0 <= y_dn + thickness < size:
                pixels[y_dn + thickness][x] = white

    raw = bytearray()
    for row in pixels:
        raw.append(0)
        for r, g, b, a in row:
            raw.extend((r, g, b, a))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack("!I", len(data)) + tag + data + struct.pack("!I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    ihdr = struct.pack("!IIBBBBB", size, size, 8, 6, 0, 0, 0)
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b"")
    return png


def _write_ico(path: Path, png_bytes: bytes, size: int) -> None:
    header = struct.pack("<HHH", 0, 1, 1)
    entry = struct.pack(
        "<BBBBHHII",
        0 if size >= 256 else size,
        0 if size >= 256 else size,
        0,
        0,
        1,
        32,
        len(png_bytes),
        6 + 16,
    )
    path.write_bytes(header + entry + png_bytes)


def main() -> None:
    BUILD_ASSETS.mkdir(parents=True, exist_ok=True)
    RESOURCES.mkdir(parents=True, exist_ok=True)

    png_1024 = _draw_icon(1024)
    png_256 = _draw_icon(256)
    png_32 = _draw_icon(32)

    (BUILD_ASSETS / "app_icon.png").write_bytes(png_1024)
    (RESOURCES / "app_icon.png").write_bytes(png_1024)
    _write_ico(BUILD_ASSETS / "app_icon.ico", png_256, 256)
    _write_ico(BUILD_ASSETS / "favicon.ico", png_32, 32)

    print(f"Generated icon assets in: {BUILD_ASSETS}")


if __name__ == "__main__":
    main()
