"""Аналитика использования: считаем людей и действия, но ничего из содержимого наружу не отдаём."""
import json
from pathlib import Path

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


# ── MCP (SERBITO-465): проверка гипотезы плана OAuth — claude.ai (OAuth) против личных токенов ──

def test_stats_mcp_numbers_and_no_content(new_client, login, monkeypatch):
    from test_mcp import call, new_token
    from test_oauth import connect
    owner, alice, bob, mcp = new_client(), new_client(), new_client(), new_client()
    o = login(owner, "owner@example.com")
    login(alice, "alice@example.com")
    login(bob, "bob@example.com")
    call(mcp, new_token(alice, "секретный ноутбук"), "capture", text="секретная задача через MCP")
    new_token(alice, "запасной")  # токен есть, но им не пользовались
    call(mcp, connect(bob, new_client())["access_token"], "list_tasks")
    A.run("insert into activity(user_id,day,channel,actions) values(%s, current_date - 8, 'mcp', 3)", (o,))
    monkeypatch.setattr(A, "ADMIN_USER_IDS", {o})
    st = owner.get("/api/admin/stats").json()
    assert st["mcp"] == {"users_7d": 2, "oauth_connections": 1, "api_tokens": 2}  # владелец — 8 дней назад, мимо
    dump = json.dumps(st, ensure_ascii=False)
    for secret in ("секретн", "запасной", "Claude", "alice@", "bob@", "gtd_", "gtdo_"):
        assert secret not in dump, secret  # ни задач, ни имён токенов и клиентов, ни самих токенов


def test_stats_mcp_empty(client, login, monkeypatch):
    monkeypatch.setattr(A, "ADMIN_USER_IDS", {login(client)})
    assert client.get("/api/admin/stats").json()["mcp"] == {"users_7d": 0, "oauth_connections": 0, "api_tokens": 0}


def test_stats_not_open_to_mcp_tokens(new_client, login, monkeypatch):
    """Статистика — только по сессии владельца: его MCP-токен (Bearer) её не открывает."""
    from test_mcp import new_token
    web = new_client()
    uid = login(web, "owner@example.com")
    monkeypatch.setattr(A, "ADMIN_USER_IDS", {uid})
    tok = new_token(web)
    assert new_client().get("/api/admin/stats", headers={"authorization": f"Bearer {tok}"}).status_code == 401


def test_stats_page_shows_mcp():
    """Раздел «Статистика» в SPA выводит три числа MCP на обоих языках."""
    html = (Path(__file__).parent.parent / "static" / "index.html").read_text(encoding="utf-8")
    assert "st.mcp.users_7d" in html and "st.mcp.oauth_connections" in html and "st.mcp.api_tokens" in html
    assert html.count('"st_mcp_text"') == 2 and html.count('"st_mcp"') == 2  # ru и en
