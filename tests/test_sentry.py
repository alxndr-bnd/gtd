"""Sentry включается только с DSN; релиз и окружение — из env Cloud Run."""
import sentry_sdk

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
