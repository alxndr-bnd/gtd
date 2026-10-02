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
            "renderButton(el,o){el.textContent='Google';window.__gsi=o;}}}};")  # o — параметры кнопки для проверки
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


def launch(pw, *args, channel=None):
    try:
        return pw.chromium.launch(args=list(args), channel=channel)
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

    def __init__(self, browser, base, lang="ru", stubs=None, viewport=(1280, 900), touch=False):
        """stubs — {начало внешнего URL: тело JS}: локальные подмены внешних скриптов (GA на «проде»).
        touch — телефон с сенсорным экраном: pointer: coarse."""
        self.base, self.errors, self.expected_404, self.stubs = base, [], set(), stubs or {}
        self.ctx = browser.new_context(locale="en-US" if lang == "en" else "ru-RU", has_touch=touch, is_mobile=touch,
                                       viewport={"width": viewport[0], "height": viewport[1]})
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
        # Нарушения CSP в режиме report-only Chromium пишет уровнем info — ловим по тексту: смоук проверяет, что
        # политика (app.CSP_REPORT_ONLY) пропускает всё, что страницы реально грузят (SERBITO-360, GTD-9)
        if "Content Security Policy" in msg.text:
            # Кроме eval без адреса скрипта: это сам Playwright — строковый предикат wait_for_function
            if msg.text.startswith("Evaluating a string as JavaScript") and not msg.location.get("url"):
                return
            self.errors.append(f"CSP: {msg.text} @ {msg.location.get('url')}")
            return
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

    def make(lang="ru", viewport=(1280, 900), touch=False):
        w = Watch(browser, server, lang, viewport=viewport, touch=touch)
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
            w.goto("/")
            w.wait('nav > a.on[data-view="inbox"]', f"[{lang}] перезагрузка с задачами")
        for view in SECTIONS + ["inbox"]:  # и обратно в Inbox: переходы между разделами
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
    ("/next", 200, '#root .login a[href="/dev-login"]'),  # адрес раздела без входа — тоже (SERBITO-354)
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
    # Опрос (POST) уходит сразу после старта входа и отвечает без ошибок — ждём его ответ, а не таймер
    with w.page.expect_response(lambda r: r.url.endswith("/api/auth/tg/poll")) as poll:
        w.page.click('#signin [data-act="tglogin"]')
    assert poll.value.ok and poll.value.json()["status"] == "pending"
    w.wait(f"#tgst {link}", f"[{lang}] вход: ссылка в бота")
    # Число для сверки в боте (GTD-3) — то самое, что сохранено для этого входа
    code = w.page.inner_text("#tgst .tgcode")
    assert A.row("select code from tg_logins order by expires desc limit 1")["code"] == int(code)

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


def test_sign_out_erases_drafts(watch):
    """GTD-14: несохранённые черновики (поле захвата, карточка) живут в localStorage — после выхода их там нет,
    следующий человек за этим компьютером их не увидит. Прочее (язык, согласие cookie) — остаётся."""
    uid = A.run("insert into users(tg_id,name,created) values(0,'Smoke',%s) returning id", (int(time.time()),))
    A.capture(uid, "Задача")
    w = watch("ru")
    w.goto("/dev-login")
    w.wait('nav > a.on[data-view="inbox"]', "вход")
    w.page.fill("#cap", "недописанная мысль")
    w.page.click("main .it .t")
    w.wait("#dlg[open] #ef", "карточка")
    w.page.fill('#ef [name="notes"]', "черновик заметки")
    w.page.keyboard.press("Escape")
    keys = "Object.keys(localStorage).filter(k => k.startsWith('gtd-draft:')).sort()"
    assert len(w.page.evaluate(keys)) == 2
    w.page.click("nav .navfoot button.user")
    w.page.click('nav .navfoot .umenu [data-act="logout"]')
    w.wait('#signin a[href="/dev-login"]', "лендинг после выхода")
    assert w.page.evaluate(keys) == [] and w.page.evaluate("localStorage.getItem('gtd-seen-version')") == "dev"
    w.check("выход")


def test_sign_out_everywhere_from_account(watch):
    """GTD-5: «Выйти на всех устройствах» в «Аккаунте» — после подтверждения гаснут все сессии, и эта тоже."""
    uid = A.run("insert into users(tg_id,name,created) values(0,'Smoke',%s) returning id", (int(time.time()),))
    other = watch("ru")
    other.goto("/dev-login")  # второй браузер того же аккаунта
    w = watch("ru")
    w.goto("/dev-login")
    w.page.click("nav .navfoot button.user")
    w.page.click('nav .navfoot .umenu a[data-view="account"]')
    w.wait('main [data-act="logoutall"]', "аккаунт: кнопка")
    assert "Открыто в браузерах и на устройствах: 2." in w.page.inner_text("main")
    w.page.once("dialog", lambda d: d.accept())
    w.page.click('main [data-act="logoutall"]')
    w.wait('#signin a[href="/dev-login"]', "лендинг после выхода везде")
    assert A.row("select count(*) n from user_sessions where user_id=%s", (uid,))["n"] == 0
    w.check("выход везде")
    other.check("второй браузер")


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
    # Курсор в конец. Не клавишей End: Chromium на macOS при прокручиваемой странице (под Inbox теперь
    # ещё и Next, SERBITO-326) прокручивает ею страницу, а не двигает курсор
    title.evaluate("el => el.setSelectionRange(el.value.length, el.value.length)")
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


# ── Экран Inbox: что делать дальше (SERBITO-326) ──
INBOX_TEXT = {"ru": {"empty": "Inbox пуст", "due": "Скоро срок", "next": "Next", "over": "просрочено"},
              "en": {"empty": "Inbox is empty", "due": "Due soon", "next": "Next", "over": "overdue"}}


def section_ids(w, cls):
    return [int(x) for x in w.page.eval_on_selector_all(f"main .{cls} .it[data-id]", "els => els.map(e => e.dataset.id)")]


@pytest.mark.parametrize("lang", ["ru", "en"])
def test_inbox_what_to_do_next(watch, lang):
    """Три состояния: пусто совсем (сообщение, поле по центру); Inbox пуст — сообщение, затем «Скоро срок»
    и Next; во Inbox есть задачи — они, затем те же блоки. «Скоро срок» — перед Next, просроченные первыми
    и помечены; задача не повторяется ни в Next, ни (если она во Inbox) в «Скоро срок». Карточки рабочие."""
    tx = INBOX_TEXT[lang]
    uid = A.run("insert into users(tg_id,name,created,lang,checklist_hidden) values(0,'Smoke',%s,%s,true) "
                "returning id", (int(time.time()), lang))  # без чек-листа: с ним поле не по центру
    w = watch(lang)
    w.goto("/dev-login")
    w.wait('nav > a.on[data-view="inbox"]', f"[{lang}] вход")
    # 1. Ничего нет
    assert w.page.locator("main .hero.solo").count()
    assert tx["empty"] in w.page.inner_text("main .hero .inbox-empty")
    assert w.page.locator("main .hero .hint .ex, main .hero .hint").count()  # подсказка с примером осталась
    assert not w.page.locator("main .below").count()

    # 2. Inbox пуст, есть сроки и Next
    now = int(time.time())
    mk = lambda text, status, remind=None: A.run(  # noqa: E731
        "update items set status=%s, remind_at=%s where id=%s returning id",
        (status, remind, A.capture(uid, text)["id"]))
    plain = mk("Просто next", "next")
    later = mk("Next через 10 дней", "next", now + 10 * 86400)
    over = mk("Просроченный next", "next", now - 7200)
    wait = mk("Ждать до завтра", "waiting", now + 86400)
    w.page.reload()
    w.wait("main .due-soon .it", f"[{lang}] «Скоро срок»")
    assert not w.page.locator("main .hero.solo").count() and tx["empty"] in w.page.inner_text("main .inbox-empty")
    heads = w.page.eval_on_selector_all("main .below h3", "els => els.map(e => e.textContent)")
    assert len(heads) == 2 and tx["due"] in heads[0] and tx["next"] in heads[1]  # «Скоро срок» — перед Next
    assert section_ids(w, "due-soon") == [over, wait]
    assert sorted(section_ids(w, "next-sec")) == sorted([plain, later])  # просроченный next не повторяется
    assert tx["over"] in w.page.inner_text(f'main .due-soon .it[data-id="{over}"] .due.over')
    assert not w.page.locator(f'main .it[data-id="{wait}"] .due.over').count()
    assert not w.page.locator("main .hero ~ .dnd").count()  # во Inbox пусто

    # 3. Во Inbox есть задачи, одна из них со сроком — она во Inbox, а не в «Скоро срок»
    inb = A.capture(uid, "Во входящих со сроком")["id"]
    A.run("update items set remind_at=%s where id=%s", (now + 600, inb))
    w.page.reload()
    w.wait(f'main > .dnd > .it[data-id="{inb}"]', f"[{lang}] задача во Inbox")
    assert not w.page.locator("main .inbox-empty").count()
    assert inb not in section_ids(w, "due-soon") and section_ids(w, "due-soon") == [over, wait]
    order = w.page.eval_on_selector_all("main > .dnd, main > .below", "els => els.map(e => e.className)")
    assert order[0] == "dnd" and "due-soon" in order[1] and "next-sec" in order[2]

    # Карточки — обычные: клик открывает, кнопки работают
    w.page.click(f'main .due-soon .it[data-id="{over}"]', position={"x": 4, "y": 4})
    w.wait("#dlg[open] #ef", f"[{lang}] карточка из «Скоро срок»")
    w.page.click('#ef [data-act="cancel"]')
    w.page.click(f'main .due-soon .it[data-id="{wait}"] [data-act="mv"][data-st="next"]')
    w.wait("#toast:not([hidden])", f"[{lang}] перенос")
    assert A.row("select status from items where id=%s", (wait,))["status"] == "next"
    w.check(f"[{lang}] итог")


# ── Выполненные задачи в проекте (SERBITO-327) ──
DONE_TEXT = {"ru": {"hide": "Скрыть выполненные", "list": "Выполненные — в списке", "section": "Выполненные — отдельно",
                    "sec": "Выполненные (2)"},
             "en": {"hide": "Hide completed", "list": "Show completed in the list", "section": "Show completed separately",
                    "sec": "Completed (2)"}}


@pytest.mark.parametrize("lang", ["ru", "en"])
def test_project_completed_modes(watch, lang):
    """Три режима: скрыть; в списке (зачёркнутые, с галочкой); отдельно — открытые, затем свёрнутый блок
    «Выполненные (N)», свежие сверху. Выбор запоминается в проекте и переживает перезагрузку."""
    tx = DONE_TEXT[lang]
    uid = A.run("insert into users(tg_id,name,created,lang,checklist_hidden) values(0,'Smoke',%s,%s,true) "
                "returning id", (int(time.time()), lang))
    now = int(time.time())
    open_id = A.capture(uid, "Открытая #Кухня")["id"]
    old = A.capture(uid, "Готова давно #Кухня")["id"]
    fresh = A.capture(uid, "Готова только что #Кухня")["id"]
    A.run("update items set status='done', completed_at=%s where id=%s", (now - 7200, old))
    A.run("update items set status='done', completed_at=%s where id=%s", (now - 60, fresh))
    pid = A.row("select id from projects where user_id=%s", (uid,))["id"]
    w = watch(lang)
    w.goto("/dev-login")
    w.wait('nav > a.on[data-view="inbox"]', f"[{lang}] вход")
    w.page.click('nav > a[data-view="projects"]')
    w.page.click("main .it.pj .t")
    w.wait("main select.dmode", f"[{lang}] проект")
    cards = lambda sel="main .it[data-id]": [int(x) for x in w.page.eval_on_selector_all(  # noqa: E731
        sel, "els => els.map(e => e.dataset.id)")]
    labels = w.page.eval_on_selector_all("main select.dmode option", "els => els.map(e => e.textContent)")
    assert labels == [tx["hide"], tx["list"], tx["section"]]
    assert w.page.input_value("main select.dmode") == "hide" and cards() == [open_id]  # новый проект — скрывает

    w.page.select_option("main select.dmode", "list")
    w.wait(f'main .it.done[data-id="{fresh}"]', f"[{lang}] в списке")
    assert sorted(cards()) == sorted([open_id, old, fresh])
    assert w.page.is_checked(f'main .it[data-id="{old}"] input[data-act="toggle"]')
    assert w.page.evaluate(f"""getComputedStyle(document.querySelector('main .it[data-id="{old}"] .t'))
                               .textDecorationLine""") == "line-through"
    assert A.row("select done_mode from projects where id=%s", (pid,))["done_mode"] == "list"

    w.page.select_option("main select.dmode", "section")
    w.wait("main details.donesec", f"[{lang}] отдельно")
    assert cards("main > .dnd > .it[data-id]") == [open_id]
    assert w.page.inner_text("main details.donesec summary") == tx["sec"]
    assert not w.page.is_visible(f'main .it[data-id="{fresh}"]')  # блок свёрнут
    w.page.click("main details.donesec summary")
    assert cards("main details.donesec .it") == [fresh, old]  # свежие сверху
    w.page.reload()  # адрес проекта (SERBITO-354) — после перезагрузки тот же проект
    w.wait("main details.donesec", f"[{lang}] после перезагрузки")
    assert w.page.input_value("main select.dmode") == "section"

    # Выполненная — рабочая карточка: снять галочку — вернуть в работу
    w.page.click("main details.donesec summary")
    w.page.click(f'main .it[data-id="{fresh}"] input[data-act="toggle"]')
    w.wait(f'main > .dnd > .it[data-id="{fresh}"]', f"[{lang}] вернулась в открытые")
    assert A.row("select status from items where id=%s", (fresh,))["status"] == "next"
    w.page.select_option("main select.dmode", "hide")
    w.page.wait_for_function("!document.querySelector('main details.donesec')", timeout=WAIT_MS)
    assert sorted(cards()) == sorted([open_id, fresh])
    w.check(f"[{lang}] итог")


# ── Ручной порядок (SERBITO-328) ──
def screen_order(w, sel="main .dnd > .it[data-id]"):
    return [int(x) for x in w.page.eval_on_selector_all(sel, "els => els.map(e => e.dataset.id)")]


def db_order(uid, status):
    return [r["id"] for r in A.rows("select id from items where user_id=%s and status=%s order by position",
                                    (uid, status))]


def until(cond, what):
    deadline = time.monotonic() + WAIT_MS / 1000
    while not cond():
        assert time.monotonic() < deadline, what
        time.sleep(0.05)


def order_user(w, lang="ru"):
    uid = A.run("insert into users(tg_id,name,created,lang,checklist_hidden) values(0,'Smoke',%s,%s,true) "
                "returning id", (int(time.time()), lang))
    a, b, c = (A.capture(uid, f"{t} @дом")["id"] for t in ("Первая", "Вторая", "Третья"))
    w.goto("/dev-login")
    w.wait('nav > a.on[data-view="inbox"]', f"[{lang}] вход")
    w.page.click('nav > a[data-view="next"]')
    w.wait('nav > a.on[data-view="next"]', f"[{lang}] Next")
    return uid, (a, b, c)  # на экране: Первая, Вторая, Третья — новые в конце


def test_drag_to_reorder_with_mouse(watch):
    """Мышью: потянуть карточку вниз — она встаёт на новое место, порядок сохраняется на сервере и
    переживает перезагрузку; перетаскивание карточку не открывает, обычный клик — открывает.
    Один язык: перетаскивание от языка не зависит, EN-экраны Next — в test_app_sections."""
    lang = "ru"
    w = watch(lang)
    uid, (first, second, third) = order_user(w, lang)
    assert screen_order(w) == [first, second, third]
    src = w.page.locator(f'main .it[data-id="{first}"]').bounding_box()
    dst = w.page.locator(f'main .it[data-id="{third}"]').bounding_box()
    x = src["x"] + src["width"] * 0.6  # по пустому месту строки заголовка, мимо ссылки #N
    w.page.mouse.move(x, src["y"] + 14)
    w.page.mouse.down()
    w.page.mouse.move(x, dst["y"] + dst["height"] - 4, steps=12)
    assert w.page.locator("main .it.dragging").count() == 1
    w.page.mouse.up()
    assert screen_order(w) == [second, third, first]
    until(lambda: db_order(uid, "next") == [second, third, first], "порядок не сохранился на сервере")
    w.page.wait_for_timeout(300)
    assert not w.page.locator("#dlg[open]").count() and "/i/" not in w.page.url
    w.page.reload()  # адрес раздела (SERBITO-354) — после перезагрузки снова Next
    w.wait('nav > a.on[data-view="next"]', f"[{lang}] перезагрузка")
    w.wait('main .dnd > .it', f"[{lang}] Next после перезагрузки")
    assert screen_order(w) == [second, third, first]
    # С фильтром @контекста — тот же список, перестановка работает
    w.page.click('main .chip[data-ctx="дом"]')
    w.page.click(f'main .it[data-id="{second}"]', position={"x": 4, "y": 4})  # обычный клик — открывает
    w.wait("#dlg[open] #ef", f"[{lang}] карточка по клику")
    w.check(f"[{lang}] итог")


def touch(w, sel, kind, dy=0):
    """Синтетическое касание: PointerEvent с pointerType touch в точке карточки (dy — смещение по вертикали)."""
    w.page.eval_on_selector(sel, """(el, [kind, dy]) => {
      const r = el.getBoundingClientRect(), x = r.left + r.width - 40, y = r.top + 12 + dy;
      const target = kind === 'pointerdown' ? el : document.elementFromPoint(x, y) || el;
      target.dispatchEvent(new PointerEvent(kind, {bubbles: true, cancelable: true, pointerId: 7, pointerType: 'touch',
        isPrimary: true, button: kind === 'pointermove' ? -1 : 0, clientX: x, clientY: y}));
    }""", [kind, dy])


def test_touch_long_press_drags_quick_swipe_scrolls(watch):
    """Палец: сразу повёл — это прокрутка, карточка не двигается; удержал ~0,35 с — перетаскивание."""
    w = watch()
    uid, (first, second, third) = order_user(w)
    card = f'main .it[data-id="{first}"]'
    step = w.page.locator(f'main .it[data-id="{second}"]').bounding_box()["y"] - w.page.locator(card).bounding_box()["y"]
    touch(w, card, "pointerdown")
    touch(w, card, "pointermove", 30)  # до удержания — прокрутка
    w.page.wait_for_timeout(450)
    assert not w.page.locator("main .it.dragging").count()
    touch(w, card, "pointerup", 30)
    assert screen_order(w) == [first, second, third]

    touch(w, card, "pointerdown")
    w.page.wait_for_timeout(450)  # удержание
    assert w.page.locator("main .it.dragging").count() == 1
    touch(w, card, "pointermove", step * 1.6)
    touch(w, card, "pointerup", step * 1.6)
    assert screen_order(w) == [second, first, third]
    until(lambda: db_order(uid, "next") == [second, first, third], "порядок не сохранился на сервере")
    w.check("итог")


def test_keyboard_reorder(watch):
    """Alt+↑↓ на выбранной карточке — на одну позицию; в списке и в проекте; порядок сохраняется."""
    w = watch()
    uid, (first, second, third) = order_user(w)
    w.page.locator("#cap").press("ArrowDown")  # из поля захвата — к первой карточке
    w.page.keyboard.press("Alt+ArrowDown")
    w.page.keyboard.press("Alt+ArrowDown")
    assert screen_order(w) == [second, third, first]
    assert w.page.get_attribute("main .it.sel", "data-id") == str(first)  # выбор едет вместе с карточкой
    w.page.keyboard.press("Alt+ArrowDown")  # уже внизу — ничего
    w.page.keyboard.press("Alt+ArrowUp")
    assert screen_order(w) == [second, first, third]
    until(lambda: db_order(uid, "next") == [second, first, third], "порядок не сохранился на сервере")
    until(lambda: w.page.evaluate("moving") == 0, "очередь сохранения")

    # Проект: свой порядок, та же клавиша
    extra = A.capture(uid, "задача #Ремонт")
    pid = extra["project_id"]
    A.run("update items set status='waiting' where id=%s", (extra["id"],))  # не в Next: там порядок проверяем ниже
    A.run("update items set project_id=%s where id in (%s,%s)", (pid, first, second))
    w.page.click('nav > a[data-view="projects"]')
    w.page.click("main .it.pj .t")
    w.wait('main .dnd[data-scope="project"] > .it', "проект")
    proj = screen_order(w)
    w.page.locator("#cap").press("ArrowDown")
    w.page.keyboard.press("Alt+ArrowDown")
    want = [proj[1], proj[0], *proj[2:]]
    assert screen_order(w) == want
    until(lambda: [r["id"] for r in A.rows("select id from items where project_id=%s order by ppos", (pid,))] == want,
          "порядок проекта не сохранился")
    assert db_order(uid, "next") == [second, first, third]  # порядок Next не тронут
    w.check("итог")


# ── Клавиатура и доступность (SERBITO-349) ──
PHONE = (390, 844)
FOCUSED = """() => { const a = document.activeElement;
  return a && a !== document.body ? {view: a.dataset.view || null, id: a.id, tag: a.tagName, cls: a.className,
    inNav: !!a.closest('nav'), card: a.closest('.it[data-id]')?.dataset.id || null} : null; }"""


def tab_walk(w, n, back=False):
    """n нажатий Tab (Shift+Tab); после каждого — что в фокусе."""
    out = []
    for _ in range(n):
        w.page.keyboard.press("Shift+Tab" if back else "Tab")
        out.append(w.page.evaluate(FOCUSED))
    return out


def test_nav_reachable_by_keyboard(watch):
    """Разделы — ссылки с href: Tab доходит до каждого, Enter открывает, фокус остаётся на пункте;
    стрелки в меню двигают и фокус браузера, текущий раздел — aria-current."""
    w = watch()
    smoke_user(w)
    assert w.page.eval_on_selector_all("nav > a[data-view]", "els => els.every(a => a.getAttribute('href'))")
    stops = tab_walk(w, 40)
    reached = [s["view"] for s in stops if s and s["inNav"] and s["view"]]
    assert set(SECTIONS) <= set(reached), reached
    w.page.focus('nav > a[data-view="waiting"]')
    w.page.keyboard.press("Enter")
    w.wait('nav > a.on[data-view="waiting"][aria-current="page"]', "раздел Enter-ом")
    assert w.page.locator('nav a[aria-current="page"]').count() == 1
    assert w.page.evaluate(FOCUSED)["view"] == "waiting" and "#" not in w.page.url  # фокус не потерялся
    w.page.keyboard.press("ArrowDown")  # стрелки в меню — фокус идёт за выбором
    assert w.page.evaluate(FOCUSED)["view"] == "scheduled"
    w.page.keyboard.press("Enter")
    w.wait('nav > a.on[data-view="scheduled"]', "раздел стрелкой")
    assert w.page.evaluate(FOCUSED)["view"] == "scheduled"
    # Меню аккаунта: кнопка сообщает, раскрыта ли, пункты — ссылки с href, Esc закрывает и возвращает фокус
    w.page.focus("nav .navfoot button.user")
    w.page.keyboard.press("Enter")
    assert w.page.get_attribute("nav .navfoot button.user", "aria-expanded") == "true"
    w.page.keyboard.press("Tab")
    assert w.page.evaluate(FOCUSED)["view"] == "account"
    w.page.keyboard.press("Escape")
    assert w.page.get_attribute("nav .navfoot button.user", "aria-expanded") == "false"
    assert "user" in w.page.evaluate(FOCUSED)["cls"]
    w.check("итог")


def test_phone_drawer_focus(watch):
    """Телефон: закрытое меню не ловит Tab; ☰ открывает — фокус внутри, Tab ходит по кругу внутри, Esc закрывает
    и возвращает фокус на ☰; выбор раздела закрывает меню, фокус — снова на ☰."""
    w = watch(viewport=PHONE)
    smoke_user(w)
    assert not any(s and s["inNav"] for s in tab_walk(w, 30)), "Tab попал в закрытое меню"
    burger = w.page.locator(".burger")
    assert burger.get_attribute("aria-expanded") == "false" and burger.get_attribute("aria-controls") == "nav"
    burger.focus()
    w.page.keyboard.press("Enter")
    w.wait("nav.open", "меню открыто")
    assert burger.get_attribute("aria-expanded") == "true"
    assert w.page.evaluate(FOCUSED)["view"] == "inbox"  # текущий раздел
    assert all(s and s["inNav"] for s in tab_walk(w, 25)), "фокус ушёл из открытого меню"
    assert all(s and s["inNav"] for s in tab_walk(w, 5, back=True))
    w.page.keyboard.press("Escape")
    w.wait("nav:not(.open)", "меню закрыто Esc")
    assert "burger" in w.page.evaluate(FOCUSED)["cls"] and burger.get_attribute("aria-expanded") == "false"
    w.page.keyboard.press("Enter")
    w.wait("nav.open", "меню снова открыто")
    w.page.focus('nav > a[data-view="next"]')
    w.page.keyboard.press("Enter")
    w.wait('nav:not(.open) > a.on[data-view="next"]', "раздел из меню")
    w.page.wait_for_function("document.activeElement.classList.contains('burger')", timeout=WAIT_MS)
    w.check("итог")


def test_task_dialog_labels_and_focus_return(watch):
    """Карточка задачи: у каждого поля подпись, у диалога имя; Esc и «Сохранить» возвращают фокус на задачу,
    с которой открыли; у галочки «выполнено» имя — название задачи."""
    w = watch()
    ids = smoke_user(w)
    card = f'main .it[data-id="{ids["inbox"]}"]'
    w.wait(card, "список")
    assert w.page.get_by_role("checkbox", name="Позвонить маме").count() == 1
    unnamed = w.page.eval_on_selector_all('main .it input[type="checkbox"]', "els => els.filter(x => !x.ariaLabel).length")
    assert unnamed == 0
    # С клавиатуры: из поля захвата ↓ — выбор и фокус на первой карточке, Enter — открыть
    w.page.locator("#cap").press("ArrowDown")
    assert w.page.evaluate(FOCUSED)["card"] == str(ids["inbox"])
    w.page.keyboard.press("Enter")
    w.wait("#dlg[open] #ef", "карточка с клавиатуры")
    assert w.page.get_by_role("dialog", name="Задача #1").count() == 1
    fields = w.page.eval_on_selector_all("#ef input, #ef select, #ef textarea",
                                         "els => els.map(x => [x.name, x.labels[0]?.textContent.trim() || ''])")
    assert len(fields) == 7 and all(label for _, label in fields), fields  # 7-е — имя нового проекта (SERBITO-354)
    w.page.keyboard.press("Escape")
    w.page.wait_for_function("!document.querySelector('#dlg[open]')", timeout=WAIT_MS)
    assert w.page.evaluate(FOCUSED)["card"] == str(ids["inbox"])
    # Мышью, с сохранением: список перерисован — фокус на той же задаче в новом списке
    w.page.click(f"{card} .t")
    w.wait("#dlg[open] #ef", "карточка мышью")
    w.page.fill("#ef-title", "Позвонить маме вечером")
    w.page.click("#ef button.pri")
    w.wait(f'{card} .t:text-is("Позвонить маме вечером")', "сохранено")
    w.page.wait_for_function(f"document.activeElement === document.querySelector('{card}')", timeout=WAIT_MS)
    w.check("итог")


@pytest.mark.parametrize("bot", [False, True])
@pytest.mark.parametrize("viewport", [PHONE, (1280, 900)])
def test_landing_signin_space_reserved(watch, monkeypatch, bot, viewport):
    """Место под кнопки входа есть в HTML до JS: блок не растёт, когда их кладёт JS, и почти не пустует."""
    if bot:
        monkeypatch.setattr(A, "TOKEN", "test-token")
        monkeypatch.setattr(A, "BOT_USERNAME", "gtd_test_bot")
    w = watch(viewport=viewport)
    w.page.add_init_script("""window.__cls = 0; new PerformanceObserver(l => l.getEntries().forEach(e => {
      if(!e.hadRecentInput) window.__cls += e.value; })).observe({type: 'layout-shift', buffered: true});""")
    w.goto("/")
    w.wait('#signin a[href="/dev-login"]', "кнопки входа")
    if bot:
        w.wait("#signin .tgfb", "кнопка Telegram")
    reserved = w.page.evaluate("parseFloat(document.querySelector('#signin').style.minHeight)")
    real = w.page.evaluate("""() => { const s = document.querySelector('#signin'); s.style.minHeight = '';
      return s.getBoundingClientRect().height; }""")
    assert 0 <= reserved - real <= 8, (reserved, real)
    assert w.page.evaluate("window.__cls") < 0.01
    w.check("итог")


def test_consent_banner_never_covers_focus(watch, monkeypatch):
    """Телефон, баннер согласия открыт: ни одна остановка Tab — на лендинге, в списке задач и в выезжающем
    меню — не оказывается под баннером."""
    monkeypatch.setattr(A, "GA_ID", "G-TEST")
    w = watch(viewport=PHONE)
    covered = """() => { const a = document.activeElement, cc = document.querySelector('#cc');
      if(!a || a === document.body || !cc || cc.hidden || cc.contains(a)) return null;
      const b = cc.getBoundingClientRect(), r = a.getBoundingClientRect();
      return r.bottom > b.top && r.top < b.bottom && r.right > b.left && r.left < b.right ? a.outerHTML.slice(0, 80) : null; }"""

    def walk(n, what):
        hidden = []
        for _ in range(n):
            w.page.keyboard.press("Tab")
            if (c := w.page.evaluate(covered)):
                hidden.append(c)
        assert not hidden, f"{what}: под баннером {hidden}"

    w.goto("/")
    banner(w, "баннер на лендинге")
    walk(40, "лендинг")
    uid = A.run("insert into users(tg_id,name,created,lang,checklist_hidden) values(0,'Smoke',%s,'ru',true) "
                "returning id", (int(time.time()),))
    for i in range(12):
        A.capture(uid, f"Задача {i}")
    w.goto("/dev-login")
    w.wait('nav > a.on[data-view="inbox"]', "вход")
    banner(w, "баннер в приложении")
    walk(60, "список задач")
    w.page.click(".burger")
    w.wait("nav.open", "меню")
    walk(20, "выезжающее меню")
    w.check("итог")


@pytest.fixture(scope="module")
def focus_browser(pw):
    """Полный Chromium в headless (channel="chromium"), а не headless shell: у него настоящий фокус окна — вкладка
    уходит на задний план, окно получает blur, а вернувшись — focus и повторный focusin на activeElement."""
    b = launch(pw, channel="chromium")
    yield b
    b.close()


# Элемент под баннером — посередине его высоты; закрыт ли он
UNDER_BANNER = """el => { const b = document.querySelector('#cc').getBoundingClientRect(), r = el.getBoundingClientRect();
  scrollBy(0, r.top + r.height / 2 - (b.top + b.height / 2)); }"""
COVERED_BY_BANNER = """el => { const b = document.querySelector('#cc').getBoundingClientRect(), r = el.getBoundingClientRect();
  return r.bottom > b.top && r.top < b.bottom; }"""


def settle(page):
    page.evaluate("() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))")


def test_window_refocus_does_not_scroll(focus_browser, server, monkeypatch):
    """SERBITO-374: окно вернуло фокус (другое приложение, вкладка) — браузер снова шлёт focusin элементу в фокусе.
    Это не ход клавиатуры: страница не прокручивается. А Tab на элемент под баннером его по-прежнему открывает
    (WCAG 2.4.11). Эмуляция фокуса Playwright выключена: с ней focus/blur окна не приходят и тест прошёл бы зря."""
    monkeypatch.setattr(A, "GA_ID", "G-TEST")
    w = Watch(focus_browser, server, viewport=PHONE)
    try:
        w.goto("/")
        banner(w, "баннер на лендинге")
        w.ctx.new_cdp_session(w.page).send("Emulation.setFocusEmulationEnabled", {"enabled": False})
        w.page.bring_to_front()
        assert w.page.evaluate("document.hasFocus()"), "у окна нет фокуса: проверять нечего"
        w.page.evaluate("window.__focusins = 0; document.addEventListener('focusin', () => __focusins++)")
        first, second = w.page.locator("footer ul a").nth(0), w.page.locator("footer ul a").nth(1)

        first.evaluate("el => el.focus()")
        first.evaluate(UNDER_BANNER)
        settle(w.page)
        assert first.evaluate(COVERED_BY_BANNER), "ссылка не под баннером"
        y, seen = w.page.evaluate("scrollY"), w.page.evaluate("__focusins")
        other = w.ctx.new_page()
        other.bring_to_front()
        w.page.bring_to_front()
        other.close()
        w.page.wait_for_function(f"__focusins > {seen}", timeout=WAIT_MS)  # окно вернуло фокус — focusin пришёл
        settle(w.page)
        assert first.evaluate("el => el === document.activeElement")
        assert w.page.evaluate("scrollY") == y, "возврат фокуса в окно прокрутил страницу"

        second.evaluate(UNDER_BANNER)
        settle(w.page)
        assert second.evaluate(COVERED_BY_BANNER), "вторая ссылка не под баннером"
        w.page.keyboard.press("Tab")
        settle(w.page)
        assert second.evaluate("el => el === document.activeElement"), "Tab ушёл не на следующую ссылку"
        assert not second.evaluate(COVERED_BY_BANNER), "Tab оставил фокус под баннером"
        w.check("итог")
    finally:
        w.close()


# Настоящая кнопка Google — iframe фиксированной ширины с отрицательными полями по бокам; заглушка рисует такой же
GSI_FIXED = ("window.google={accounts:{id:{initialize(){},renderButton(el){el.innerHTML="
             "'<iframe title=\"Google\" style=\"width:262px;height:44px;margin:0 -10px;border:0\"></iframe>';}}}};")
WIDER = """() => { const W = document.documentElement.clientWidth;
  return [...document.querySelectorAll('body *')].filter(e => e.getBoundingClientRect().right > W + .5)
    .map(e => e.tagName + '.' + e.className).slice(0, 10); }"""


@pytest.mark.parametrize("path", ["/", "/en/", "/about", "/privacy", "/changes"])
def test_no_horizontal_scroll_at_320px(watch, monkeypatch, path):
    """WCAG 1.4.10 (SERBITO-349): при ширине 320 px (1280 px при 400 %) страница не прокручивается вбок — и лендинг
    со всеми кнопками входа: Google, почта, Telegram."""
    monkeypatch.setattr(A, "TOKEN", "test-token")
    monkeypatch.setattr(A, "BOT_USERNAME", "gtd_test_bot")
    w = watch("en" if path.startswith("/en") else "ru", viewport=(320, 640))
    w.page.add_init_script(GSI_FIXED)
    w.goto(path)
    if path in ("/", "/en/"):
        w.wait("#signin .tgfb", "кнопки входа")
        w.wait("#signin .gbtn iframe", "кнопка Google")
    assert w.page.evaluate("document.documentElement.scrollWidth") <= 320, w.page.evaluate(WIDER)
    w.check("итог")


@pytest.mark.parametrize("key", ["Enter", "Space"])
def test_sign_out_from_keyboard(watch, key):
    """«Выйти» в меню аккаунта — настоящая кнопка (SERBITO-349): срабатывает и от Enter, и от пробела."""
    A.run("insert into users(tg_id,name,created) values(0,'Smoke',%s)", (int(time.time()),))
    w = watch()
    w.goto("/dev-login")
    w.wait('nav > a.on[data-view="inbox"]', "вход")
    w.page.focus("nav .navfoot button.user")
    w.page.keyboard.press("Enter")
    w.page.keyboard.press("Tab")
    w.page.keyboard.press("Tab")
    assert w.page.evaluate("[document.activeElement.tagName, document.activeElement.textContent]") == ["BUTTON", "Выйти"]
    w.page.keyboard.press(key)
    w.wait('#signin a[href="/dev-login"]', f"лендинг после выхода ({key})")
    w.check("итог")


@pytest.mark.parametrize("viewport", [PHONE, (1280, 900)])
def test_consent_buttons_equal_weight(watch, monkeypatch, viewport):
    """«Принять» и «Отклонить» одного веса (решение владельца, SERBITO-349): цвет, рамка, шрифт и размер одинаковые —
    отказаться так же легко, как согласиться."""
    monkeypatch.setattr(A, "GA_ID", "G-TEST")
    w = watch(viewport=viewport)
    w.goto("/")
    banner(w, "баннер")
    look = """b => { const s = getComputedStyle(b), r = b.getBoundingClientRect();
      return [s.backgroundColor, s.color, s.borderTopColor, s.borderTopWidth, s.fontWeight, s.fontSize,
              Math.round(r.width), Math.round(r.height)]; }"""
    yes = w.page.eval_on_selector('#cc [data-cc="granted"]', look)
    no = w.page.eval_on_selector('#cc [data-cc="denied"]', look)
    assert yes == no, (yes, no)
    w.check("итог")


# Виджет Telegram встаёт iframe'ом 234×40 на место своего <script> — заглушка делает так же
TG_WIDGET_STUB = ("(s => { const f = document.createElement('iframe'); f.title = 'Telegram'; "
                  "f.style.cssText = 'height:40px;width:234px;border:none'; s.after(f); })(document.currentScript);")
WIDGETS = ("https://accounts.google.com/", "https://telegram.org/")


def test_signin_widgets_load_when_visible(prod_browser, server, monkeypatch):
    """Виджеты Google и Telegram (~300 КБ, SERBITO-349) не грузятся вместе с лендингом: пока блок входа за краем
    экрана, запросов к accounts.google.com и telegram.org нет; докрутили до него — оба встают на зарезервированное
    место, и ничего не сдвигается. Прод-хост — только там включается виджет Telegram."""
    monkeypatch.setattr(A, "TOKEN", "test-token")
    monkeypatch.setattr(A, "BOT_USERNAME", "gtd_test_bot")
    base = "http://gtd.serbito.rs:" + server.rsplit(":", 1)[1]
    w = Watch(prod_browser, base, viewport=(390, 300),
              stubs={"https://telegram.org/js/telegram-widget.js": TG_WIDGET_STUB,
                     "https://static.cloudflareinsights.com/beacon.min.js": ""})
    try:
        asked = []
        w.page.on("request", lambda r: r.url.startswith(WIDGETS) and asked.append(r.url))
        w.goto("/")
        w.wait('#signin a[href="/dev-login"]', "кнопки входа")
        assert w.page.evaluate("document.querySelector('#signin').getBoundingClientRect().top") > 300
        w.page.wait_for_load_state("load")
        w.page.wait_for_timeout(1000)  # простой после load: блок не на экране — виджеты всё ещё не нужны
        assert asked == [] and w.page.is_hidden("#signin .tgfb")
        # Ниже блока входа ничего не сдвигается (внутри него, под Telegram, — только кнопка Dev login: её на проде нет)
        below = "document.querySelector('.signin > .note').getBoundingClientRect().top + scrollY"
        y = w.page.evaluate(below)
        w.page.evaluate("document.querySelector('#signin').scrollIntoView({block: 'center'})")
        w.wait('#signin .gbtn:has-text("Google")', "кнопка Google")
        w.wait("#signin .tgw iframe", "виджет Telegram")
        assert sorted(u.split("/")[2] for u in asked) == ["accounts.google.com", "telegram.org"], asked
        assert w.page.evaluate(below) == y
        w.check("итог")
    finally:
        w.close()


# ── SERBITO-354: gtd UX ──
def wait_db(w, cond, what):
    """Как until, но ждём через Playwright: пока тест спит в time.sleep, перехват запросов (ctx.route) не крутится
    и запрос страницы к серверу стоит."""
    deadline = time.monotonic() + WAIT_MS / 1000
    while not cond():
        assert time.monotonic() < deadline, what
        w.page.wait_for_timeout(50)
    w.check(what)


def path_is(w, path, what):
    """Ждём адрес: история и перерисовка после popstate — асинхронные."""
    try:
        w.page.wait_for_function("p => location.pathname === p", arg=path, timeout=WAIT_MS)
    except PWTimeout:
        pytest.fail(f"{what}: адрес {w.page.evaluate('location.pathname')}, ждали {path}", pytrace=False)
    w.check(what)


def test_back_button_stays_in_app(watch):
    """G1: каждый раздел и проект — свой адрес и запись в истории. Back (кнопка браузера, жест на телефоне) ведёт
    в прошлый раздел, а не с сайта; открытая карточка закрывается по Back; Forward возвращает. Адрес раздела
    открывается заново после перезагрузки."""
    w = watch()
    ids = smoke_user(w)
    pid = A.row("select project_id from items where id=%s", (ids["proj"],))["project_id"]
    assert w.page.get_attribute('nav > a[data-view="next"]', "href") == "/next"
    w.page.click('nav > a[data-view="next"]')
    path_is(w, "/next", "Next")
    w.page.click('nav > a[data-view="projects"]')
    path_is(w, "/projects", "Projects")
    w.page.click("main .it.pj .t")
    path_is(w, f"/p/{pid}", "проект")
    w.wait('main [data-act="back"]', "проект открыт")
    w.page.click("main .it .t")
    w.wait("#dlg[open] #ef", "карточка")
    path_is(w, "/i/3", "карточка")

    w.page.go_back()  # карточка закрывается, проект на месте
    path_is(w, f"/p/{pid}", "Back из карточки")
    w.page.wait_for_selector("#dlg:not([open])", state="attached", timeout=WAIT_MS)
    assert w.page.locator('main [data-act="back"]').count()
    w.page.go_back()
    path_is(w, "/projects", "Back из проекта")
    w.wait("main .it.pj", "список проектов")
    w.page.go_back()
    path_is(w, "/next", "Back в Next")
    w.wait('nav > a.on[data-view="next"]', "Next после Back")
    w.page.go_forward()
    path_is(w, "/projects", "Forward")
    w.wait('nav > a.on[data-view="projects"]', "Projects после Forward")
    w.page.go_back()
    w.page.go_back()
    path_is(w, "/", "Back в Inbox")
    w.wait('nav > a.on[data-view="inbox"]', "Inbox после Back")

    # Адрес раздела переживает перезагрузку; проект — тоже
    w.goto("/waiting")
    w.wait('nav > a.on[data-view="waiting"]', "/waiting напрямую")
    w.goto(f"/p/{pid}")
    w.wait('main [data-act="back"]', "/p/N напрямую")
    # «← Projects» — тоже переход с записью в истории
    w.page.click('main [data-act="back"]')
    path_is(w, "/projects", "← Projects")
    w.page.go_back()
    path_is(w, f"/p/{pid}", "Back к проекту")
    w.wait('main [data-act="back"]', "проект после Back")
    w.check("итог")


def test_card_link_closes_to_its_section(watch):
    """Карточка, открытая прямой ссылкой /i/N, закрывается в Inbox (/), без лишней записи в истории; на /en/…
    разделы остаются под /en/."""
    w = watch("en")
    smoke_user(w, "en")
    w.goto("/i/1")
    w.wait("#dlg[open] #ef", "карточка по ссылке")
    w.page.keyboard.press("Escape")
    path_is(w, "/", "закрыта")
    w.goto("/en/next")
    w.wait('nav > a.on[data-view="next"]', "/en/next")
    assert w.page.evaluate("document.documentElement.lang") == "en"
    w.page.click('nav > a[data-view="someday"]')
    path_is(w, "/en/someday", "раздел под /en/")
    w.page.go_back()
    path_is(w, "/en/next", "Back под /en/")
    w.wait('nav > a.on[data-view="next"]', "Next после Back")
    w.check("итог")


@pytest.mark.parametrize("lang", ["ru", "en"])
def test_capture_on_project_page(watch, lang):
    """Items 2c, 3: на странице проекта поле «добавить задачу» — под его заголовком и пишет в этот проект; тост
    говорит, куда ушла задача. В других разделах (кроме Inbox) тост тоже говорит куда — задача не пропадает молча."""
    w = watch(lang)
    ids = smoke_user(w, lang)
    pid = A.row("select project_id from items where id=%s", (ids["proj"],))["project_id"]
    w.goto(f"/p/{pid}")
    w.wait('main [data-act="back"]', "проект")
    order = w.page.eval_on_selector_all("main h2.pjh, main #cap", "els => els.map(e => e.tagName)")
    assert order == ["H2", "TEXTAREA"], order  # поле — под заголовком проекта
    assert "Проект Альфа" in w.page.get_attribute("#cap", "placeholder")
    assert "Проект Альфа" in w.page.get_attribute('main [data-act="capture"]', "aria-label")
    w.page.fill("#cap", "Купить плитку")
    w.page.press("#cap", "Enter")
    w.wait('main .dnd .it .t:text-is("Купить плитку")', "задача в списке проекта")
    it = A.row("select project_id, status from items where title='Купить плитку'")
    assert (it["project_id"], it["status"]) == (pid, "next")
    w.wait("#toast:not([hidden])", "тост")
    assert "#Проект Альфа" in w.page.inner_text("#toast .tx")
    w.page.click('#toast [data-act="undo"]')  # «Отменить» — задачи нет
    wait_db(w, lambda: not A.row("select id from items where title='Купить плитку'"), "отмена записи")

    w.page.click('nav > a[data-view="waiting"]')
    w.wait('nav > a.on[data-view="waiting"]', "Waiting")
    w.page.fill("#cap", "Мысль")
    w.page.press("#cap", "Enter")
    w.wait("#toast:not([hidden])", "тост в Waiting")
    assert "Inbox" in w.page.inner_text("#toast .tx")
    assert A.row("select status, project_id from items where title='Мысль'") == {"status": "inbox", "project_id": None}
    w.check("итог")


def test_inbox_item_to_project(watch):
    """Items 2a, 2b: у задачи во Inbox есть «→ Project» — выбрать проект или создать новый, задача уходит в него
    (в Next) с тостом и «Отменить». В карточке задачи в списке проектов есть «+ Новый проект»."""
    w = watch()
    ids = smoke_user(w)
    alpha = A.row("select project_id from items where id=%s", (ids["proj"],))["project_id"]
    card = f'main .it[data-id="{ids["inbox"]}"]'
    w.page.click(f'{card} [data-act="toproj"]')
    w.wait("#dlg[open] #tp", "выбор проекта")
    assert w.page.locator("#dlg").get_attribute("aria-labelledby") == "dlg-h"
    w.page.click('#tp [data-pick]:has-text("#Проект Альфа")')
    wait_db(w, lambda: A.row("select project_id, status from items where id=%s", (ids["inbox"],))
          == {"project_id": alpha, "status": "next"}, "задача не ушла в проект")
    w.wait("#toast:not([hidden])", "тост")
    assert "#Проект Альфа" in w.page.inner_text("#toast .tx")
    w.page.click('#toast [data-act="undo"]')  # вернуть как было: Inbox, без проекта
    wait_db(w, lambda: A.row("select project_id, status from items where id=%s", (ids["inbox"],))
          == {"project_id": None, "status": "inbox"}, "отмена")

    # Новый проект прямо из Inbox; пустое название — подсказка, проект не создан
    w.wait(f'{card} [data-act="toproj"]', "кнопка после отмены")
    w.page.click(f'{card} [data-act="toproj"]')
    w.wait("#dlg[open] #tp", "выбор проекта снова")
    n = A.row("select count(*) n from projects")["n"]
    w.page.click('#tp [data-pick="new"]')
    assert w.page.inner_text("#tpmsg") and A.row("select count(*) n from projects")["n"] == n
    w.page.fill("#tp-new", "Отпуск")
    w.page.press("#tp-new", "Enter")
    wait_db(w, lambda: (A.row("select p.title from items i join projects p on p.id=i.project_id where i.id=%s",
                         (ids["inbox"],)) or {}).get("title") == "Отпуск", "новый проект из Inbox")
    w.page.wait_for_selector("#dlg:not([open])", state="attached", timeout=WAIT_MS)

    # Карточка задачи: «+ Новый проект» в списке проектов
    w.page.click('nav > a[data-view="someday"]')
    w.page.click(f'main .it[data-id="{ids["someday"]}"] .t')
    w.wait("#dlg[open] #ef", "карточка")
    assert w.page.is_hidden("#ef-newproj")
    w.page.select_option("#ef-project", "new")
    assert w.page.is_visible("#ef-newproj") and w.page.evaluate("document.activeElement.id") == "ef-newproj"
    w.page.fill("#ef-newproj", "Ремонт")
    w.page.click("#ef button.pri")
    wait_db(w, lambda: (A.row("select p.title from items i join projects p on p.id=i.project_id where i.id=%s",
                         (ids["someday"],)) or {}).get("title") == "Ремонт", "новый проект из карточки")
    w.check("итог")


def test_projects_screen_one_clear_input(watch):
    """Item 4 (G4): на экране проектов одно поле — «Новый проект» с подписью; общего поля захвата нет. «Создать»
    без названия — подсказка, а не тишина; создан — тост; такой уже есть — тост об этом. Пустой экран не повторяет
    подзаголовок дважды."""
    A.run("insert into users(tg_id,name,created,checklist_hidden) values(0,'Smoke',%s,true)", (int(time.time()),))
    w = watch()
    w.goto("/dev-login")
    w.wait('nav > a.on[data-view="inbox"]', "вход")
    w.page.click('nav > a[data-view="projects"]')
    w.wait("main #np", "экран проектов")
    assert not w.page.locator("main #cap").count()
    assert w.page.eval_on_selector("#np", "el => el.labels[0]?.textContent.trim()")
    main = w.page.inner_text("main")
    assert main.count("больше одного шага") == 1, main  # подзаголовок и пустой экран не дублируют друг друга
    w.page.click('main [data-act="newproj"]')
    assert w.page.inner_text("#npmsg") and w.page.evaluate("document.activeElement.id") == "np"
    assert not A.rows("select id from projects")
    w.page.fill("#np", "Дача")
    w.page.press("#np", "Enter")
    w.wait('main .it.pj .t:text-is("Дача")', "проект в списке")
    w.wait("#toast:not([hidden])", "тост: создан")
    assert "Дача" in w.page.inner_text("#toast .tx") and w.page.input_value("#np") == ""
    assert not w.page.inner_text("#npmsg")
    w.page.fill("#np", "дача")
    w.page.click('main [data-act="newproj"]')
    w.wait('#toast .tx:has-text("уже есть")', "тост: уже есть")
    assert len(A.rows("select id from projects")) == 1
    w.check("итог")


def test_touch_drag_grip_and_tip(watch):
    """Item 6 (G6): на сенсорном экране у карточки видна ручка ⠿ — тянуть за неё можно сразу, без удержания; над
    списком — разовая подсказка, как переставлять. Перетащил один раз (или закрыл) — подсказки больше нет.
    Мышью ручки нет: тянется вся карточка."""
    w = watch(viewport=PHONE, touch=True)
    uid = A.run("insert into users(tg_id,name,created,checklist_hidden) values(0,'Smoke',%s,true) returning id",
                (int(time.time()),))
    first, second, third = (A.capture(uid, f"{t} @дом")["id"] for t in ("Первая", "Вторая", "Третья"))
    w.goto("/dev-login")
    w.goto("/next")  # на телефоне меню в выезжающей панели — сразу по адресу раздела
    w.wait('main .dnd > .it', "Next")
    assert w.page.evaluate("matchMedia('(pointer: coarse)').matches")
    grip = f'main .it[data-id="{first}"] .grip'
    assert w.page.is_visible(grip)
    box = w.page.locator(grip).bounding_box()
    assert box["width"] >= 44 and box["height"] >= 44, box  # под палец
    w.wait("main .dndtip", "подсказка")
    assert w.page.is_visible("main .dndtip") and "⠿" in w.page.inner_text("main .dndtip")
    step = (w.page.locator(f'main .it[data-id="{second}"]').bounding_box()["y"]
            - w.page.locator(f'main .it[data-id="{first}"]').bounding_box()["y"])
    w.page.eval_on_selector(grip, """(el, dy) => {
      const r = el.getBoundingClientRect(), x = r.left + r.width / 2, y = r.top + r.height / 2;
      const ev = (kind, t, yy) => t.dispatchEvent(new PointerEvent(kind, {bubbles: true, cancelable: true, pointerId: 9,
        pointerType: 'touch', isPrimary: true, button: kind === 'pointermove' ? -1 : 0, clientX: x, clientY: yy}));
      ev('pointerdown', el, y);
      ev('pointermove', document.elementFromPoint(x, y + 20) || el, y + 20);  // сразу повёл — это перетаскивание
      window.__dragging = !!document.querySelector('main .it.dragging');
      ev('pointermove', document.elementFromPoint(x, y + dy) || el, y + dy);
      ev('pointerup', document.elementFromPoint(x, y + dy) || el, y + dy);
    }""", step * 1.6)
    assert w.page.evaluate("window.__dragging"), "ручка не начала перетаскивание сразу"
    assert screen_order(w) == [second, first, third]
    wait_db(w, lambda: db_order(uid, "next") == [second, first, third], "порядок не сохранился")
    w.page.reload()
    w.wait("main .dnd > .it", "после перезагрузки")
    assert not w.page.locator("main .dndtip").count()  # подсказка разовая
    # Во Inbox у карточек ряд кнопок списков — ручка всё равно видна и под палец
    A.capture(uid, "Во входящих")
    w.goto("/")
    grip = "main > .dnd > .it .grip"  # список самого Inbox (Next ниже — свой)
    w.wait(grip, "Inbox")
    box = w.page.locator(grip).bounding_box()
    assert w.page.is_visible(grip) and box["width"] >= 44 and box["height"] >= 44, box
    card = w.page.locator("main > .dnd > .it").bounding_box()
    for sel in (grip, "main > .dnd > .it a.num", "main > .dnd > .it .act button.del"):  # ничего не вылезает за карточку
        b = w.page.locator(sel).bounding_box()
        assert b["x"] + b["width"] <= card["x"] + card["width"] + 0.5, (sel, b, card)
    assert w.page.evaluate("document.documentElement.scrollWidth") <= PHONE[0]
    w.check("итог")


def test_no_grip_or_tip_with_mouse(watch):
    w = watch()
    order_user(w)
    assert not w.page.is_visible("main .dnd > .it .grip") and not w.page.locator("main .dndtip").count()
    w.check("итог")


def test_phone_tap_targets(watch):
    """G7 (остаток): на телефоне галочка «выполнено» и ссылка «#N» — не меньше 44×44 px; тап по краю зоны галочки
    (мимо самого квадратика) тоже отмечает задачу. Имя галочки — название задачи, как раньше."""
    w = watch(viewport=PHONE)
    ids = smoke_user(w)
    card = f'main .it[data-id="{ids["inbox"]}"]'
    w.wait(card, "список")
    for sel in (f"{card} .ck", f"{card} a.num"):
        box = w.page.locator(sel).bounding_box()
        assert box and box["width"] >= 44 and box["height"] >= 44, (sel, box)
    assert w.page.get_by_role("checkbox", name="Позвонить маме").count() == 1
    box = w.page.locator(f"{card} .ck").bounding_box()
    w.page.mouse.click(box["x"] + 3, box["y"] + box["height"] - 3)  # угол зоны, не квадратик
    wait_db(w, lambda: A.row("select status from items where id=%s", (ids["inbox"],))["status"] == "done", "галочка")
    assert not w.page.locator("#dlg[open]").count()
    w.check("итог")


@pytest.mark.parametrize("viewport", [PHONE, (1280, 900)])
def test_no_gap_after_onboarding(watch, viewport):
    """G8: когда карточка «Первые шаги» исчезла, а во Inbox есть задачи, поле захвата не съезжает вниз — сверху
    тот же небольшой отступ, что и под карточкой; пустого места на её месте нет. Совсем пустой Inbox — по центру."""
    uid = A.run("insert into users(tg_id,name,created) values(0,'Smoke',%s) returning id", (int(time.time()),))
    A.capture(uid, "Задача")
    w = watch(viewport=viewport)
    w.goto("/dev-login")
    w.wait("main .onb", "чек-лист")
    pad = lambda: w.page.eval_on_selector("main .hero", "el => parseFloat(getComputedStyle(el).paddingTop)")  # noqa: E731
    with_card = pad()
    A.run("update users set checklist_hidden=true where id=%s", (uid,))
    w.page.reload()
    w.wait("main .hero", "без чек-листа")
    assert not w.page.locator("main .onb").count() and not w.page.locator("main .hero.solo").count()
    assert pad() == with_card <= 32, (pad(), with_card)
    w.check("итог")


def test_onboarding_bot_link(watch, monkeypatch):
    """G9: «или боту» в чек-листе — ссылка на бота; без бота на сервере про бота не говорим."""
    A.run("insert into users(tg_id,name,created) values(0,'Smoke',%s)", (int(time.time()),))
    w = watch()
    w.goto("/dev-login")
    w.wait("main .onb", "чек-лист без бота")
    assert "бот" not in w.page.inner_text("main .onb div.onbi:first-of-type")
    monkeypatch.setattr(A, "TOKEN", "smoke-token")
    monkeypatch.setattr(A, "BOT_USERNAME", "gtd_smoke_bot")
    w.page.reload()
    link = 'main .onb div.onbi:first-of-type a[href="https://t.me/gtd_smoke_bot"]'
    w.wait(link, "ссылка на бота")
    assert w.page.get_attribute(link, "target") == "_blank" and "бот" in w.page.inner_text(link)
    w.check("итог")


def test_next_offers_context_chips(watch):
    """G9: «→ Next» из Inbox не спрашивает контекст, но тост предлагает уже знакомые @контексты — один тап, и у задачи
    есть контекст для фильтра в Next."""
    w = watch()
    ids = smoke_user(w)  # «Buy milk @home» — контекст уже есть
    w.page.click(f'main .it[data-id="{ids["inbox"]}"] [data-act="mv"][data-st="next"]')
    chip = '#toast [data-act="ctxset"][data-c="home"]'
    w.wait(chip, "чип контекста в тосте")
    w.page.click(chip)
    wait_db(w, lambda: A.row("select context, status from items where id=%s", (ids["inbox"],))
            == {"context": "home", "status": "next"}, "контекст из тоста")
    assert "@home" in w.page.inner_text("#toast .tx")
    w.check("итог")


@pytest.mark.parametrize("lang", ["ru", "en"])
@pytest.mark.parametrize("viewport", [PHONE, (1280, 900)])
def test_signin_buttons_consistent(watch, monkeypatch, lang, viewport):
    """G11: кнопки входа одной ширины (Google — шириной блока, строка почты, Telegram) и с одним глаголом
    («Войти …» / «Sign in …»); кнопка Telegram не выглядит выключенной — тот же вид, что у «Войти по коду»."""
    monkeypatch.setattr(A, "TOKEN", "smoke-token")
    monkeypatch.setattr(A, "BOT_USERNAME", "gtd_smoke_bot")
    w = watch(lang, viewport=viewport)
    w.goto("/en/" if lang == "en" else "/")
    w.wait("#signin .tgfb", "кнопки входа")
    w.page.hover("#signin")  # виджеты грузятся, когда к блоку потянулись
    w.page.wait_for_function("window.__gsi", timeout=WAIT_MS)
    width = lambda sel: w.page.locator(sel).bounding_box()["width"]  # noqa: E731
    box = width("#signin")
    assert abs(width("#signin .cap") - box) < 1 and abs(width("#signin .tgfb") - box) < 1
    gsi = w.page.evaluate("window.__gsi")
    assert gsi["text"] == "signin_with" and abs(gsi["width"] - min(box, 400)) < 1, gsi
    verb = "Войти " if lang == "ru" else "Sign in "
    assert w.page.inner_text('#signin [data-act="emailsend"]').startswith(verb)
    assert verb in w.page.inner_text("#signin .tgfb")
    look = """el => { const c = getComputedStyle(el); return [c.color, c.borderColor, c.backgroundColor, c.fontWeight]; }"""
    assert w.page.eval_on_selector("#signin .tgfb", look) == w.page.eval_on_selector('#signin [data-act="emailsend"]', look)
    assert int(w.page.eval_on_selector("#signin .tgfb", "el => getComputedStyle(el).fontWeight")) >= 600
    w.check("итог")


@pytest.mark.parametrize("lang, label", [("ru", "Добавить"), ("en", "Add")])
def test_capture_button_has_visible_label_on_wide_screens(watch, lang, label):
    """G12: на широком экране у кнопки захвата есть видимая подпись, и она входит в её доступное имя; на телефоне —
    только значок (место под поле), имя то же."""
    for viewport, shown in (((1280, 900), True), (PHONE, False)):
        w = watch(lang, viewport=viewport)
        smoke_user(w, lang) if viewport != PHONE else w.goto("/dev-login")
        btn = 'main [data-act="capture"]'
        w.wait(btn, "кнопка захвата")
        assert w.page.inner_text(btn).strip() == (label if shown else ""), viewport
        assert w.page.get_attribute(btn, "aria-label").startswith(label)
        w.check(f"{viewport}")
        w.close()
