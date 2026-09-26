"""Картинки превью ссылок (og:image, 1200×630): static/og.png (RU) и static/og-en.png (EN).
Pillow и resvg в зависимостях не нужны — во временном venv:
    python3 -m venv /tmp/og && /tmp/og/bin/pip install pillow resvg-py && /tmp/og/bin/python scripts/og_image.py
Шрифт — Arial из macOS; на другой системе передай путь к TTF с кириллицей: FONT_DIR=/path."""
import io
import os
import sys

import resvg_py
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import pages  # noqa: E402

FONT_DIR = os.getenv("FONT_DIR", "/System/Library/Fonts/Supplemental")
BOLD, REG = os.path.join(FONT_DIR, "Arial Bold.ttf"), os.path.join(FONT_DIR, "Arial.ttf")
W, H, PAD = 1200, 630, 80
BG, FG, MUT = (15, 118, 110), (255, 255, 255), (204, 236, 231)  # pages.BRAND (#0F766E) — цвет логотипа и --ac
TEXTS = {
    "og.png": ("Записал → Разобрал → Сделал", "GTD онлайн бесплатно — на сайте и в Telegram-боте"),
    "og-en.png": ("Capture → Clarify → Do", "Free GTD online — on the web and in a Telegram bot"),
}


def fit(text, path, size, width):
    """Самый крупный кегль не больше size, при котором строка влезает в ширину."""
    while size > 20:
        font = ImageFont.truetype(path, size)
        if font.getlength(text) <= width:
            return font
        size -= 2
    return font


def draw(name, headline, sub):
    im = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(im)
    # Логотип: знак «Входящие» в обратных цветах (на бирюзовом фоне — белая плашка) + «GTD»
    inverse = pages.mark().replace(f'fill="{pages.BRAND}"', 'fill="X"').replace("#fff", pages.BRAND).replace('"X"', '"#fff"')
    logo = Image.open(io.BytesIO(bytes(resvg_py.svg_to_bytes(svg_string=inverse, width=88, height=88))))
    im.paste(logo, (PAD, PAD), logo.convert("RGBA"))
    d.text((PAD + 112, PAD + 44), "GTD", font=ImageFont.truetype(BOLD, 72), fill=FG, anchor="lm")
    d.text((PAD, 300), headline, font=fit(headline, BOLD, 76, W - 2 * PAD), fill=FG, anchor="ls")
    d.text((PAD, 380), sub, font=fit(sub, REG, 40, W - 2 * PAD), fill=MUT, anchor="ls")
    d.text((PAD, H - PAD), "gtd.serbito.rs  ·  @gtdsrbot", font=ImageFont.truetype(BOLD, 36), fill=FG, anchor="ls")
    out = os.path.join(os.path.dirname(__file__), "..", "static", name)
    im.save(out, optimize=True)
    print(out, os.path.getsize(out), "bytes")


for name, (headline, sub) in TEXTS.items():
    draw(name, headline, sub)
