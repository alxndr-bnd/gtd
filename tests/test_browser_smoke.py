"""Браузерный смоук SPA (SERBITO-293): pytest до сих пор только грепал static/index.html, и исключение
в рендере (TypeError в emptyHtml — пустые Next/Waiting/Someday не рисовались, v0.10.0) ушло в прод
с зелёным гейтом. Здесь страница реально исполняется в Chromium: каждый раздел на RU и EN, с пустыми
списками и с задачами, карточка /i/N и публичные страницы. Любая ошибка в консоли, необработанное
исключение или ответ ≥400 — падение.

Сервер — то же приложение в этом же процессе (uvicorn в потоке) на свободном порту: база, env и
заглушки — из conftest (временная база, DEV=1, бот выключен), наружу ничего не ходит. Внешние
запросы браузера блокируются и тоже роняют тест; Google Sign-In подменён заглушкой.

Не пропускается никогда: нет Playwright или Chromium — тест падает с командой установки."""
import socket
import threading
import time

import pytest
import uvicorn

import app as A

try:
    from playwright.sync_api import Error as PWError, TimeoutError as PWTimeout, sync_playwright
except ImportError:  # громко на сборе тестов, а не тихий skip
    pytest.fail("Нет Playwright: .venv/bin/pip install -r requirements-dev.txt && "
                ".venv/bin/playwright install chromium", pytrace=False)

INSTALL_HINT = "Нет Chromium для Playwright: запустите `.venv/bin/playwright install chromium`"
WAIT_MS = 5000  # потолок ожидания одного условия рендера; обычно — десятки миллисекунд

# Кнопка Google грузит скрипт с accounts.google.com — в тестах вместо него заглушка с тем же API
GSI_STUB = ("window.google={accounts:{id:{initialize(){},"
            "renderButton(el){el.textContent='Google';}}}};")
SECTIONS = ["inbox", "next", "waiting", "scheduled", "projects", "someday", "reference", "done", "review"]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def server():
    """Приложение в фоновом потоке. lifespan выключен: иначе на выходе он закроет общий пул базы,
    а фоновые циклы (бот, напоминания) смоуку не нужны."""
    port = _free_port()
    srv = uvicorn.Server(uvicorn.Config(A.app, host="127.0.0.1", port=port, lifespan="off", log_level="warning"))
    th = threading.Thread(target=srv.run, daemon=True)
    th.start()
    deadline = time.monotonic() + 10
    while not srv.started:
        assert th.is_alive() and time.monotonic() < deadline, "uvicorn не поднялся"
        time.sleep(0.02)
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True
    th.join(5)


@pytest.fixture(scope="module")
def pw():
    with sync_playwright() as p:  # один на модуль: второй sync_playwright внутри первого Playwright не даёт
        yield p


def launch(pw, *args):
    try:
        return pw.chromium.launch(args=list(args))
    except PWError as e:
        if "Executable doesn't exist" in str(e) or "playwright install" in str(e):
            pytest.fail(f"{INSTALL_HINT}\n{str(e).splitlines()[0]}", pytrace=False)
        raise


@pytest.fixture(scope="module")
def browser(pw):
    b = launch(pw)
    yield b
    b.close()


class Watch:
    """Страница, которая копит всё, что считается поломкой: ошибки консоли, необработанные исключения,
    ответы ≥400 своего сервера и попытки сходить наружу."""

    def __init__(self, browser, base, lang="ru", stubs=None):
        """stubs — {начало внешнего URL: тело JS}: локальные подмены внешних скриптов (GA на «проде»)."""
        self.base, self.errors, self.expected_404, self.stubs = base, [], set(), stubs or {}
        self.ctx = browser.new_context(locale="en-US" if lang == "en" else "ru-RU",
                                       viewport={"width": 1280, "height": 900})
        self.ctx.route("**/*", self._route)
        self.page = self.ctx.new_page()
        self.page.on("console", self._console)
        self.page.on("pageerror", lambda e: self.errors.append(f"pageerror: {e}"))
        self.page.on("response", self._response)

    def _route(self, route):
        url = route.request.url
        if url.startswith(self.base + "/"):
            return route.continue_()
        if url.startswith("https://accounts.google.com/gsi/client"):
            return route.fulfill(status=200, content_type="text/javascript", body=GSI_STUB)
        for prefix, body in self.stubs.items():
            if url.startswith(prefix):
                return route.fulfill(status=200, content_type="text/javascript", body=body)
        # Cloudflare и GA подключаются только на gtd.serbito.rs — здесь любой внешний запрос это поломка
        self.errors.append(f"внешний запрос: {url}")
        return route.abort()

    def _console(self, msg):
        if msg.type != "error":
            return
        # Единственный ожидаемый шум: Chromium пишет в консоль «Failed to load resource … 404» для самой
        # страницы 404, которую мы открываем нарочно. Разрешаем ровно этот URL и ровно этот текст.
        if (msg.location.get("url") in self.expected_404
                and msg.text.startswith("Failed to load resource: the server responded with a status of 404")):
            return
        self.errors.append(f"console.error: {msg.text} @ {msg.location.get('url')}")

    def _response(self, r):
        if r.url.startswith(self.base) and r.status >= 400 and r.url not in self.expected_404:
            self.errors.append(f"HTTP {r.status}: {r.request.method} {r.url}")

    def goto(self, path, expect_status=200):
        url = self.base + path
        if expect_status == 404:
            self.expected_404.add(url)
        r = self.page.goto(url)
        assert r.status == expect_status, f"{path}: HTTP {r.status}"

    def wait(self, selector, what):
        """Ждём признак отрисовки. Ждём кусками по 100 мс: если страница уже упала с ошибкой, рендера
        не будет — падаем сразу с её текстом, а не через весь таймаут."""
        deadline = time.monotonic() + WAIT_MS / 1000
        while True:
            try:
                self.page.wait_for_selector(selector, timeout=100, state="attached")
                break
            except PWTimeout:
                self.check(f"{what} (нет «{selector}»)")
                if time.monotonic() > deadline:
                    pytest.fail(f"{what}: нет «{selector}» за {WAIT_MS} мс, ошибок на странице нет", pytrace=False)
        self.check(what)

    def check(self, what):
        assert not self.errors, f"{what}:\n" + "\n".join(self.errors)

    def close(self):
        self.ctx.close()


@pytest.fixture
def watch(browser, server):
    opened = []

    def make(lang="ru"):
        w = Watch(browser, server, lang)
        opened.append(w)
        return w
    yield make
    for w in opened:
        w.close()


def seed(uid):
    """По задаче в каждый список: контекст и проект (Next, чипы), напоминание (Scheduled),
    старое ожидание (Review), выполненная и удалённая."""
    ids = {k: A.capture(uid, text)["id"] for k, text in [
        ("inbox", "Позвонить маме"), ("ctx", "Buy milk @home"), ("proj", "Отчёт #Проект_Альфа"),
        ("waiting", "Ждать ответа"), ("someday", "Выучить сербский"), ("reference", "Справка"),
        ("done", "Сделано"), ("trash", "В корзину"), ("remind", "Записаться к врачу")]}
    now = int(time.time())
    for status in ("waiting", "someday", "reference", "done", "trash"):
        A.run("update items set status=%s where id=%s", (status, ids[status]))
    A.run("update items set created=%s where id=%s", (now - 10 * 86400, ids["waiting"]))
    A.run("update items set completed_at=%s where id=%s", (now, ids["done"]))
    A.run("update items set remind_at=%s where id=%s", (now + 3600, ids["remind"]))
    return ids


def open_section(w, view, has_items, lang):
    w.page.click(f'nav > a[data-view="{view}"]')
    w.wait(f'nav > a.on[data-view="{view}"]', f"[{lang}] раздел {view}")
    main = w.page.locator("main")
    if view == "review":
        assert main.locator(".rv").count() >= 6, f"[{lang}] review: нет шагов обзора"
    elif view == "projects":
        assert main.locator(".it.pj" if has_items else ".empty").count(), f"[{lang}] projects"
    elif view == "inbox":
        assert main.locator(".hero #cap").count(), f"[{lang}] inbox: нет поля захвата"
        assert (main.locator(".it").count() > 0) == has_items, f"[{lang}] inbox: список"
    else:  # пустой список рисуется через emptyHtml, непустой — карточками задач
        assert main.locator(".it" if has_items else ".empty").count(), f"[{lang}] {view}: пусто/задачи"


@pytest.mark.parametrize("lang", ["ru", "en"])
def test_app_sections(watch, monkeypatch, lang):
    uid = A.run("insert into users(tg_id,name,created,lang) values(0,'Smoke',%s,%s) returning id",
                (int(time.time()), lang))
    monkeypatch.setattr(A, "ADMIN_USER_IDS", {uid})  # владельцу видна «Статистика»
    w = watch(lang)
    w.goto("/dev-login")  # вход DEV=1: сессия первого пользователя и редирект на /
    w.wait('nav > a.on[data-view="inbox"]', f"[{lang}] вход")
    assert w.page.evaluate("document.documentElement.lang") == lang

    for has_items in (False, True):
        if has_items:
            ids = seed(uid)
            w.page.reload()
            w.wait('nav > a.on[data-view="inbox"]', f"[{lang}] перезагрузка с задачами")
        for view in SECTIONS + ["inbox"]:  # и обратно во Входящие: переходы между разделами
            open_section(w, view, has_items, lang)
        # «Аккаунт» — в свёрнутом меню пользователя; кнопка Google — из заглушки GSI
        w.page.click("nav .navfoot button.user")
        w.page.click('nav .navfoot .umenu a[data-view="account"]')
        w.wait('main .gbtn:has-text("Google")', f"[{lang}] account")
        w.page.click('nav > a[data-view="stats"]')
        w.wait('nav > a.on[data-view="stats"]', f"[{lang}] stats")
        w.wait("main .tiles", f"[{lang}] stats")

    # Проект: карточка в списке проектов → его задачи
    open_section(w, "projects", True, lang)
    w.page.click("main .it.pj .t")
    w.wait('main [data-act="back"]', f"[{lang}] проект")
    assert w.page.locator("main .it").count() == 1

    # Карточка задачи — кликом из списка и прямой ссылкой /i/N
    open_section(w, "next", True, lang)
    w.page.click("main .it .t")
    w.wait("#dlg[open] #ef", f"[{lang}] карточка из списка")
    assert "/i/" in w.page.url
    num = A.row("select num from items where id=%s", (ids["inbox"],))["num"]
    w.goto(f"/i/{num}")
    w.wait("#dlg[open] #ef", f"[{lang}] карточка /i/{num}")
    assert w.page.input_value('#ef [name="title"]') == "Позвонить маме"
    w.check(f"[{lang}] итог")


@pytest.mark.parametrize("path, status, ready", [
    ("/", 200, '#signin a[href="/dev-login"]'),   # лендинг гостю: кнопки входа кладёт JS
    ("/en/", 200, '#signin a[href="/dev-login"]'),
    ("/i/1", 200, '#root .login a[href="/dev-login"]'),  # ссылка на задачу без входа — экран входа
    ("/about", 200, "body"),
    ("/en/about", 200, "body"),
    ("/changes", 200, "section.rel"),
    ("/en/changes", 200, "section.rel"),
    ("/no-such-page", 404, "body"),
    ("/en/no-such-page", 404, "body"),
])
def test_public_pages(watch, path, status, ready):
    w = watch("en" if path.startswith("/en") else "ru")
    w.goto(path, status)
    w.page.wait_for_load_state("load")
    w.wait(ready, path)


def test_menu_whats_new_and_version(watch, monkeypatch):
    """SERBITO-329: в меню — «Что нового» с работающей версией. Первый визит запоминает версию молча;
    вышла новая — у ссылки точка, пока человек не откроет /changes."""
    import pages as P
    A.run("insert into users(tg_id,name,created) values(0,'Smoke',%s)", (int(time.time()),))
    w = watch("ru")
    w.goto("/dev-login")
    link = 'nav > a.changes[href="/changes"]'
    w.wait(link, "меню: Что нового")
    assert w.page.locator(link).inner_text().endswith("dev") and not w.page.locator(link + " .newdot").count()
    assert w.page.evaluate("localStorage.getItem('gtd-seen-version')") == "dev"
    monkeypatch.setattr(P, "VERSION", "0.99.0")  # «вышел релиз»
    monkeypatch.setattr(P, "VERSION_LABEL", "v0.99.0")
    w.page.reload()
    w.wait(link + " .newdot", "точка: версия новее увиденной")
    w.page.click(link)
    w.wait("section.rel", "/changes из меню")
    w.goto("/")
    w.wait(link, "меню после /changes")
    assert "v0.99.0" in w.page.locator(link).inner_text() and not w.page.locator(link + " .newdot").count()
    w.check("меню: Что нового")


@pytest.mark.parametrize("lang", ["ru", "en"])
def test_telegram_fallback_deep_link(watch, monkeypatch, lang):
    """SERBITO-292: не на gtd.serbito.rs Login Widget не грузится (запрос к telegram.org уронил бы тест) —
    работает запасной путь: кнопка → ссылка в бота, и на экране входа, и в «Аккаунте»."""
    monkeypatch.setattr(A, "TOKEN", "smoke-token")  # бот «включён»: /api/config отдаёт его имя
    monkeypatch.setattr(A, "BOT_USERNAME", "gtd_smoke_bot")
    link = 'a[href^="https://t.me/gtd_smoke_bot?start="]'
    w = watch(lang)
    w.goto("/en/" if lang == "en" else "/")
    w.wait('#signin button.tgfb[data-act="tglogin"]', f"[{lang}] вход: кнопка Telegram")
    assert w.page.is_visible("#signin .tgfb") and not w.page.locator(".tgw script").count()
    w.page.click('#signin [data-act="tglogin"]')
    w.wait(f"#tgst {link}", f"[{lang}] вход: ссылка в бота")

    A.run("insert into users(name,created,lang) values('Smoke',%s,%s)", (int(time.time()), lang))
    w.goto("/dev-login")
    w.wait('nav > a.on[data-view="inbox"]', f"[{lang}] вход")
    w.page.click("nav .navfoot button.user")
    w.page.click('nav .navfoot .umenu a[data-view="account"]')
    w.wait('main button.tgfb[data-act="tglogin"]', f"[{lang}] account: кнопка Telegram")
    assert w.page.is_visible("main .tgfb") and not w.page.locator(".tgw script").count()
    w.page.click('main [data-act="tglogin"]')
    w.wait(f"main #tgst {link}", f"[{lang}] account: ссылка в бота")
    w.check(f"[{lang}] итог")


def smoke_user(w, lang="ru"):
    """Пользователь с задачами во всех списках, вошедший через /dev-login; возвращает id задач."""
    uid = A.run("insert into users(tg_id,name,created,lang) values(0,'Smoke',%s,%s) returning id",
                (int(time.time()), lang))
    ids = seed(uid)
    w.goto("/dev-login")
    w.wait('nav > a.on[data-view="inbox"]', f"[{lang}] вход")
    return ids


def test_card_title_suggestions(watch):
    """SERBITO-298: в заголовке карточки те же подсказки #проект / @контекст, что в поле захвата,
    с выбором с клавиатуры; сохранение разбирает токены на сервере."""
    w = watch()
    ids = smoke_user(w)
    w.page.click(f'main .it[data-id="{ids["inbox"]}"] .t')
    w.wait("#dlg[open] #ef", "карточка")
    title = w.page.locator('#ef [name="title"]')
    title.press("End")
    title.type(" #Про")
    w.wait('#ef .ac:not([hidden]) .aco:has-text("#Проект Альфа")', "подсказка #")
    title.press("Enter")  # выбирает подсказку, а не отправляет карточку
    assert w.page.locator("#dlg[open]").count() and title.input_value() == "Позвонить маме #Проект_Альфа "
    title.type("@h")
    w.wait('#ef .ac:not([hidden]) .aco:has-text("@home")', "подсказка @")
    title.press("Escape")  # закрывает только подсказки
    assert w.page.locator("#ef .ac[hidden]").count() and w.page.locator("#dlg[open]").count()
    title.type("o")
    w.wait('#ef .ac:not([hidden]) .aco:has-text("@home")', "подсказка @ снова")
    title.press("Tab")
    assert title.input_value() == "Позвонить маме #Проект_Альфа @home "
    w.page.click('#ef button.pri')
    w.wait(f'main .it[data-id="{ids["inbox"]}"] .meta:has-text("@home")', "сохранено")
    it = A.row("select i.title, i.context, p.title project from items i join projects p on p.id=i.project_id "
               "where i.id=%s", (ids["inbox"],))
    assert (it["title"], it["context"], it["project"]) == ("Позвонить маме", "home", "Проект Альфа")
    w.check("итог")


def test_click_anywhere_on_card_opens_it(watch):
    """SERBITO-299: карточку открывает клик по любому её месту, а не только по заголовку; кнопки
    внутри делают своё и карточку не открывают; конец выделения текста — тоже не открывает."""
    w = watch()
    ids = smoke_user(w)
    card = f'main .it[data-id="{ids["inbox"]}"]'
    w.wait(card, "список")
    w.page.click(card, position={"x": 4, "y": 4})  # угол карточки — мимо заголовка
    w.wait("#dlg[open] #ef", "карточка по клику в тело")
    assert w.page.url.endswith("/i/1")
    w.page.click('#ef [data-act="cancel"]')
    w.page.wait_for_function("location.pathname === '/'", timeout=WAIT_MS)  # close — событие, адрес меняется в нём
    assert not w.page.locator("#dlg[open]").count()

    # Выделили часть заголовка мышью — это не клик «открыть»
    box = w.page.locator(f"{card} .t").bounding_box()
    w.page.mouse.move(box["x"] + 2, box["y"] + box["height"] / 2)
    w.page.mouse.down()
    w.page.mouse.move(box["x"] + box["width"] - 2, box["y"] + box["height"] / 2, steps=5)
    w.page.mouse.up()
    assert w.page.evaluate("getSelection().toString()")
    w.page.wait_for_timeout(200)
    assert not w.page.locator("#dlg[open]").count()

    # ⌘/Ctrl-клик по номеру #N — ссылка /i/N в новой вкладке, здесь карточка не открывается
    with w.ctx.expect_page() as tab:
        w.page.click(f"{card} a.num", modifiers=["ControlOrMeta"])
    assert tab.value.url.endswith("/i/1")
    tab.value.close()
    assert not w.page.locator("#dlg[open]").count()

    # Кнопка списка — переносит задачу, карточку не открывает
    w.page.click(f'{card} [data-act="mv"][data-st="someday"]')
    w.wait("#toast:not([hidden])", "перенос")
    w.page.wait_for_timeout(200)
    assert not w.page.locator("#dlg[open]").count() and "/i/" not in w.page.url
    assert A.row("select status from items where id=%s", (ids["inbox"],))["status"] == "someday"
    w.check("итог")


# ── Согласие на cookie аналитики (SERBITO-319) ──
import pages as P  # noqa: E402

YEAR_MS = 365 * 86400 * 1000


def consent_calls(w):
    """Вызовы gtag('consent', …) по порядку: gtag() — это dataLayer.push, он есть на любом хосте."""
    return w.page.evaluate("dataLayer.filter(a => a[0] === 'consent').map(a => [a[1], a[2]])")


def all_kinds(v, **extra):
    """Состояние Consent Mode: выбор — только про аналитику, рекламные разрешения всегда denied."""
    return {"ad_storage": "denied", "analytics_storage": v, "ad_user_data": "denied",
            "ad_personalization": "denied", **extra}


def stored_choice(w):
    return w.page.evaluate(f"JSON.parse(localStorage.getItem('{P.CONSENT_KEY}'))")


def banner(w, what):
    w.wait("#cc:not([hidden])", what)
    assert w.page.is_visible("#cc"), what
    return w.page.locator("#cc")


@pytest.mark.parametrize("lang", ["ru", "en"])
def test_consent_banner(watch, monkeypatch, lang):
    """Первый визит — баннер на языке страницы и consent default «запрещено»; Принять/Отклонить — нужные
    update, выбор с датой в localStorage и применяется в default при следующем визите; через 12 месяцев
    спрашиваем снова; «Настройки cookie» в подвале каждой страницы и в меню приложения открывают баннер,
    отказ стирает _ga*. Всё — с клавиатуры в том числе."""
    monkeypatch.setattr(A, "GA_ID", "G-TEST")  # баннер — только при заданном GA; gtag.js на localhost не грузится
    t, home = P.CONSENT[lang], P.PATHS[(lang, "home")]
    w = watch(lang)
    w.goto(home)
    cc = banner(w, f"[{lang}] баннер при первом визите")
    assert t["text"] in cc.inner_text() and cc.get_attribute("aria-label") == t["label"]
    assert cc.locator(f'a[href="{P.PATHS[(lang, "privacy")]}#cookies"]').inner_text() == t["more"]
    assert consent_calls(w) == [["default", all_kinds("denied", wait_for_update=500)]]
    assert stored_choice(w) is None

    # С клавиатуры: баннер первый в порядке Tab — ссылка «Подробнее», затем «Принять»
    w.page.keyboard.press("Tab")
    w.page.keyboard.press("Tab")
    assert w.page.evaluate("document.activeElement.textContent") == t["yes"]
    w.page.keyboard.press("Enter")
    assert consent_calls(w)[-1] == ["update", all_kinds("granted")]
    assert w.page.is_hidden("#cc")
    c = stored_choice(w)
    assert c["v"] == "granted" and abs(c["t"] - w.page.evaluate("Date.now()")) < 60_000

    # Вернулся — баннера нет, согласие сразу в default
    w.page.reload()
    w.wait('#signin a[href="/dev-login"]', f"[{lang}] перезагрузка")
    assert not w.page.locator("#cc").count()
    assert consent_calls(w) == [["default", all_kinds("granted", wait_for_update=500)]]

    # «Настройки cookie» в подвале → баннер с фокусом на «Принять»; «Отклонить» стирает _ga*
    w.page.evaluate("document.cookie = '_ga=GA1.1.1.1; path=/'; document.cookie = '_ga_TEST=GS1; path=/'; "
                    "document.cookie = 'keep=1; path=/'")
    w.page.click("footer [data-cc-open]")
    banner(w, f"[{lang}] баннер из подвала")
    assert w.page.evaluate("document.activeElement.dataset.cc") == "granted"
    w.page.click('#cc [data-cc="denied"]')
    assert consent_calls(w)[-1] == ["update", all_kinds("denied")]
    assert stored_choice(w)["v"] == "denied" and w.page.is_hidden("#cc")
    assert w.page.evaluate("document.cookie") == "keep=1"

    # Выбор старше 12 месяцев — снова «запрещено» и баннер
    w.page.evaluate(f"localStorage.setItem('{P.CONSENT_KEY}', "
                    f"JSON.stringify({{v: 'granted', t: Date.now() - {YEAR_MS} - 1000}}))")
    w.page.reload()
    banner(w, f"[{lang}] баннер через 12 месяцев")
    assert consent_calls(w) == [["default", all_kinds("denied", wait_for_update=500)]]
    w.page.click('#cc [data-cc="denied"]')

    # Подвал /about и /privacy
    for page in ("about", "privacy"):
        w.goto(P.PATHS[(lang, page)])
        w.page.wait_for_load_state("load")
        assert not w.page.locator("#cc").count(), page
        w.page.click(f'footer a[data-cc-open]:text-is("{t["settings"]}")')
        banner(w, f"[{lang}] {page}: баннер из подвала")
        assert t["yes"] in w.page.inner_text("#cc")
        w.page.click('#cc [data-cc="granted"]')
        assert consent_calls(w)[-1] == ["update", all_kinds("granted")]

    # Приложение: ссылка в меню; язык баннера — язык интерфейса (SPA меняет его после входа)
    A.run("insert into users(tg_id,name,created,lang) values(0,'Smoke',%s,%s)", (int(time.time()), lang))
    w.goto("/dev-login")
    w.wait('nav > a.on[data-view="inbox"]', f"[{lang}] вход")
    w.page.click(f'nav a.ccl[data-cc-open]:text-is("{t["settings"]}")')
    cc = banner(w, f"[{lang}] баннер из меню приложения")
    assert t["text"] in cc.inner_text()
    w.page.click('#cc [data-cc="denied"]')
    assert consent_calls(w)[-1] == ["update", all_kinds("denied")] and w.page.is_hidden("#cc")
    w.check(f"[{lang}] итог")


def test_consent_without_storage(watch, monkeypatch):
    """localStorage недоступен (приватный режим, запрет сайта): без ошибок, выбор работает на этой странице,
    а при следующем визите баннер появляется снова."""
    monkeypatch.setattr(A, "GA_ID", "G-TEST")
    w = watch()
    w.ctx.add_init_script("Object.defineProperty(window, 'localStorage', "
                          "{get(){ throw new DOMException('blocked', 'SecurityError'); }})")
    w.goto("/")
    banner(w, "баннер без localStorage")
    w.page.click('#cc [data-cc="granted"]')
    assert consent_calls(w)[-1] == ["update", all_kinds("granted")] and w.page.is_hidden("#cc")
    w.page.reload()
    banner(w, "баннер снова")
    assert consent_calls(w) == [["default", all_kinds("denied", wait_for_update=500)]]
    w.check("итог")


def test_no_consent_without_ga(watch):
    """Self-hosted копия без GA: ни баннера, ни «Настройки cookie» — ни в подвале, ни в меню приложения."""
    assert A.GA_ID == ""
    w = watch()
    w.goto("/")
    w.wait('#signin a[href="/dev-login"]', "лендинг")
    assert not w.page.locator("#cc, [data-cc-open]").count()
    assert w.page.evaluate("typeof window.gtdConsent === 'undefined' && typeof window.dataLayer === 'undefined'")
    smoke_user(w)
    w.page.wait_for_timeout(300)  # баннер появился бы на DOMContentLoaded — ждём с запасом
    assert not w.page.locator("#cc, nav a.ccl, [data-cc-open]").count()
    w.check("итог")


# Заглушка gtag.js: разбирает dataLayer, как настоящий тег, и ставит _ga и _ga_<ID> только при
# analytics_storage = granted — host-only при cookie_domain 'none' в config, иначе на .serbito.rs (как GA
# с cookie_domain auto). Хиты копит в __ga.hits: [событие, состояние согласия]
GA_STANDIN = """(() => {
  const st = {}, hits = [];
  let dom = '; domain=serbito.rs';
  window.__ga = {hits};
  const cookie = () => { document.cookie = '_ga=GA1.1.1.1; path=/; max-age=63072000' + dom;
                         document.cookie = '_ga_TEST=GS1.1.1; path=/; max-age=63072000' + dom; };
  const handle = a => {
    if(a[0] === 'consent') Object.assign(st, a[2]);
    else if(a[0] === 'config' && a[2] && a[2].cookie_domain === 'none') dom = '';
    else if(a[0] === 'event') hits.push([a[1], st.analytics_storage]);
    if(st.analytics_storage === 'granted') cookie();
  };
  dataLayer.forEach(handle);
  const push = dataLayer.push.bind(dataLayer);
  dataLayer.push = (...xs) => { xs.forEach(handle); return push(...xs); };
})();"""


@pytest.fixture(scope="module")
def prod_browser(pw):
    """Chromium, у которого gtd.serbito.rs ведёт на локальный сервер: страницы с тегом GA — как на проде."""
    b = launch(pw, "--host-resolver-rules=MAP gtd.serbito.rs 127.0.0.1")
    yield b
    b.close()


def ga_cookies(w):
    return sorted((c["name"], c["domain"]) for c in w.ctx.cookies() if c["name"].startswith("_ga"))


def test_consent_on_prod_host(prod_browser, server, monkeypatch):
    """Прод-хост с настоящим порядком тегов и заглушкой gtag.js: до «Принять» — только пинги «запрещено»
    и ни одной cookie _ga; после — cookie есть и только на gtd.serbito.rs, события (about_view, task_capture)
    уходят уже с согласием, без первого пинга «запрещено»; отказ из меню приложения стирает _ga* хоста
    и старые _ga на .serbito.rs."""
    monkeypatch.setattr(A, "GA_ID", "G-TEST")
    base = "http://gtd.serbito.rs:" + server.rsplit(":", 1)[1]
    w = Watch(prod_browser, base, stubs={"https://www.googletagmanager.com/gtag/js?id=G-TEST": GA_STANDIN,
                                         "https://static.cloudflareinsights.com/beacon.min.js": ""})
    try:
        hits = lambda: w.page.evaluate("window.__ga ? __ga.hits : null")  # noqa: E731
        w.goto("/")
        banner(w, "прод: баннер")
        w.page.wait_for_function("window.__ga && __ga.hits.length", timeout=WAIT_MS)
        assert ["page_view", "denied"] in hits() and all(s == "denied" for _, s in hits())
        assert ga_cookies(w) == []

        w.page.click('#cc [data-cc="granted"]')
        assert consent_calls(w)[-1] == ["update", all_kinds("granted")]  # рекламные — всё так же denied
        assert ga_cookies(w) == [("_ga", "gtd.serbito.rs"), ("_ga_TEST", "gtd.serbito.rs")]  # host-only

        w.goto("/en/about")
        w.page.wait_for_function("window.__ga && __ga.hits.some(h => h[0] === 'about_view')", timeout=WAIT_MS)
        assert not w.page.locator("#cc").count()
        assert hits() == [["page_view", "granted"], ["about_view", "granted"]]

        A.run("insert into users(tg_id,name,created,lang) values(0,'Smoke',%s,'ru')", (int(time.time()),))
        w.goto("/dev-login")
        w.wait('nav > a.on[data-view="inbox"]', "прод: вход")
        w.page.fill("#cap", "Проверить согласие")
        w.page.press("#cap", "Enter")
        w.page.wait_for_function("__ga.hits.some(h => h[0] === 'task_capture')", timeout=WAIT_MS)
        assert all(s == "granted" for _, s in hits()) and ["page_view", "granted"] in hits()

        # Старая cookie с .serbito.rs (до host-only) — тоже уйдёт при отказе; соседняя не-GA cookie — останется
        w.ctx.add_cookies([{"name": "_ga", "value": "GA1.2.old", "domain": ".serbito.rs", "path": "/"},
                           {"name": "other", "value": "1", "domain": ".serbito.rs", "path": "/"}])
        w.page.click("nav a.ccl[data-cc-open]")
        banner(w, "прод: баннер из меню")
        w.page.click('#cc [data-cc="denied"]')
        assert ga_cookies(w) == [] and [c["name"] for c in w.ctx.cookies() if c["name"] == "other"] == ["other"]
        w.check("прод: итог")
    finally:
        w.close()
