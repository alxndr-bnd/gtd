"""OAuth 2.1 для MCP-коннекторов (SERBITO-375, этап 2): claude.ai и другие клиенты входят в /mcp без копирования
токена. Дизайн — docs/plans/2026-10-03-mcp-oauth.md, правила — спецификация авторизации MCP.

gtd сам себе сервер авторизации: вход — обычный вход на сайте (Google, почта, Telegram), потом экран согласия.
- метаданные: /.well-known/oauth-protected-resource[/mcp] (RFC 9728) и /.well-known/oauth-authorization-server
  (RFC 8414); CORS только на них;
- POST /register — динамическая регистрация клиента (RFC 7591), публичные клиенты без секрета;
- GET/POST /authorize — код авторизации с PKCE (только S256), resource (RFC 8707) = BASE_URL/mcp;
- POST /token — обмен кода и ротация refresh-токенов; POST /revoke — отзыв (RFC 7009);
- /api/oauth/apps — «Подключённые приложения» в «👤 Аккаунте» (только по сессии сайта).

Токены — случайные строки, как личные: gtdo_ (доступ, 1 час) и gtdr_ (обновление, 90 дней, меняется при каждом
использовании). В базе только их sha256. Повторное использование кода или старого refresh-токена — признак кражи:
отзываем всё подключение. Ни коды, ни токены в журнал не пишутся."""
import base64
import hashlib
import hmac
import html
import logging
import re
import secrets
import time
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from mcp.shared.auth import OAuthMetadata, ProtectedResourceMetadata

import app as A
import pages

log = logging.getLogger("gtd")
router = APIRouter(include_in_schema=False)

ISSUER = A.BASE_URL                      # без «/» на конце: так его сравнивают клиенты (RFC 8414 §3.3)
RESOURCE = ISSUER + "/mcp"               # аудитория токенов (RFC 8707)
SCOPE = "tasks"                          # один scope: все задачи аккаунта (чтение и запись)
PRM_PATH = "/.well-known/oauth-protected-resource"
RESOURCE_METADATA = ISSUER + PRM_PATH + "/mcp"
DOCS_URL = pages.REPO_URL + "#connect-claude-mcp"

ACCESS_PREFIX, REFRESH_PREFIX, CLIENT_PREFIX = "gtdo_", "gtdr_", "gtdc_"
CODE_TTL = 600                # код авторизации живёт 10 минут и годится один раз
ACCESS_TTL = 3600             # токен доступа — час
REFRESH_TTL = 90 * 86400      # refresh — 90 дней, каждый раз новый
CLIENT_IDLE = 90 * 86400      # клиентов без подключений, не появлявшихся 90 дней, удаляем
GRANT_TOUCH = 60              # last_used подключения — не чаще раза в минуту
REGISTER_RATE = (10, 3600)    # регистраций с одного IP в час
TOKEN_RATE = (30, 60)         # запросов к /token на клиента в минуту
NAME_MAX, REDIRECTS_MAX, URI_MAX = 80, 5, 300
RESUME_KEY = "gtd-oauth-next"  # sessionStorage: куда SPA вернёт после входа (static/index.html, oauthResume)

# Куда можно вернуть код: только точные адреса. Claude (claude.ai и claude.com) и loopback для программ на этом
# компьютере (Claude Code, Claude Desktop, MCP Inspector; RFC 8252 §7.3) — http://localhost|127.0.0.1|[::1]
CLAUDE_CALLBACKS = ("https://claude.ai/api/mcp/auth_callback", "https://claude.com/api/mcp/auth_callback")
LOOPBACK = ("localhost", "127.0.0.1", "::1")
EXTRA_SCOPES = {"offline_access"}  # просят некоторые клиенты; refresh-токен выдаём и так
NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}
# Страницы входа и согласия: не кешировать, не индексировать. Referrer — только своим: при no-referrer браузер
# шлёт с формы согласия Origin: null, и проверка «свой сайт» её отбивает. В адресе страницы секретов нет (state и
# code_challenge), а ответ с кодом уходит с no-referrer
PAGE_HEADERS = {**A.AUTH_HEADERS, "Referrer-Policy": "same-origin", "Pragma": "no-cache"}
REDIRECT_HEADERS = {**A.AUTH_HEADERS, "Pragma": "no-cache"}
CHALLENGE_RE = re.compile(r"[A-Za-z0-9_-]{43}")       # base64url(sha256) без «=»
VERIFIER_RE = re.compile(r"[A-Za-z0-9._~-]{43,128}")  # RFC 7636 §4.1

SCHEMA = """
create table if not exists oauth_clients(
  client_id text primary key, name text not null, redirect_uris text[] not null,
  created bigint not null, last_used bigint);
create table if not exists oauth_grants(
  id bigserial primary key, user_id bigint not null,
  client_id text not null references oauth_clients(client_id) on delete cascade,
  resource text not null, scope text not null, created bigint not null, last_used bigint);
create index if not exists oauth_grants_user on oauth_grants(user_id);
create table if not exists oauth_codes(
  code_hash text primary key, client_id text not null references oauth_clients(client_id) on delete cascade,
  user_id bigint not null, redirect_uri text not null, challenge text not null, resource text not null,
  scope text not null, expires bigint not null, used bigint, grant_id bigint);
create table if not exists oauth_tokens(
  id bigserial primary key, grant_id bigint not null references oauth_grants(id) on delete cascade,
  kind text not null, token_hash text not null unique, expires bigint not null, used bigint);
create index if not exists oauth_tokens_grant on oauth_tokens(grant_id);
"""
with A._pool.connection() as _c:
    _c.execute(SCHEMA)


# ───────────────────────── Метаданные ─────────────────────────

CORS = {"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Methods": "GET, OPTIONS",
        "Access-Control-Allow-Headers": "*", "Cache-Control": "public, max-age=3600"}


def resource_metadata() -> dict:
    return ProtectedResourceMetadata(
        resource=RESOURCE, authorization_servers=[ISSUER], scopes_supported=[SCOPE], bearer_methods_supported=["header"],
        resource_name="GTD", resource_documentation=DOCS_URL).model_dump(mode="json", exclude_none=True)


def server_metadata() -> dict:
    return OAuthMetadata(
        issuer=ISSUER, authorization_endpoint=ISSUER + "/authorize", token_endpoint=ISSUER + "/token",
        registration_endpoint=ISSUER + "/register", revocation_endpoint=ISSUER + "/revoke",
        scopes_supported=[SCOPE], response_types_supported=["code"],
        grant_types_supported=["authorization_code", "refresh_token"],
        token_endpoint_auth_methods_supported=["none"], revocation_endpoint_auth_methods_supported=["none"],
        code_challenge_methods_supported=["S256"], authorization_response_iss_parameter_supported=True,
        service_documentation=DOCS_URL).model_dump(mode="json", exclude_none=True)


def metadata_response(request: Request, doc) -> Response:
    if request.method == "OPTIONS":  # preflight браузерных клиентов
        return Response(status_code=204, headers=CORS)
    return JSONResponse(doc(), headers=CORS)


@router.api_route(PRM_PATH, methods=["GET", "OPTIONS"])
@router.api_route(PRM_PATH + "/mcp", methods=["GET", "OPTIONS"])
def protected_resource(request: Request):
    return metadata_response(request, resource_metadata)


@router.api_route("/.well-known/oauth-authorization-server", methods=["GET", "OPTIONS"])
def authorization_server(request: Request):
    return metadata_response(request, server_metadata)


def challenge(error: str = "") -> str:
    """WWW-Authenticate для 401 на /mcp: где искать метаданные (RFC 9728 §5.1) и какой scope просить."""
    return (f'Bearer realm="gtd", resource_metadata="{RESOURCE_METADATA}", scope="{SCOPE}"'
            + (f', error="{error}"' if error else ""))


# ───────────────────────── Общее ─────────────────────────

def now() -> int:
    return int(time.time())


def new_secret(prefix: str = "") -> str:
    return prefix + secrets.token_urlsafe(32)


def oauth_error(error: str, desc: str = "", status: int = 400, headers: dict | None = None) -> JSONResponse:
    return JSONResponse({"error": error, **({"error_description": desc} if desc else {})}, status,
                        headers={**NO_STORE, **(headers or {})})


def too_many(window: int) -> JSONResponse:
    return oauth_error("slow_down", "too many requests", 429, {"Retry-After": str(window)})


async def form_body(request: Request) -> dict | None:
    """Тело application/x-www-form-urlencoded → {имя: значение}. Повтор параметра (RFC 6749 §3.1) или другой
    тип тела — None."""
    if request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/x-www-form-urlencoded":
        return None
    raw = (await request.body()).decode("utf-8", "replace")
    parsed = parse_qs(raw, keep_blank_values=True)
    if any(len(v) > 1 for v in parsed.values()):
        return None
    return {k: v[0] for k, v in parsed.items()}


def client_of(form: dict, request: Request) -> str:
    """client_id публичного клиента: из тела или из Basic (некоторые клиенты шлют пустой секрет так)."""
    cid = form.get("client_id", "")
    scheme, _, cred = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() == "basic" and cred:
        try:
            basic = base64.b64decode(cred).decode().partition(":")[0]
        except ValueError:
            return ""
        if cid and basic != cid:
            return ""
        cid = basic
    return cid


def client_get(cid: str) -> dict | None:
    if not cid or not cid.startswith(CLIENT_PREFIX) or len(cid) > 100:
        return None
    return A.row("select * from oauth_clients where client_id=%s", (cid,))


def same_resource(value: str) -> bool:
    """resource из запроса — это /mcp? Регистр схемы и хоста не важен, «/» на конце — тоже (RFC 8707 §2)."""
    u = urlsplit(value)
    if u.fragment:
        return False
    canon = urlunsplit((u.scheme.lower(), u.netloc.lower(), u.path.rstrip("/"), u.query, ""))
    return canon == RESOURCE.lower()  # ISSUER из BASE_URL; хост в нижнем регистре сравниваем так же


def scope_ok(value: str | None) -> bool:
    return not (set((value or SCOPE).split()) - {SCOPE} - EXTRA_SCOPES)


def pkce_ok(verifier: str, challenge_: str) -> bool:
    if not isinstance(verifier, str) or not VERIFIER_RE.fullmatch(verifier):
        return False
    calc = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode()
    return hmac.compare_digest(calc, challenge_)


def redirect_allowed(uri) -> bool:
    """Адрес возврата при регистрации: Claude или loopback по http, без логина в адресе и без #фрагмента."""
    if not isinstance(uri, str) or len(uri) > URI_MAX or any(c.isspace() for c in uri):
        return False
    if uri in CLAUDE_CALLBACKS:
        return True
    try:
        u = urlsplit(uri)
        u.port  # noqa: B018 — кривой порт бросает ValueError
    except ValueError:
        return False
    return u.scheme == "http" and u.hostname in LOOPBACK and "@" not in u.netloc and not u.fragment


def redirect_host(uri: str) -> str:
    u = urlsplit(uri)
    return u.hostname or "?"


def with_params(uri: str, **params) -> str:
    u = urlsplit(uri)
    q = urlencode({k: v for k, v in params.items() if v is not None})
    return urlunsplit(u._replace(query=f"{u.query}&{q}" if u.query else q))


def drop_grant(gid: int, why: str):
    if A.run("delete from oauth_grants where id=%s returning id", (gid,)):
        log.warning("oauth grant %s revoked: %s", gid, why)


# ───────────────────────── Регистрация (RFC 7591) ─────────────────────────

@router.post("/register")
async def register(request: Request):
    key = "oauthreg:" + A.ip_key(A.client_ip(request))
    if A.limit_hit(key, REGISTER_RATE[1]) > REGISTER_RATE[0]:
        return too_many(REGISTER_RATE[1])
    try:
        body = await request.json()
    except ValueError:
        body = None
    if not isinstance(body, dict):
        return oauth_error("invalid_client_metadata", "JSON object expected")
    uris = body.get("redirect_uris")
    if not isinstance(uris, list) or not 1 <= len(uris) <= REDIRECTS_MAX:
        return oauth_error("invalid_redirect_uri", f"redirect_uris: 1 to {REDIRECTS_MAX} URIs")
    bad = [u for u in uris if not redirect_allowed(u)]
    if bad:
        return oauth_error("invalid_redirect_uri", "only Claude's callback or a loopback http://localhost URI")
    grants = body.get("grant_types", ["authorization_code", "refresh_token"])
    if not isinstance(grants, list) or "authorization_code" not in grants \
            or set(grants) - {"authorization_code", "refresh_token"}:
        return oauth_error("invalid_client_metadata", "grant_types: authorization_code, refresh_token")
    if body.get("response_types", ["code"]) != ["code"]:
        return oauth_error("invalid_client_metadata", "response_types: code")
    if not scope_ok(body.get("scope") if isinstance(body.get("scope"), str) else None):
        return oauth_error("invalid_client_metadata", f"scope: {SCOPE}")
    name = body.get("client_name")
    name = " ".join(name.split())[:NAME_MAX] if isinstance(name, str) else ""
    cid, ts = new_secret(CLIENT_PREFIX), now()
    # Секрет публичному клиенту ни к чему (PKCE его заменяет): метод — всегда none, даже если просили другой
    # (RFC 7591 §3.2.1 разрешает серверу заменить значения)
    A.run("delete from oauth_clients c where coalesce(c.last_used, c.created) < %s "
          "and not exists (select 1 from oauth_grants g where g.client_id=c.client_id)", (ts - CLIENT_IDLE,))
    A.run("insert into oauth_clients(client_id, name, redirect_uris, created) values(%s,%s,%s,%s)",
          (cid, name or "MCP client", list(dict.fromkeys(uris)), ts))
    log.info("oauth client registered: %s", cid)
    return JSONResponse({
        "client_id": cid, "client_id_issued_at": ts, "client_name": name or "MCP client",
        "redirect_uris": list(dict.fromkeys(uris)), "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"], "token_endpoint_auth_method": "none", "scope": SCOPE}, 201, headers=NO_STORE)


# ───────────────────────── Авторизация и согласие ─────────────────────────

TEXTS = {
    "ru": {
        "consent_h": "Подключить {client} к GTD?",
        "consent_text": "{client} сможет читать, добавлять и менять задачи аккаунта <b>{who}</b>.",
        "consent_where": "После ответа вы вернётесь на <b>{host}</b>.",
        "consent_local": "После ответа вы вернётесь в программу на этом компьютере (<b>{host}</b>).",
        "consent_note": "Отключить можно в любой момент: «👤 Аккаунт → 🤖 AI-ассистенты».",
        "allow": "Разрешить", "deny": "Отказать",
        "signin_h": "Войдите, чтобы подключить {client}",
        "signin_text": "{client} просит доступ к вашим задачам в GTD. Сначала войдите, потом подтвердите доступ.",
        "signin_go": "Войти",
        "bad_h": "Ссылка подключения не работает",
        "bad_text": "Приложение прислало неверный запрос. Начните подключение заново в самом приложении.",
        "home": "На главную",
    },
    "en": {
        "consent_h": "Connect {client} to GTD?",
        "consent_text": "{client} will be able to read, add and change tasks in the account <b>{who}</b>.",
        "consent_where": "After you answer, you return to <b>{host}</b>.",
        "consent_local": "After you answer, you return to an app on this computer (<b>{host}</b>).",
        "consent_note": "You can disconnect at any time: “👤 Account → 🤖 AI assistants”.",
        "allow": "Allow", "deny": "Deny",
        "signin_h": "Sign in to connect {client}",
        "signin_text": "{client} asks for access to your tasks in GTD. Sign in first, then confirm the access.",
        "signin_go": "Sign in",
        "bad_h": "This connection link does not work",
        "bad_text": "The app sent an invalid request. Start the connection again in the app.",
        "home": "Home",
    },
}
AUTHZ_FIELDS = ("response_type", "client_id", "redirect_uri", "state", "code_challenge", "code_challenge_method",
                "scope", "resource")


class BadRequest(Exception):
    """Клиент или адрес возврата неизвестны: никуда не перенаправляем, показываем страницу (RFC 6749 §4.1.2.1)."""


class RedirectError(Exception):
    def __init__(self, uri: str, error: str, desc: str, state: str | None):
        super().__init__(error)
        self.url = with_params(uri, error=error, error_description=desc, state=state, iss=ISSUER)


def authz_request(params: dict) -> dict:
    """Проверка запроса авторизации — одна для экрана согласия (GET) и для ответа на него (POST)."""
    client = client_get(params.get("client_id", ""))
    uri = params.get("redirect_uri", "")
    if not client or uri not in client["redirect_uris"]:  # точное совпадение, без шаблонов
        raise BadRequest()
    state = params.get("state")

    def fail(error, desc):
        raise RedirectError(uri, error, desc, state)

    if params.get("response_type") != "code":
        fail("unsupported_response_type", "response_type must be code")
    if params.get("code_challenge_method") != "S256":
        fail("invalid_request", "PKCE with code_challenge_method=S256 is required")
    if not CHALLENGE_RE.fullmatch(params.get("code_challenge", "")):
        fail("invalid_request", "code_challenge is missing or malformed")
    if not scope_ok(params.get("scope")):
        fail("invalid_scope", f"supported scope: {SCOPE}")
    if params.get("resource") and not same_resource(params["resource"]):
        fail("invalid_target", f"resource must be {RESOURCE}")
    return {"client": client, "redirect_uri": uri, "state": state, "challenge": params["code_challenge"],
            "fields": {k: params[k] for k in AUTHZ_FIELDS if params.get(k) is not None}}


def page(lang: str, title: str, body: str, status: int = 200) -> HTMLResponse:
    home = pages.PATHS[(lang, "home")]
    return HTMLResponse(f"""<!doctype html>
<html lang="{lang}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex">
<title>{html.escape(title)} — {pages.SITE_NAME}</title>
{pages.ICONS}
{pages.BASE_CSS}
{pages.PUBLIC_CSS}
<style>.oauth .btn.alt{{background:none;color:inherit;border:1px solid currentColor}}.oauth .note{{opacity:.8}}</style>
</head>
<body>
<div class="pub oauth"><header class="top"><a class="brand" href="{home}">{pages.mark(28)} GTD</a></header><main>
<h1>{html.escape(title)}</h1>
{body}
</main></div>
</body>
</html>""", status_code=status, headers=PAGE_HEADERS)


def bad_page(lang: str) -> HTMLResponse:
    t = TEXTS[lang]
    return page(lang, t["bad_h"], f'<p class="lead">{t["bad_text"]}</p>'
                f'<p><a class="btn" href="{pages.PATHS[(lang, "home")]}">{t["home"]}</a></p>', 400)


def signin_page(lang: str, client: str) -> HTMLResponse:
    """Не вошёл: запоминаем этот адрес в sessionStorage и ведём на вход. SPA после входа вернёт сюда
    (oauthResume в static/index.html) — любым способом: Google, почта, Telegram."""
    t, c = TEXTS[lang], html.escape(client)
    home = pages.PATHS[(lang, "home")]
    return page(lang, t["signin_h"].format(client=client), f"""<p class="lead">{t["signin_text"].format(client=c)}</p>
<p><a class="btn" id="oauth-signin" href="{home}">{t["signin_go"]}</a></p>
<script>try{{sessionStorage.setItem({RESUME_KEY!r},JSON.stringify({{u:location.pathname+location.search,t:Date.now()}}))}}catch(e){{}}</script>""")


def consent_page(lang: str, req: dict, who: str) -> HTMLResponse:
    t, client = TEXTS[lang], req["client"]["name"]
    c, host = html.escape(client), html.escape(redirect_host(req["redirect_uri"]))
    where = t["consent_local" if redirect_host(req["redirect_uri"]) in LOOPBACK else "consent_where"]
    hidden = "\n".join(f'<input type="hidden" name="{k}" value="{html.escape(v)}">' for k, v in req["fields"].items())
    return page(lang, t["consent_h"].format(client=client), f"""<p class="lead">{t["consent_text"].format(client=c, who=html.escape(who or "?"))}</p>
<p>{where.format(host=host)}</p>
<form method="post" action="/authorize" id="consent">
{hidden}
<p><button class="btn" type="submit" name="decision" value="allow">{t["allow"]}</button>&emsp;<button class="btn alt" type="submit" name="decision" value="deny">{t["deny"]}</button></p>
</form>
<p class="note">{t["consent_note"]}</p>""")


def single_params(request: Request) -> dict | None:
    items = request.query_params.multi_items()
    keys = [k for k, _ in items]
    return None if len(keys) != len(set(keys)) else dict(items)


@router.get("/authorize")
def authorize(request: Request):
    uid = A.session_user(request)
    lang = A.req_lang(request, uid)
    params = single_params(request)
    try:
        if params is None:
            raise BadRequest()
        req = authz_request(params)
    except BadRequest:
        return bad_page(lang)
    except RedirectError as e:
        return RedirectResponse(e.url, 302, headers=REDIRECT_HEADERS)
    if not uid:
        return signin_page(lang, req["client"]["name"])
    return consent_page(lang, req, A.account_label(uid))


@router.post("/authorize")
async def authorize_answer(request: Request):
    """Ответ на экране согласия. Форма только со своей страницы: чужой Origin — 403 (cookie сессии SameSite=Lax
    к тому же не уходит с чужого сайта)."""
    headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in request.scope["headers"]}
    if A.cross_site(headers):
        return JSONResponse({"detail": "cross-site request"}, 403)
    uid = A.session_user(request)
    lang = A.req_lang(request, uid)
    form = await form_body(request)
    try:
        if form is None:
            raise BadRequest()
        req = authz_request(form)
    except BadRequest:
        return bad_page(lang)
    except RedirectError as e:
        return RedirectResponse(e.url, 303, headers=REDIRECT_HEADERS)
    if not uid:  # сессия кончилась, пока был открыт экран согласия — снова через вход
        return signin_page(lang, req["client"]["name"])
    uri, state = req["redirect_uri"], req["state"]
    if form.get("decision") != "allow":
        return RedirectResponse(with_params(uri, error="access_denied", state=state, iss=ISSUER), 303,
                                headers=REDIRECT_HEADERS)
    code, ts = new_secret(), now()
    A.run("delete from oauth_codes where expires<%s", (ts,))
    A.run("insert into oauth_codes(code_hash, client_id, user_id, redirect_uri, challenge, resource, scope, expires) "
          "values(%s,%s,%s,%s,%s,%s,%s,%s)",
          (A.token_hash(code), req["client"]["client_id"], uid, uri, req["challenge"], RESOURCE, SCOPE, ts + CODE_TTL))
    log.info("oauth consent: user %s, client %s", uid, req["client"]["client_id"])
    return RedirectResponse(with_params(uri, code=code, state=state, iss=ISSUER), 303, headers=REDIRECT_HEADERS)


# ───────────────────────── Токены ─────────────────────────

def issue(conn, gid: int, ts: int) -> dict:
    """Новая пара токенов подключения; заодно чистим его просроченные строки."""
    access, refresh = new_secret(ACCESS_PREFIX), new_secret(REFRESH_PREFIX)
    conn.execute("delete from oauth_tokens where grant_id=%s and expires<%s", (gid, ts))
    conn.execute("insert into oauth_tokens(grant_id, kind, token_hash, expires) values "
                 "(%s,'access',%s,%s), (%s,'refresh',%s,%s)",
                 (gid, A.token_hash(access), ts + ACCESS_TTL, gid, A.token_hash(refresh), ts + REFRESH_TTL))
    return {"access_token": access, "token_type": "Bearer", "expires_in": ACCESS_TTL, "refresh_token": refresh,
            "scope": SCOPE}


def exchange_code(form: dict, client: dict) -> JSONResponse:
    ts, h = now(), A.token_hash(form.get("code", ""))
    # Код — один раз: помечаем атомарно. Не вышло — код чужой, старый или уже обменян; повтор — признак кражи,
    # и подключение, выданное по этому коду, отзываем (RFC 6749 §4.1.2)
    c = A.row("update oauth_codes set used=%s where code_hash=%s and used is null returning *", (ts, h))
    if not c:
        old = A.row("select grant_id from oauth_codes where code_hash=%s", (h,))
        if old and old["grant_id"]:
            drop_grant(old["grant_id"], "authorization code reused")
        return oauth_error("invalid_grant", "invalid or used code")
    if c["expires"] < ts or c["client_id"] != client["client_id"]:
        return oauth_error("invalid_grant", "invalid or expired code")
    if form.get("redirect_uri") != c["redirect_uri"]:
        return oauth_error("invalid_grant", "redirect_uri does not match")
    if not pkce_ok(form.get("code_verifier", ""), c["challenge"]):
        return oauth_error("invalid_grant", "PKCE verification failed")
    if form.get("resource") and not same_resource(form["resource"]):
        return oauth_error("invalid_target", f"resource must be {RESOURCE}")
    if not A.row("select 1 from users where id=%s", (c["user_id"],)):
        return oauth_error("invalid_grant", "account not found")
    with A._pool.connection() as conn, conn.transaction():
        # Одно подключение на пару (аккаунт, клиент): новое согласие заменяет старое вместе с его токенами
        conn.execute("delete from oauth_grants where user_id=%s and client_id=%s", (c["user_id"], c["client_id"]))
        gid = conn.execute("insert into oauth_grants(user_id, client_id, resource, scope, created, last_used) "
                           "values(%s,%s,%s,%s,%s,%s) returning id",
                           (c["user_id"], c["client_id"], c["resource"], c["scope"], ts, ts)).fetchone()["id"]
        conn.execute("update oauth_codes set grant_id=%s where code_hash=%s", (gid, h))
        conn.execute("update oauth_clients set last_used=%s where client_id=%s", (ts, c["client_id"]))
        out = issue(conn, gid, ts)
    log.info("oauth grant %s: user %s, client %s", gid, c["user_id"], c["client_id"])
    return JSONResponse(out, headers=NO_STORE)


def refresh(form: dict, client: dict) -> JSONResponse:
    ts = now()
    t = A.row("select t.id, t.grant_id, t.expires, t.used, g.client_id from oauth_tokens t "
              "join oauth_grants g on g.id=t.grant_id where t.token_hash=%s and t.kind='refresh'",
              (A.token_hash(form.get("refresh_token", "")),))
    if not t:
        return oauth_error("invalid_grant", "invalid refresh token")
    if t["client_id"] != client["client_id"]:
        drop_grant(t["grant_id"], "refresh token presented by another client")
        return oauth_error("invalid_grant", "invalid refresh token")
    if t["expires"] < ts:
        return oauth_error("invalid_grant", "refresh token expired")
    if not scope_ok(form.get("scope")):
        return oauth_error("invalid_scope", f"supported scope: {SCOPE}")
    if form.get("resource") and not same_resource(form["resource"]):
        return oauth_error("invalid_target", f"resource must be {RESOURCE}")
    # Ротация: старый refresh гаснет. Пришёл уже использованный — его украли (или клиент сбился): отзываем всё
    if t["used"] or not A.run("update oauth_tokens set used=%s where id=%s and used is null returning id",
                              (ts, t["id"])):
        drop_grant(t["grant_id"], "refresh token reused")
        return oauth_error("invalid_grant", "refresh token already used")
    with A._pool.connection() as conn, conn.transaction():
        conn.execute("update oauth_grants set last_used=%s where id=%s", (ts, t["grant_id"]))
        conn.execute("update oauth_clients set last_used=%s where client_id=%s", (ts, t["client_id"]))
        out = issue(conn, t["grant_id"], ts)
    return JSONResponse(out, headers=NO_STORE)


@router.post("/token")
async def token(request: Request):
    form = await form_body(request)
    if form is None:
        return oauth_error("invalid_request", "application/x-www-form-urlencoded body without repeated parameters")
    cid = client_of(form, request)
    if A.limit_hit("oauthtok:" + (cid[:100] or A.ip_key(A.client_ip(request))), TOKEN_RATE[1]) > TOKEN_RATE[0]:
        return too_many(TOKEN_RATE[1])
    client = client_get(cid)
    if not client:
        return oauth_error("invalid_client", "unknown client_id", 401)
    grant = form.get("grant_type")
    if grant == "authorization_code":
        return exchange_code(form, client)
    if grant == "refresh_token":
        return refresh(form, client)
    return oauth_error("unsupported_grant_type", "authorization_code or refresh_token")


@router.post("/revoke")
async def revoke(request: Request):
    """RFC 7009: ответ 200 и для неизвестного токена. Refresh-токен отзывает всё подключение, токен доступа —
    только себя."""
    form = await form_body(request)
    if form is None or not form.get("token"):
        return oauth_error("invalid_request", "token is required")
    cid = client_of(form, request)
    t = A.row("select t.id, t.kind, t.grant_id, g.client_id from oauth_tokens t join oauth_grants g "
              "on g.id=t.grant_id where t.token_hash=%s", (A.token_hash(form["token"]),))
    if t and (not cid or cid == t["client_id"]):
        if t["kind"] == "refresh":
            A.run("delete from oauth_grants where id=%s", (t["grant_id"],))
            log.info("oauth grant %s revoked by client", t["grant_id"])
        else:
            A.run("delete from oauth_tokens where id=%s", (t["id"],))
    return Response(status_code=200, headers=NO_STORE)


def access_token_user(raw: str) -> dict | None:
    """Чей токен доступа: {id, user_id} или None (чужой, просроченный, отозванный, для другого ресурса).
    id — ключ лимита запросов: у всего подключения он общий."""
    if not raw.startswith(ACCESS_PREFIX):
        return None
    r = A.row("select t.grant_id, t.expires, g.user_id, g.resource, g.last_used from oauth_tokens t "
              "join oauth_grants g on g.id=t.grant_id where t.token_hash=%s and t.kind='access'", (A.token_hash(raw),))
    ts = now()
    if not r or r["expires"] <= ts or r["resource"] != RESOURCE:
        return None
    if (r["last_used"] or 0) < ts - GRANT_TOUCH:
        A.run("update oauth_grants set last_used=%s where id=%s", (ts, r["grant_id"]))
    return {"id": f"o{r['grant_id']}", "user_id": r["user_id"]}


# ───────────────────────── «Подключённые приложения» в «Аккаунте» ─────────────────────────

@router.get("/api/oauth/apps")
def list_apps(uid: int = Depends(A.current_user)):
    """Подключения аккаунта. Без живого refresh-токена подключение мертво — такие заодно удаляем."""
    A.run("delete from oauth_grants g where g.user_id=%s and not exists (select 1 from oauth_tokens t "
          "where t.grant_id=g.id and t.kind='refresh' and t.used is null and t.expires>%s)", (uid, now()))
    apps = A.rows("select g.id, c.name, c.redirect_uris[1] uri, g.created, g.last_used from oauth_grants g "
                  "join oauth_clients c on c.client_id=g.client_id where g.user_id=%s order by g.id", (uid,))
    return [{"id": a["id"], "name": a["name"], "host": redirect_host(a["uri"]), "created": a["created"],
             "last_used": a["last_used"]} for a in apps]


@router.delete("/api/oauth/apps/{gid}")
def disconnect_app(gid: int, uid: int = Depends(A.current_user)):
    """Отключить: подключение и все его токены удаляются, следующий запрос приложения получает 401."""
    if not A.run("delete from oauth_grants where id=%s and user_id=%s returning id", (gid, uid)):
        raise HTTPException(404)
    log.info("oauth grant %s disconnected by user %s", gid, uid)
    return {"ok": True}
