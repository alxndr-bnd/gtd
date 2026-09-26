"""Картинки превью ссылок (og:image, 1200×630): static/og.png (RU) и static/og-en.png (EN).
Pillow в зависимостях не нужен — во временном venv:
    python3 -m venv /tmp/og && /tmp/og/bin/pip install pillow && /tmp/og/bin/python scripts/og_image.py
Шрифт — Arial из macOS; на другой системе передай путь к TTF с кириллицей: FONT_DIR=/path."""
import os

from PIL import Image, ImageDraw, ImageFont

FONT_DIR = os.getenv("FONT_DIR", "/System/Library/Fonts/Supplemental")
BOLD, REG = os.path.join(FONT_DIR, "Arial Bold.ttf"), os.path.join(FONT_DIR, "Arial.ttf")
W, H, PAD = 1200, 630, 80
BG, FG, MUT = (59, 108, 246), (255, 255, 255), (214, 225, 255)  # --ac из index.html
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
    # Логотип: белая плашка с галочкой + «GTD»
    d.rounded_rectangle((PAD, PAD, PAD + 88, PAD + 88), radius=20, fill=FG)
    d.line([(PAD + 22, PAD + 46), (PAD + 38, PAD + 62), (PAD + 67, PAD + 28)], fill=BG, width=11, joint="curve")
    d.text((PAD + 112, PAD + 44), "GTD", font=ImageFont.truetype(BOLD, 72), fill=FG, anchor="lm")
    d.text((PAD, 300), headline, font=fit(headline, BOLD, 76, W - 2 * PAD), fill=FG, anchor="ls")
    d.text((PAD, 380), sub, font=fit(sub, REG, 40, W - 2 * PAD), fill=MUT, anchor="ls")
    d.text((PAD, H - PAD), "gtd.serbito.rs  ·  @gtdsrbot", font=ImageFont.truetype(BOLD, 36), fill=FG, anchor="ls")
    out = os.path.join(os.path.dirname(__file__), "..", "static", name)
    im.save(out, optimize=True)
    print(out, os.path.getsize(out), "bytes")


for name, (headline, sub) in TEXTS.items():
    draw(name, headline, sub)
