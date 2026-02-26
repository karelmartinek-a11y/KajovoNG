#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
from typing import Iterable

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
BUILD_ASSETS = ROOT / "Build" / "assets"
RESOURCES = ROOT / "resources"
SOURCE_LOGO = RESOURCES / "Kajovo_new.png"


def _square_crop(img: Image.Image) -> Image.Image:
    """Center-crop to a square before resizing so the icon doesn't distort."""
    w, h = img.size
    side = min(w, h)
    left = (w - side) // 2
    top = (h - side) // 2
    return img.crop((left, top, left + side, top + side))


def _save_png(img: Image.Image, path: Path, size: int) -> None:
    img.resize((size, size), Image.LANCZOS).save(path, format="PNG")


def _save_ico(img: Image.Image, path: Path, sizes: Iterable[int]) -> None:
    img.save(path, format="ICO", sizes=[(s, s) for s in sizes])


def main() -> None:
    if not SOURCE_LOGO.exists():
        raise FileNotFoundError(f"Missing logo file: {SOURCE_LOGO}")

    BUILD_ASSETS.mkdir(parents=True, exist_ok=True)
    RESOURCES.mkdir(parents=True, exist_ok=True)

    img = Image.open(SOURCE_LOGO).convert("RGBA")
    img = _square_crop(img)

    # Runtime window icon (PNG) + Windows exe icon (ICO) + favicon
    _save_png(img, RESOURCES / "app_icon.png", 512)
    _save_png(img, BUILD_ASSETS / "app_icon.png", 512)
    _save_ico(img, BUILD_ASSETS / "app_icon.ico", sizes=[256, 128, 64, 48, 32, 24, 16])
    _save_ico(img, BUILD_ASSETS / "favicon.ico", sizes=[32, 16])

    print(f"Generated icon assets from {SOURCE_LOGO} into: {BUILD_ASSETS}")


if __name__ == "__main__":
    main()
