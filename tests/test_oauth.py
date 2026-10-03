"""OAuth 2.1 для MCP-коннекторов (SERBITO-375, этап 2): метаданные, регистрация клиента, код с PKCE, токены,
ротация, отзыв, «Подключённые приложения», изоляция аккаунтов и официальный клиент SDK целиком."""
import asyncio
import base64
import hashlib
import logging
import re
import secrets
from html import unescape
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx2
import pytest
from mcp import Client
from mcp.client.auth import OAuthClientProvider
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.auth import AuthorizationCodeResult, OAuthClientMetadata

import app as A
import oauth as O
from test_mcp import call, rpc

CLAUDE = "https://claude.ai/api/mcp/auth_callback"
LOCAL = "http://localhost:33418/callback"
CHATGPT = "https://chatgpt.com/connector/oauth/abc123"
CURSOR = "cursor://anysphere.cursor-mcp/oauth/callback"
VSCODE = ["https://insiders.vscode.dev/redirect", "https://vscode.dev/redirect", "http://127.0.0.1/",
          "http://127.0.0.1:33418/"]  # что VS Code шлёт в /register
BASE = "http://localhost:8000"  # BASE_URL тестов (conftest): issuer и resource строятся от него
RESOURCE = BASE + "/mcp"


def pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    return verifier, base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


def register(c, uris=(CLAUDE,), **extra):
    return c.post("/register", json={"redirect_uris": list(uris), "client_name": "Claude", **extra})


def client_id(c, uris=(CLAUDE,)) -> str:
    r = register(c, uris)
    assert r.status_code == 201, r.text
    return r.json()["client_id"]


def authz(cid, challenge, redirect=CLAUDE, **kw) -> dict:
    return {"response_type": "code", "client_id": cid, "redirect_uri": redirect, "state": "st-1",
            "code_challenge": challenge, "code_challenge_method": "S256", "scope": "tasks", "resource": RESOURCE,
            **kw}


def form_fields(page: str) -> dict:
    return {m[0]: unescape(m[1]) for m in re.findall(r'<input type="hidden" name="(\w+)" value="([^"]*)">', page)}


def answer(web, params, decision="allow", **headers):
    """Экран согласия и ответ на нём: открыть /authorize, отправить его же форму. Возвращает ответ POST."""
    page = web.get("/authorize?" + urlencode(params), follow_redirects=False)
    assert page.status_code == 200, page.text
    return web.post("/authorize", data={**form_fields(page.text), "decision": decision}, follow_redirects=False,
                    headers=headers)


def back(r) -> dict:
    """Куда сервер вернул пользователя: {параметр: значение} из Location."""
    assert r.status_code in (302, 303), r.text
    return {k: v[0] for k, v in parse_qs(urlsplit(r.headers["location"]).query).items()}


def get_code(web, cid, redirect=CLAUDE) -> tuple[str, str]:
    verifier, challenge = pkce()
    q = back(answer(web, authz(cid, challenge, redirect)))
    assert q["state"] == "st-1" and q["iss"] == BASE, q
    return q["code"], verifier


def token(c, **form):
    return c.post("/token", data=form)


def exchange(c, cid, code, verifier, redirect=CLAUDE, resource=RESOURCE):
    return token(c, grant_type="authorization_code", client_id=cid, code=code, code_verifier=verifier,
                 redirect_uri=redirect, resource=resource)


def connect(web, c, cid=None) -> dict:
    """Весь путь до токенов: регистрация, согласие, обмен кода. {client_id, access_token, refresh_token, …}."""
    cid = cid or client_id(c)
    code, verifier = get_code(web, cid)
    r = exchange(c, cid, code, verifier)
    assert r.status_code == 200, r.text
    return {"client_id": cid, **r.json()}


def refresh(c, cid, rt):
    return token(c, grant_type="refresh_token", client_id=cid, refresh_token=rt, resource=RESOURCE)


@pytest.fixture
def web(new_client, login):
    """Алиса вошла на сайте."""
    c = new_client()
    c.uid = login(c, "alice@example.com")
    return c


# ── метаданные и 401 ──

def test_metadata_documents(client):
    for path in ("/.well-known/oauth-protected-resource", "/.well-known/oauth-protected-resource/mcp"):
        r = client.get(path)
        assert r.status_code == 200 and r.headers["access-control-allow-origin"] == "*"
        d = r.json()
        assert d["resource"] == RESOURCE and d["authorization_servers"] == [BASE] and d["scopes_supported"] == ["tasks"]
    d = client.get("/.well-known/oauth-authorization-server").json()
    assert d["issuer"] == BASE
    assert {d[k] for k in ("authorization_endpoint", "token_endpoint", "registration_endpoint",
                           "revocation_endpoint")} == {BASE + p for p in ("/authorize", "/token", "/register", "/revoke")}
    assert d["code_challenge_methods_supported"] == ["S256"]
    assert d["grant_types_supported"] == ["authorization_code", "refresh_token"]
    assert d["token_endpoint_auth_methods_supported"] == ["none"]
    assert d["authorization_response_iss_parameter_supported"] is True
    pre = client.options("/.well-known/oauth-authorization-server", headers={
        "origin": "https://claude.ai", "access-control-request-method": "GET"})
    assert pre.status_code == 204 and pre.headers["access-control-allow-origin"] == "*"
    # CORS — только у метаданных
    assert "access-control-allow-origin" not in register(client).headers


def test_mcp_401_points_to_resource_metadata(client):
    for tok in (None, "gtdo_wrong"):
        r = rpc(client, tok, "tools/list")
        assert r.status_code == 401
        assert f'resource_metadata="{BASE}/.well-known/oauth-protected-resource/mcp"' in r.headers["www-authenticate"]


# ── регистрация клиента ──

@pytest.mark.parametrize("uri", [
    "http://claude.ai/api/mcp/auth_callback", "http://example.com/callback",  # http — только loopback
    "https://localhost/cb", "https://127.0.0.1/cb", "https://intranet/cb",    # https — только обычное имя хоста
    "https://localhost:8000/cb",                                              # наш сайт (BASE_URL тестов)
    "http://user@localhost:3000/cb", "https://user:pw@example.com/cb",         # логин в адресе
    "http://localhost:3000/cb#x", "https://example.com/cb#", "https://example.com/cb#frag",  # фрагмент
    "https://*.example.com/cb", "https://example.com/*", "https://example.com/cb?x=*",      # шаблоны
    "javascript:alert(1)", "data:text/html,<b>x</b>", "file:///etc/passwd", "ftp://example.com/cb",
    "https:example.com/cb", "https:/example.com/cb", "https://", "//example.com/cb", "example.com/cb",
    "https://exa mple.com/cb", "https://example.com/c b", "https://example.com\\@evil.com/cb",
    "https://пример.рф/cb", "https://example.com/cb\n", "https://-bad-.example.com/cb", "https://example..com/cb",
    "http://localhost:99999/cb", "https://example.com:99999/cb",
    "evil://callback", "claude://callback", "cursor-evil://anysphere.cursor-mcp/cb",          # схема не из списка
    "cursor:anysphere.cursor-mcp/oauth/callback", "cursor:///oauth/callback",                  # у схемы нет хоста
    "cursor://anysphere.cursor-mcp:1234/cb", "cursor://user@anysphere.cursor-mcp/cb", "vscode://x/cb#y",
    "https://example.com/" + "x" * 300, 5, None])
def test_register_rejects_bad_redirect_uri(client, uri):
    r = client.post("/register", json={"redirect_uris": [uri]})
    assert r.status_code == 400 and r.json()["error"] == "invalid_redirect_uri", uri


@pytest.mark.parametrize("uris", [[], [CLAUDE, "https://evil.example/cb#x"], [CHATGPT] * 11, "http://localhost/cb"])
def test_register_rejects_bad_redirect_list(client, uris):
    r = client.post("/register", json={"redirect_uris": uris})
    assert r.status_code == 400 and r.json()["error"] == "invalid_redirect_uri"


@pytest.mark.parametrize("uris", [
    [CHATGPT], ["https://chatgpt.com/connector_platform_oauth_redirect"], VSCODE, [CURSOR],
    [CURSOR, "http://localhost:8787/callback"], ["vscode://vscode.github-authentication/did-authenticate"],
    ["vscode-insiders://ms-vscode.mcp/callback"], ["https://claude.ai/api/organizations/custom-connectors/oauth/callback"],
    ["https://evil.example/cb"], ["https://app.example.com:8443/oauth/cb?tenant=1"], ["http://[::1]/cb"]])
def test_register_accepts_other_clients(client, uris):
    """SERBITO-375: другие клиенты тоже — ChatGPT, VS Code, Cursor и любой https-хост. Ворота — согласие."""
    r = client.post("/register", json={"redirect_uris": uris, "client_name": "Other"})
    assert r.status_code == 201, r.text
    assert r.json()["redirect_uris"] == uris


def test_register(client):
    r = register(client, [CLAUDE, "https://claude.com/api/mcp/auth_callback", LOCAL, "http://127.0.0.1:5000/cb",
                          "http://[::1]:5000/cb"], token_endpoint_auth_method="client_secret_post")
    assert r.status_code == 201 and r.headers["cache-control"] == "no-store"
    d = r.json()
    assert d["client_id"].startswith("gtdc_") and "client_secret" not in d
    assert d["token_endpoint_auth_method"] == "none" and d["client_name"] == "Claude"  # секрет не нужен: PKCE
    assert A.row("select name, redirect_uris from oauth_clients")["redirect_uris"][0] == CLAUDE
    for bad in ({"redirect_uris": [CLAUDE], "grant_types": ["client_credentials"]},
                {"redirect_uris": [CLAUDE], "response_types": ["token"]},
                {"redirect_uris": [CLAUDE], "scope": "admin"}):
        assert client.post("/register", json=bad).json()["error"] == "invalid_client_metadata"
    assert client.post("/register", content="x", headers={"content-type": "application/json"}).status_code == 400
    assert client.post("/register", json={"redirect_uris": [CLAUDE]}).json()["client_name"] == "MCP client"


def test_register_rate_limited_per_ip(client, monkeypatch):
    monkeypatch.setattr(O, "REGISTER_RATE", (2, 3600))
    assert [register(client).status_code for _ in range(3)] == [201, 201, 429]


def test_unused_clients_are_cleaned_up(client, web):
    old = client_id(client)
    used = connect(web, client)["client_id"]
    A.run("update oauth_clients set created=1, last_used=1")
    client_id(client)  # регистрация чистит давно не появлявшихся клиентов без подключений
    left = {r["client_id"] for r in A.rows("select client_id from oauth_clients")}
    assert old not in left and used in left


# ── авторизация и согласие ──

def test_signed_out_user_goes_to_sign_in_first(client):
    cid = client_id(client)
    r = client.get("/authorize?" + urlencode(authz(cid, pkce()[1])), headers={"accept-language": "en"})
    assert r.status_code == 200 and "Sign in to connect Claude" in r.text
    assert O.RESUME_KEY in r.text and r.headers["cache-control"] == "no-store"
    assert not A.rows("select 1 from oauth_codes")


def test_consent_page(web, client):
    cid = client_id(client, [CLAUDE, LOCAL])
    _, challenge = pkce()
    r = web.get("/authorize?" + urlencode(authz(cid, challenge)))
    assert r.status_code == 200 and "Подключить Claude к GTD?" in r.text
    assert "<b>claude.ai</b>" in r.text and "a***@example.com" in r.text  # адрес возврата и аккаунт — на виду
    assert r.headers["x-frame-options"] == "DENY" and r.headers["referrer-policy"] == "same-origin"
    assert 'id="oauth-warn"' not in r.text  # адрес Claude: код уходит Claude, кто бы ни назвался
    A.run("update users set lang='en'")
    r = web.get("/authorize?" + urlencode(authz(cid, challenge, LOCAL)))
    assert "Connect Claude to GTD?" in r.text and "an app on this computer (<b>localhost</b>)" in r.text
    assert 'id="oauth-warn"' in r.text and "Any program on this computer" in r.text  # loopback: предупреждение


@pytest.mark.parametrize("lang, uri, host, text", [
    ("en", "https://evil.example/cb", "evil.example", "After you answer, you return to <b>evil.example</b>."),
    ("ru", "https://evil.example/cb", "evil.example", "После ответа вы вернётесь на <b>evil.example</b>."),
    ("en", CHATGPT, "chatgpt.com", "you return to <b>chatgpt.com</b>"),
    ("en", CURSOR, "cursor://anysphere.cursor-mcp", "the Cursor app opens on this computer"),
    ("ru", "vscode://ms-vscode.mcp/cb", "vscode://ms-vscode.mcp", "откроется программа VS Code на этом компьютере"),
])
def test_consent_shows_client_and_redirect_host(web, client, lang, uri, host, text):
    """Название клиента — с его слов, поэтому рядом хост, куда уйдёт код, и предупреждение. Здесь клиент назвался
    «Claude», а код просит на чужой адрес."""
    A.run("update users set lang=%s", (lang,))
    cid = client_id(client, [uri])
    r = web.get("/authorize?" + urlencode(authz(cid, pkce()[1], uri)))
    assert r.status_code == 200, r.text
    assert ("Connect Claude to GTD?" if lang == "en" else "Подключить Claude к GTD?") in r.text
    assert text in r.text
    warn = re.search(r'<p class="warn" id="oauth-warn">(.*?)</p>', r.text).group(1)
    assert f"<b>{host}</b>" in warn and "Claude" in warn
    assert ("does not check it" if lang == "en" else "GTD его не проверяет") in warn


def test_client_name_is_escaped(web, client):
    cid = client.post("/register", json={"redirect_uris": [CLAUDE], "client_name": "<script>x</script>"}
                      ).json()["client_id"]
    r = web.get("/authorize?" + urlencode(authz(cid, pkce()[1])))
    assert "<script>x" not in r.text and "&lt;script&gt;x" in r.text


@pytest.mark.parametrize("change", [{"client_id": "gtdc_unknown"}, {"redirect_uri": "https://evil.example/cb"},
                                    {"redirect_uri": CLAUDE + "/"}, {"redirect_uri": ""},
                                    {"redirect_uri": "https://claude.ai/api/mcp/auth_callback?x=1"},
                                    {"redirect_uri": "https://claude.ai:8443/api/mcp/auth_callback"},
                                    {"redirect_uri": "HTTPS://claude.ai/api/mcp/auth_callback"},
                                    {"redirect_uri": "http://localhost:33418/callback"}])
def test_unknown_client_or_redirect_never_redirects(web, client, change):
    cid = client_id(client)
    r = web.get("/authorize?" + urlencode({**authz(cid, pkce()[1]), **change}), follow_redirects=False)
    assert r.status_code == 400 and "location" not in r.headers


def test_repeated_parameter_rejected(web, client):
    cid = client_id(client)
    q = urlencode(authz(cid, pkce()[1])) + "&redirect_uri=" + LOCAL
    assert web.get("/authorize?" + q, follow_redirects=False).status_code == 400


@pytest.mark.parametrize("change, error", [
    ({"response_type": "token"}, "unsupported_response_type"),
    ({"code_challenge_method": "plain"}, "invalid_request"),
    ({"code_challenge_method": ""}, "invalid_request"),
    ({"code_challenge": "short"}, "invalid_request"),
    ({"scope": "tasks admin"}, "invalid_scope"),
    ({"resource": "https://other.example/mcp"}, "invalid_target"),
])
def test_bad_authorization_request_redirects_with_error(web, client, change, error):
    cid = client_id(client)
    q = back(web.get("/authorize?" + urlencode({**authz(cid, pkce()[1]), **change}), follow_redirects=False))
    assert (q["error"], q["state"], q["iss"]) == (error, "st-1", BASE)
    assert not A.rows("select 1 from oauth_codes")


def test_deny(web, client):
    cid = client_id(client)
    q = back(answer(web, authz(cid, pkce()[1]), decision="deny"))
    assert q["error"] == "access_denied" and "code" not in q
    assert not A.rows("select 1 from oauth_codes")


def test_consent_post_rejected_cross_site(web, client):
    cid = client_id(client)
    r = answer(web, authz(cid, pkce()[1]), origin="https://evil.example")
    assert r.status_code == 403 and not A.rows("select 1 from oauth_codes")


def test_consent_post_without_session_asks_to_sign_in(web, client, new_client):
    cid = client_id(client)
    page = web.get("/authorize?" + urlencode(authz(cid, pkce()[1])))
    r = new_client().post("/authorize", data={**form_fields(page.text), "decision": "allow"}, follow_redirects=False)
    assert r.status_code == 200 and "Войдите" in r.text and not A.rows("select 1 from oauth_codes")


def test_code_stored_only_as_hash(web, client):
    code, _ = get_code(web, client_id(client))
    stored = A.row("select * from oauth_codes")
    assert stored["code_hash"] == A.token_hash(code) and not any(code in str(v) for v in stored.values())


# ── полный путь ──

def test_full_flow(web, client):
    """register → authorize (PKCE) → token → /mcp → refresh → revoke."""
    A.capture(web.uid, "alice task")
    t = connect(web, client)
    assert t["token_type"] == "Bearer" and t["expires_in"] == 3600 and t["scope"] == "tasks"
    at, rt = t["access_token"], t["refresh_token"]
    assert at.startswith("gtdo_") and rt.startswith("gtdr_")
    stored = A.rows("select token_hash from oauth_tokens")
    assert {s["token_hash"] for s in stored} == {A.token_hash(at), A.token_hash(rt)}  # в базе — только хеши
    assert [x["title"] for x in call(client, at, "list_tasks")["tasks"]] == ["alice task"]
    assert call(client, at, "capture", text="via oauth")["number"] == 2

    r = refresh(client, t["client_id"], rt)
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    t2 = r.json()
    assert t2["refresh_token"] != rt and t2["access_token"] != at
    assert call(client, t2["access_token"], "list_tasks")["total"] == 2

    r = client.post("/revoke", data={"token": t2["refresh_token"], "client_id": t["client_id"]})
    assert r.status_code == 200
    for tok in (at, t2["access_token"]):
        assert rpc(client, tok, "tools/list").status_code == 401  # отзыв refresh гасит всё подключение
    assert refresh(client, t["client_id"], t2["refresh_token"]).json()["error"] == "invalid_grant"
    assert not A.rows("select 1 from oauth_grants")


def test_loopback_client_flow(web, client):
    cid = client_id(client, [LOCAL])
    code, verifier = get_code(web, cid, LOCAL)
    r = exchange(client, cid, code, verifier, LOCAL)
    assert r.status_code == 200 and rpc(client, r.json()["access_token"], "tools/list").status_code == 200


def flow(web, client, cid, redirect) -> str:
    """Согласие → код на redirect → токен → вызов /mcp. Возвращает Location с кодом."""
    verifier, challenge = pkce()
    r = answer(web, authz(cid, challenge, redirect))
    loc = r.headers["location"]
    q = back(r)
    assert q["state"] == "st-1" and q["iss"] == BASE
    t = exchange(client, cid, q["code"], verifier, redirect)
    assert t.status_code == 200, t.text
    assert call(client, t.json()["access_token"], "capture", text="from " + redirect)["number"] >= 1
    return loc


@pytest.mark.parametrize("redirect", [CHATGPT, "https://app.example.com:8443/oauth/cb?tenant=1"])
def test_full_flow_other_https_client(web, client, redirect):
    """Не-Claude клиент с https: код уходит ровно на зарегистрированный адрес, свои параметры адреса целы."""
    cid = client_id(client, [redirect])
    loc = flow(web, client, cid, redirect)
    assert loc.startswith(redirect + ("&" if "?" in redirect else "?") + "code=")
    apps = web.get("/api/oauth/apps").json()
    assert [a["host"] for a in apps] == [urlsplit(redirect).hostname]


def test_full_flow_cursor_custom_scheme(web, client):
    cid = client_id(client, [CURSOR])
    assert flow(web, client, cid, CURSOR).startswith(CURSOR + "?code=")
    assert web.get("/api/oauth/apps").json()[0]["host"] == "cursor://anysphere.cursor-mcp"


def test_loopback_any_port(web, client):
    """RFC 8252 §7.3: VS Code регистрирует http://127.0.0.1/ и 127.0.0.1:33418, а входит с любого свободного
    порта. Порт свободен, остальное — точно; токен — только на тот же адрес, что и код."""
    cid = client_id(client, VSCODE)
    for redirect in ("http://127.0.0.1:59656/", "http://127.0.0.1/", "http://127.0.0.1:33418/"):
        assert flow(web, client, cid, redirect).startswith(redirect + "?code=")
    verifier, challenge = pkce()
    code = back(answer(web, authz(cid, challenge, "http://127.0.0.1:40000/")))["code"]
    assert exchange(client, cid, code, verifier, "http://127.0.0.1:40001/").json()["error"] == "invalid_grant"
    assert web.get("/api/oauth/apps").json()[0]["host"] == "127.0.0.1"
    for bad in ("http://localhost:59656/", "http://127.0.0.1:59656/cb", "http://127.0.0.1:59656/?x=1",
                "https://127.0.0.1:59656/", "http://[::1]:59656/"):
        r = web.get("/authorize?" + urlencode(authz(cid, pkce()[1], bad)), follow_redirects=False)
        assert r.status_code == 400 and "location" not in r.headers, bad


def test_port_is_exact_for_non_loopback(web, client):
    cid = client_id(client, ["https://app.example.com/cb"])
    r = web.get("/authorize?" + urlencode(authz(cid, pkce()[1], "https://app.example.com:444/cb")),
                follow_redirects=False)
    assert r.status_code == 400 and "location" not in r.headers


def test_client_id_in_basic_auth(web, client):
    cid = client_id(client)
    code, verifier = get_code(web, cid)
    basic = "Basic " + base64.b64encode(f"{cid}:".encode()).decode()
    r = client.post("/token", data={"grant_type": "authorization_code", "code": code, "code_verifier": verifier,
                                    "redirect_uri": CLAUDE}, headers={"authorization": basic})
    assert r.status_code == 200, r.text


def test_wrong_verifier(web, client):
    cid = client_id(client)
    code, verifier = get_code(web, cid)
    for bad in (pkce()[0], "short", verifier + "x"):
        r = exchange(client, cid, code, bad)
        assert r.status_code == 400 and r.json()["error"] == "invalid_grant"
    # Код сгорел на первой же попытке: верный verifier после неверного уже не поможет
    assert exchange(client, cid, code, verifier).json()["error"] == "invalid_grant"
    assert not A.rows("select 1 from oauth_tokens")


def test_wrong_redirect_at_token(web, client):
    cid = client_id(client, [CLAUDE, LOCAL])
    code, verifier = get_code(web, cid)
    assert exchange(client, cid, code, verifier, LOCAL).json()["error"] == "invalid_grant"


def test_code_bound_to_client_and_resource(web, client):
    cid, other = client_id(client), client_id(client)
    code, verifier = get_code(web, cid)
    assert exchange(client, other, code, verifier).json()["error"] == "invalid_grant"
    code, verifier = get_code(web, cid)
    r = exchange(client, cid, code, verifier, resource="https://other.example/mcp")
    assert r.json()["error"] == "invalid_target"
    r = token(client, grant_type="authorization_code", client_id="gtdc_nobody", code=code, code_verifier=verifier,
              redirect_uri=CLAUDE)
    assert r.status_code == 401 and r.json()["error"] == "invalid_client"


def test_reused_code_revokes_the_grant(web, client):
    cid = client_id(client)
    code, verifier = get_code(web, cid)
    at = exchange(client, cid, code, verifier).json()["access_token"]
    assert rpc(client, at, "tools/list").status_code == 200
    r = exchange(client, cid, code, verifier)
    assert r.status_code == 400 and r.json()["error"] == "invalid_grant"
    assert rpc(client, at, "tools/list").status_code == 401  # код украли — выданное по нему отозвано


def test_expired_code(web, client):
    cid = client_id(client)
    code, verifier = get_code(web, cid)
    A.run("update oauth_codes set expires=%s", (int(A.time.time()) - 1,))
    assert exchange(client, cid, code, verifier).json()["error"] == "invalid_grant"


def test_expired_access_token(web, client):
    t = connect(web, client)
    A.run("update oauth_tokens set expires=%s where kind='access'", (int(A.time.time()) - 1,))
    r = rpc(client, t["access_token"], "tools/list")
    assert r.status_code == 401 and 'error="invalid_token"' in r.headers["www-authenticate"]
    t2 = refresh(client, t["client_id"], t["refresh_token"]).json()  # клиент обновляет токен и продолжает
    assert rpc(client, t2["access_token"], "tools/list").status_code == 200


def test_expired_refresh_token(web, client):
    t = connect(web, client)
    A.run("update oauth_tokens set expires=%s where kind='refresh'", (int(A.time.time()) - 1,))
    assert refresh(client, t["client_id"], t["refresh_token"]).json()["error"] == "invalid_grant"


def test_refresh_reuse_revokes_the_grant(web, client):
    t = connect(web, client)
    t2 = refresh(client, t["client_id"], t["refresh_token"]).json()
    r = refresh(client, t["client_id"], t["refresh_token"])  # старый refresh — второй раз
    assert r.status_code == 400 and r.json()["error"] == "invalid_grant"
    assert rpc(client, t2["access_token"], "tools/list").status_code == 401
    assert refresh(client, t["client_id"], t2["refresh_token"]).json()["error"] == "invalid_grant"


def test_refresh_by_another_client_revokes_the_grant(web, client):
    t = connect(web, client)
    other = client_id(client)
    assert refresh(client, other, t["refresh_token"]).json()["error"] == "invalid_grant"
    assert rpc(client, t["access_token"], "tools/list").status_code == 401


def test_access_token_is_not_a_refresh_token(web, client):
    t = connect(web, client)
    assert refresh(client, t["client_id"], t["access_token"]).json()["error"] == "invalid_grant"
    assert rpc(client, t["refresh_token"], "tools/list").status_code == 401


def test_token_endpoint_errors(client):
    assert client.post("/token", json={"grant_type": "x"}).json()["error"] == "invalid_request"
    cid = client_id(client)
    assert token(client, grant_type="password", client_id=cid).json()["error"] == "unsupported_grant_type"
    r = client.post("/token", content=f"grant_type=refresh_token&client_id={cid}&client_id={cid}",
                    headers={"content-type": "application/x-www-form-urlencoded"})
    assert r.json()["error"] == "invalid_request"


def test_token_rate_limited_per_client(client, monkeypatch):
    monkeypatch.setattr(O, "TOKEN_RATE", (2, 60))
    cid = client_id(client)
    codes = [token(client, grant_type="authorization_code", client_id=cid, code="x").status_code for _ in range(3)]
    assert codes == [400, 400, 429]


def test_revoke_access_token_only(web, client):
    t = connect(web, client)
    assert client.post("/revoke", data={"token": t["access_token"]}).status_code == 200
    assert rpc(client, t["access_token"], "tools/list").status_code == 401
    assert refresh(client, t["client_id"], t["refresh_token"]).status_code == 200  # подключение живо


def test_revoke_unknown_or_foreign_token(web, client):
    assert client.post("/revoke", data={"token": "gtdr_nothing"}).status_code == 200
    assert client.post("/revoke", data={}).status_code == 400
    t = connect(web, client)
    other = client_id(client)
    assert client.post("/revoke", data={"token": t["refresh_token"], "client_id": other}).status_code == 200
    assert rpc(client, t["access_token"], "tools/list").status_code == 200  # чужой клиент не отзывает


def test_new_consent_replaces_the_old_grant(web, client):
    t = connect(web, client)
    t2 = connect(web, client, t["client_id"])
    assert rpc(client, t["access_token"], "tools/list").status_code == 401
    assert rpc(client, t2["access_token"], "tools/list").status_code == 200
    assert A.row("select count(*) n from oauth_grants")["n"] == 1


def test_oauth_tokens_rate_limited_per_grant(web, client, monkeypatch):
    monkeypatch.setattr(__import__("mcp_server"), "MCP_RATE", (2, 60))
    t = connect(web, client)
    assert [rpc(client, t["access_token"], "tools/list").status_code for _ in range(3)] == [200, 200, 429]


def test_oauth_token_does_not_open_the_api(web, client):
    t = connect(web, client)
    assert client.get("/api/items", headers={"authorization": f"Bearer {t['access_token']}"}).status_code == 401


# ── изоляция ──

def test_cross_user_isolation(web, client, new_client, login):
    bob = new_client()
    bob_uid = login(bob, "bob@example.com")
    A.capture(web.uid, "alice secret")
    A.capture(bob_uid, "bob secret")
    cid = client_id(client)  # один и тот же клиент (Claude) у обоих
    ta, tb = connect(web, client, cid), connect(bob, client, cid)
    assert [x["title"] for x in call(client, ta["access_token"], "list_tasks")["tasks"]] == ["alice secret"]
    assert [x["title"] for x in call(client, tb["access_token"], "list_tasks")["tasks"]] == ["bob secret"]
    call(client, tb["access_token"], "complete_task", number=1)
    assert A.item_by_num(web.uid, 1)["status"] == "inbox"
    assert A.row("select count(*) n from oauth_grants")["n"] == 2  # подключение Боба не заменило Алисино
    gid = web.get("/api/oauth/apps").json()[0]["id"]
    assert bob.delete(f"/api/oauth/apps/{gid}").status_code == 404  # чужое не отключить
    assert rpc(client, ta["access_token"], "tools/list").status_code == 200


# ── «Подключённые приложения» ──

def test_connected_apps_and_disconnect(web, client):
    assert web.get("/api/oauth/apps").json() == []
    t = connect(web, client)
    apps = web.get("/api/oauth/apps").json()
    assert [(a["name"], a["host"]) for a in apps] == [("Claude", "claude.ai")]
    assert apps[0]["created"] and apps[0]["last_used"]
    assert web.delete(f"/api/oauth/apps/{apps[0]['id']}").json() == {"ok": True}
    assert web.get("/api/oauth/apps").json() == []
    assert rpc(client, t["access_token"], "tools/list").status_code == 401
    assert refresh(client, t["client_id"], t["refresh_token"]).json()["error"] == "invalid_grant"
    assert web.delete(f"/api/oauth/apps/{apps[0]['id']}").status_code == 404


def test_connected_apps_need_a_session(client, web):
    connect(web, client)
    assert client.get("/api/oauth/apps").status_code == 401
    gid = web.get("/api/oauth/apps").json()[0]["id"]
    assert client.delete(f"/api/oauth/apps/{gid}").status_code == 401
    r = web.delete(f"/api/oauth/apps/{gid}", headers={"origin": "https://evil.example"})
    assert r.status_code == 403


def test_dead_grants_drop_from_the_list(web, client):
    connect(web, client)
    A.run("update oauth_tokens set expires=1 where kind='refresh'")
    assert web.get("/api/oauth/apps").json() == []


def test_grants_follow_account_merge(web, client, new_client, login):
    bob = new_client()
    bob_uid = login(bob, "bob@example.com")
    t = connect(bob, client)
    A.merge_accounts(web.uid, bob_uid)
    assert call(client, t["access_token"], "capture", text="after merge")["number"] == 1
    assert A.row("select user_id from items")["user_id"] == web.uid
    assert [a["name"] for a in web.get("/api/oauth/apps").json()] == ["Claude"]


def test_tokens_and_codes_never_logged(web, client, caplog):
    caplog.set_level(logging.DEBUG)
    cid = client_id(client)
    code, verifier = get_code(web, cid)
    t = exchange(client, cid, code, verifier).json()
    refresh(client, cid, t["refresh_token"])
    call(client, t["access_token"], "list_tasks")
    for secret in (code, verifier, t["access_token"], t["refresh_token"]):
        assert secret not in caplog.text


# ── официальный клиент SDK: весь путь OAuth, как у MCP-клиентов ──

class Memory:
    def __init__(self):
        self.tokens = self.client = None

    async def get_tokens(self):
        return self.tokens

    async def set_tokens(self, tokens):
        self.tokens = tokens

    async def get_client_info(self):
        return self.client

    async def set_client_info(self, info):
        self.client = info


@pytest.mark.parametrize("mode", ["legacy", "auto"])
def test_official_client_oauth(web, mode):
    """Клиент SDK сам находит метаданные по 401, регистрируется, проходит согласие (за пользователя — браузер
    с его сессией) и вызывает инструменты с токеном доступа."""
    A.capture(web.uid, "found via oauth")
    got: dict = {}

    async def run():
        transport = httpx2.ASGITransport(app=A.app)
        browser = httpx2.AsyncClient(transport=transport, base_url=BASE, cookies=dict(web.cookies))

        async def redirect_handler(url: str):
            page = await browser.get(url)
            assert page.status_code == 200, page.text
            r = await browser.post("/authorize", data={**form_fields(page.text), "decision": "allow"})
            got.update({k: v[0] for k, v in parse_qs(urlsplit(r.headers["location"]).query).items()})

        async def callback_handler():
            return AuthorizationCodeResult(code=got["code"], state=got["state"], iss=got.get("iss"))

        storage = Memory()
        auth = OAuthClientProvider(
            RESOURCE, OAuthClientMetadata(redirect_uris=[LOCAL], client_name="SDK test", scope="tasks",
                                          token_endpoint_auth_method="none"),
            storage, redirect_handler=redirect_handler, callback_handler=callback_handler)
        http = httpx2.AsyncClient(transport=transport, base_url=BASE, auth=auth)
        async with browser, http, Client(streamable_http_client(RESOURCE, http_client=http), mode=mode) as c:
            listed = await c.call_tool("list_tasks", {})
        return listed, storage
    listed, storage = asyncio.run(run())
    assert [t["title"] for t in listed.structured_content["tasks"]] == ["found via oauth"]
    assert storage.tokens.access_token.startswith("gtdo_") and storage.client.client_id.startswith("gtdc_")
    assert A.row("select name from oauth_clients")["name"] == "SDK test"
