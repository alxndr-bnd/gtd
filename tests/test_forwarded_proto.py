"""SERBITO-440: TLS снимает прокси, к контейнеру приходит http. Редирект на слэш (/en → /en/) строился со схемой
http: 307 на http://…, потом 301 обратно на https.

ForwardedProto берёт схему из X-Forwarded-Proto, но только если соединение пришло от прокси (как client_ip).
Адрес клиента он не трогает — лимиты по IP считаются по-прежнему."""
import pytest
from fastapi.testclient import TestClient

import app as A

CLOUD_RUN = "169.254.169.126"  # Google Front End подключается к контейнеру с link-local
PUBLIC = "8.8.8.8"  # не 203.0.113.x: TEST-NET для ipaddress — частная сеть


def via(peer):
    return TestClient(A.app, client=(peer, 1234), follow_redirects=False)


@pytest.mark.parametrize("path, target", [("/en", "/en/"), ("/about/", "/about")])
def test_slash_redirect_keeps_https_behind_the_proxy(path, target):
    r = via(CLOUD_RUN).get(path, headers={"X-Forwarded-Proto": "https"})
    assert r.status_code == 308 and r.headers["location"] == "https://testserver" + target


def test_nearest_proxy_value_wins():
    r = via(CLOUD_RUN).get("/en", headers={"X-Forwarded-Proto": "http, https"})
    assert r.headers["location"] == "https://testserver/en/"


def test_without_the_header_the_scheme_is_unchanged():
    r = via(CLOUD_RUN).get("/en")
    assert r.headers["location"] == "http://testserver/en/"


def test_header_from_a_direct_client_is_ignored():
    r = via(PUBLIC).get("/en", headers={"X-Forwarded-Proto": "https"})
    assert r.headers["location"] == "http://testserver/en/"


def test_junk_value_is_ignored():
    r = via(CLOUD_RUN).get("/en", headers={"X-Forwarded-Proto": "javascript"})
    assert r.headers["location"] == "http://testserver/en/"


def test_from_proxy_matches_the_client_ip_trust_model():
    assert A.from_proxy(CLOUD_RUN) and A.from_proxy("127.0.0.1") and A.from_proxy("10.1.2.3")
    assert not A.from_proxy(PUBLIC) and not A.from_proxy("testclient") and not A.from_proxy("")
