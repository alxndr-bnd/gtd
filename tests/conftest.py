"""Тесты гоняются на временной базе в локальном Postgres (brew postgresql@17): база создаётся
при старте сессии и удаляется в конце. Сервер — TEST_PG_URL (по умолчанию localhost:5432).
Telegram и почта заменены заглушками: наружу тесты не ходят."""
import asyncio
import os
import re
import uuid

import psycopg
import pytest
from fastapi.testclient import TestClient

ADMIN_URL = os.getenv("TEST_PG_URL", "postgresql://localhost:5432/postgres")
DB = f"gtd_test_{uuid.uuid4().hex[:8]}"


def _admin(sql):
    with psycopg.connect(ADMIN_URL, autocommit=True, connect_timeout=5) as c:
        c.execute(sql)


try:
    _admin(f'create database "{DB}"')
except psycopg.OperationalError as e:
    pytest.exit(f"Нужен локальный Postgres ({ADMIN_URL}): brew services start postgresql@17\n{e}", 2)

os.environ.update(DATABASE_URL=f"{ADMIN_URL.rsplit('/', 1)[0]}/{DB}", DEV="1", TELEGRAM_BOT_TOKEN="",
                  SMTP_PASSWORD="", GOOGLE_CLIENT_ID="test-client", BASE_URL="http://localhost:8000")

import app as A  # noqa: E402 — модуль создаёт пул и схему при импорте, поэтому после env


def pytest_sessionfinish(session, exitstatus):
    A._pool.close()
    _admin(f'drop database if exists "{DB}" with (force)')


TABLES = ("users, sessions, user_sessions, login_tokens, projects, items, email_codes, tg_logins, merge_offers, "
          "tg_email_links, activity, auth_limits, api_tokens, oauth_clients, oauth_grants, oauth_codes, oauth_tokens")


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    # Браузерный смоук: запрос страницы прошлого теста может ещё идти в сервере-потоке (select по items и projects),
    # и truncate, берущий те же таблицы в другом порядке, изредка ловит deadlock — тогда просто повторяем
    for attempt in range(5):
        try:
            A.run(f"truncate {TABLES} restart identity")
            break
        except psycopg.errors.DeadlockDetected:
            if attempt == 4:
                raise
    A._seen_today.clear()
    monkeypatch.setattr(A, "TOKEN", "")
    monkeypatch.setattr(A, "BOT_USERNAME", "")


@pytest.fixture
def new_client():
    # Без `with`: иначе lifespan запустит фоновые задачи и на выходе закроет пул базы
    return lambda: TestClient(A.app)


@pytest.fixture
def client(new_client):
    return new_client()


@pytest.fixture
def tg(monkeypatch):
    """Бот включён, вызовы Telegram API копятся в списке вместо сети."""
    sent = []

    async def fake(method, **params):
        sent.append((method, params))
        return True

    monkeypatch.setattr(A, "tg", fake)
    monkeypatch.setattr(A, "TOKEN", "test-token")
    monkeypatch.setattr(A, "BOT_USERNAME", "gtd_test_bot")
    return sent


@pytest.fixture
def mail(monkeypatch):
    """Почта «настроена», письма копятся в списке."""
    outbox = []
    monkeypatch.setattr(A, "SMTP_PASSWORD", "test")
    monkeypatch.setattr(A, "send_email", lambda to, subject, body: outbox.append((to, subject, body)))
    return outbox


def last_code(outbox) -> str:
    return re.search(r"\b(\d{6})\b", outbox[-1][2]).group(1)


def email_start(c, email, **kw):
    return c.post("/api/auth/email/start", json={"email": email}, **kw)


def email_verify(c, email, code, link=False, **kw):
    return c.post("/api/auth/email/verify", json={"email": email, "code": code, "link": link}, **kw)


def texts(sent):
    """Тексты сообщений бота (новые и отредактированные) из заглушки tg."""
    return [p.get("text", "") for m, p in sent if m in ("sendMessage", "editMessageText")]


@pytest.fixture
def login(mail):
    """login(client, email) — вход по коду из письма; возвращает id пользователя."""
    def do(c, email="alice@example.com", link=False):
        assert c.post("/api/auth/email/start", json={"email": email}).status_code == 200
        r = c.post("/api/auth/email/verify", json={"email": email, "code": last_code(mail), "link": link})
        assert r.status_code == 200, r.text
        A.run("delete from email_codes")  # снять минутный кулдаун и лимиты для следующего входа
        A.run("delete from auth_limits")
        return A.row("select id from users where email=%s", (email.strip().lower(),))["id"]
    return do


def tg_from(tg_id, name, lang):
    """Отправитель апдейта; lang — language_code клиента Telegram (None — клиент его не прислал)."""
    return {"id": tg_id, "first_name": name, **({"language_code": lang} if lang else {})}


def bot_message(text, tg_id=777, name="Tom", lang=None):
    asyncio.run(A.handle_message({"chat": {"id": tg_id, "type": "private"}, "from": tg_from(tg_id, name, lang),
                                  "text": text}))


def tg_start(c, link=False) -> dict:
    """Кнопка «Войти через Telegram» на сайте: {nonce, code, url}; браузер c получает cookie tgl."""
    r = c.post("/api/auth/tg/start", json={"link": link})
    assert r.status_code == 200, r.text
    return r.json()


def tg_poll(c, nonce) -> dict:
    return c.post("/api/auth/tg/poll", json={"nonce": nonce}).json()


def tg_confirm(d, tg_id=777, lang=None, code=None):
    """Нажатие в боте кнопки с числом с сайта (или code — другого числа)."""
    bot_callback(f"tgok:{d['nonce']}:{d['code'] if code is None else code}", tg_id=tg_id, lang=lang)


def bot_callback(data, tg_id=777, message=True, lang=None):
    asyncio.run(A.handle_callback({"id": "cb", "from": tg_from(tg_id, "Tom", lang), "data": data,
                                   "message": {"chat": {"id": tg_id, "type": "private"}, "message_id": 1}
                                   if message else {}}))
