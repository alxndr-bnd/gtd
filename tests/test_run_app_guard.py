"""SERBITO-430: запрос на *.run.app минует Cloudflare перед gtd.serbito.rs (WAF, Bot Fight Mode).

RunAppGuard: на run.app-хосте GET/HEAD — 301 на тот же путь на домене из BASE_URL, другие методы — 403.
Открыт только хост тега candidate---…run.app: по нему деплой прогревает ревизию (/api/config), проверяет
главную и заголовки. Другие хосты (gtd.serbito.rs, localhost) защита не трогает."""
import pytest
from fastapi.testclient import TestClient

import app as A

SERVICE_HOSTS = ["gtd-aay5lcpxha-ew.a.run.app", "gtd-488744139718.europe-west1.run.app"]
CANDIDATE = "candidate---gtd-aay5lcpxha-ew.a.run.app"
REAL = "https://gtd.serbito.rs"


@pytest.fixture(autouse=True)
def prod_target(monkeypatch):
    # В тестах BASE_URL — localhost; редирект ведёт на домен из BASE_URL, на проде это gtd.serbito.rs
    monkeypatch.setattr(A, "RUN_APP_TARGET", REAL)


def on(host):
    return TestClient(A.app, base_url=f"https://{host}", follow_redirects=False)


@pytest.mark.parametrize("host", SERVICE_HOSTS)
@pytest.mark.parametrize("path", ["/", "/en/about", "/api/config", "/i/1", "/robots.txt"])
def test_get_on_run_app_redirects_to_the_real_domain(host, path):
    r = on(host).get(path)
    assert r.status_code == 301 and r.headers["location"] == REAL + path
    assert r.headers["x-content-type-options"] == "nosniff"  # 301 тоже идёт через Guard


def test_redirect_keeps_the_encoded_path_and_the_query():
    r = on(SERVICE_HOSTS[0]).get("/en/about%20x?a=1&b=%2F")
    assert r.status_code == 301 and r.headers["location"] == REAL + "/en/about%20x?a=1&b=%2F"


def test_head_on_run_app_redirects_too():
    r = on(SERVICE_HOSTS[0]).head("/")
    assert r.status_code == 301 and r.headers["location"] == REAL + "/"


@pytest.mark.parametrize("method, path", [
    ("POST", "/tasks/reminders"), ("POST", "/tg/webhook"), ("POST", "/api/items"), ("POST", "/mcp"),
    ("POST", "/oauth/token"), ("PUT", "/api/items/1"), ("DELETE", "/api/items/1"), ("OPTIONS", "/"),
])
def test_other_methods_on_run_app_get_403(method, path):
    r = on(SERVICE_HOSTS[1]).request(method, path, headers={"content-type": "application/json"})
    assert r.status_code == 403 and REAL in r.text
    assert "location" not in r.headers


@pytest.mark.parametrize("host", ["GTD-AAY5LCPXHA-EW.A.RUN.APP", "gtd-aay5lcpxha-ew.a.run.app:443",
                                  "gtd-aay5lcpxha-ew.a.run.app."])
def test_host_case_port_and_trailing_dot_do_not_bypass(host):
    r = TestClient(A.app, follow_redirects=False).get("/", headers={"host": host})
    assert r.status_code == 301 and r.headers["location"] == REAL + "/"


@pytest.mark.parametrize("path", ["/", "/api/config"])
def test_candidate_tag_host_is_served(path):
    # Деплой: прогрев /api/config, smoke главной и проверка заголовков — по адресу тега
    r = on(CANDIDATE).get(path)
    assert r.status_code == 200
    assert on(CANDIDATE).head("/").status_code == 200


@pytest.mark.parametrize("host", ["gtd.serbito.rs", "localhost:8000", "run.app.example.com", "notrun.app"])
def test_other_hosts_are_untouched(host):
    c = TestClient(A.app, base_url=f"https://{host}", follow_redirects=False)
    assert c.get("/").status_code == 200
    assert c.get("/api/config").status_code == 200
    assert f"Use {REAL}" not in c.post("/tasks/reminders").text  # отвечает проверка крона, не RunAppGuard


def test_guard_is_off_when_base_url_itself_is_run_app(monkeypatch):
    # Self-host на Cloud Run без своего домена: редирект вёл бы сам на себя
    monkeypatch.setattr(A, "RUN_APP_GUARD", False)
    assert on(SERVICE_HOSTS[0]).get("/").status_code == 200
    assert A.run_app_host(A.host_name("my-gtd-abc-ew.a.run.app"))
    assert not A.run_app_host(A.host_name("gtd.serbito.rs"))


def test_guard_is_on_for_a_real_base_url():
    assert A.RUN_APP_GUARD is True  # тесты: BASE_URL=http://localhost:8000
    assert A.host_name("[::1]:8000") == "[::1]:8000" and not A.run_app_host(A.host_name("[::1]:8000"))
