"""Установка на экран телефона (манифест, SERBITO-281) и страница 404 для браузера (SERBITO-283)."""
import json

import pytest

import app as A
import pages as P

BROWSER = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"


# Правило выбора языка (SERBITO-259) — в test_i18n::test_accept_language; здесь — что манифест им пользуется
@pytest.mark.parametrize("accept_language, lang", [(None, "ru"), ("en-US,en;q=0.9,ru;q=0.8", "en")])
def test_manifest(client, accept_language, lang):
    r = client.get("/manifest.webmanifest", headers={"accept-language": accept_language} if accept_language else {})
    assert r.status_code == 200 and r.headers["content-type"] == "application/manifest+json"
    assert r.headers["vary"] == "Accept-Language"
    m = json.loads(r.content.decode("utf-8"))
    assert m["name"] == m["short_name"] == "GTD" and m["description"] == P.MANIFEST_DESC[lang] and m["lang"] == lang
    assert (m["start_url"], m["scope"], m["display"]) == ("/", "/", "standalone")
    assert m["background_color"] == "#f6f7f9" and m["theme_color"] == P.BRAND == "#0F766E"
    icons = {i["src"]: (i["sizes"], i["purpose"]) for i in m["icons"]}
    assert icons == {"/icon-192.png": ("192x192", "any"), "/icon-512.png": ("512x512", "any"),
                     "/icon-maskable-192.png": ("192x192", "maskable"), "/icon-maskable-512.png": ("512x512", "maskable")}
    for src in icons:  # иконки из манифеста реально отдаются
        assert client.get(src).headers["content-type"] == "image/png", src


def test_manifest_and_ios_meta_on_every_page(client, login):
    pages = [client.get(p).text for p in ("/", "/en/", "/about", "/en/about", "/i/1")]
    pages.append(client.get("/nope", headers={"accept": BROWSER}).text)  # и на странице 404
    login(client)
    pages.append(client.get("/").text)  # приложение после входа
    for h in pages:
        assert h.count('<link rel="manifest" href="/manifest.webmanifest">') == 1
        assert '<meta name="apple-mobile-web-app-capable" content="yes">' in h
        assert '<meta name="mobile-web-app-capable" content="yes">' in h
        assert '<meta name="apple-mobile-web-app-status-bar-style" content="default">' in h
        assert '<meta name="apple-mobile-web-app-title" content="GTD">' in h


def test_about_install_section_last(client):
    for path, words in (("/about", ("На экран «Домой»", "Установить приложение")),
                        ("/en/about", ("Add to Home Screen", "Install app"))):
        h = client.get(path).text
        body = h[h.index('<div class="pub">'):h.index("</main>")]  # последнее в <main>, перед подвалом
        assert body.endswith(P.INSTALL["ru" if path == "/about" else "en"])  # отдельный раздел в самом конце
        for w in words:
            assert w in body, w


@pytest.mark.parametrize("path, headers, lang", [
    ("/nope", {}, "ru"),
    ("/some/deep/path", {"accept-language": "ru-RU,ru;q=0.9"}, "ru"),
    ("/nope", {"accept-language": "en-US,en;q=0.9,ru;q=0.5"}, "en"),
    ("/en/nope", {"accept-language": "ru"}, "en"),  # язык из адреса важнее браузера
    ("/i/abc", {}, "ru"),  # не номер задачи — 404, а не 422 (SERBITO-271)
])
def test_html_404_for_browser(client, path, headers, lang):
    r = client.get(path, headers={"accept": BROWSER, **headers})
    assert r.status_code == 404 and r.headers["content-type"].startswith("text/html")
    assert r.headers["x-robots-tag"] == "noindex"
    h = r.text
    title, text, go, how = P.NOT_FOUND[lang]
    assert f'<html lang="{lang}">' in h and title in h and text in h
    assert title == ("Страница не найдена" if lang == "ru" else "Page not found")
    assert '<meta name="robots" content="noindex">' in h and P.mark(28) in h
    home, about = ("/", "/about") if lang == "ru" else ("/en/", "/en/about")
    assert f'<a class="btn" href="{home}">{go}</a>' in h and f'<a href="{about}">{how}</a>' in h
    assert P.BASE_CSS in h and P.PUBLIC_CSS in h and P.ICONS in h
    assert "function loginScreen" not in h  # лёгкая страница, без JS приложения


@pytest.mark.parametrize("path, accept", [
    ("/api/nope", BROWSER), ("/api", BROWSER), ("/api/nope", None),
    ("/nope", "application/json"), ("/i/abc", "application/json"),
])
def test_json_404_for_api_and_json_clients(client, path, accept):
    r = client.get(path, headers={"accept": accept} if accept else {})
    assert r.status_code == 404 and r.headers["content-type"] == "application/json"
    assert r.json() == {"detail": "Not Found"}


@pytest.mark.parametrize("accept", [BROWSER, None])
def test_api_http_exceptions_unchanged(client, login, accept):
    """HTTPException(404) из обработчиков API — тот же JSON с detail, даже если Accept браузерный."""
    login(client)
    headers = {"accept": accept} if accept else {}
    for method, path, detail in (("get", "/api/items/n/999", "Задача не найдена"),
                                 ("patch", "/api/items/999", "Not Found"),
                                 ("patch", "/api/projects/999", "Not Found")):
        r = client.request(method, path, json={"title": "x"} if method == "patch" else None, headers=headers)
        assert r.status_code == 404 and r.headers["content-type"] == "application/json", path
        assert r.json() == {"detail": detail}, path


def test_other_errors_stay_json_in_browser(new_client):
    c = new_client()
    for method, path, status in (("get", "/api/counts", 401), ("get", "/tg/webhook", 405), ("get", "/i/1/x", 404)):
        r = c.request(method, path, headers={"accept": BROWSER})
        assert r.status_code == status, path
        assert r.headers["content-type"] == ("text/html; charset=utf-8" if status == 404 else "application/json"), path


def test_dev_login_404_in_prod_is_html_for_browser(client, monkeypatch):
    monkeypatch.setattr(A, "DEV", False)
    r = client.get("/dev-login", headers={"accept": BROWSER})
    assert r.status_code == 404 and "Страница не найдена" in r.text
    assert client.get("/dev-login", headers={"accept": "application/json"}).json() == {"detail": "Not Found"}


@pytest.mark.parametrize("accept", [None, "*/*", "text/plain"])
def test_html_404_without_html_in_accept(client, accept):
    """SERBITO-354 (G10): curl, link previews and simple clients send */* or nothing — they get the same HTML page,
    not a bare JSON {"detail": ...}. Only /api/* and clients that ask for JSON get JSON."""
    r = client.get("/nope", headers={"accept": accept} if accept else {})
    assert r.status_code == 404 and r.headers["content-type"].startswith("text/html")
    assert P.NOT_FOUND["ru"][0] in r.text


@pytest.mark.parametrize("path, to", [
    ("/ru", "/"), ("/ru/", "/"), ("/ru/about", "/about"), ("/ru/privacy", "/privacy"), ("/ru/changes", "/changes"),
    ("/ru/i/5", "/i/5"), ("/ru/about?x=1", "/about?x=1"),
    ("/ru//evil.example", "/evil.example"), ("/ru/%5Cevil.example", "/evil.example"),  # не открытый редирект
])
def test_ru_prefix_redirects_to_russian_root(client, path, to):
    """SERBITO-354 (G10): Russian lives at /, English at /en/. A guessed /ru/… leads to the same page without
    the prefix (permanent redirect), not to a 404."""
    r = client.get(path, headers={"accept": BROWSER}, follow_redirects=False)
    assert r.status_code == 308 and r.headers["location"] == to


# ── SERBITO-392: на macOS установленное приложение было чёрным ──
# Обычные иконки 192/512 были RGBA со скруглёнными прозрачными углами, а цвет прозрачных пикселей — чёрный (0,0,0,0).
# Теперь у всех иконок установки непрозрачный бирюзовый фон во весь холст; проверяем сами пиксели.

def png_pixels(data: bytes):
    """Минимальный декодер PNG (8 бит, RGB или RGBA, без interlace) — Pillow в зависимостях не нужен."""
    import struct
    import zlib
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    i, idat = 8, b""
    while i < len(data):
        n, kind = struct.unpack(">I4s", data[i:i + 8])
        body = data[i + 8:i + 8 + n]
        if kind == b"IHDR":
            w, h, depth, color, _, _, interlace = struct.unpack(">IIBBBBB", body)
        elif kind == b"IDAT":
            idat += body
        i += 12 + n
    assert depth == 8 and interlace == 0 and color in (2, 6), (depth, color, interlace)
    ch = 3 if color == 2 else 4
    raw, stride, prev, rows, p = zlib.decompress(idat), w * ch, bytearray(w * ch), [], 0
    for _ in range(h):
        f, line = raw[p], bytearray(raw[p + 1:p + 1 + stride])
        p += 1 + stride
        for x in range(stride):
            a, b, c = (line[x - ch] if x >= ch else 0), prev[x], (prev[x - ch] if x >= ch else 0)
            if f == 4:
                pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                pred = a if pa <= pb and pa <= pc else b if pb <= pc else c
            else:
                pred = (0, a, b, (a + b) // 2)[f]
            line[x] = (line[x] + pred) & 255
        rows.append([tuple(line[x:x + ch]) for x in range(0, stride, ch)])
        prev = line
    return w, h, rows


BRAND_RGB = tuple(int(P.BRAND[i:i + 2], 16) for i in (1, 3, 5))


def test_install_icons_are_opaque_and_green(client):
    """Каждая иконка из манифеста и apple-touch-icon: размер как заявлен, ни одного прозрачного пикселя, по всему
    краю — бирюзовый фон бренда (углы тоже: их скругляет система, а не картинка)."""
    srcs = [(i["src"], int(i["sizes"].split("x")[0])) for i in P.manifest("ru")["icons"]]
    for src, side in srcs + [("/apple-touch-icon.png", 180)]:
        w, h, rows = png_pixels(client.get(src).content)
        assert (w, h) == (side, side), src
        assert all(len(px) == 3 or px[3] == 255 for row in rows for px in row), f"{src}: есть прозрачные пиксели"
        edge = rows[0] + rows[-1] + [row[0] for row in rows] + [row[-1] for row in rows]
        assert {px[:3] for px in edge} == {BRAND_RGB}, f"{src}: край не бирюзовый"
        center = rows[h // 2][w // 2][:3]
        assert center in (BRAND_RGB, (255, 255, 255)), f"{src}: в центре {center}"  # фон или белый знак
    assert {"any", "maskable"} == {i["purpose"] for i in P.manifest("en")["icons"]}
