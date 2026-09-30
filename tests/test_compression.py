"""Сжатие ответов (SERBITO-349): текст от GZIP_MIN байт — gzip, остальное как есть. Проверяем на уровне ASGI
(call из test_head): TestClient сам распаковывает тело и не показал бы, что именно ушло по сети."""
import gzip

import pytest
from fastapi import FastAPI
from fastapi.responses import PlainTextResponse, StreamingResponse

import app as A
from tests.test_head import call

GZ = [(b"accept-encoding", b"gzip, deflate, br")]


def test_landing_html_is_gzipped():
    status, headers, body = call("GET", "/", GZ)
    h = dict(headers)
    assert status == 200 and h[b"content-encoding"] == b"gzip" and b"accept-encoding" in h[b"vary"].lower()
    html = gzip.decompress(body)
    assert b'id="signin"' in html and int(h[b"content-length"]) == len(body) < len(html) / 3


@pytest.mark.parametrize("accept", [b"identity", b"gzip;q=0", b"*;q=0", b""])
def test_no_gzip_unless_asked(accept):
    h = dict(call("GET", "/", [(b"accept-encoding", accept)] if accept else [])[1])
    assert b"content-encoding" not in h
    assert b"accept-encoding" in h[b"vary"].lower()  # кэш между нами и браузером не перепутает версии


def test_accepts_gzip():
    assert A.accepts_gzip("gzip") and A.accepts_gzip("br, gzip;q=0.5") and A.accepts_gzip("*")
    assert not A.accepts_gzip("br") and not A.accepts_gzip("gzip;q=0") and not A.accepts_gzip("*, gzip;q=0")


def test_head_matches_compressed_get():
    status, headers, _ = call("GET", "/about", GZ)
    assert dict(headers)[b"content-encoding"] == b"gzip"
    assert call("HEAD", "/about", GZ) == (status, headers, b"")


@pytest.mark.parametrize("path", ["/og.png", "/favicon.ico", "/robots.txt"])
def test_binary_and_small_as_is(path):
    """PNG/ico уже сжаты (и отдаются потоком FileResponse); robots.txt меньше GZIP_MIN."""
    status, headers, _ = call("GET", path, GZ)
    assert status == 200 and b"content-encoding" not in dict(headers)


def test_streaming_and_encoded_responses_untouched():
    mini = FastAPI()
    big = "x" * (A.GZIP_MIN * 4)

    @mini.get("/stream")
    def stream():
        return StreamingResponse(iter([big, big]), media_type="text/plain")

    @mini.get("/encoded")
    def encoded():
        return PlainTextResponse(big, headers={"content-encoding": "identity"})

    @mini.get("/plain")
    def plain():
        return PlainTextResponse(big)

    mini.add_middleware(A.Gzip)
    orig = A.app
    try:
        A.app = mini  # call() ходит в A.app
        for path in ("/stream", "/encoded"):
            status, headers, body = call("GET", path, GZ)
            assert status == 200 and dict(headers).get(b"content-encoding") != b"gzip" and big.encode() in body, path
        headers, body = call("GET", "/plain", GZ)[1:]
        assert dict(headers)[b"content-encoding"] == b"gzip" and gzip.decompress(body) == big.encode()
    finally:
        A.app = orig
