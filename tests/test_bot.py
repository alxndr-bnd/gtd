"""Telegram-бот: команды, захват, кнопки под задачей, вход по /login, напоминания."""
import asyncio
import re
import time

import httpx

import app as A
from conftest import bot_callback, bot_message, last_code


def texts(sent):
    return [p.get("text", "") for m, p in sent if m in ("sendMessage", "editMessageText")]


def test_anyone_gets_an_account(tg):
    bot_message("/start", tg_id=999, name="Stranger")
    assert texts(tg)[-1] == A.tr("ru", "start")
    assert A.row("select name from users where tg_id=999")["name"] == "Stranger"


def test_capture_from_telegram(tg):
    bot_message("позвонить маме завтра в 10:00 @телефон")
    it = A.row("select * from items")
    assert (it["title"], it["status"], it["context"], it["source"]) == ("позвонить маме", "next", "телефон", "telegram")
    method, p = tg[-2]  # последнее — подсказка после первой задачи аккаунта
    assert p["text"].startswith(f'✓ Next <a href="http://localhost:8000/i/{it["num"]}">#{it["num"]}</a>: позвонить маме')
    assert p["parse_mode"] == "HTML" and p["link_preview_options"] == {"is_disabled": True}
    assert [b["callback_data"] for b in p["reply_markup"]["inline_keyboard"][0]] == \
           [f"done:{it['id']}", f"snz:{it['id']}", f"next:{it['id']}"]


def test_non_text_message(tg):
    asyncio.run(A.handle_message({"chat": {"id": 777}, "from": {"id": 777}, "photo": [{}]}))
    assert texts(tg)[-1] == "Пока понимаю только текст."


def test_inbox_and_next_lists(tg):
    bot_message("мысль")
    bot_message("дело @дом")
    bot_message("/inbox")
    assert "INBOX:" in texts(tg)[-1] and "мысль" in texts(tg)[-1] and "дело" not in texts(tg)[-1]
    bot_message("/next@gtd_test_bot")
    assert "NEXT:" in texts(tg)[-1] and "дело" in texts(tg)[-1]
    A.run("delete from items")
    bot_message("/inbox")
    assert texts(tg)[-1] == "INBOX:\nПусто 🎉"


def test_done_command_names_the_task(tg):
    bot_message("задача")
    num = A.row("select num from items")["num"]
    bot_message("/done abc")
    assert texts(tg)[-1] == "Использование: /done 12"
    bot_message(f"/done #{num + 100}")
    assert texts(tg)[-1] == "Не нашёл такую задачу"
    bot_message(f"/done {num}")
    assert texts(tg)[-1] == f'✅ Готово: <a href="http://localhost:8000/i/{num}">#{num}</a> задача'
    assert A.row("select status from items")["status"] == "done"


def test_bot_escapes_html_in_titles(tg):
    bot_message("сравнить <b>a</b> & b")
    assert "сравнить &lt;b&gt;a&lt;/b&gt; &amp; b" in texts(tg)[-2]  # последнее — подсказка после первой задачи


def test_unknown_command_shows_help(tg):
    bot_message("/whatever")
    assert "/inbox" in texts(tg)[-1]


def test_buttons(tg):
    bot_message("задача")
    iid = A.row("select id from items")["id"]

    bot_callback(f"next:{iid}")
    assert A.row("select status from items")["status"] == "next"
    before = int(time.time())
    bot_callback(f"snz:{iid}")
    it = A.row("select remind_at, reminded from items")
    assert it["remind_at"] >= before + 3600 and it["reminded"] == 0
    bot_callback(f"done:{iid}")
    assert A.row("select status from items")["status"] == "done"
    assert texts(tg)[-1] == "✅ Готово: задача"


def test_buttons_ignore_foreign_items(tg):
    bot_message("моё", tg_id=777)
    iid = A.row("select id from items")["id"]
    A.tg_user(888, "Eve")
    bot_callback(f"done:{iid}", tg_id=888)
    assert A.row("select status from items")["status"] == "inbox"
    assert ("answerCallbackQuery", {"callback_query_id": "cb", "text": "Не найдено"}) in tg


def test_login_link(tg, client):
    bot_message("/login")
    tok = re.search(r"/auth\?t=(\S+)", texts(tg)[-1]).group(1)
    r = client.get("/auth", params={"t": tok}, follow_redirects=False)
    assert r.status_code == 303 and client.get("/api/me").json()["tg"] is True
    assert client.get("/auth", params={"t": tok}).status_code == 400  # одноразовая


def test_login_link_expires(tg, client):
    bot_message("/login")
    tok = re.search(r"/auth\?t=(\S+)", texts(tg)[-1]).group(1)
    A.run("update login_tokens set expires=0")
    assert client.get("/auth", params={"t": tok}).status_code == 400


def test_reminders_sent_once(tg):
    bot_message("полить цветы")
    bot_message("уже сделано")
    A.run("update items set remind_at=%s", (int(time.time()) - 1,))
    A.run("update items set status='done' where title='уже сделано'")
    tg.clear()
    asyncio.run(A.send_due_reminders())
    num = A.row("select num from items where title='полить цветы'")["num"]
    assert texts(tg) == [f'⏰ <a href="http://localhost:8000/i/{num}">#{num}</a> полить цветы']
    asyncio.run(A.send_due_reminders())
    assert len(texts(tg)) == 1


def test_reminders_marked_even_without_bot(tg, monkeypatch):
    bot_message("задача через 1 минуту")
    A.run("update items set remind_at=%s", (int(time.time()) - 1,))
    monkeypatch.setattr(A, "TOKEN", "")
    tg.clear()
    asyncio.run(A.send_due_reminders())
    assert tg == [] and A.row("select reminded from items")["reminded"] == 1


# ── /email: привязка почты из бота ──

def test_email_command_without_address_asks_for_it(tg, mail):
    bot_message("/email")
    assert "Пришли адрес почты" in texts(tg)[-1]
    bot_message("Tom@Example.com")  # адрес следующим сообщением
    assert mail[-1][0] == "tom@example.com"


def test_start_shows_inline_buttons(tg):
    bot_message("/start")
    markup = tg[-1][1]["reply_markup"]
    assert "keyboard" not in markup  # никакой постоянной клавиатуры под полем ввода
    # локально (http) без «Открыть сайт»: ссылку на localhost Telegram не примет и не отправит всё сообщение
    assert [[(b["text"], b["callback_data"]) for b in r] for r in markup["inline_keyboard"]] == \
           [[(A.tr("ru", "btn_about"), "about"), (A.tr("ru", "btn_email"), "addemail")]]
    bot_callback("help")  # кнопка «🚀 Начать» под приветствиями прежних версий по-прежнему работает
    assert "/inbox" in texts(tg)[-1] and A.row("select count(*) n from items")["n"] == 0


def test_start_greeting_explains_inbox_with_examples(tg, monkeypatch):
    monkeypatch.setattr(A, "BASE_URL", "https://gtd.serbito.rs")
    bot_message("/start")
    text, markup = tg[-1][1]["text"], tg[-1][1]["reply_markup"]
    assert "попадёт во Входящие" in text
    assert "«позвонить маме завтра в 10:00» → напомню" in text
    assert "«отчёт #Работа @комп» → сразу в проект и контекст" in text
    assert markup["inline_keyboard"][1] == [{"text": A.tr("ru", "btn_site"), "url": "https://gtd.serbito.rs"}]
    assert A.row("select count(*) n from items")["n"] == 0  # /start — не задача


def test_about_command_and_button(tg):
    bot_message("/about")
    about = texts(tg)[-1]
    assert "Дэвида Аллена" in about and "«Getting Things Done»" in about
    assert about.endswith("http://localhost:8000/about")
    assert tg[-1][1]["link_preview_options"] == {"is_disabled": True}
    bot_callback("about")  # «Как это работает» под приветствием — тот же текст
    assert texts(tg)[-1] == about and ("answerCallbackQuery", {"callback_query_id": "cb"}) in tg
    bot_message("/help")
    assert "/about" in texts(tg)[-1]
    assert A.row("select count(*) n from items")["n"] == 0


def test_first_task_hint_once_per_account(tg):
    bot_message("/start")
    bot_message("первая мысль")
    assert texts(tg)[-1] == "Готово! Можно добавить срок — «завтра в 10:00», — а разобрать всё удобнее на сайте: " \
                            "http://localhost:8000"
    assert texts(tg)[-2].startswith("✓ Inbox")  # подсказка — после подтверждения, отдельным сообщением
    bot_message("вторая мысль")
    A.run("delete from items")  # даже если всё удалить — счётчик задач аккаунта не сбрасывается
    bot_message("третья мысль")
    assert sum("Готово! Можно добавить срок" in t for t in texts(tg)) == 1


def test_no_first_task_hint_for_existing_users(tg, client, login):
    uid = login(client, "alice@example.com")
    client.post("/api/capture", json={"text": "с сайта"})
    A.run("update users set tg_id=777 where id=%s", (uid,))  # тот же человек пишет боту
    bot_message("из бота")
    assert texts(tg)[-1].startswith("✓ Inbox") and not any("Можно добавить срок" in t for t in texts(tg))


def test_bot_profile_texts_fit_telegram_limits():
    assert set(A.BOT_PROFILE) == set(A.BOT_COMMANDS) == {"ru", "en"}
    for desc, short in A.BOT_PROFILE.values():
        assert 0 < len(desc) <= 512 and 0 < len(short) <= 120 and "gtd.serbito.rs" in short
    assert A.BOT_PROFILE["ru"][0].startswith("Записывай задачи и мысли в один тап")
    # меню команд на обоих языках — одни и те же команды, описания в пределах лимита Telegram
    ru, en = ([c["command"] for c in A.BOT_COMMANDS[lg]] for lg in ("ru", "en"))
    assert ru == en and "about" in ru
    assert all(0 < len(c["description"]) <= 256 for lg in A.BOT_COMMANDS for c in A.BOT_COMMANDS[lg])
    # профиль по language_code: русский — ru/uk/be/sr, всем прочим ("") — английский
    assert {c: A.profile_lang(c) for c in A.PROFILE_CODES} == {"": "en", "ru": "ru", "uk": "ru", "be": "ru", "sr": "ru"}


def test_add_email_button_flow(tg, mail):
    bot_callback("addemail")
    assert "Пришли адрес почты" in texts(tg)[-1]
    bot_message("tom@example.com")
    assert "Код отправлен на tom@example.com" in texts(tg)[-1]
    bot_message(last_code(mail))
    assert "привязана" in texts(tg)[-1]
    assert A.row("select email from users where tg_id=777")["email"] == "tom@example.com"
    bot_callback("addemail")  # уже привязана — предлагаем сменить
    assert "Сейчас привязана tom@example.com" in texts(tg)[-1]
    assert A.row("select count(*) n from items")["n"] == 0


def test_old_keyboard_buttons_answer_and_remove_keyboard(tg):
    bot_message(A.BTN_START)  # у старых пользователей клавиатура ещё на экране
    assert tg[-1][1]["reply_markup"] == {"remove_keyboard": True} and "GTD-бот готов" in texts(tg)[-1]
    bot_message(A.BTN_EMAIL)
    assert tg[-1][1]["reply_markup"] == {"remove_keyboard": True} and "Пришли адрес почты" in texts(tg)[-1]
    assert A.row("select count(*) n from items")["n"] == 0  # нажатия не стали задачами


def test_waiting_for_email_but_got_a_task(tg, mail):
    bot_callback("addemail")
    bot_message("купить хлеб")  # передумал — это обычная задача
    assert A.row("select title from items")["title"] == "купить хлеб" and mail == []
    assert A.row("select count(*) n from tg_email_links")["n"] == 0


def test_email_links_free_address(tg, mail):
    bot_message("/email Tom@Example.com")
    assert mail[-1][0] == "tom@example.com" and "Код отправлен" in texts(tg)[-1]
    bot_message(last_code(mail))
    assert "✅ Почта tom@example.com привязана" in texts(tg)[-1]
    assert A.row("select email from users where tg_id=777")["email"] == "tom@example.com"


def test_email_wrong_code_and_six_digits_without_request(tg, mail):
    bot_message("123456")  # никто не ждёт кода — это просто задача
    assert A.row("select title from items")["title"] == "123456"
    bot_message("/email tom@example.com")
    code = last_code(mail)
    bot_message("000000" if code != "000000" else "111111")
    assert texts(tg)[-1] == "Неверный код"
    bot_message(code)
    assert "привязана" in texts(tg)[-1]


def test_email_bad_address(tg, mail):
    bot_message("/email nope")
    assert texts(tg)[-1] == "Неверный адрес почты" and mail == []


def test_email_of_existing_account_merges_on_confirm(tg, mail, login, new_client):
    web = new_client()
    alice = login(web, "alice@example.com")
    web.post("/api/capture", json={"text": "с сайта #Дом"})
    bot_message("из бота #дом")

    bot_message("/email alice@example.com")
    bot_message(last_code(mail))
    method, p = tg[-1]
    assert "задач — 1, проектов — 1" in p["text"]
    merge_cb, no_cb = (b["callback_data"] for b in p["reply_markup"]["inline_keyboard"][0])

    bot_callback(merge_cb, tg_id=999)  # чужой Telegram подтвердить не может
    assert A.row("select count(*) n from users")["n"] == 2
    bot_callback(merge_cb)
    assert texts(tg)[-1] == "✅ Аккаунты объединены"
    u = A.row("select * from users")
    assert A.row("select count(*) n from users where tg_id is not null")["n"] == 1
    assert (u["tg_id"], u["email"]) == (777, "alice@example.com")
    assert A.row("select count(*) n from items where user_id=%s", (u["id"],))["n"] == 2
    assert A.row("select count(*) n from projects")["n"] == 1  # «Дом» и «дом» склеились
    assert web.get("/api/me").json()["tg"] is True  # веб-сессия Алисы жива и видит общий аккаунт


def test_email_merge_declined(tg, mail, login, new_client):
    web = new_client()
    login(web, "alice@example.com")
    web.post("/api/capture", json={"text": "с сайта"})
    bot_message("из бота")
    bot_message("/email alice@example.com")
    bot_message(last_code(mail))
    no_cb = tg[-1][1]["reply_markup"]["inline_keyboard"][0][1]["callback_data"]
    bot_callback(no_cb)
    assert "не объединяю" in texts(tg)[-1]
    assert A.row("select count(*) n from users")["n"] == 2 and A.row("select count(*) n from merge_offers")["n"] == 0


# ── вебхук и будильник: в проде бот не опрашивает Telegram и инстанс может спать ──

def test_webhook_requires_secret(client, tg):
    upd = {"update_id": 1, "message": {"chat": {"id": 777}, "from": {"id": 777, "first_name": "Tom"}, "text": "из вебхука"}}
    assert client.post("/tg/webhook", json=upd).status_code == 403
    assert client.post("/tg/webhook", json=upd, headers={"X-Telegram-Bot-Api-Secret-Token": "nope"}).status_code == 403
    r = client.post("/tg/webhook", json=upd, headers={"X-Telegram-Bot-Api-Secret-Token": A.webhook_secret()})
    assert r.json() == {"ok": True} and A.row("select title from items")["title"] == "из вебхука"


def test_webhook_off_without_bot(client):
    assert client.post("/tg/webhook", json={}, headers={"X-Telegram-Bot-Api-Secret-Token": A.webhook_secret()}).status_code == 403


def test_cron_reminders(client, tg, monkeypatch):
    bot_message("полить цветы")
    A.run("update items set remind_at=%s", (int(time.time()) - 1,))
    assert client.post("/tasks/reminders").status_code == 403  # секрет не задан — закрыто
    monkeypatch.setattr(A, "CRON_SECRET", "s3cret")
    assert client.post("/tasks/reminders", headers={"X-Cron-Secret": "wrong"}).status_code == 403
    assert client.post("/tasks/reminders", headers={"X-Cron-Secret": "s3cret"}).json() == {"sent": 1}
    assert client.post("/tasks/reminders", headers={"X-Cron-Secret": "s3cret"}).json() == {"sent": 0}


def fake_tg(monkeypatch, answers):
    calls = []

    async def fake(method, **params):
        calls.append((method, params))
        return answers.get(method, True)
    monkeypatch.setattr(A, "tg", fake)
    monkeypatch.setattr(A, "TOKEN", "test-token")
    return calls


def test_bot_setup_sets_webhook_in_prod(monkeypatch):
    calls = fake_tg(monkeypatch, {"getMe": {"username": "gtdsrbot"}})
    monkeypatch.setattr(A, "WEBHOOK", True)
    monkeypatch.setattr(A, "BASE_URL", "https://gtd.serbito.rs")
    asyncio.run(A.bot_setup())
    assert A.BOT_USERNAME == "gtdsrbot"
    hook = dict(calls)["setWebhook"]
    assert hook["url"] == "https://gtd.serbito.rs/tg/webhook" and hook["secret_token"] == A.webhook_secret()


def test_bot_setup_local_has_no_webhook(monkeypatch):
    calls = fake_tg(monkeypatch, {"getMe": {"username": "gtdsrbot"}})
    monkeypatch.setattr(A, "WEBHOOK", False)
    asyncio.run(A.bot_setup())
    assert "setWebhook" not in dict(calls)


def test_local_polling_leaves_prod_webhook_alone(monkeypatch):
    calls = fake_tg(monkeypatch, {"getWebhookInfo": {"url": "https://gtd.serbito.rs/tg/webhook"}})
    asyncio.run(A.poll_loop())  # вернулся сразу, а не завис в getUpdates
    assert [m for m, _ in calls] == ["getWebhookInfo"]


# ── профиль бота: описания и меню команд ставит bot_setup, только когда они разошлись с кодом ──

def current_profile():
    """Ответы Telegram, когда профиль уже совпадает с кодом."""
    lang = lambda p: A.profile_lang(p["language_code"])
    return {"getMe": {"username": "gtdsrbot"}, "getMyCommands": lambda p: A.BOT_COMMANDS[lang(p)],
            "getMyDescription": lambda p: {"description": A.BOT_PROFILE[lang(p)][0]},
            "getMyShortDescription": lambda p: {"short_description": A.BOT_PROFILE[lang(p)][1]}}


def profile_tg(monkeypatch, answers):
    calls = []

    async def fake(method, **params):
        calls.append((method, params))
        a = answers.get(method, True)
        return a(params) if callable(a) else a
    monkeypatch.setattr(A, "tg", fake)
    monkeypatch.setattr(A, "TOKEN", "test-token")
    monkeypatch.setattr(A, "WEBHOOK", True)
    return calls


def profile_sets(calls):
    return [(m, p) for m, p in calls if m.startswith("setMy")]


def test_bot_setup_sets_profile_when_it_differs(monkeypatch):
    calls = profile_tg(monkeypatch, {"getMe": {"username": "gtdsrbot"},
                                     "getMyDescription": {"description": ""}})  # у нового бота описания нет
    asyncio.run(A.bot_setup())
    sets = profile_sets(calls)
    for code in A.PROFILE_CODES:
        lang = A.profile_lang(code)
        desc, short = A.BOT_PROFILE[lang]
        assert ("setMyCommands", {"commands": A.BOT_COMMANDS[lang], "language_code": code}) in sets
        assert ("setMyDescription", {"description": desc, "language_code": code}) in sets
        assert ("setMyShortDescription", {"short_description": short, "language_code": code}) in sets
    assert len(sets) == 3 * len(A.PROFILE_CODES)
    assert ("setMyCommands", {"commands": A.BOT_COMMANDS["en"], "language_code": ""}) in sets  # всем прочим — английское


def test_bot_setup_skips_unchanged_profile(monkeypatch):
    calls = profile_tg(monkeypatch, current_profile())
    asyncio.run(A.bot_setup())
    assert profile_sets(calls) == []  # холодный старт без изменений — только чтение
    assert "setWebhook" in dict(calls)


def test_bot_setup_updates_only_changed_text(monkeypatch):
    answers = current_profile()
    answers["getMyShortDescription"] = lambda p: {"short_description": "старое" if p["language_code"] == "uk"
                                                  else A.BOT_PROFILE[A.profile_lang(p["language_code"])][1]}
    answers["getMyCommands"] = lambda p: [] if p["language_code"] == "" else A.BOT_COMMANDS["ru"]
    calls = profile_tg(monkeypatch, answers)
    asyncio.run(A.bot_setup())
    assert profile_sets(calls) == \
           [("setMyCommands", {"commands": A.BOT_COMMANDS["en"], "language_code": ""}),
            ("setMyShortDescription", {"short_description": A.BOT_PROFILE["ru"][1], "language_code": "uk"})]


def test_bot_setup_local_leaves_profile_alone(monkeypatch):
    calls = profile_tg(monkeypatch, current_profile())
    monkeypatch.setattr(A, "WEBHOOK", False)  # локальный сервер ходит в того же бота, что и прод
    asyncio.run(A.bot_setup())
    assert [m for m, _ in calls] == ["getMe"] and A.BOT_USERNAME == "gtdsrbot"


def test_bot_setup_survives_exceptions(monkeypatch, caplog):
    calls = profile_tg(monkeypatch, {})

    async def broken(method, **params):
        calls.append((method, params))
        if method != "setWebhook":
            raise RuntimeError("https://api.telegram.org/bottest-token/getMe: boom")
        return True
    monkeypatch.setattr(A, "tg", broken)
    asyncio.run(A.bot_setup())  # не падает: шаги независимы, ошибки — в лог
    assert A.BOT_USERNAME == "" and "setWebhook" in dict(calls)
    assert "RuntimeError" in caplog.text and "test-token" not in caplog.text


def test_bot_setup_when_telegram_is_down(monkeypatch, caplog):
    class Down:  # настоящий tg(), а сеть лежит
        async def post(self, url, **kw):
            raise httpx.ConnectError("connection refused")
    monkeypatch.setattr(A, "_client", Down())
    monkeypatch.setattr(A, "TOKEN", "test-token")
    monkeypatch.setattr(A, "WEBHOOK", True)
    asyncio.run(A.bot_setup())
    assert A.BOT_USERNAME == "" and "setMyDescription failed" in caplog.text and "test-token" not in caplog.text



def test_tg_error_log_hides_token(monkeypatch, caplog):
    token = "123456:" + "A" * 35
    class Broken:  # ошибка httpx с URL запроса в тексте — как у HTTPStatusError/InvalidURL
        async def post(self, url, **kw):
            raise httpx.InvalidURL(f"bad url {url}")
    monkeypatch.setattr(A, "_client", Broken())
    monkeypatch.setattr(A, "TOKEN", token)
    assert asyncio.run(A.tg("getMe")) is None
    assert "getMe failed" in caplog.text and token not in caplog.text

def test_bot_setup_garbage_answers(monkeypatch):
    calls = profile_tg(monkeypatch, {"getMe": None, "getMyCommands": None, "getMyDescription": "??",
                                     "getMyShortDescription": [1]})
    asyncio.run(A.bot_setup())  # непонятный ответ — считаем, что текста нет, и ставим заново
    assert A.BOT_USERNAME == "" and len(profile_sets(calls)) == 3 * len(A.PROFILE_CODES)
