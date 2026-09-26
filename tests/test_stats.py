"""Аналитика использования: считаем людей и действия, но ничего из содержимого наружу не отдаём."""
import json

import app as A
from conftest import bot_message


def activity():
    return {(r["user_id"], r["channel"]): r["actions"] for r in A.rows("select * from activity")}


def test_actions_are_tracked_per_channel(client, login, tg):
    uid = login(client)
    client.post("/api/capture", json={"text": "с сайта"})
    iid = client.get("/api/items?status=all").json()[0]["id"]
    client.patch(f"/api/items/{iid}", json={"status": "next"})
    bot_message("из бота", tg_id=777)
    bot_message("/inbox", tg_id=777)
    tom = A.row("select id from users where tg_id=777")["id"]
    a = activity()
    assert a[(uid, "web")] == 2  # захват + правка; просмотры идут с нулём
    assert a[(tom, "telegram")] == 2  # захват в боте + команда


def test_visits_mark_active_once_per_day(client, login):
    uid = login(client)
    for _ in range(5):
        client.get("/api/counts")
    assert A.row("select count(*) n from activity where user_id=%s", (uid,))["n"] == 1
    assert activity()[(uid, "web")] == 0  # был, но ничего не делал


def test_stats_only_for_admin(client, login, monkeypatch):
    uid = login(client)
    assert client.get("/api/admin/stats").status_code == 404  # не владельцу эндпоинта «нет»
    assert client.get("/api/me").json()["admin"] is False
    monkeypatch.setattr(A, "ADMIN_USER_IDS", {uid})
    assert client.get("/api/me").json()["admin"] is True
    assert client.get("/api/admin/stats").status_code == 200


def test_stats_numbers_and_no_content(new_client, login, tg, monkeypatch):
    alice, bob = new_client(), new_client()
    a = login(alice, "alice@example.com")
    login(bob, "bob@example.com")
    alice.post("/api/capture", json={"text": "секретный план захвата мира"})
    bob.post("/api/capture", json={"text": "личное дело Боба"})
    bot_message("задача из телеграма", tg_id=777, name="Tom")
    monkeypatch.setattr(A, "ADMIN_USER_IDS", {a})
    st = alice.get("/api/admin/stats").json()
    assert st["users"]["total"] == 3 and st["users"]["with_email"] == 2 and st["users"]["with_telegram"] == 1
    assert st["active"]["day"] == 3 and st["active"]["web_30d"] == 2 and st["active"]["telegram_30d"] == 1
    assert st["tasks"]["created_7d"] == 3 and len(st["daily"]) == 30 and st["daily"][-1]["users"] == 3
    dump = json.dumps(st, ensure_ascii=False)
    for secret in ("секретный", "Боба", "телеграма", "alice@", "bob@", "Tom", "777"):
        assert secret not in dump, secret  # ни названий задач, ни почт, ни имён, ни Telegram id
