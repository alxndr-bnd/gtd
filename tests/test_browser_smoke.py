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
def browser():
    with sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except PWError as e:
            if "Executable doesn't exist" in str(e) or "playwright install" in str(e):
                pytest.fail(f"{INSTALL_HINT}\n{str(e).splitlines()[0]}", pytrace=False)
            raise
        yield b
        b.close()


class Watch:
    """Страница, которая копит всё, что считается поломкой: ошибки консоли, необработанные исключения,
    ответы ≥400 своего сервера и попытки сходить наружу."""

    def __init__(self, browser, base, lang="ru"):
        self.base, self.errors, self.expected_404 = base, [], set()
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
    ("/no-such-page", 404, "body"),
    ("/en/no-such-page", 404, "body"),
])
def test_public_pages(watch, path, status, ready):
    w = watch("en" if path.startswith("/en") else "ru")
    w.goto(path, status)
    w.page.wait_for_load_state("load")
    w.wait(ready, path)
