"""Аудит безопасности SERBITO-332, находки GTD-3…GTD-14 (SERBITO-360): сессии, CSRF, заголовки, режим DEV,
логи, лимиты писем, бот в группах, виджет Telegram. Вход по ссылке в бота (GTD-3) — в test_auth.py,
страница /auth (GTD-7) — в test_bot.py, черновики при выходе (GTD-14) — в test_browser_smoke.py."""
import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import smtplib
import subprocess
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app as A
from conftest import bot_message

ROOT = Path(__file__).resolve().parent.parent


def texts_of(sent):
    return [p.get("text", "") for m, p in sent if m in ("sendMessage", "editMessageText")]


# ── GTD-5: сессии ──

def test_session_token_stored_only_as_hash(client, login):
    uid = login(client)
    sid = client.cookies["sid"]
    r = A.row("select * from user_sessions where user_id=%s", (uid,))
    assert r["token_hash"] == hashlib.sha256(sid.encode()).hexdigest() != sid
    assert A.row("select count(*) n from user_sessions where token_hash=%s", (sid,))["n"] == 0


def test_session_expires_after_ttl_without_activity(client, login):
    login(client)
    A.run("update user_sessions set seen=%s", (int(time.time()) - A.SESSION_TTL - 1,))
    assert client.get("/api/me").status_code == 401


def test_session_expiry_slides_with_activity(client, login):
    login(client)
    sid = client.cookies["sid"]
    r = client.get("/api/me")
    assert "set-cookie" not in r.headers  # свежая сессия: базу и cookie не трогаем на каждый запрос
    two_days_ago = int(time.time()) - 2 * 86400
    A.run("update user_sessions set seen=%s", (two_days_ago,))
    r = client.get("/api/me")
    assert r.status_code == 200 and A.row("select seen from user_sessions")["seen"] > two_days_ago
    cookie = r.headers["set-cookie"]
    assert cookie.startswith(f"sid={sid};") and f"Max-Age={A.SESSION_TTL}" in cookie and "HttpOnly" in cookie


def test_legacy_plaintext_sessions_migrate_to_hashes(client):
    """Сессии прежних версий (токен как есть в sessions) переезжают хешами при старте и продолжают работать."""
    uid = A.run("insert into users(name,created) values('old',0) returning id")
    A.run("insert into sessions(token,user_id,created) values('legacy-token',%s,0)", (uid,))
    with A._pool.connection() as c:
        c.execute(A.SCHEMA)
    assert A.row("select count(*) n from sessions")["n"] == 0
    assert A.row("select user_id from user_sessions where token_hash=%s", (A.token_hash("legacy-token"),))["user_id"] == uid
    client.cookies.set("sid", "legacy-token")
    assert client.get("/api/me").status_code == 200


def test_logout_clears_cookie_and_session(client, login):
    login(client)
    r = client.post("/api/logout")
    assert r.json() == {"ok": True} and re.match(r'sid=""?;.*Max-Age=0', r.headers["set-cookie"])
    assert "sid" not in client.cookies and A.row("select count(*) n from user_sessions")["n"] == 0


def test_sign_out_everywhere(new_client, login):
    laptop, phone, bob = new_client(), new_client(), new_client()
    login(laptop, "alice@example.com")
    login(phone, "alice@example.com")
    login(bob, "bob@example.com")
    assert laptop.get("/api/me").json()["sessions"] == 2
    r = laptop.post("/api/logout/all")
    assert r.json() == {"ok": True} and "Max-Age=0" in r.headers["set-cookie"]
    assert laptop.get("/api/me").status_code == 401 and phone.get("/api/me").status_code == 401
    assert bob.get("/api/me").status_code == 200  # чужие сессии не тронуты
    assert new_client().post("/api/logout/all").status_code == 401


# ── GTD-6: запросы с чужих страниц ──

@pytest.mark.parametrize("headers", [
    {"origin": "https://evil.serbito.rs"},
    {"origin": "null"},
    {"sec-fetch-site": "same-site"},  # соседний поддомен *.serbito.rs
    {"sec-fetch-site": "cross-site", "origin": "http://testserver"},
])
def test_cross_site_writes_rejected(client, login, headers):
    login(client)
    assert client.post("/api/capture", json={"text": "чужое"}, headers=headers).status_code == 403
    assert client.post("/api/logout", headers=headers).status_code == 403
    assert client.post("/api/auth/email/start", json={"email": "a@example.com"}, headers=headers).status_code == 403
    assert A.row("select count(*) n from items")["n"] == 0 and client.get("/api/me").status_code == 200


def test_same_origin_writes_pass(client, login):
    login(client)
    for headers in ({"origin": "http://testserver", "sec-fetch-site": "same-origin"}, {},
                    {"origin": "http://localhost:8000", "host": "app:8080"}):  # BASE_URL за прокси, меняющим Host
        assert client.post("/api/capture", json={"text": "своё"}, headers=headers).status_code == 200
    assert client.get("/api/counts", headers={"origin": "https://evil.example"}).status_code == 200  # чтение — можно


def test_api_body_must_be_json(client, login):
    login(client)
    for ct in ("text/plain", "application/x-www-form-urlencoded", None):
        r = client.post("/api/capture", content=b'{"text": "x"}', headers={"content-type": ct} if ct else {})
        assert r.status_code == 415, ct
    ok = client.post("/api/capture", content=b'{"text": "x"}', headers={"content-type": "application/json; charset=utf-8"})
    assert ok.status_code == 200


def test_auth_form_post_rejected_cross_site(client, tg):
    bot_message("/login")
    tok = re.search(r"/auth\?t=(\S+)", texts_of(tg)[-1]).group(1)
    assert client.post("/auth", data={"t": tok}, headers={"origin": "https://evil.example"}).status_code == 403
    assert A.row("select count(*) n from login_tokens")["n"] == 1  # токен не сожжён


def test_webhook_not_subject_to_origin_check(client, tg):
    upd = {"update_id": 1, "message": {"chat": {"id": 777, "type": "private"}, "from": {"id": 777}, "text": "x"}}
    r = client.post("/tg/webhook", json=upd, headers={"X-Telegram-Bot-Api-Secret-Token": A.webhook_secret(),
                                                     "origin": "https://api.telegram.org"})
    assert r.status_code == 200


# ── GTD-7: вход по ссылке из бота ──

def test_auth_link_page_warns_about_account_switch(client, login, tg):
    login(client, "alice@example.com")
    bot_message("/login", tg_id=888, name="Mallory")
    tok = re.search(r"/auth\?t=(\S+)", texts_of(tg)[-1]).group(1)
    page = client.get("/auth", params={"t": tok}).text
    assert "Mallory" in page and "a***@example.com" in page and "переключит" in page
    assert client.get("/api/me").json()["email"] == "alice@example.com"  # пока не нажата кнопка — всё как было


def test_auth_link_page_escapes_name(client, tg):
    bot_message("/login", name="<img src=x onerror=alert(1)>")
    tok = re.search(r"/auth\?t=(\S+)", texts_of(tg)[-1]).group(1)
    page = client.get("/auth", params={"t": tok}).text
    assert "<img src=x" not in page and "&lt;img src=x" in page


# ── GTD-8: бот только в личке ──

@pytest.mark.parametrize("chat_type", ["group", "supergroup", "channel", None])
def test_bot_ignores_non_private_chats(tg, chat_type):
    chat = {"id": -100, **({"type": chat_type} if chat_type else {})}
    for text in ("/login", "/start", "/inbox", "задача в группе", "/email a@example.com"):
        asyncio.run(A.handle_message({"chat": chat, "from": {"id": 777, "first_name": "Tom"}, "text": text}))
    assert tg == [] and A.row("select count(*) n from users")["n"] == 0


def test_bot_buttons_in_groups_are_ignored(tg):
    bot_message("задача")
    tg.clear()
    iid = A.row("select id from items")["id"]
    asyncio.run(A.handle_callback({"id": "cb", "from": {"id": 777}, "data": f"done:{iid}",
                                   "message": {"chat": {"id": -100, "type": "group"}, "message_id": 1}}))
    assert tg == [("answerCallbackQuery", {"callback_query_id": "cb"})]
    assert A.row("select status from items")["status"] == "inbox"


# ── GTD-9: заголовки ──

@pytest.mark.parametrize("path", ["/", "/api/config", "/about", "/no-such-page", "/api/me"])
def test_security_headers_on_every_response(client, path):
    h = client.get(path).headers
    assert h["x-content-type-options"] == "nosniff"
    assert h["referrer-policy"] == "strict-origin-when-cross-origin"
    assert h["x-frame-options"] == "DENY" and h["content-security-policy"] == "frame-ancestors 'none'"
    assert "camera=()" in h["permissions-policy"]
    csp = h["content-security-policy-report-only"]
    assert "default-src 'self'" in csp and "https://accounts.google.com" in csp and "https://oauth.telegram.org" in csp
    assert "strict-transport-security" not in h  # http: HSTS не отдаём


def test_csp_reports_go_to_sentry(monkeypatch):
    monkeypatch.setattr(A, "SENTRY_DSN", "https://abc123@o42.ingest.de.sentry.io/77")
    assert A.sentry_csp_uri() == "https://o42.ingest.de.sentry.io/api/77/security/?sentry_key=abc123"
    monkeypatch.setattr(A, "SENTRY_DSN", "")
    assert A.sentry_csp_uri() == ""


def test_api_docs_available_only_in_dev(client):
    assert client.get("/openapi.json").status_code == 200  # тесты — DEV=1


def probe(**env) -> tuple[dict, list]:
    """Импорт приложения в отдельном процессе с другим окружением (прод): что отвечают служебные адреса и что
    ушло в stdout. Та же тестовая база, Sentry и бот выключены."""
    code = """
import json, app
from fastapi.testclient import TestClient
c = TestClient(app.app)
cfg = c.get("/api/config")
app.log.error("boom %s", "x", exc_info=ZeroDivisionError("z"))
out = {"dev": app.DEV, "config_dev": cfg.json()["dev"], "hsts": cfg.headers.get("strict-transport-security"),
       **{p: c.get(p, follow_redirects=False).status_code for p in ("/docs", "/redoc", "/openapi.json", "/dev-login")}}
app._pool.close()
print("PROBE " + json.dumps(out), flush=True)
"""
    base = {k: v for k, v in os.environ.items() if k not in ("K_SERVICE", "LOG_FORMAT")}
    p = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=120,
                       env={**base, "SENTRY_DSN": "", "TELEGRAM_BOT_TOKEN": "", **env})
    assert p.returncode == 0, p.stderr
    lines = p.stdout.splitlines()
    return json.loads(next(x for x in lines if x.startswith("PROBE "))[6:]), [x for x in lines if not x.startswith("PROBE ")]


def test_prod_instance_ignores_dev_and_hides_docs():
    """GTD-12 и GTD-9: DEV=1 на https-инстансе не включает ни /dev-login, ни /docs; HSTS есть."""
    out, _ = probe(DEV="1", BASE_URL="https://gtd.example")
    assert out["dev"] is False and out["config_dev"] is False
    assert out["/dev-login"] == out["/docs"] == out["/redoc"] == out["/openapi.json"] == 404
    assert out["hsts"] == "max-age=31536000; includeSubDomains"


def test_cloud_run_ignores_dev_and_logs_json():
    """GTD-12 и SERBITO-336: в Cloud Run (K_SERVICE) DEV=1 не действует даже с http BASE_URL, а логи —
    JSON-строки с severity, трейсбек — в message."""
    out, lines = probe(DEV="1", BASE_URL="http://localhost:8000", K_SERVICE="gtd")
    assert out["dev"] is False and out["/dev-login"] == 404
    logs = [json.loads(x) for x in lines]
    assert {"severity": "WARNING", "logger": "gtd",
            "message": "DEV=1 игнорируется: инстанс публичный (https BASE_URL или Cloud Run)"} in logs
    err = next(x for x in logs if x["message"].startswith("boom x"))
    assert err["severity"] == "ERROR" and "ZeroDivisionError: z" in err["message"]


def test_local_logs_stay_plain_text():
    out, lines = probe(DEV="1", BASE_URL="http://localhost:8000")
    assert out["dev"] is True and out["/dev-login"] == 303
    assert not any(x.startswith("{") for x in lines)


def test_json_formatter_severity_levels():
    f = A.CloudJson()
    for level, sev in ((logging.INFO, "INFO"), (logging.WARNING, "WARNING"), (logging.CRITICAL, "CRITICAL")):
        rec = logging.LogRecord("gtd", level, __file__, 1, "привет %s", ("мир",), None)
        assert json.loads(f.format(rec)) == {"severity": sev, "message": "привет мир", "logger": "gtd"}


# ── GTD-10: что попадает в логи ──

def test_access_log_hides_login_tokens():
    rec = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
                            ("1.2.3.4:5", "GET", "/auth?t=SECRET-TOKEN&lang=en", "1.1", 200), None)
    for f in logging.getLogger("uvicorn.access").filters:
        f.filter(rec)
    assert rec.getMessage() == '1.2.3.4:5 - "GET /auth?t=[Filtered]&lang=en HTTP/1.1" 200'
    rec = logging.LogRecord("x", logging.INFO, __file__, 1, "/api/auth/tg/poll?nonce=abc&status=inbox", None, None)
    A.ScrubQuery().filter(rec)
    assert rec.getMessage() == "/api/auth/tg/poll?nonce=[Filtered]&status=inbox"


def test_smtp_failure_log_has_no_recipient(client, monkeypatch, caplog):
    monkeypatch.setattr(A, "SMTP_PASSWORD", "x")

    def refuse(to, subject, body):
        raise smtplib.SMTPRecipientsRefused({to: (550, b"no such user " + to.encode())})
    monkeypatch.setattr(A, "send_email", refuse)
    assert client.post("/api/auth/email/start", json={"email": "victim@example.com"}).status_code == 502
    assert "smtp failed: SMTPRecipientsRefused" in caplog.text and "victim" not in caplog.text


# ── GTD-4: общий потолок писем ──

def test_global_daily_email_cap_and_alert(new_client, mail, monkeypatch, caplog):
    monkeypatch.setattr(A, "EMAIL_DAILY_CAP", 5)
    caplog.set_level(logging.ERROR, "gtd")
    for i in range(5):  # разные адреса и «разные IP» — лимиты на адрес и отправителя не срабатывают
        A.run("delete from auth_limits where key like 'send%%'")
        assert new_client().post("/api/auth/email/start", json={"email": f"u{i}@example.com"}).status_code == 200
    assert [r.getMessage() for r in caplog.records] == ["email: отправлено 4 писем за сутки — 80% потолка 5"]
    A.run("delete from auth_limits where key like 'send%%'")
    r = new_client().post("/api/auth/email/start", json={"email": "u5@example.com"})
    assert r.status_code == 503 and r.json()["detail"].startswith("Вход по почте сейчас недоступен")
    assert len(mail) == 5 and "достигнут суточный потолок писем (5)" in caplog.records[-1].getMessage()
    new_client().post("/api/auth/email/start", json={"email": "u6@example.com"})
    assert len(caplog.records) == 2  # алерт об упоре — один раз, а не на каждую попытку


def test_default_cap_leaves_room_in_shared_brevo_quota():
    assert 0 < A.EMAIL_DAILY_CAP <= 250  # Brevo free — 300 писем в сутки на аккаунт, общий с serbito


# ── GTD-11: виджет Telegram ──

def widget_payload(**fields):
    data = {"id": 777, "first_name": "Tom", "auth_date": int(time.time()), **fields}
    check = "\n".join(f"{k}={data[k]}" for k in sorted(data))
    data["hash"] = hmac.new(hashlib.sha256(b"test-token").digest(), check.encode(), hashlib.sha256).hexdigest()
    return data


def test_widget_payload_short_lived_and_single_use(new_client, tg):
    assert A.TG_WIDGET_MAX_AGE == 600
    auth = widget_payload()
    assert new_client().post("/api/auth/tg/widget", json={"auth": auth}).status_code == 200
    replay = new_client()
    r = replay.post("/api/auth/tg/widget", json={"auth": auth})
    assert r.status_code == 400 and "sid" not in replay.cookies
    old = widget_payload(auth_date=int(time.time()) - 11 * 60)
    assert new_client().post("/api/auth/tg/widget", json={"auth": old}).status_code == 400


# ── GTD-13: публичный конфиг деплоя ──

def test_deploy_config_has_no_smtp_login_or_admin_ids():
    env = (ROOT / ".github" / "deploy.env.yaml").read_text(encoding="utf-8")
    assert not re.search(r"^\s*(SMTP_USER|ADMIN_USER_IDS)\s*:", env, re.M) and "smtp-brevo.com" not in env
    wf = (ROOT / ".github" / "workflows" / "deploy.yml").read_text(encoding="utf-8")
    assert "secrets.SMTP_USER" in wf and "secrets.ADMIN_USER_IDS" in wf
