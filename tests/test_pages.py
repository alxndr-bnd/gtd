"""Публичные страницы: лендинг, «Как это работает», политика конфиденциальности, «Что нового», страница бота (/bot),
чек-лист Weekly Review (/weekly-review), SEO-теги, robots.txt, sitemap.xml, noindex для /i/N."""
import json
import os
import re
import xml.etree.ElementTree as ET
from html.parser import HTMLParser

import pytest

import app as A
import pages as P

BASE = "http://localhost:8000"
PUBLIC = {"/": "ru", "/about": "ru", "/en/": "en", "/en/about": "en", "/privacy": "ru", "/en/privacy": "en",
          "/changes": "ru", "/en/changes": "en", "/bot": "ru", "/en/bot": "en",
          "/weekly-review": "ru", "/en/weekly-review": "en",
          "/alternativa-todoist": "ru", "/en/todoist-alternative-open-source": "en", "/en/free-gtd-apps": "en",
          "/gtd-dlya-nachinayushih": "ru"}
# Страницы под поисковый запрос (SERBITO-441, SERBITO-446, SERBITO-470): запрос — в title и h1
GUIDES = {"/bot": "Telegram бот для задач", "/en/bot": "Telegram bot for tasks",
          "/weekly-review": "Еженедельный обзор GTD", "/en/weekly-review": "GTD weekly review",
          "/alternativa-todoist": "Альтернатива Todoist", "/en/todoist-alternative-open-source":
          "Open source Todoist alternative", "/en/free-gtd-apps": "Free GTD apps",
          "/gtd-dlya-nachinayushih": "GTD для начинающих"}
# У этих страниц нет пары на другом языке (SERBITO-470): ни hreflang, ни альтернатив в sitemap
SINGLE = {"/en/free-gtd-apps", "/gtd-dlya-nachinayushih"}
# Слова в тексте — как их считает читатель: «GTD-приложение» — одно слово, «—» — не слово
WORD = re.compile(r"[^\W_][\w'’.-]*")


def words(html: str) -> int:
    return len(WORD.findall(re.sub(r"<[^>]+>", " ", html)))
TM = {"ru": "GTD® и Getting Things Done® — товарные знаки David Allen Company. Сервис независимый и не связан с автором метода",
      "en": "GTD® and Getting Things Done® are trademarks of the David Allen Company"}


def app_code(client, html: str) -> str:
    """Код SPA — во внешнем бандле (SERBITO-444): страница ссылается на /app.<хеш>.js."""
    return client.get(re.search(r'<script defer src="(/app\.[0-9a-f]{12}\.js)"></script>', html).group(1)).text


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
    assert title == meta(h, "og:title") and ("Getting Things Done" in title or "privacy" in path or path in GUIDES)
    assert len(meta(h, "description")) <= 155  # длиннее Google обрезает (SERBITO-441, SERBITO-442)
    assert meta(h, "description") and meta(h, "og:description") == meta(h, "description")
    assert f'<link rel="canonical" href="{BASE}{path}">' in h and meta(h, "og:url") == BASE + path
    assert meta(h, "og:site_name") and meta(h, "og:locale") == ("ru_RU" if lang == "ru" else "en_US")
    assert meta(h, "og:image") == BASE + ("/og.png" if lang == "ru" else "/og-en.png")
    assert meta(h, "twitter:card") == "summary_large_image"
    # hreflang — пары ru/en и x-default на русскую версию; у страницы одного языка — ни одной ссылки
    page_key = next(pg for (lg, pg), p in P.PATHS.items() if p == path)
    if path in SINGLE:
        assert 'hreflang="' not in h[:h.index("</head>")] and "og:locale:alternate" not in h
    else:
        pair = (P.PATHS[("ru", page_key)], P.PATHS[("en", page_key)])
        for hl, p in (("ru", pair[0]), ("en", pair[1]), ("x-default", pair[0])):
            assert f'<link rel="alternate" hreflang="{hl}" href="{BASE}{p}">' in h
    ld = json.loads(re.search(r'<script type="application/ld\+json">(.+?)</script>', h).group(1))
    nodes = {n["@type"]: n for n in ld["@graph"]}
    app, page, home = nodes["WebApplication"], nodes["WebPage"], BASE + "/"
    assert app["@id"] == home + "#app" and app["url"] == home  # одна сущность на всех страницах обоих языков
    assert app["applicationCategory"] == "ProductivityApplication" and app["offers"]["price"] == "0"
    assert app["isAccessibleForFree"] is True and app["license"] == "https://opensource.org/licenses/MIT"
    assert app["sameAs"] == ["https://t.me/gtdsrbot", "https://github.com/alxndr-bnd/gtd"]
    assert nodes["Organization"]["@id"] == home + "#org" == app["author"]["@id"] == nodes["WebSite"]["publisher"]["@id"]
    assert page["url"] == BASE + path and page["inLanguage"] == lang and page["name"] == title
    assert page["isPartOf"]["@id"] == nodes["WebSite"]["@id"] and page["about"]["@id"] == app["@id"]
    # Дата редакции — у всех, кроме главной (SERBITO-445, SERBITO-470): у главной честной даты нет
    assert ("dateModified" in page) == (page_key != "home") and page.get("dateModified") == P.modified(page_key)
    crumbs = nodes.get("BreadcrumbList")
    if path in ("/", "/en/"):
        assert crumbs is None
    else:
        items = crumbs["itemListElement"]
        assert [i["item"] for i in items] == [BASE + ("/" if lang == "ru" else "/en/"), BASE + path]
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
        assert words.lower() in ru.lower().replace("-", " "), words  # «Telegram-бот» поисковик читает как «telegram бот»
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
    assert "<!--LANDING-->" not in h and "function loginScreen" in app_code(client, h)


def test_signed_in_root_serves_app(client, login):
    login(client)
    for path in ("/", "/en/"):
        h = client.get(path).text
        assert "<title>GTD</title>" in h and "function loginScreen" in app_code(client, h)  # приложение, как раньше
        assert 'id="signin"' not in h and "Записал" not in h and 'rel="canonical"' not in h


def test_about_content(client):
    ru = client.get("/about").text
    for w in ("Дэвида Аллена", "«Getting Things Done» (2001", "«Как привести дела в порядок»",
              "Собрать", "Обработать", "Организовать", "Пересмотреть", "Делать",
              "Поле захвата", "Inbox", "Next, Waiting, Projects, Someday, Reference, Календарь", "Weekly Review",
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


@pytest.mark.parametrize("path, words", [
    ("/", ["Telegram-бот", "задача — одним сообщением", "напоминания"]),
    ("/en/", ["Telegram bot", "send a task as a message", "reminders"]),
    ("/about", ["принимает задачи обычными сообщениями", "присылает напоминания"]),
    ("/en/about", ["takes tasks as plain messages", "sends reminders"]),
])
def test_bot_name_and_link_on_site(client, path, words):
    """SERBITO-422: имя бота — ссылкой t.me на лендинге (над блоком входа) и на странице помощи, с одной строкой о том,
    что бот делает."""
    assert P.BOT_URL == "https://t.me/gtdsrbot"
    h = client.get(path).text
    part = h
    if "about" not in path:  # лендинг: над блоком входа, под текстом (SERBITO-448: на телефоне её не закрывает баннер)
        part = h[h.index('<div class="intro">'):h.index('<div class="card signin">')]
    assert f'<a href="{P.BOT_URL}">@{P.BOT_NAME}</a>' in part
    for w in words:
        assert w in part, w


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
        assert ("Где открытый код?</h2>" if lang == "ru" else "Where is the source code?</h2>") in body
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
    for p in ("/", "/about", "/privacy", "/changes", "/bot", "/weekly-review", "/alternativa-todoist",
              "/gtd-dlya-nachinayushih", "/en/"):
        assert f"Allow: {p}" in lines
    for p in ("/api/", "/auth", "/dev-login", "/tg/", "/tasks/"):
        assert f"Disallow: {p}" in lines
    # /i/N не закрыт (SERBITO-448): робот, которому запрещён адрес, не видит его X-Robots-Tag: noindex, и Google
    # оставлял такие ссылки в индексе без текста. noindex у /i/N — test_item_page_noindex
    assert not any(line.startswith("Disallow: /i") for line in lines)
    assert f"Sitemap: {BASE}/sitemap.xml" in lines


def test_llms_txt(client):
    """SERBITO-448: /llms.txt — короткая справка для LLM: что это за сервис и где главное. Google её не читает,
    но файл дешёвый."""
    r = client.get("/llms.txt")
    assert r.status_code == 200 and r.headers["content-type"] == "text/plain; charset=utf-8"
    assert r.text.startswith("# GTD\n\n> ") and "Getting Things Done" in r.text and "<" not in r.text
    for path in PUBLIC:
        if "privacy" not in path:  # политика — не про продукт
            assert f"({BASE}{path})" in r.text, path
    assert f"({P.REPO_URL})" in r.text and f"({P.BOT_URL})" in r.text
    assert "Sitemap" not in r.text and r.text.endswith("\n")


@pytest.mark.parametrize("path, to", [("/en", "/en/"), ("/about/", "/about"), ("/en/about/", "/en/about"),
                                      ("/changes/", "/changes"), ("/bot/", "/bot"),
                                      ("/en/weekly-review/", "/en/weekly-review")])
@pytest.mark.parametrize("method", ["GET", "HEAD"])
def test_trailing_slash_redirect_is_permanent(client, method, path, to):
    """SERBITO-448: /en → /en/ и /about/ → /about — постоянный редирект (308), а не временный 307: поисковик
    переносит вес ссылки на адрес со слэшем (или без), а браузер запоминает переход."""
    r = client.request(method, path, follow_redirects=False)
    assert r.status_code == 308 and r.headers["location"] == "http://testserver" + to


def test_api_slash_redirect_stays_temporary(client):
    """POST и /api/ — как раньше, 307: постоянный редирект запросов API браузер запомнил бы навсегда."""
    r = client.post("/api/auth/email/start/", json={"email": "a@b.c"}, follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"].endswith("/api/auth/email/start")
    assert client.get("/api/config/", follow_redirects=False).status_code == 307


def test_sitemap(client):
    r = client.get("/sitemap.xml")
    assert r.status_code == 200 and "xml" in r.headers["content-type"]
    ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9", "x": "http://www.w3.org/1999/xhtml"}
    urls = ET.fromstring(r.text).findall("s:url", ns)
    assert [u.find("s:loc", ns).text for u in urls] == [BASE + p for p in PUBLIC]
    for u in urls:
        alts = {a.get("hreflang"): a.get("href") for a in u.findall("x:link", ns)}
        if u.find("s:loc", ns).text.removeprefix(BASE) in SINGLE:
            assert alts == {}  # пары на другом языке нет
        else:
            assert set(alts) == {"ru", "en", "x-default"} and alts["x-default"] == alts["ru"]
    # lastmod — только где дата настоящая (SERBITO-445): «Что нового» — последний релиз, политика — её редакция
    lastmod = {u.find("s:loc", ns).text: getattr(u.find("s:lastmod", ns), "text", None) for u in urls}
    for p in ("/changes", "/en/changes"):
        assert lastmod[BASE + p] == P.RELEASES[0].date
    for p in ("/privacy", "/en/privacy"):
        assert lastmod[BASE + p] == P.PRIVACY_DAY
    for p in ("/bot", "/en/bot", "/weekly-review", "/en/weekly-review"):  # дата редакции текста страницы
        assert lastmod[BASE + p] == P.GUIDES_DAY
    for p in ("/about", "/en/about"):  # /about переписан с видимой датой «Обновлено» (SERBITO-470)
        assert lastmod[BASE + p] == P.ABOUT_DAY
    for p in ("/alternativa-todoist", "/en/todoist-alternative-open-source", "/en/free-gtd-apps",
              "/gtd-dlya-nachinayushih"):
        assert lastmod[BASE + p] == P.COMPARE_DAY
    for p in ("/", "/en/"):
        assert lastmod[BASE + p] is None


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
                       ("/icon-maskable-192.png", 192), ("/icon-maskable-512.png", 512)):
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


APP_PATHS = ["/next", "/waiting", "/scheduled", "/projects", "/someday", "/reference", "/done", "/review", "/account",
             "/stats", "/p/7"]


@pytest.mark.parametrize("path", APP_PATHS + ["/en" + p for p in APP_PATHS])
def test_section_addresses_open_the_app(client, path):
    """SERBITO-354: у каждого раздела и проекта свой адрес — Back в браузере ходит по ним, а ссылку можно открыть
    заново. Это то же приложение, что /i/N: личное, без лендинга, не для поисковиков."""
    r = client.get(path)
    assert r.status_code == 200 and r.headers["x-robots-tag"] == "noindex"
    assert "function loginScreen" in app_code(client, r.text) and 'id="signin"' not in r.text
    assert f'<html lang="{"en" if path.startswith("/en/") else "ru"}">' in r.text


@pytest.mark.parametrize("path", ["/inbox", "/p/x", "/nextt", "/en/p/x"])
def test_unknown_section_is_404(client, path):
    assert client.get(path).status_code == 404


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
    # Под заголовком — что это за страница и как часто выходят версии (SERBITO-448)
    intro = re.search(r"</h1><p class=\"lead\">([^<]+)</p>", body).group(1)
    assert intro == P.CHANGES[lang]["intro"]
    assert ("несколько раз в неделю" if lang == "ru" else "several times a week") in intro
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
    # Версия — из тега, который деплоится (пересборка по расписанию — тоже его тег, SERBITO-401)
    assert 'echo "APP_VERSION: \\"${RELEASE_TAG#v}\\""' in deploy


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


# ── Страница бота (SERBITO-441) и чек-лист Weekly Review (SERBITO-446) ──

def main_of(h: str) -> str:
    return h[h.index("<main>"):h.index("</main>")]


@pytest.mark.parametrize("path", GUIDES)
def test_guide_title_and_h1_carry_the_query(client, path):
    h = client.get(path).text
    query = GUIDES[path].lower()
    title = re.search(r"<title>([^<]+)</title>", h).group(1)
    h1 = re.search(r"<h1>([^<]+)</h1>", h).group(1)
    assert query in title.lower().replace("-", " "), title
    assert query in h1.lower().replace("-", " "), h1
    assert P.SITE_NAME in title  # имя продукта — из одного места (pages.SITE_NAME)
    assert "function loginScreen" not in h  # лёгкая страница, как /about


@pytest.mark.parametrize("path, lang", [("/bot", "ru"), ("/en/bot", "en")])
def test_bot_page_content(client, path, lang):
    h = client.get(path).text
    body = main_of(h)
    cta = "Открыть @gtdsrbot" if lang == "ru" else "Open @gtdsrbot"
    # Главная кнопка — сразу под вступлением, до первого h2, и ещё раз внизу
    assert body.index(f'<a class="btn" href="{P.BOT_URL}">{cta}</a>') < body.index("<h2>")
    assert body.count(f'<a class="btn" href="{P.BOT_URL}">{cta}</a>') == 2
    words = {"ru": ["Start", "Inbox", "Next", "#Проект", "@контекст", "✅ Готово", "💤 +1ч", "⏭ Next", "/login", "/email",
                    "/inbox", "/next", "/done 12", "«Привязать Telegram»", "Claude", "Add custom connector", "в 9:00"],
             "en": ["Start", "Inbox", "Next", "#Project", "@context", "✅ Done", "💤 +1h", "⏭ Next", "/login", "/email",
                    "/inbox", "/next", "/done 12", "Link Telegram", "Claude", "Add custom connector", "at 9:00"]}[lang]
    for w in words:
        assert w in body, w
    assert f"{BASE}/mcp" in body and f'href="{P.REPO_URL}#connect-claude-mcp"' in body
    assert f'href="{P.PATHS[(lang, "privacy")]}"' in body
    for text, _ in P.BOT_CAPTURE[lang]:
        assert f"<q>{text}</q>" in body, text


# Что бот делает с примерами со страницы /bot — проверяем настоящим захватом: страница не обещает того, чего нет.
# (заголовок, список, контексты, проект, есть ли напоминание)
CAPTURED = {
    "ru": {"позвонить маме завтра в 10:00": ("позвонить маме", "inbox", [], None, True),
           "через 2 часа забрать посылку": ("забрать посылку", "inbox", [], None, True),
           "в пятницу отчёт #Клиент_X @работа": ("отчёт", "next", ["работа"], "Клиент X", True),
           "оплатить счёт @телефон @комп": ("оплатить счёт", "next", ["телефон", "комп"], None, False),
           "24.10 12:00 стоматолог": ("стоматолог", "inbox", [], None, True),
           "напомни купить хлеб в 18:30": ("купить хлеб", "inbox", [], None, True)},
    "en": {"call mom tomorrow at 10am": ("call mom", "inbox", [], None, True),
           "in 2 hours pick up the parcel": ("pick up the parcel", "inbox", [], None, True),
           "report on friday #Client_X @work": ("report", "next", ["work"], "Client X", True),
           "pay the bill @phone @computer": ("pay the bill", "next", ["phone", "computer"], None, False),
           "dentist 24 oct 12:00": ("dentist", "inbox", [], None, True),
           "remind me to buy bread at 6:30pm": ("buy bread", "inbox", [], None, True)},
}


@pytest.mark.parametrize("lang", ["ru", "en"])
def test_bot_page_capture_examples_work(client, lang):
    uid = A.email_user("alice@example.com")
    assert [t for t, _ in P.BOT_CAPTURE[lang]] == list(CAPTURED[lang])
    for text, want in CAPTURED[lang].items():
        it = A.capture(uid, text, "telegram")
        got = (it["title"], it["status"], it["contexts"], it["project"], it["remind_at"] is not None)
        assert got == want, text


def app_review_steps() -> dict:
    """Шаги раздела Weekly Review в приложении — из словаря интерфейса index.html (review.s1…s6), по языкам."""
    page = open(f"{A.STATIC}/index.html", encoding="utf-8").read()
    steps = re.findall(r'"s\d": "\d\. ([^"]+)"', page)
    assert len(steps) == 12  # шесть шагов, русский и английский словарь
    return {"ru": steps[:6], "en": steps[6:]}


@pytest.mark.parametrize("path, lang", [("/weekly-review", "ru"), ("/en/weekly-review", "en")])
def test_weekly_review_page_matches_app(client, path, lang):
    h = client.get(path).text
    body = main_of(h)
    steps = app_review_steps()[lang]
    # Чек-лист — те же шесть шагов и в том же порядке, что в приложении
    listed = re.findall(r"<li><b>\d\. ([^<]+)</b>", body)
    assert listed == steps
    # Шаблон — простым текстом, его можно скопировать: все шаги, флажки «[ ]», без HTML-разметки внутри
    tpl = re.search(r'<pre id="tpl">([^<]+)</pre>', body).group(1)
    for i, step in enumerate(steps, 1):
        assert f"{i}. {step}" in tpl, step
    assert tpl.count("[ ]") >= 6 and 'data-copy="tpl"' in body
    # Пример на 30 минут и кнопка начать обзор в приложении
    assert "30" in re.search(r"<h2>([^<]+30[^<]+)</h2>", body).group(1)
    review = "/review" if lang == "ru" else "/en/review"
    cta = ("Начать обзор в " if lang == "ru" else "Start the review in ") + P.SITE_NAME
    assert f'<a class="btn" href="{review}">{cta}</a>' in body
    # Заголовки разделов — вопросы, и сразу под каждым — короткий прямой ответ
    for q, answer in re.findall(r"<h2>([^<]+)</h2>\s*<p>(.+?)</p>", body, re.S):
        assert q.endswith("?"), q
        first = re.sub(r"<[^>]+>", "", answer).split(". ")[0]
        assert len(first.split()) <= 25, (q, first)
    assert all(q.endswith("?") for q in re.findall(r"<h2>([^<]+)</h2>", body))


@pytest.mark.parametrize("lang", ["ru", "en"])
def test_guides_linked_from_landing_about_and_footer(client, lang):
    bot, weekly = P.PATHS[(lang, "bot")], P.PATHS[(lang, "weekly")]
    land = client.get(P.PATHS[(lang, "home")]).text
    root = land[land.index('<div id="root">'):land.index("<footer>")]
    about = client.get(P.PATHS[(lang, "about")]).text
    for page in (root, main_of(about)):
        assert f'href="{bot}"' in page and f'href="{weekly}"' in page
    for path in [p for p, lg in PUBLIC.items() if lg == lang]:
        h = client.get(path).text
        foot = h[h.index("<footer>"):h.index("</footer>")]
        assert f'<a href="{bot}">' in foot and f'<a href="{weekly}">' in foot, path
    assert "/weekly-review" not in A.APP_VIEWS and "bot" not in A.APP_VIEWS  # не путаем с разделами приложения


def test_guides_ga_page_view_only_on_prod(client, monkeypatch):
    monkeypatch.setattr(A, "GA_ID", "G-TEST123")
    assert "googletagmanager" not in client.get("/bot").text
    prod = client.get("/en/weekly-review", headers={"host": "gtd.serbito.rs"}).text
    assert "gtag/js?id=G-TEST123" in prod and "location.origin + '/en/weekly-review'" in prod


class Outline(HTMLParser):
    """Ориентиры и заголовки страницы: сколько <main>, какие h1–h6 по порядку и внутри ли они <main>.
    Содержимое <script> HTMLParser не разбирает — заготовки разметки в JS SPA не считаются."""

    def __init__(self):
        super().__init__()
        self.mains, self.depth, self.heads = 0, 0, []

    def handle_starttag(self, tag, attrs):
        if tag == "main":
            self.mains += 1
            self.depth += 1
        elif re.fullmatch(r"h[1-6]", tag):
            self.heads.append((int(tag[1]), self.depth > 0))

    def handle_endtag(self, tag):
        if tag == "main":
            self.depth -= 1


def outline(html):
    o = Outline()
    o.feed(html)
    o.close()
    return o


@pytest.mark.parametrize("path, status", [*((p, 200) for p in PUBLIC), ("/no-such-page", 404), ("/en/no-such-page", 404)])
def test_public_page_has_main_and_heading_order(client, path, status):
    """WCAG 1.3.1 (SERBITO-349): у каждой публичной страницы один <main> с единственным h1, и уровни заголовков
    не перескакивают (за h2 — h3, а не h4)."""
    r = client.get(path, headers={"Accept": "text/html"})  # без text/html 404 отдаётся JSON-ом
    assert r.status_code == status
    o = outline(r.text)
    assert o.mains == 1 and o.depth == 0, path
    levels = [lvl for lvl, _ in o.heads]
    assert levels[0] == 1 and levels.count(1) == 1 and o.heads[0][1], (path, o.heads)
    assert all(b <= a + 1 for a, b in zip(levels, levels[1:])), (path, levels)


@pytest.mark.parametrize("lang", ["ru", "en"])
def test_service_pages_have_main(lang):
    """Служебные страницы из pages.py — устаревшая ссылка входа и подтверждение входа — тоже с <main> и одним h1."""
    for html in (P.auth_stale(lang, "gtd_test_bot"), P.auth_confirm(lang, "alice@example.com", "tok", current="bob")):
        o = outline(html)
        assert o.mains == 1 and [lvl for lvl, _ in o.heads] == [1] and o.heads[0][1]


# ── Лендинг: отличие, скриншоты, сравнение, FAQ, свой сервер, кто делает (SERBITO-442) ──

def landing_main(client, lang) -> str:
    h = client.get(P.PATHS[(lang, "home")]).text
    return h[h.index("<main>"):h.index("</main>")]


def webp_size(data: bytes) -> tuple[int, int]:
    """Ширина и высота WebP по заголовку: VP8 (с потерями), VP8L (без потерь) или VP8X (расширенный)."""
    assert data[:4] == b"RIFF" and data[8:12] == b"WEBP", data[:16]
    kind = data[12:16]
    if kind == b"VP8 ":
        return int.from_bytes(data[26:28], "little") & 0x3FFF, int.from_bytes(data[28:30], "little") & 0x3FFF
    if kind == b"VP8L":
        b = int.from_bytes(data[21:25], "little")
        return (b & 0x3FFF) + 1, ((b >> 14) & 0x3FFF) + 1
    assert kind == b"VP8X", kind
    return int.from_bytes(data[24:27], "little") + 1, int.from_bytes(data[27:30], "little") + 1


@pytest.mark.parametrize("lang", ["ru", "en"])
def test_landing_states_the_difference_and_has_600_words(client, lang):
    """Первый экран называет отличие (GTD + Telegram-бот + открытый код), описание не длиннее 155 знаков,
    а текста на странице — 600+ слов (аудит SEO: было ~270)."""
    body = landing_main(client, lang)
    h1 = re.search(r"<h1>([^<]+)</h1>", body).group(1)
    for w in {"ru": ("GTD", "Telegram", "открытым кодом", "бесплатно"), "en": ("GTD", "Telegram", "open source", "Free")}[lang]:
        assert w in h1, (w, h1)
    assert len(P.T[lang]["home_desc"]) <= 155
    assert words(body) >= 600, words(body)


@pytest.mark.parametrize("lang", ["ru", "en"])
def test_landing_screenshots(client, lang):
    """Три настоящих скриншота (Inbox, Next по контексту, Weekly Review): у каждого alt, width и height — место
    под картинку есть до загрузки (CLS); файлы отдаются как WebP нужного размера и кэшируются."""
    imgs = re.findall(r"<img ([^>]+)>", landing_main(client, lang))
    assert len(imgs) == 3
    for view, img in zip(P.SHOT_VIEWS, imgs):
        attrs = dict(re.findall(r'(\w+)="([^"]*)"', img))
        assert attrs["src"] == f"/shots/{view}-{lang}.webp"
        assert (int(attrs["width"]), int(attrs["height"])) == P.SHOT_SIZE
        assert len(attrs["alt"].split()) >= 6 and attrs["loading"] == "lazy"
        r = client.get(attrs["src"])
        assert r.status_code == 200 and r.headers["content-type"] == "image/webp"
        assert "max-age=" in r.headers["cache-control"]
        assert webp_size(r.content) == P.SHOT_SIZE, attrs["src"]
        assert len(r.content) < 80_000  # лёгкие: картинки внизу страницы не тормозят телефон


@pytest.mark.parametrize("path", ["/shots/nope.webp", "/shots/..%2Fpages.py", "/shots/index.html"])
def test_screenshots_route_serves_only_listed_files(client, path):
    assert client.get(path).status_code == 404


@pytest.mark.parametrize("lang", ["ru", "en"])
def test_landing_comparison_is_sourced_and_honest(client, lang):
    """Сравнение с Todoist, TickTick и Things: факты — со ссылками на официальные страницы и с датой проверки;
    есть «кому не стоит переходить» и ссылка на подробное сравнение."""
    body = landing_main(client, lang)
    table = body[body.index('<table class="cmp">'):body.index("</table>")]
    for name in ("GTD", "Todoist", "TickTick", "Things"):
        assert f"<td>{name}</td>" in table, name
    for app in ("todoist", "ticktick", "things"):
        for u, _ in P.APPS[app]["src"]:
            assert u.startswith("https://") and f'href="{u}"' in body, u
    assert P.COMPARE_DAY.startswith("2026-10") and ("октябрь 2026" if lang == "ru" else "October 2026") in body
    for item in P.WHO_NOT[lang]:
        assert f"<li>{item}</li>" in body
    assert f'href="{P.PATHS[(lang, "alt")]}"' in body


@pytest.mark.parametrize("lang", ["ru", "en"])
def test_landing_faq_plain_html(client, lang):
    """FAQ — обычный HTML, 5–6 вопросов, ответы по 40–60 слов. Разметки FAQPage нет: Google больше не показывает
    FAQ в выдаче (решение аудита SERBITO-437)."""
    h = client.get(P.PATHS[(lang, "home")]).text
    faq = re.search(r'<section class="faq">(.+?)</section>', h, re.S).group(1)
    qa = re.findall(r"<h3>([^<]+)</h3><p>(.+?)</p>", faq, re.S)
    assert 5 <= len(qa) <= 6
    for q, a in qa:
        assert q.endswith("?") and 40 <= words(a) <= 60, (q, words(a))
    assert "FAQPage" not in h and "Question" not in re.search(r'application/ld\+json">(.+?)</script>', h).group(1)


@pytest.mark.parametrize("lang", ["ru", "en"])
def test_landing_self_host_mcp_and_who_makes_it(client, lang):
    """Для разработчиков — свой сервер и MCP; «Кто делает» — только то, что уже публично: No Handoff, лицензия
    и Issues на GitHub, дата первой версии и их число. Личных данных сверх этого нет (почта — только на /privacy)."""
    body = landing_main(client, lang)
    assert f'href="{P.SELF_HOST_URL}"' in body and "Docker Compose" in body and "MCP" in body
    assert f'href="{P.PATHS[(lang, "bot")]}#claude"' in body
    assert f'href="{P.NOHANDOFF_URL}">No Handoff</a>' in body
    assert f'href="{P.REPO_URL}/blob/main/LICENSE"' in body and f'href="{P.REPO_URL}/issues"' in body
    assert "Alexander Bondarchuk" in body and P.human_date(P.RELEASES[-1].date, lang) in body
    assert P.PRIVACY_EMAIL not in body and "tel:" not in body
    assert P.WHY == {"ru": "", "en": ""}  # истории «почему» публично ещё нет — блок появится, когда владелец впишет


# ── /about в форме «сначала ответ» (SERBITO-470) ──

@pytest.mark.parametrize("lang", ["ru", "en"])
def test_about_is_answer_first(client, lang):
    """Первый абзац отвечает, что это, для кого и бесплатно ли. Заголовки разделов — вопросы, сразу под каждым —
    ответ на 40–60 слов, и ровно одно определение на 140–160 слов. Видна дата «Обновлено»."""
    body = main_of(client.get(P.PATHS[(lang, "about")]).text)
    lead = re.search(r'<p class="lead">(.+?)</p>', body, re.S).group(1)
    for w in {"ru": ("бесплатное приложение", "Getting Things Done", "Telegram"),
              "en": ("free app", "Getting Things Done", "Telegram")}[lang]:
        assert w in lead, w
    assert f"{P.UPDATED[lang]} {P.human_date(P.ABOUT_DAY, lang)}" in body
    heads = re.findall(r"<h2>([^<]+)</h2>", body)
    answers = re.findall(r"<h2>[^<]+</h2>\s*<p>(.+?)</p>", body, re.S)
    assert len(heads) == len(answers) >= 6 and all(q.endswith("?") for q in heads), heads
    counts = [words(a) for a in answers]
    assert sum(140 <= n <= 160 for n in counts) == 1, counts
    assert all(40 <= n <= 60 for n in counts if not 140 <= n <= 160), counts


# ── Сравнение и опорные страницы (SERBITO-470) ──

@pytest.mark.parametrize("path, lang", [("/alternativa-todoist", "ru"), ("/en/todoist-alternative-open-source", "en"),
                                        ("/en/free-gtd-apps", "en"), ("/gtd-dlya-nachinayushih", "ru")])
def test_comparison_and_pillar_pages(client, path, lang):
    h = client.get(path).text
    body = main_of(h)
    assert all(q.endswith("?") for q in re.findall(r"<h2>([^<]+)</h2>", body))  # заголовки — вопросы
    assert f"{P.UPDATED[lang]} {P.human_date(P.COMPARE_DAY, lang)}" in body
    if path != "/gtd-dlya-nachinayushih":  # сравнения: источники с датой и «кому не стоит переходить»
        assert ("октябрь 2026" if lang == "ru" else "October 2026") in body
        assert 'href="https://www.todoist.com/pricing"' in body
        for item in P.WHO_NOT[lang]:
            assert f"<li>{item}</li>" in body
    else:  # опорная: ведёт на все справочные страницы
        for page in ("about", "weekly", "bot", "alt"):
            assert f'href="{P.PATHS[("ru", page)]}"' in body, page
    # Переключатель языка: на ту же страницу, а если пары нет — на главную другого языка
    other = "en" if lang == "ru" else "ru"
    key = next(pg for (lg, pg), p in P.PATHS.items() if p == path)
    switch = re.search(r'<a href="([^"]+)" hreflang="' + other, h).group(1)
    assert switch == P.PATHS.get((other, key), P.PATHS[(other, "home")])


def test_free_gtd_apps_lists_five_apps_with_sources(client):
    body = main_of(client.get("/en/free-gtd-apps").text)
    for app in ("gtd", "nirvana", "todoist", "ticktick", "things"):
        assert f"<td>{P.APPS[app]['name']}</td>" in body, app
        for u, _ in P.APPS[app]["src"]:
            assert f'href="{u}"' in body, u
