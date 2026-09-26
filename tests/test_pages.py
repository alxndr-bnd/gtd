"""Публичные страницы: лендинг, «Как это работает», политика конфиденциальности, SEO-теги, robots.txt, sitemap.xml, noindex для /i/N."""
import json
import re
import xml.etree.ElementTree as ET

import pytest

import app as A
import pages as P

BASE = "http://localhost:8000"
PUBLIC = {"/": "ru", "/about": "ru", "/en/": "en", "/en/about": "en", "/privacy": "ru", "/en/privacy": "en"}
TM = {"ru": "GTD® и Getting Things Done® — товарные знаки David Allen Company. Сервис независимый и не связан с автором метода",
      "en": "GTD® and Getting Things Done® are trademarks of the David Allen Company"}


def meta(html, prop):
    m = re.search(rf'<meta (?:property|name)="{re.escape(prop)}" content="([^"]*)">', html)
    return m and m.group(1)


@pytest.mark.parametrize("path, lang", PUBLIC.items())
def test_public_page_seo_tags(client, path, lang):
    r = client.get(path)
    assert r.status_code == 200 and "noindex" not in r.headers.get("x-robots-tag", "")
    h = r.text
    assert f'<html lang="{lang}">' in h and h.count("<title>") == 1
    title = re.search(r"<title>([^<]+)</title>", h).group(1)
    assert title == meta(h, "og:title") and ("Getting Things Done" in title or "privacy" in path)
    assert meta(h, "description") and meta(h, "og:description") == meta(h, "description")
    assert f'<link rel="canonical" href="{BASE}{path}">' in h and meta(h, "og:url") == BASE + path
    assert meta(h, "og:site_name") and meta(h, "og:locale") == ("ru_RU" if lang == "ru" else "en_US")
    assert meta(h, "og:image") == BASE + ("/og.png" if lang == "ru" else "/og-en.png")
    assert meta(h, "twitter:card") == "summary_large_image"
    # hreflang — пары ru/en и x-default на русскую версию
    pair = {"/": ("/", "/en/"), "/en/": ("/", "/en/"), "/about": ("/about", "/en/about"), "/en/about": ("/about", "/en/about"),
            "/privacy": ("/privacy", "/en/privacy"), "/en/privacy": ("/privacy", "/en/privacy")}[path]
    for hl, p in (("ru", pair[0]), ("en", pair[1]), ("x-default", pair[0])):
        assert f'<link rel="alternate" hreflang="{hl}" href="{BASE}{p}">' in h
    ld = json.loads(re.search(r'<script type="application/ld\+json">(.+?)</script>', h).group(1))
    assert ld["@type"] == "WebApplication" and ld["applicationCategory"] == "ProductivityApplication"
    assert ld["offers"]["price"] == "0" and ld["inLanguage"] == lang and ld["sameAs"] == ["https://t.me/gtdsrbot"]
    # Подвал: оговорка о товарном знаке, другие проекты с UTM, подпись No Handoff
    assert TM[lang] in h and 'href="https://gettingthingsdone.com"' in h
    assert ("Другие проекты" if lang == "ru" else "Other projects") in h
    for _, url, ru, en in P.PRODUCTS:
        assert f'href="{url}?utm_source=gtd&utm_medium=crosspromo&utm_campaign=footer"' in h
        assert (ru if lang == "ru" else en) in h
    assert 'href="https://www.linkedin.com/company/nohandoff/">No Handoff</a>' in h
    assert ("Сделано" if lang == "ru" else "Made by") in h
    # и ссылка на политику конфиденциальности своего языка
    priv = "/privacy" if lang == "ru" else "/en/privacy"
    foot = h[h.index("<footer>"):h.index("</footer>")]
    assert f'<a href="{priv}">{"Конфиденциальность" if lang == "ru" else "Privacy"}</a>' in foot


def test_seo_search_words(client):
    ru, en = client.get("/").text, client.get("/en/").text
    for words in ("GTD онлайн бесплатно", "Getting Things Done приложение", "telegram бот"):
        assert words.lower() in ru.lower(), words
    for words in ("GTD online", "Getting Things Done app", "Telegram bot"):
        assert words in en, words


@pytest.mark.parametrize("path, words", [
    ("/", ["Записал", "Разобрал", "Сделал", "позвонить маме завтра в 10:00", 'href="https://t.me/gtdsrbot"',
           'id="signin"', 'href="/about"']),
    ("/en/", ["Capture", "Clarify", "Do", 'href="https://t.me/gtdsrbot"', 'id="signin"', 'href="/en/about"']),
])
def test_landing_text_is_server_rendered(client, path, words):
    """Гость и поисковик получают текст лендинга в самом HTML, без JS; приложение (скрипт) — то же."""
    h = client.get(path).text
    root = h[h.index('<div id="root">'):h.index('<dialog id="dlg">')]
    for w in words:
        assert w in root, w
    assert "<!--LANDING-->" not in h and "function loginScreen" in h


def test_signed_in_root_serves_app(client, login):
    login(client)
    for path in ("/", "/en/"):
        h = client.get(path).text
        assert "<title>GTD</title>" in h and "function loginScreen" in h  # приложение, как раньше
        assert 'id="signin"' not in h and "Записал" not in h and 'rel="canonical"' not in h


def test_about_content(client):
    ru = client.get("/about").text
    for w in ("Дэвида Аллена", "«Getting Things Done» (2001", "«Как привести дела в порядок»",
              "Собрать", "Обработать", "Организовать", "Пересмотреть", "Делать",
              "Поле захвата", "Inbox", "Next, Waiting, Проекты, Someday, Reference, Календарь", "Weekly Review",
              "@контексту", "позвонить маме завтра в 10:00", "отчёт #Работа @комп", "@gtdsrbot",
              "<kbd>C</kbd>", "<kbd>N</kbd>", "<kbd>Enter</kbd>", "<kbd>↑</kbd>"):
        assert w in ru, w
    en = client.get("/en/about").text
    for w in ("David Allen", "Capture", "Clarify", "Organize", "Reflect", "Engage", "Weekly Review", "<kbd>Enter</kbd>"):
        assert w in en, w
    assert "function loginScreen" not in ru  # лёгкая страница, без JS приложения


@pytest.mark.parametrize("path, words", [
    ("/privacy", ["Политика конфиденциальности", "Что мы храним", "Аналитика", "Кто обрабатывает данные",
                  "Сколько храним", "Удаление аккаунта", "Контакты", "No Handoff", "Google Analytics 4",
                  "Cloudflare Web Analytics", "Brevo", "Sentry", "europe-west1", "в течение 30 дней", "<code>sid</code>"]),
    ("/en/privacy", ["Privacy policy", "What we store", "Analytics", "Who processes data", "How long we keep data",
                     "Deleting your account", "Contact", "No Handoff", "Google Analytics 4", "Cloudflare Web Analytics",
                     "Brevo", "Sentry", "europe-west1", "within 30 days", "<code>sid</code>"]),
])
def test_privacy_content(client, path, words):
    h = client.get(path).text
    for w in words:
        assert w in h, w
    # почта для запросов о данных — ссылкой mailto на самой политике, и больше ни на одной странице
    assert f'<a href="mailto:{P.PRIVACY_EMAIL}">{P.PRIVACY_EMAIL}</a>' in h
    for other in ("/", "/en/", "/about", "/en/about"):
        assert P.PRIVACY_EMAIL not in client.get(other).text, other
    assert "function loginScreen" not in h and "{email}" not in h and "{date}" not in h


@pytest.mark.parametrize("path, link", [("/", "/privacy"), ("/en/", "/en/privacy")])
def test_landing_links_privacy_near_signin(client, path, link):
    h = client.get(path).text
    card = h[h.index('<div class="card signin">'):h.index("<footer>")]
    assert f'href="{link}"' in card


def test_privacy_ga_only_on_prod(client, monkeypatch):
    monkeypatch.setattr(A, "GA_ID", "G-TEST123")
    assert "googletagmanager" not in client.get("/privacy").text
    prod = client.get("/en/privacy", headers={"host": "gtd.serbito.rs"}).text
    assert "gtag/js?id=G-TEST123" in prod and "location.origin + '/en/privacy'" in prod


def test_about_ga_event_only_on_prod(client, monkeypatch):
    monkeypatch.setattr(A, "GA_ID", "G-TEST123")
    assert "googletagmanager" not in client.get("/about").text
    prod = client.get("/about", headers={"host": "gtd.serbito.rs"}).text
    assert "gtag/js?id=G-TEST123" in prod and "ga('about_view'" in prod
    assert "ga('about_view', {language: 'en'})" in client.get("/en/about", headers={"host": "gtd.serbito.rs"}).text


def test_app_menu_links_to_about():
    page = open(f"{A.STATIC}/index.html", encoding="utf-8").read()
    # ссылка в меню — из словаря интерфейса: /about по-русски, /en/about по-английски
    assert "<a href=\"${t('about_url')}\"" in page
    assert '"about_url": "/about"' in page and '"about_url": "/en/about"' in page


def test_robots(client):
    r = client.get("/robots.txt")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain")
    lines = r.text.splitlines()
    for p in ("/", "/about", "/privacy", "/en/"):
        assert f"Allow: {p}" in lines
    for p in ("/api/", "/auth", "/dev-login", "/tg/", "/tasks/", "/i/"):
        assert f"Disallow: {p}" in lines
    assert f"Sitemap: {BASE}/sitemap.xml" in lines


def test_sitemap(client):
    r = client.get("/sitemap.xml")
    assert r.status_code == 200 and "xml" in r.headers["content-type"]
    ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9", "x": "http://www.w3.org/1999/xhtml"}
    urls = ET.fromstring(r.text).findall("s:url", ns)
    assert [u.find("s:loc", ns).text for u in urls] == [BASE + p for p in PUBLIC]
    for u in urls:
        alts = {a.get("hreflang"): a.get("href") for a in u.findall("x:link", ns)}
        assert set(alts) == {"ru", "en", "x-default"} and alts["x-default"] == alts["ru"]


def test_og_images(client):
    for path in ("/og.png", "/og-en.png"):
        r = client.get(path)
        assert r.status_code == 200 and r.headers["content-type"] == "image/png"
        assert r.content[:8] == b"\x89PNG\r\n\x1a\n"
        assert (int.from_bytes(r.content[16:20]), int.from_bytes(r.content[20:24])) == (1200, 630)  # IHDR



def test_icons_served_from_root(client):
    ico = client.get("/favicon.ico")  # Google берёт favicon для выдачи отсюда, а не из data:-ссылки
    assert ico.status_code == 200 and ico.headers["content-type"] == "image/x-icon" and ico.content[:4] == b"\0\0\1\0"
    svg = client.get("/favicon.svg")
    assert svg.headers["content-type"].startswith("image/svg+xml") and svg.text.strip() == P.mark()  # файл = знак из кода
    for path, side in (("/apple-touch-icon.png", 180), ("/icon-192.png", 192), ("/icon-512.png", 512),
                       ("/icon-maskable-512.png", 512)):
        r = client.get(path)
        assert r.status_code == 200 and r.headers["content-type"] == "image/png", path
        assert (int.from_bytes(r.content[16:20]), int.from_bytes(r.content[20:24])) == (side, side), path


def test_icons_and_name_on_every_page(client, login):
    pages = [client.get(p).text for p in ("/", "/en/", "/about", "/en/about", "/i/1")]
    login(client)
    pages.append(client.get("/").text)  # приложение после входа
    for h in pages:
        assert '<link rel="icon" href="/favicon.ico"' in h and '<link rel="apple-touch-icon"' in h
        assert f'<meta name="theme-color" content="{P.BRAND}"' in h and "<!--ICONS-->" not in h
        assert "data:image/svg+xml" not in h and "GTD for free" not in h
    assert '<meta property="og:site_name" content="GTD">' in pages[0]

def test_item_page_noindex(client):
    r = client.get("/i/1")
    assert r.status_code == 200 and r.headers["x-robots-tag"] == "noindex"
    assert "<title>GTD</title>" in r.text and 'id="signin"' not in r.text  # личная ссылка — не лендинг
    assert "noindex" not in client.get("/").headers.get("x-robots-tag", "")


def test_absolute_urls_follow_base_url(monkeypatch, client):
    monkeypatch.setattr(A, "BASE_URL", "https://gtd.example")
    h = client.get("/en/about").text
    assert '<link rel="canonical" href="https://gtd.example/en/about">' in h
    assert "https://gtd.example/og-en.png" in h
    assert "Sitemap: https://gtd.example/sitemap.xml" in client.get("/robots.txt").text
