#!/usr/bin/env python3
"""Regenerate the PWA brand icons (green rounded tile + white ▲ mark).

Dev-time only — Pillow is NOT a runtime dependency; the PNGs are committed to
app/static/icons/. To regenerate: pip install pillow && python scripts/gen_pwa_icons.py
"""
import os
from pathlib import Path

from PIL import Image, ImageDraw

GREEN = (20, 168, 0)       # brand DEFAULT #14a800
GREEN_HI = (30, 195, 10)   # top highlight for a subtle vertical gradient


def tile(size):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    for y in range(size):
        c = tuple(int(GREEN_HI[i] + (GREEN[i] - GREEN_HI[i]) * (y / max(1, size - 1))) for i in range(3))
        d.line([(0, y), (size, y)], fill=c)
    r = int(size * 0.22)
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=r, fill=GREEN)
    m = size * 0.30
    cy = size * 0.52
    d.polygon([(size / 2, cy - m), (size / 2 - m, cy + m * 0.82), (size / 2 + m, cy + m * 0.82)], fill=(247, 247, 248))
    return img


def maskable(size=512):
    mk = Image.new("RGBA", (size, size), GREEN)
    d = ImageDraw.Draw(mk)
    r = int(size * 0.22)
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=r, fill=GREEN)
    m = size * 0.30
    cy = size * 0.52
    d.polygon([(size / 2, cy - m), (size / 2 - m, cy + m * 0.82), (size / 2 + m, cy + m * 0.82)], fill=(247, 247, 248))
    return mk


def main():
    out = Path(__file__).resolve().parent.parent / "app" / "static" / "icons"
    out.mkdir(parents=True, exist_ok=True)
    (out / "icon-192.png").save(tile(192))
    (out / "icon-512.png").save(tile(512))
    (out / "icon-512-maskable.png").save(maskable(512))
    (out / "apple-touch-icon.png").save(tile(192))
    for f in sorted(os.listdir(out)):
        p = out / f
        print(f"{f}  {Image.open(p).size}  {p.stat().st_size} bytes")


if __name__ == "__main__":
    main()
