"""Telegram-бот: команды, захват, кнопки под задачей, вход по /login, напоминания."""
import asyncio
import re
import time

import app as A
from conftest import bot_callback, bot_message, last_code


def texts(sent):
    return [p.get("text", "") for m, p in sent if m in ("sendMessage", "editMessageText")]


def test_anyone_gets_an_account(tg):
    bot_message("/start", tg_id=999, name="Stranger")
    assert "GTD-бот готов" in texts(tg)[-1]
    assert A.row("select name from users where tg_id=999")["name"] == "Stranger"


def test_capture_from_telegram(tg):
    bot_message("позвонить маме завтра в 10:00 @телефон")
    it = A.row("select * from items")
    assert (it["title"], it["status"], it["context"], it["source"]) == ("позвонить маме", "next", "телефон", "telegram")
    method, p = tg[-1]
    assert p["text"].startswith(f"✓ Next #{it['id']}: позвонить маме")
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


def test_done_command(tg):
    bot_message("задача")
    iid = A.row("select id from items")["id"]
    bot_message("/done abc")
    assert texts(tg)[-1] == "Использование: /done 12"
    bot_message(f"/done #{iid + 100}")
    assert texts(tg)[-1] == "Не нашёл такую задачу"
    bot_message(f"/done {iid}")
    assert texts(tg)[-1] == "✅ Готово" and A.row("select status from items")["status"] == "done"


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
    assert texts(tg) == ["⏰ полить цветы"]
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

def test_email_command_without_address(tg):
    bot_message("/email")
    assert "/email you@example.com" in texts(tg)[-1]


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
