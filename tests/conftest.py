"""Тесты гоняются на временной базе в локальном Postgres (brew postgresql@17): база создаётся
при старте сессии и удаляется в конце. Сервер — TEST_PG_URL (по умолчанию localhost:5432).
Telegram и почта заменены заглушками: наружу тесты не ходят.

Параллельно (pytest -n auto, SERBITO-553): у каждого воркера xdist своя база gtd_test_<воркер>_<uuid>,
главный процесс базы не создаёт и приложение не импортирует. База удаляется в конце сессии, при Ctrl-C,
SIGTERM и при выходе процесса (atexit); что осталось после kill -9 — убирает старт следующей сессии
(_drop_orphans)."""
import asyncio
import atexit
import os
import re
import signal
import time
import uuid

import psycopg
import pytest
from fastapi.testclient import TestClient

ADMIN_URL = os.getenv("TEST_PG_URL", "postgresql://localhost:5432/postgres")
WORKER = os.getenv("PYTEST_XDIST_WORKER", "main")  # gw0, gw1… в воркерах xdist
DB = f"gtd_test_{WORKER}_{uuid.uuid4().hex[:8]}"
ORPHAN_AGE_S = 3600  # чужая сессия жива, пока держит соединение; база старше часа и без соединений — сирота

A = None  # модуль app: импортируется в pytest_configure, после env — он создаёт пул и схему при импорте


def _admin(sql, args=None):
    with psycopg.connect(ADMIN_URL, autocommit=True, connect_timeout=5) as c:
        cur = c.execute(sql, args)
        return cur.fetchall() if cur.description else None


def _drop_orphans(like=r"gtd\_test\_%", age_s=ORPHAN_AGE_S):
    """Базы gtd_test_* старше часа без единого соединения: остатки сессий, убитых без sessionfinish и atexit.
    Возраст — время создания файла базы PG_VERSION (pg_stat_file, нужен суперпользователь: локально и в CI он)."""
    try:
        old = _admin("""select datname from pg_database d
                        where datname like %s
                          and not exists (select 1 from pg_stat_activity a where a.datname = d.datname)
                          and (pg_stat_file('base/' || d.oid || '/PG_VERSION')).modification
                              < now() - make_interval(secs => %s)""", (like, age_s))
    except psycopg.Error:  # нет прав на pg_stat_file (не суперпользователь) — без уборки
        return
    for (name,) in old:
        try:
            _admin(f'drop database if exists "{name}"')  # без force: кто-то подключился — значит, не сирота
        except psycopg.Error:
            pass


def _xdist_controller(config) -> bool:
    """Главный процесс pytest -n N: тесты гоняют воркеры, своей базы ему не нужно."""
    n = getattr(config.option, "numprocesses", None)
    return not hasattr(config, "workerinput") and n not in (None, 0, "0")


_dropped = False


def _drop_db():
    global _dropped
    if _dropped:
        return
    _dropped = True
    if A is not None:
        A._pool.close()
    try:
        _admin(f'drop database if exists "{DB}" with (force)')
    except psycopg.Error:
        pass  # сервер уже недоступен; базу уберёт _drop_orphans следующей сессии


def _interrupt(signum, frame):
    raise KeyboardInterrupt


def pytest_configure(config):
    global A
    # SIGTERM (kill, закрытое окно терминала) — как Ctrl-C, и в главном процессе xdist тоже: он гасит воркеров
    # штатно, они завершают сессию и удаляют свои базы
    try:
        signal.signal(signal.SIGTERM, _interrupt)
    except ValueError:
        pass  # не главный поток: остаются atexit и _drop_orphans
    if not hasattr(config, "workerinput"):
        try:
            _drop_orphans()
        except psycopg.OperationalError:
            pass  # нет сервера — ниже скажем понятно
    if _xdist_controller(config):
        return
    try:
        _admin(f'create database "{DB}"')
    except psycopg.OperationalError as e:
        pytest.exit(f"Нужен локальный Postgres ({ADMIN_URL}): brew services start postgresql@17\n{e}", 2)
    atexit.register(_drop_db)  # Ctrl-C в воркере xdist или выход без sessionfinish
    os.environ.update(DATABASE_URL=f"{ADMIN_URL.rsplit('/', 1)[0]}/{DB}", DEV="1", TELEGRAM_BOT_TOKEN="",
                      SMTP_PASSWORD="", GOOGLE_CLIENT_ID="test-client", BASE_URL="http://localhost:8000")
    import app

    A = app


def pytest_sessionfinish(session, exitstatus):
    if not _xdist_controller(session.config):
        _drop_db()


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


def kill_pool_connections(fill=True) -> int:
    """Обрыв всех соединений пула, как при рестарте Cloud SQL: pg_terminate_backend для каждого бэкенда базы.
    fill: сначала поднимает пул до max_size, чтобы мёртвыми были все пять соединений (худший случай).
    Возвращает, сколько соединений было в пуле."""
    if fill:
        held = [A._pool.getconn() for _ in range(A._pool.max_size)]
        for c in held:
            A._pool.putconn(c)
    n = A._pool.get_stats()["pool_size"]
    db = A._pool.conninfo.rsplit("/", 1)[1]
    with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
        assert admin.execute("select count(pg_terminate_backend(pid)) from pg_stat_activity where datname=%s",
                             (db,)).fetchone()[0] >= 1
        for _ in range(100):  # бэкенды завершаются асинхронно
            if not admin.execute("select 1 from pg_stat_activity where datname=%s", (db,)).fetchone():
                break
            time.sleep(0.05)
    return n


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
