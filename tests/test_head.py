"""HEAD на публичных адресах (SERBITO-325): мониторинги аптайма и превью ссылок сперва шлют HEAD.
Ответ — тот же статус и заголовки, что у GET, без тела. Проверяем на уровне ASGI: TestClient (httpx)
сам выбрасывает тело у HEAD и не заметил бы, что приложение его всё-таки отдало."""
import asyncio
import time

import pytest

import app as A

PATHS = ["/", "/en/", "/about", "/en/about", "/privacy", "/en/privacy", "/i/1", "/manifest.webmanifest",
         "/robots.txt", "/sitemap.xml", *("/" + name for name in A.ROOT_FILES)]
HTML = [(b"accept", b"text/html")]


def call(method, path, headers=(), query=b""):
    """Запрос прямо в ASGI-приложение: (статус, заголовки, все куски тела как отправило приложение)."""
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method, "scheme": "http",
             "path": path, "raw_path": path.encode(), "root_path": "", "query_string": query,
             "headers": [(b"host", b"localhost:8000"), *headers], "client": ("127.0.0.1", 1), "server": ("localhost", 8000)}
    sent = []

    async def run():
        got = asyncio.Event()

        async def receive():
            if got.is_set():
                await asyncio.Event().wait()  # клиент не отключается, пока приложение само не закончит
            got.set()
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(msg):
            sent.append(msg)

        await A.app(scope, receive, send)

    asyncio.run(run())
    start = sent[0]
    assert start["type"] == "http.response.start"
    return start["status"], sorted(start["headers"]), b"".join(m.get("body", b"") for m in sent[1:])


@pytest.mark.parametrize("path", PATHS)
def test_head_like_get_without_body(path):
    status, headers, body = call("GET", path, HTML)
    assert status == 200 and body
    assert call("HEAD", path, HTML) == (status, headers, b"")  # Content-Type, Cache-Control, X-Robots-Tag, длина — как у GET


def test_head_keeps_page_headers():
    h = dict(call("HEAD", "/i/1", HTML)[1])
    assert h[b"x-robots-tag"] == b"noindex" and h[b"content-type"].startswith(b"text/html")
    h = dict(call("HEAD", "/og.png")[1])
    assert h[b"cache-control"] == b"public, max-age=86400" and h[b"content-type"] == b"image/png"


@pytest.mark.parametrize("headers", [HTML, []])
def test_head_unknown_path_404_like_get(headers):
    status, hdrs, body = call("GET", "/no-such-page", headers)
    assert status == 404 and body
    assert call("HEAD", "/no-such-page", headers) == (404, hdrs, b"")


def test_head_through_http_client(client):
    r = client.head("/")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html") and r.content == b""


def test_api_and_login_link_stay_get_only(client, login):
    login(client)
    assert client.head("/api/me").status_code == 405
    uid = A.row("select id from users")["id"]
    A.run("insert into login_tokens(token,user_id,expires) values('t1',%s,%s)", (uid, int(time.time()) + 600))
    assert call("HEAD", "/auth", query=b"t=t1")[0] == 405  # бот-превью не сжигает одноразовую ссылку входа
    assert A.row("select 1 as ok from login_tokens where token='t1'")
