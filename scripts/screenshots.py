"""Скриншоты приложения для лендинга и страниц сравнения (SERBITO-442): static/shots/{inbox,next,review}-{ru,en}.webp.

Данные — демо во временной базе локального Postgres (как у тестов): ни одного настоящего пользователя, база
удаляется в конце. Приложение — в этом же процессе (uvicorn в потоке), браузер — Chromium из Playwright,
телефон 390×700 с плотностью 2. PNG сжимается в WebP утилитой cwebp (brew install webp).
    .venv/bin/python scripts/screenshots.py
Размер файлов меняется — поправь SHOTS_SIZE в pages.py (скрипт печатает ширину и высоту)."""
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid

import psycopg

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
OUT = os.path.join(ROOT, "static", "shots")
ADMIN = os.getenv("TEST_PG_URL", "postgresql://localhost:5432/postgres")
DB = f"gtd_shots_{uuid.uuid4().hex[:8]}"
WIDTH = 600  # px итогового WebP: на странице картинка не шире ~280 px, этого хватает и для экрана с плотностью 2

with psycopg.connect(ADMIN, autocommit=True) as c:
    c.execute(f'create database "{DB}"')
os.environ.update(DATABASE_URL=f"{ADMIN.rsplit('/', 1)[0]}/{DB}", DEV="1", TELEGRAM_BOT_TOKEN="", SMTP_PASSWORD="",
                  GOOGLE_CLIENT_ID="", GA_MEASUREMENT_ID="", SENTRY_DSN="", BASE_URL="http://localhost:8000")
sys.path.insert(0, ROOT)
import app as A  # noqa: E402 — схема создаётся при импорте, поэтому после env
import uvicorn  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

# Демо-задачи: (текст захвата, список). Проекты и контексты — из самого текста, как у живого человека
DEMO = {
    "ru": [("Позвонить маме завтра в 10:00", "inbox"), ("Идея: подарок Ане на день рождения", "inbox"),
           ("Записаться к стоматологу", "inbox"), ("Прочитать главу про еженедельный обзор", "inbox"),
           ("Ответить бухгалтеру про декларацию #Налоги @комп", "next"),
           ("Выбрать плитку #Ремонт_кухни @магазин", "next"), ("Купить лампочки @магазин", "next"),
           ("Позвонить в банк про карту @телефон", "next"), ("Оплатить интернет @телефон @комп", "next"),
           ("Обновить резюме @комп", "next"), ("Счёт от бухгалтера", "waiting"),
           ("Курсы испанского", "someday"), ("Поход в горы летом", "someday")],
    "en": [("Call mom tomorrow at 10am", "inbox"), ("Idea: a birthday gift for Anna", "inbox"),
           ("Book a dentist appointment", "inbox"), ("Read the chapter on the weekly review", "inbox"),
           ("Reply to the accountant about taxes #Taxes @computer", "next"),
           ("Choose tiles #Kitchen_renovation @store", "next"), ("Buy light bulbs @store", "next"),
           ("Call the bank about the card @phone", "next"), ("Pay the internet bill @phone @computer", "next"),
           ("Update my CV @computer", "next"), ("Invoice from the accountant", "waiting"),
           ("Spanish classes", "someday"), ("Hiking trip in the summer", "someday")],
}
CTX = {"ru": "магазин", "en": "store"}  # контекст, по которому фильтруем Next на скриншоте


def seed(lang: str) -> None:
    A.run("truncate users, sessions, user_sessions, projects, items, activity restart identity cascade")
    uid = A.run("insert into users(tg_id,name,created,lang,checklist_hidden,dnd_tip_seen) "
                "values(0,'Demo',%s,%s,true,true) returning id", (int(time.time()), lang))
    for text, status in DEMO[lang]:
        it = A.capture(uid, text)
        A.run("update items set status=%s where id=%s", (status, it["id"]))
    # Ожидание старше недели и одно напоминание — чтобы шаги обзора были не пустыми
    A.run("update items set created=%s where status='waiting'", (int(time.time()) - 9 * 86400,))


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main() -> None:
    os.makedirs(OUT, exist_ok=True)
    port = free_port()
    srv = uvicorn.Server(uvicorn.Config(A.app, host="127.0.0.1", port=port, lifespan="off", log_level="warning"))
    threading.Thread(target=srv.run, daemon=True).start()
    while not srv.started:
        time.sleep(0.05)
    base = f"http://127.0.0.1:{port}"
    tmp = tempfile.mkdtemp()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            for lang in ("ru", "en"):
                seed(lang)
                ctx = browser.new_context(viewport={"width": 390, "height": 700}, device_scale_factor=2,
                                          locale="ru-RU" if lang == "ru" else "en-US", color_scheme="light")
                # Только свой сервер: ни Google, ни Telegram, ни аналитики
                ctx.route("**/*", lambda r: r.continue_() if r.request.url.startswith(base) else r.abort())
                page = ctx.new_page()
                page.goto(base + "/dev-login")
                page.wait_for_selector('nav > a.on[data-view="inbox"]', state="attached")
                page.wait_for_selector("main .it")
                shots = {"inbox": None, "next": None, "review": None}
                for view in shots:
                    if view != "inbox":
                        page.goto(f"{base}{'' if lang == 'ru' else '/en'}/{view}")
                        page.wait_for_selector("main .rv" if view == "review" else "main .it")
                    if view == "next":
                        page.click(f'main .chip[data-ctx="{CTX[lang]}"]')
                        page.wait_for_selector(f'main .chip.on[data-ctx="{CTX[lang]}"]')
                    page.wait_for_timeout(300)  # шрифты и анимация появления
                    png = os.path.join(tmp, f"{view}-{lang}.png")
                    page.screenshot(path=png)
                    webp = os.path.join(OUT, f"{view}-{lang}.webp")
                    subprocess.run(["cwebp", "-quiet", "-q", "80", "-m", "6", "-resize", str(WIDTH), "0", png, "-o", webp],
                                   check=True)
                    size = os.path.getsize(webp)
                    print(f"{webp}: {WIDTH}×{round(700 * WIDTH / 390)}, {size // 1024} KB")
                ctx.close()
            browser.close()
    finally:
        srv.should_exit = True
        A._pool.close()
        with psycopg.connect(ADMIN, autocommit=True) as c:
            c.execute(f'drop database if exists "{DB}" with (force)')


if __name__ == "__main__":
    main()
