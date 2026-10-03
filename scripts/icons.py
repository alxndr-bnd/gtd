"""Иконки из знака pages.mark(): favicon.svg/.ico, apple-touch-icon и иконки 192/512 (обычные и maskable).
Иконки для установки — непрозрачные, бирюзовый фон во весь холст (SERBITO-392): у прозрачных скруглённых углов
цвет — чёрный (0,0,0,0), и macOS показывал установленное приложение чёрным. Углы скругляет сама система.
Pillow и resvg в зависимостях не нужны — во временном venv:
    python3 -m venv /tmp/icons && /tmp/icons/bin/pip install pillow resvg-py && /tmp/icons/bin/python scripts/icons.py"""
import io
import os
import sys

import resvg_py
from PIL import Image

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, ROOT)
import pages  # noqa: E402

STATIC = os.path.join(ROOT, "static")
# Квадрат во весь холст: iOS и Android сами скругляют углы. Maskable — знак в безопасной зоне (80% в центре)
FULL = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" fill="{pages.BRAND}"/>'
        '<g transform="translate({o} {o}) scale({k})">' + pages.MARK_GLYPH + "</g></svg>")


def png(svg: str, size: int) -> Image.Image:
    return Image.open(io.BytesIO(bytes(resvg_py.svg_to_bytes(svg_string=svg, width=size, height=size)))).convert("RGBA")


def save(img: Image.Image, name: str, **kw):
    path = os.path.join(STATIC, name)
    img.save(path, **kw)
    print(path, os.path.getsize(path), "bytes")


with open(os.path.join(STATIC, "favicon.svg"), "w", encoding="utf-8") as f:
    f.write(pages.mark() + "\n")
save(png(pages.mark(), 48), "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48)])
# Обычные (any) — знак 90%, как apple-touch-icon; maskable — 80%, в безопасной зоне. Все RGB, без альфа-канала
for size in (192, 512):
    save(png(FULL.format(o=3.2, k=0.9), size).convert("RGB"), f"icon-{size}.png", optimize=True)
    save(png(FULL.format(o=6.4, k=0.8), size).convert("RGB"), f"icon-maskable-{size}.png", optimize=True)
save(png(FULL.format(o=3.2, k=0.9), 180).convert("RGB"), "apple-touch-icon.png", optimize=True)
