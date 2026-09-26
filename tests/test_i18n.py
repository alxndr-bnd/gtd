"""Английская версия (SERBITO-259): выбор языка, настройка в /api/me, ответы бота и сервера, письмо с кодом."""
import asyncio
import re
import string
import time

import pytest

import app as A
import pages as P
from conftest import bot_callback, bot_message, last_code

CYR = re.compile(r"[А-Яа-яЁё]")
EN = {"accept-language": "en-US,en;q=0.9"}


def texts(sent):
    return [p.get("text", "") for m, p in sent if m in ("sendMessage", "editMessageText")]


# ── правило языка ──

@pytest.mark.parametrize("code, lang", [
    ("ru", "ru"), ("ru-RU", "ru"), ("uk", "ru"), ("be-BY", "ru"), ("sr", "ru"), ("sr-RS", "ru"), ("sr-Cyrl-RS", "ru"),
    ("sr-Latn", "en"), ("sr-Latn-RS", "en"), ("sr_Latn", "en"), ("en", "en"), ("en-GB", "en"), ("de", "en"),
    ("hr", "en"), ("pt-br", "en"), ("", "ru"), (None, "ru"), ("*", "ru"),
])
def test_lang_of(code, lang):
    assert P.lang_of(code) == lang


@pytest.mark.parametrize("header, lang", [
    ("en-US,en;q=0.9,ru;q=0.8", "en"), ("ru,en;q=0.9", "ru"), ("de-DE,de;q=0.9,ru;q=0.5", "en"),
    ("fr;q=0.5,uk;q=0.8", "ru"), ("en;q=0,ru", "ru"), ("", "ru"), (None, "ru"),
])
def test_accept_language(header, lang):
    assert P.pick_lang(header) == lang


def test_bot_lang_priority():
    assert A.bot_lang(None) == "ru"  # Telegram не прислал язык — русский
    assert A.bot_lang(None, "en") == "en" and A.bot_lang(None, "de") == "en" and A.bot_lang(None, "uk") == "ru"
    assert A.bot_lang({"lang": None, "tg_lang": "en"}) == "en"  # напоминание: язык из последнего апдейта
    assert A.bot_lang({"lang": "ru", "tg_lang": "en"}, "en") == "ru"  # явная настройка важнее всего


# ── настройка в /api/me ──

def test_me_lang_get_and_patch(client, login):
    login(client)
    assert client.get("/api/me").json()["lang"] is None  # по умолчанию — авто
    for v in ("en", "ru", None):
        assert client.patch("/api/me", json={"lang": v}).json()["lang"] == v
    for bad in ("de", "EN", "", 1, True, ["en"]):
        assert client.patch("/api/me", json={"lang": bad}).status_code == 400, bad
    assert client.get("/api/me").json()["lang"] is None
    client.patch("/api/me", json={"lang": "en"})
    assert client.patch("/api/me", json={"undo_seconds": 5}).json()["lang"] == "en"  # другие поля его не трогают


# ── сервер: ошибки, письмо, /auth ──

def test_errors_follow_accept_language(client, mail):
    assert client.post("/api/auth/email/start", json={"email": "nope"}).json()["detail"] == "Неверный адрес почты"
    r = client.post("/api/auth/email/start", json={"email": "nope"}, headers=EN)
    assert r.json()["detail"] == "Invalid email address"
    client.post("/api/auth/email/start", json={"email": "a@example.com"})
    r = client.post("/api/auth/email/verify", json={"email": "a@example.com", "code": "000000"}, headers=EN)
    assert r.json()["detail"] in ("Wrong code", "The code has expired — request a new one")


def test_explicit_lang_beats_accept_language(client, login):
    login(client)
    client.post("/api/capture", json={"text": "x #Home"})
    client.post("/api/capture", json={"text": "y #Work"})
    home, work = sorted(client.get("/api/projects").json(), key=lambda p: p["title"])
    r = client.patch(f"/api/projects/{work['id']}", json={"title": "home"}, headers=EN)
    assert r.status_code == 409 and r.json()["detail"] == "Project “home” already exists"
    client.patch("/api/me", json={"lang": "ru"})
    r = client.patch(f"/api/projects/{work['id']}", json={"title": " "}, headers=EN)
    assert r.json()["detail"] == "Название не может быть пустым"


def test_sign_in_email_in_english(client, mail):
    assert client.post("/api/auth/email/start", json={"email": "bob@example.com"}, headers=EN).status_code == 200
    to, subject, body = mail[-1]
    code = last_code(mail)
    assert to == "bob@example.com" and subject == f"Your GTD sign-in code: {code}"
    assert body.startswith(f"Your GTD sign-in code: {code}") and "valid for 10 minutes" in body
    assert not CYR.search(subject + body)
    A.run("delete from email_codes")
    client.post("/api/auth/email/start", json={"email": "bob@example.com"})  # без заголовка — по-русски
    assert mail[-1][1].startswith("Код входа в GTD")


def test_merge_offer_in_english(new_client, login, mail):
    alice, bob = new_client(), new_client()
    login(alice, "alice@example.com")
    alice.post("/api/capture", json={"text": "task"})
    login(bob, "bob@example.com")
    bob.post("/api/auth/email/start", json={"email": "alice@example.com"}, headers=EN)
    r = bob.post("/api/auth/email/verify", json={"email": "alice@example.com", "code": last_code(mail), "link": True},
                 headers=EN)
    assert r.status_code == 409
    assert r.json()["detail"].startswith("This email already belongs to another account (tasks: 1, projects: 0)")


def test_auth_link_page_language(client, tg):
    r = client.get("/auth", params={"t": "nope"}, headers=EN)
    assert r.status_code == 400 and r.json()["error"] == "This link has expired. Send /login to the bot again."
    assert client.get("/auth", params={"t": "nope"}).json()["error"].startswith("Ссылка устарела")
    assert client.get("/auth", params={"t": "nope", "lang": "en"}).json()["error"].startswith("This link")


def test_bot_login_link_opens_english_app(client, tg, new_client):
    bot_message("/login", lang="en")
    link = texts(tg)[-1]
    assert link.startswith("Sign-in link") and link.endswith("&lang=en")
    tok = re.search(r"/auth\?t=([^&\s]+)", link).group(1)
    r = client.get("/auth", params={"t": tok, "lang": "en"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/en/"
    # явный русский в «Аккаунте» важнее языка Telegram
    A.run("update users set lang='ru'")
    bot_message("/login", lang="en")
    tok = re.search(r"/auth\?t=([^&\s]+)", texts(tg)[-1]).group(1)
    r = new_client().get("/auth", params={"t": tok, "lang": "en"}, follow_redirects=False)
    assert r.headers["location"] == "/" and texts(tg)[-1].startswith("Вход")


def test_en_path_serves_english_html_lang(client, login):
    assert '<html lang="en">' in client.get("/en/").text
    login(client)
    assert '<html lang="en">' in client.get("/en/").text  # приложение, но язык страницы — английский
    assert '<html lang="ru">' in client.get("/").text  # «/» — SPA сама поставит язык по настройке и браузеру


# ── бот ──

def test_bot_start_and_help_in_both_languages(tg, monkeypatch):
    monkeypatch.setattr(A, "BASE_URL", "https://gtd.serbito.rs")
    bot_message("/start", tg_id=1, lang="en")
    text, kb = tg[-1][1]["text"], tg[-1][1]["reply_markup"]["inline_keyboard"]
    assert text == A.tr("en", "start") and "lands in your Inbox" in text
    assert [b["text"] for b in kb[0]] == ["ℹ️ How it works", "📧 Add email"]
    assert kb[1] == [{"text": "🌐 Open the website", "url": "https://gtd.serbito.rs/en/"}]
    bot_message("/start", tg_id=2, lang="ru")
    assert tg[-1][1]["text"] == A.tr("ru", "start")
    assert tg[-1][1]["reply_markup"]["inline_keyboard"][1][0]["url"] == "https://gtd.serbito.rs"
    bot_message("/help", tg_id=3, lang="de")  # прочие языки — английский
    assert texts(tg)[-1].startswith("The GTD bot is ready.") and "/done 12 — complete task #12" in texts(tg)[-1]
    bot_message("/help", tg_id=4)  # клиент не прислал язык — русский
    assert texts(tg)[-1].startswith("GTD-бот готов.")
    bot_message("/help", tg_id=5, lang="uk")
    assert texts(tg)[-1].startswith("GTD-бот готов.")


def test_bot_about_and_callbacks_in_english(tg):
    bot_message("/about", lang="en")
    assert "David Allen" in texts(tg)[-1] and texts(tg)[-1].endswith("http://localhost:8000/en/about")
    bot_callback("about", lang="en")
    assert texts(tg)[-1].endswith("/en/about")
    bot_callback("addemail", lang="en")
    assert texts(tg)[-1].startswith("Send me your email address")


def test_bot_capture_reply_buttons_and_hint_in_english(tg):
    bot_message("call mom tomorrow at 10am @phone", lang="en")
    it = A.row("select * from items")
    assert (it["title"], it["context"], it["status"]) == ("call mom", "phone", "next")
    method, p = tg[-2]
    assert p["text"].startswith(f'✓ Next <a href="http://localhost:8000/i/{it["num"]}">#{it["num"]}</a>: call mom')
    assert re.search(r"⏰ \d{1,2} [A-Z][a-z]{2} 10:00  @phone$", p["text"])  # месяц словом, а не 26.09
    assert [b["text"] for b in p["reply_markup"]["inline_keyboard"][0]] == ["✅ Done", "💤 +1h", "⏭ Next"]
    assert texts(tg)[-1] == "Got it! You can add a time — “tomorrow at 10:00” — and sorting everything out is easier " \
                            "on the website: http://localhost:8000/en/"
    bot_callback(f"snz:{it['id']}", lang="en")
    assert texts(tg)[-1] == "💤 I'll remind you in an hour: call mom"
    bot_callback(f"done:{it['id']}", lang="en")
    assert texts(tg)[-1] == "✅ Done: call mom"
    bot_callback("next:999999", lang="en")
    assert ("answerCallbackQuery", {"callback_query_id": "cb", "text": "Not found"}) in tg


def test_bot_lists_and_done_in_english(tg):
    bot_message("/inbox", lang="en")
    assert texts(tg)[-1] == "INBOX:\nEmpty 🎉"
    bot_message("/done abc", lang="en")
    assert texts(tg)[-1] == "Usage: /done 12"
    bot_message("/done 42", lang="en")
    assert texts(tg)[-1] == "Couldn't find that task"
    bot_message("task", lang="en")
    num = A.row("select num from items")["num"]
    bot_message(f"/done {num}", lang="en")
    assert texts(tg)[-1] == f'✅ Done: <a href="http://localhost:8000/i/{num}">#{num}</a> task'
    asyncio.run(A.handle_message({"chat": {"id": 777}, "from": {"id": 777, "language_code": "en"}, "photo": [{}]}))
    assert texts(tg)[-1] == "I only understand text for now."


def test_bot_email_flow_in_english(tg, mail):
    bot_message("/email nope", lang="en")
    assert texts(tg)[-1] == "Invalid email address"
    bot_message("/email tom@example.com", lang="en")
    assert texts(tg)[-1] == "Code sent to tom@example.com. Send it here — 6 digits."
    assert mail[-1][1].startswith("Your GTD sign-in code")  # письмо — на языке бота
    bot_message("000000" if last_code(mail) != "000000" else "111111", lang="en")
    assert texts(tg)[-1] == "Wrong code"
    bot_message(last_code(mail), lang="en")
    assert texts(tg)[-1].startswith("✅ tom@example.com is linked.")


def test_bot_tg_login_confirm_in_english(client, tg):
    nonce = client.post("/api/auth/tg/start", json={}).json()["nonce"]
    bot_message(f"/start {nonce}", tg_id=555, lang="en")
    assert texts(tg)[-1].startswith("Confirm: sign in to GTD in your browser?")
    assert tg[-1][1]["reply_markup"]["inline_keyboard"][0][0]["text"] == "✅ Confirm"
    bot_callback(f"tgok:{nonce}", tg_id=555, lang="en")
    assert texts(tg)[-1] == "✅ Sign-in confirmed — go back to your browser"
    bot_message("/start stale", tg_id=556, lang="en")
    assert texts(tg)[-1] == "This link has expired — press the button on the website again."


def test_explicit_lang_beats_telegram_language(tg, client, login):
    uid = login(client)
    A.run("update users set tg_id=777 where id=%s", (uid,))
    client.patch("/api/me", json={"lang": "en"})
    bot_message("/help", lang="ru")
    assert texts(tg)[-1].startswith("The GTD bot is ready.")
    client.patch("/api/me", json={"lang": None})  # снова авто — по Telegram
    bot_message("/help", lang="ru")
    assert texts(tg)[-1].startswith("GTD-бот готов.")


def test_reminder_buttons_follow_last_telegram_language(tg):
    bot_message("water the plants", lang="en")
    bot_message("полить цветы", tg_id=888, lang="ru")
    A.run("update items set remind_at=%s", (int(time.time()) - 1,))
    tg.clear()
    asyncio.run(A.send_due_reminders())
    kb = {p["chat_id"]: [b["text"] for b in p["reply_markup"]["inline_keyboard"][0]] for m, p in tg}
    assert kb == {777: ["✅ Done", "💤 +1h", "⏭ Next"], 888: ["✅ Готово", "💤 +1ч", "⏭ Next"]}
    assert A.row("select tg_lang from users where tg_id=777")["tg_lang"] == "en"


def test_merge_keeps_language_choice(tg, mail, login, new_client):
    web = new_client()
    login(web, "alice@example.com")
    web.post("/api/capture", json={"text": "с сайта"})
    web.patch("/api/me", json={"lang": "en"})
    bot_message("из бота", lang="de")
    bot_message("/email alice@example.com")
    bot_message(last_code(mail))
    bot_callback(tg[-1][1]["reply_markup"]["inline_keyboard"][0][0]["callback_data"])
    u = A.row("select * from users")
    assert (u["lang"], u["tg_lang"]) == ("en", "de")  # выбор с сайта и язык Telegram пережили объединение


# ── словари ──

def fields(s):
    return {f for _, f, _, _ in string.Formatter().parse(s) if f}


def test_texts_have_same_keys_and_placeholders():
    assert set(A.TEXTS["ru"]) == set(A.TEXTS["en"])
    for k in A.TEXTS["ru"]:
        assert fields(A.TEXTS["ru"][k]) == fields(A.TEXTS["en"][k]), k


def test_no_russian_in_english_bot_and_server_texts():
    for k, v in A.TEXTS["en"].items():
        assert not CYR.search(v), k
    assert not CYR.search(str(A.BOT_PROFILE["en"]) + str(A.BOT_COMMANDS["en"]))
    for lang in ("ru", "en"):
        assert [c["command"] for c in A.BOT_COMMANDS[lang]] == [c["command"] for c in A.BOT_COMMANDS["ru"]]


def test_english_public_pages_have_no_russian_ui_note(client):
    for path in ("/en/", "/en/about"):
        assert "The app interface is in Russian" not in client.get(path).text
