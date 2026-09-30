"""Публичные страницы: лендинг, «Как это работает», политика конфиденциальности, «Что нового», SEO-теги, robots.txt,
sitemap.xml, noindex для /i/N."""
import json
import os
import re
import xml.etree.ElementTree as ET

import pytest

import app as A
import pages as P

BASE = "http://localhost:8000"
PUBLIC = {"/": "ru", "/about": "ru", "/en/": "en", "/en/about": "en", "/privacy": "ru", "/en/privacy": "en",
          "/changes": "ru", "/en/changes": "en"}
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
            "/privacy": ("/privacy", "/en/privacy"), "/en/privacy": ("/privacy", "/en/privacy"),
            "/changes": ("/changes", "/en/changes"), "/en/changes": ("/changes", "/en/changes")}[path]
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
    root = h[h.index('<div id="root">'):h.index('<dialog id="dlg"')]
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


@pytest.mark.parametrize("path, lang", PUBLIC.items())
def test_open_source_links(client, path, lang):
    """SERBITO-291: код открыт — ссылка на репозиторий в подвале каждой публичной страницы,
    на лендинге — строка под текстом, на /about — раздел со ссылкой на инструкцию по self-hosting."""
    h = client.get(path).text
    foot = h[h.index("<footer>"):h.index("</footer>")]
    assert f'{P.T[lang]["oss"]} · <a href="{P.REPO_URL}">GitHub</a>' in foot
    assert ("Открытый код (MIT)" if lang == "ru" else "Open source (MIT)") in foot
    assert "mailto:" not in foot  # контактной почты в подвале нет; адрес для запросов о данных — только на /privacy
    if path in ("/", "/en/"):
        root = h[h.index('<div id="root">'):h.index("<footer>")]
        assert f'<p class="note">{P.T[lang]["oss_note"]} · <a href="{P.REPO_URL}">GitHub</a></p>' in root
    elif path.endswith("/about"):
        body = h[:h.index("<footer>")]
        assert ("<h2>Открытый код</h2>" if lang == "ru" else "<h2>Open source</h2>") in body
        assert f'href="{P.REPO_URL}"' in body and f'href="{P.SELF_HOST_URL}"' in body
    assert P.REPO_URL == "https://github.com/alxndr-bnd/gtd"
    assert P.SELF_HOST_URL == P.REPO_URL + "/blob/main/docs/self-host.md"


def test_self_host_doc_exists():
    """Ссылка с /about ведёт на файл, который лежит в репозитории."""
    assert os.path.isfile(os.path.join(os.path.dirname(P.__file__), "docs", "self-host.md"))


def test_app_menu_links_to_about():
    page = open(f"{A.STATIC}/index.html", encoding="utf-8").read()
    # ссылка в меню — из словаря интерфейса: /about по-русски, /en/about по-английски
    assert "<a href=\"${t('about_url')}\"" in page
    assert '"about_url": "/about"' in page and '"about_url": "/en/about"' in page


def test_robots(client):
    r = client.get("/robots.txt")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain")
    lines = r.text.splitlines()
    for p in ("/", "/about", "/privacy", "/changes", "/en/"):
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


# ── «Что нового» и версия (SERBITO-329) ──

@pytest.mark.parametrize("lang", ["ru", "en"])
def test_changes_page_lists_releases_newest_first(client, lang):
    h = client.get(P.PATHS[(lang, "changes")]).text
    body = h[h.index("<h1>"):h.index("<footer>")]
    assert ("<h1>Что нового</h1>" if lang == "ru" else "<h1>What's new</h1>") in body
    versions = re.findall(r'<section class="rel" id="v([\d.]+)"><h2>v\1 ', body)
    assert versions == [r.version for r in P.RELEASES] and versions[-1] == "0.1.0" and "Unreleased" not in body
    assert ("27 сентября 2026" if lang == "ru" else "27 September 2026") in body
    assert ("<h3>Исправлено</h3>" if lang == "ru" else "<h3>Fixed</h3>") in body
    for r in P.RELEASES:  # каждый пункт — на языке страницы, и только на нём
        for en, ru in r.entries:
            assert P.entry_html(ru if lang == "ru" else en) in body
            assert P.entry_html(en if lang == "ru" else ru) not in body
    assert f'href="{P.CHANGELOG_URL}"' in body and "function loginScreen" not in h
    # открыл страницу — текущая версия увидена: точка в меню приложения гаснет
    assert f"localStorage.setItem('{P.SEEN_KEY}', '{P.VERSION_LABEL}')" in h


@pytest.mark.parametrize("path, lang", PUBLIC.items())
def test_footer_links_changes_and_shows_version(client, monkeypatch, path, lang):
    monkeypatch.setattr(P, "VERSION", "0.16.0")
    monkeypatch.setattr(P, "VERSION_LABEL", "v0.16.0")
    h = client.get(path).text
    foot = h[h.index("<footer>"):h.index("</footer>")]
    link = "Что нового" if lang == "ru" else "What's new"
    assert f'<a href="{P.PATHS[(lang, "changes")]}">{link}</a> · <span class="ver">v0.16.0</span>' in foot


def test_version_comes_from_deploy_env(monkeypatch):
    """APP_VERSION ставит deploy.yml из тега; локально его нет — «dev»."""
    import importlib
    try:
        monkeypatch.setenv("APP_VERSION", "0.16.0")
        assert importlib.reload(P).VERSION_LABEL == "v0.16.0"
        monkeypatch.setenv("APP_VERSION", "v0.17.0<script>")
        assert importlib.reload(P).VERSION_LABEL == "v0.17.0script"  # в HTML и JS — только безопасные символы
        monkeypatch.delenv("APP_VERSION")
        assert importlib.reload(P).VERSION_LABEL == "dev"
    finally:
        monkeypatch.delenv("APP_VERSION", raising=False)
        importlib.reload(P)
    deploy = open(os.path.join(os.path.dirname(P.__file__), ".github", "workflows", "deploy.yml")).read()
    assert 'echo "APP_VERSION: \\"${GITHUB_REF_NAME#v}\\"" >> "$ENV_FILE"' in deploy


def test_changes_page_says_which_version_runs(client, monkeypatch):
    assert "Это локальная сборка (dev)." in client.get("/changes").text
    monkeypatch.setattr(P, "VERSION", "0.16.0")
    monkeypatch.setattr(P, "VERSION_LABEL", "v0.16.0")
    assert "You are using v0.16.0." in client.get("/en/changes").text


def test_config_reports_version(client, monkeypatch):
    assert client.get("/api/config").json()["version"] == "dev"
    monkeypatch.setattr(P, "VERSION_LABEL", "v0.16.0")
    assert client.get("/api/config").json()["version"] == "v0.16.0"


def test_app_menu_links_to_changes():
    """Ссылка, точка новой версии и ключ localStorage — в браузере (test_menu_whats_new_and_version, RU);
    здесь — только адреса для обоих языков."""
    page = open(f"{A.STATIC}/index.html", encoding="utf-8").read()
    assert '"changes_url": "/changes"' in page and '"changes_url": "/en/changes"' in page
