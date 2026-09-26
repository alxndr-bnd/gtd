"""Вход и привязка через Telegram Login Widget (SERBITO-292): проверка подписи и пути входа/привязки/объединения."""
import hashlib
import hmac
import time

import pytest

import app as A
from conftest import bot_message

EN = {"accept-language": "en-US,en;q=0.9"}


def signed(token="test-token", **fields):
    """Payload, как его отдаёт виджет: поля + hash = HMAC-SHA256(data-check-string, SHA256(токена))."""
    data = {"id": 777, "first_name": "Tom", "username": "tom", "auth_date": int(time.time()), **fields}
    check = "\n".join(f"{k}={data[k]}" for k in sorted(data))
    data["hash"] = hmac.new(hashlib.sha256(token.encode()).digest(), check.encode(), hashlib.sha256).hexdigest()
    return data


def widget(c, auth, link=False, headers=None):
    return c.post("/api/auth/tg/widget", json={"auth": auth, "link": link}, headers=headers or {})


# ── Подпись ──

def test_verify_valid_payload(tg):
    assert A.tg_widget_verify(signed(photo_url="https://t.me/i/userpic/320/tom.jpg")) == {"id": 777, "first_name": "Tom"}


@pytest.mark.parametrize("spoil", [
    lambda d: d.update(id=888),                        # подменили поле после подписи
    lambda d: d.update(first_name="Mallory"),
    lambda d: d.update(extra="x"),                     # добавили неподписанное поле
    lambda d: d.update(hash="0" * 64),                 # чужой hash
    lambda d: d.update(hash=signed(token="other-token")["hash"]),  # подписано другим ботом
    lambda d: d.update(hash="я" * 64),                 # не-ASCII не роняет сравнение
    lambda d: d.pop("hash"),
    lambda d: d.update(hash=""),
    lambda d: d.update(hash=None),
])
def test_verify_rejects_tampered(tg, spoil):
    d = signed()
    spoil(d)
    with pytest.raises(A.AuthError) as e:
        A.tg_widget_verify(d)
    assert e.value.key == "tg_widget_fail"


@pytest.mark.parametrize("bad", [None, [], "id=777", {}])
def test_verify_rejects_garbage(tg, bad):
    with pytest.raises(A.AuthError):
        A.tg_widget_verify(bad)


def test_verify_rejects_signed_but_malformed(tg):
    with pytest.raises(A.AuthError):
        A.tg_widget_verify(signed(id="abc"))
    with pytest.raises(A.AuthError):
        A.tg_widget_verify(signed(first_name=None))


def test_verify_stale_and_future_auth_date(tg):
    old = int(time.time()) - A.TG_WIDGET_MAX_AGE - 60
    with pytest.raises(A.AuthError) as e:
        A.tg_widget_verify(signed(auth_date=old))
    assert e.value.key == "tg_widget_stale"
    with pytest.raises(A.AuthError) as e:
        A.tg_widget_verify(signed(auth_date=int(time.time()) + 3600))
    assert e.value.key == "tg_widget_stale"
    assert A.tg_widget_verify(signed(auth_date=int(time.time()) - A.TG_WIDGET_MAX_AGE + 60))["id"] == 777


def test_verify_needs_bot_token():
    with pytest.raises(A.AuthError):  # без токена проверять нечем — ничего не принимаем
        A.tg_widget_verify(signed())


# ── Вход ──

def test_widget_login_new_user(client, tg):
    r = widget(client, signed())
    assert r.status_code == 200 and r.json() == {"ok": True} and "sid" in r.cookies
    u = A.row("select * from users")
    assert (u["tg_id"], u["name"]) == (777, "Tom")
    assert client.get("/api/me").json()["tg"] is True


def test_widget_login_existing_user(client, tg):
    bot_message("задача из бота", tg_id=777)  # аккаунт уже есть — создан ботом
    uid = A.row("select id from users where tg_id=777")["id"]
    assert widget(client, signed()).status_code == 200
    assert A.row("select count(*) n from users")["n"] == 1
    assert [i["title"] for i in client.get("/api/items?status=all").json()] == ["задача из бота"]
    assert A.row("select user_id from sessions")["user_id"] == uid


def test_widget_rejected_payload_gives_no_session(client, tg):
    d = signed()
    d["id"] = 888
    r = widget(client, d)
    assert r.status_code == 400 and "sid" not in r.cookies
    assert A.row("select count(*) n from users")["n"] == 0


def test_widget_off_without_bot(client):
    r = widget(client, signed())
    assert r.status_code == 400 and r.json()["detail"] == "Telegram-бот выключен"


def test_widget_error_texts_ru_en(client, tg):
    bad = {**signed(), "hash": "0" * 64}
    assert widget(client, bad).json()["detail"] == "Telegram не подтвердил вход — попробуй ещё раз"
    assert widget(client, bad, headers=EN).json()["detail"] == "Telegram didn't confirm the sign-in — try again"
    stale = signed(auth_date=int(time.time()) - A.TG_WIDGET_MAX_AGE - 60)
    assert widget(client, stale).json()["detail"] == "Вход через Telegram устарел — нажми кнопку ещё раз"
    assert widget(client, stale, headers=EN).json()["detail"] == "The Telegram sign-in has expired — press the button again"


# ── Привязка ──

def test_widget_links_to_signed_in_account(client, login, tg):
    alice = login(client)
    r = widget(client, signed(), link=True)
    assert r.json() == {"ok": True}
    assert A.row("select tg_id from users where id=%s", (alice,))["tg_id"] == 777
    assert A.row("select count(*) n from users")["n"] == 1
    assert client.get("/api/me").json()["email"] == "alice@example.com"


def test_widget_link_without_session_signs_in(client, tg):
    assert widget(client, signed(), link=True).json() == {"ok": True}  # «link» без сессии — обычный вход
    assert client.get("/api/me").json()["tg"] is True


def test_widget_link_busy_tg_offers_merge(new_client, login, tg):
    c = new_client()
    alice = login(c)
    c.post("/api/capture", json={"text": "моё"})
    bot_message("задача из бота", tg_id=888)  # у Telegram 888 свой аккаунт с задачей

    r = widget(c, signed(id=888), link=True)
    assert r.status_code == 409
    d = r.json()
    assert d["items"] == 1 and d["merge"] and "Этот Telegram" in d["detail"] and "Объединить" in d["detail"]
    assert A.row("select tg_id from users where id=%s", (alice,))["tg_id"] is None  # пока ничего не тронуто

    assert c.post("/api/auth/merge", json={"token": d["merge"]}).json() == {"ok": True}
    assert A.row("select tg_id from users where id=%s", (alice,))["tg_id"] == 888
    assert A.row("select count(*) n from users")["n"] == 1
    assert len(c.get("/api/items?status=all").json()) == 2


def test_widget_merge_offer_in_english(new_client, login, tg):
    c = new_client()
    login(c)
    c.post("/api/capture", json={"text": "mine"})
    bot_message("from the bot", tg_id=888)
    d = widget(c, signed(id=888), link=True, headers=EN).json()
    assert "This Telegram" in d["detail"]


# ── Фронт ──

def test_spa_uses_widget_with_fallback():
    html = open(A.os.path.join(A.os.path.dirname(A.__file__), "static", "index.html"), encoding="utf-8").read()
    assert "https://telegram.org/js/telegram-widget.js?22" in html
    assert "location.hostname === 'gtd.serbito.rs'" in html and "pointer: coarse" in html  # где виджет не работает
    assert "authPost('tg/widget'" in html and "data-act=\"tglogin\"" in html  # и запасной путь через бота
