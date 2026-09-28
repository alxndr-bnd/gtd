"""Sentry включается только с DSN; релиз и окружение — из env Cloud Run; секреты в события не попадают."""
import json

import pytest
import sentry_sdk
import sentry_sdk.transport
from fastapi.testclient import TestClient

import app as A


def test_disabled_without_dsn(monkeypatch):
    calls = []
    monkeypatch.setattr(A, "SENTRY_DSN", "")
    monkeypatch.setattr(sentry_sdk, "init", lambda **kw: calls.append(kw))
    assert A.init_sentry() is False and calls == []
    assert not sentry_sdk.get_client().is_active()  # в тестах ничего не уходит наружу


def test_enabled_on_cloud_run(monkeypatch):
    calls = []
    monkeypatch.setattr(A, "SENTRY_DSN", "https://key@example.ingest.sentry.io/1")
    monkeypatch.setattr(sentry_sdk, "init", lambda **kw: calls.append(kw))
    monkeypatch.setenv("SENTRY_RELEASE", "gtd@0.3.0")
    monkeypatch.setenv("K_SERVICE", "gtd")
    assert A.init_sentry() is True
    kw = calls[0]
    assert (kw["dsn"], kw["release"], kw["environment"], kw["send_default_pii"], kw["traces_sample_rate"]) == \
           ("https://key@example.ingest.sentry.io/1", "gtd@0.3.0", "production", False, 0.1)
    # данные пользователей не уходят: ни локальные переменные, ни тела запросов
    assert kw["include_local_variables"] is False and kw["max_request_body_size"] == "never"
    assert kw["before_breadcrumb"]({"category": "httpx", "message": "x"}, None) is None


def test_development_without_k_service(monkeypatch):
    calls = []
    monkeypatch.setattr(A, "SENTRY_DSN", "https://key@example.ingest.sentry.io/1")
    monkeypatch.setattr(sentry_sdk, "init", lambda **kw: calls.append(kw))
    monkeypatch.delenv("K_SERVICE", raising=False)
    monkeypatch.delenv("SENTRY_RELEASE", raising=False)
    A.init_sentry()
    assert calls[0]["environment"] == "development" and calls[0]["release"] is None


def test_scrub_removes_bot_token_everywhere():
    tok = "bot1234567890:AAH-abcdefghijklmnopqrstuvwxyz_0123"
    ev = {"message": f"POST https://api.telegram.org/{tok}/sendMessage",
          "exception": {"values": [{"value": f"boom {tok}"}]}, "extra": [{"u": tok}], "n": 5}
    out = A.sentry_scrub(ev)
    assert "AAH-" not in str(out) and out["n"] == 5
    assert out["message"] == "POST https://api.telegram.org/bot<redacted>/sendMessage"


def test_httpx_does_not_log_urls_with_token():
    import logging
    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING


def test_no_server_side_prepared_statements():
    # иначе миграция схемы под работающим сервером роняет запросы (Sentry GTD-1)
    with A._pool.connection() as c:
        assert c.prepare_threshold is None


# ── Секреты не уходят в Sentry (SERBITO-346, GTD-2): настоящий клиент sentry_sdk, события ловит транспорт ──

class Captured(sentry_sdk.transport.Transport):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.events = []

    def capture_envelope(self, envelope):
        self.events += [i.payload.json for i in envelope.items if i.type in ("event", "transaction")]


@pytest.fixture
def sentry_events(monkeypatch):
    transport = Captured()
    real_init = sentry_sdk.init
    monkeypatch.setattr(A, "SENTRY_DSN", "https://key@example.ingest.sentry.io/1")
    monkeypatch.setattr(sentry_sdk, "init", lambda **kw: real_init(**{**kw, "transport": transport,
                                                                    "traces_sample_rate": 1.0}))
    monkeypatch.setattr(A, "CRON_SECRET", "cron-s3cret-value")
    monkeypatch.setattr(A, "TOKEN", "1234567890:AAH-abcdefghijklmnopqrstuvwxyz_0123")
    assert A.init_sentry()
    yield transport.events
    sentry_sdk.get_client().close()
    sentry_sdk.get_global_scope().set_client(None)
    assert not sentry_sdk.get_client().is_active()


# В коде теста ниже секреты — только по именам: иначе они попадут в события как строки исходника в кадрах стека
LOGIN_TOKEN, COOKIE, CLIENT_IP = "login-tok-123", "session-cookie-456", "203.0.113.7"
SECRETS = ("cron-s3cret-value", "AAH-abcdefghij", LOGIN_TOKEN, COOKIE, CLIENT_IP)


def test_error_event_has_no_secrets(sentry_events, monkeypatch):
    async def boom():
        A.log.warning("opening %s", f"https://gtd.example/auth?t={LOGIN_TOKEN}&lang=en")
        raise RuntimeError(f"boom cron={A.CRON_SECRET} hook={A.webhook_secret()}")
    monkeypatch.setattr(A, "send_due_reminders", boom)
    c = TestClient(A.app, raise_server_exceptions=False, cookies={"sid": COOKIE})
    r = c.post(f"/tasks/reminders?t={LOGIN_TOKEN}",
               headers={"X-Cron-Secret": A.CRON_SECRET, "X-Telegram-Bot-Api-Secret-Token": A.webhook_secret(),
                        "X-Forwarded-For": CLIENT_IP, "User-Agent": "pytest-agent"})
    assert r.status_code == 500
    sentry_sdk.flush()
    errors = [e for e in sentry_events if e.get("type") != "transaction"]
    assert errors, sentry_events
    dump = json.dumps(sentry_events)
    for s in SECRETS + (A.webhook_secret(),):
        assert s not in dump
    req = errors[0]["request"]
    assert req["query_string"] == "t=[Filtered]" and "cookies" not in req
    assert req["headers"].get("user-agent") == "pytest-agent"
    assert not {"cookie", "x-cron-secret", "x-telegram-bot-api-secret-token", "x-forwarded-for"} & set(req["headers"])
    crumbs = json.dumps(errors[0].get("breadcrumbs"))
    assert "auth?t=[Filtered]&lang=[Filtered]" in crumbs  # breadcrumb из лога остался, но без токена


def test_transaction_has_no_login_token(sentry_events):
    TestClient(A.app, cookies={"sid": COOKIE}).get(f"/auth?t={LOGIN_TOKEN}", follow_redirects=False)
    sentry_sdk.flush()
    tx = [e for e in sentry_events if e.get("type") == "transaction"]
    assert tx and tx[0]["request"]["query_string"] == "t=[Filtered]"
    dump = json.dumps(sentry_events)
    assert LOGIN_TOKEN not in dump and COOKIE not in dump


def test_scrub_query_values_everywhere():
    out = A.sentry_scrub({"message": 'GET /auth?t=abc&lang=en HTTP/1.1', "logentry": {"params": ("/x?nonce=zz",)}})
    assert out["message"] == "GET /auth?t=[Filtered]&lang=[Filtered] HTTP/1.1"
    assert out["logentry"]["params"] == ["/x?nonce=[Filtered]"]
