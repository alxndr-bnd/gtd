"""Установка на экран телефона (манифест, SERBITO-281) и страница 404 для браузера (SERBITO-283)."""
import json

import pytest

import app as A
import pages as P

BROWSER = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"


@pytest.mark.parametrize("accept_language, lang", [
    (None, "ru"), ("ru-RU,ru;q=0.9,en;q=0.8", "ru"), ("en-US,en;q=0.9,ru;q=0.8", "en"), ("en-GB", "en"),
    # правило языка SERBITO-259: сербский кириллицей — русский, латиницей и прочие языки — английский
    ("sr-RS,sr;q=0.9,en;q=0.8,ru;q=0.7", "ru"), ("sr-Latn-RS", "en"), ("de-DE,de;q=0.9", "en"),
    ("en;q=0,ru", "ru"), ("en;q=bad", "ru"),
])
def test_manifest(client, accept_language, lang):
    r = client.get("/manifest.webmanifest", headers={"accept-language": accept_language} if accept_language else {})
    assert r.status_code == 200 and r.headers["content-type"] == "application/manifest+json"
    assert r.headers["vary"] == "Accept-Language"
    m = json.loads(r.content.decode("utf-8"))
    assert m["name"] == m["short_name"] == "GTD" and m["description"] == P.MANIFEST_DESC[lang] and m["lang"] == lang
    assert (m["start_url"], m["scope"], m["display"]) == ("/", "/", "standalone")
    assert m["background_color"] == "#f6f7f9" and m["theme_color"] == P.BRAND == "#0F766E"
    icons = {i["src"]: i for i in m["icons"]}
    assert set(icons) == {"/icon-192.png", "/icon-512.png", "/icon-maskable-512.png"}
    assert icons["/icon-192.png"]["sizes"] == "192x192" and icons["/icon-512.png"]["sizes"] == "512x512"
    assert icons["/icon-maskable-512.png"]["purpose"] == "maskable" and "purpose" not in icons["/icon-512.png"]
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
        body = h[h.index('<div class="pub">'):h.index("<footer>")]
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
    ("/nope", None), ("/nope", "application/json"), ("/nope", "*/*"), ("/i/abc", "application/json"),
])
def test_json_404_for_api_and_non_html(client, path, accept):
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
    assert client.get("/dev-login").json() == {"detail": "Not Found"}
