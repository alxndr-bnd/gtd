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
    assert calls == [{"dsn": "https://key@example.ingest.sentry.io/1", "release": "gtd@0.3.0",
                      "environment": "production", "send_default_pii": False, "traces_sample_rate": 0.1}]


def test_development_without_k_service(monkeypatch):
    calls = []
    monkeypatch.setattr(A, "SENTRY_DSN", "https://key@example.ingest.sentry.io/1")
    monkeypatch.setattr(sentry_sdk, "init", lambda **kw: calls.append(kw))
    monkeypatch.delenv("K_SERVICE", raising=False)
    monkeypatch.delenv("SENTRY_RELEASE", raising=False)
    A.init_sentry()
    assert calls[0]["environment"] == "development" and calls[0]["release"] is None
