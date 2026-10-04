"""MCP для AI-ассистентов (SERBITO-375): личные API-токены и /mcp — авторизация, лимиты, изоляция, инструменты."""
import asyncio
import hashlib
import logging

import httpx2
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

import app as A
import mcp_server as M

LEGACY = "2025-06-18"  # версия протокола с initialize — её шлёт большинство клиентов сейчас


def new_token(c, name="Claude") -> str:
    r = c.post("/api/tokens", json={"name": name})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def rpc(c, tok, method, params=None, headers=None):
    h = {"accept": "application/json, text/event-stream", "content-type": "application/json",
         "mcp-protocol-version": LEGACY, **({"authorization": f"Bearer {tok}"} if tok else {}), **(headers or {})}
    return c.post("/mcp", headers=h, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}})


def call(c, tok, tool, **args) -> dict:
    r = rpc(c, tok, "tools/call", {"name": tool, "arguments": args})
    assert r.status_code == 200, r.text
    res = r.json()["result"]
    assert not res["isError"], res
    return res["structuredContent"]


def call_error(c, tok, tool, **args) -> str:
    r = rpc(c, tok, "tools/call", {"name": tool, "arguments": args})
    assert r.status_code == 200, r.text
    res = r.json()["result"]
    assert res["isError"], res
    return res["content"][0]["text"]


@pytest.fixture
def alice(new_client, login):
    """Вошедшая в браузере Алиса и её токен; MCP-запросы — с отдельного клиента без cookie."""
    web = new_client()
    uid = login(web, "alice@example.com")
    return {"web": web, "uid": uid, "tok": new_token(web), "mcp": new_client()}


# ── токены ──

def test_token_shown_once_and_stored_only_as_hash(alice):
    web, tok = alice["web"], alice["tok"]
    assert tok.startswith("gtd_") and len(tok) > 40
    stored = A.rows("select * from api_tokens")
    assert len(stored) == 1 and stored[0]["token_hash"] == hashlib.sha256(tok.encode()).hexdigest()
    assert not any(tok in str(v) for v in stored[0].values())  # самого токена в базе нет
    listed = web.get("/api/tokens").json()
    assert [t["name"] for t in listed] == ["Claude"] and "token" not in listed[0] and "token_hash" not in listed[0]
    assert listed[0]["last_used"] is None


def test_create_token_response_not_cached(client, login):
    login(client)
    assert client.post("/api/tokens", json={"name": "x"}).headers["cache-control"] == "no-store"


def test_token_name_and_limit(client, login, monkeypatch):
    login(client)
    for bad in ({}, {"name": "  "}, {"name": 5}):
        assert client.post("/api/tokens", json=bad).status_code == 400
    assert client.post("/api/tokens", json={"name": "x" * 200}).json()["name"] == "x" * A.API_TOKEN_NAME_MAX
    monkeypatch.setattr(A, "API_TOKENS_MAX", 2)
    assert client.post("/api/tokens", json={"name": "b"}).status_code == 200
    r = client.post("/api/tokens", json={"name": "c"}, headers={"accept-language": "en"})
    assert r.status_code == 400 and "revoke" in r.json()["detail"]


def test_tokens_need_a_web_session(client, alice):
    assert client.get("/api/tokens").status_code == 401
    assert client.post("/api/tokens", json={"name": "x"}).status_code == 401
    # Сам токен токенами не управляет: /api — только по cookie сессии
    bearer = {"authorization": f"Bearer {alice['tok']}"}
    assert client.get("/api/tokens", headers=bearer).status_code == 401
    assert client.get("/api/items", headers=bearer).status_code == 401


def test_revoke_token(alice):
    web, mcp, tok = alice["web"], alice["mcp"], alice["tok"]
    assert rpc(mcp, tok, "tools/list").status_code == 200
    tid = web.get("/api/tokens").json()[0]["id"]
    assert web.delete(f"/api/tokens/{tid}").json() == {"ok": True}
    assert web.get("/api/tokens").json() == []
    assert rpc(mcp, tok, "tools/list").status_code == 401
    assert web.delete(f"/api/tokens/{tid}").status_code == 404


def test_tokens_are_per_account(alice, new_client, login):
    bob = new_client()
    login(bob, "bob@example.com")
    tid = alice["web"].get("/api/tokens").json()[0]["id"]
    assert bob.get("/api/tokens").json() == []
    assert bob.delete(f"/api/tokens/{tid}").status_code == 404  # чужой токен не отозвать
    assert len(alice["web"].get("/api/tokens").json()) == 1


def test_token_creation_rejected_cross_site(client, login):
    login(client)
    r = client.post("/api/tokens", json={"name": "x"}, headers={"origin": "https://evil.example"})
    assert r.status_code == 403


def test_tokens_follow_account_merge(alice, new_client, login):
    bob = new_client()
    bob_uid = login(bob, "bob@example.com")
    bob_tok = new_token(bob, "Bob's Claude")
    A.merge_accounts(alice["uid"], bob_uid)
    assert call(alice["mcp"], bob_tok, "capture", text="after merge")["number"] == 1
    assert A.row("select user_id from items")["user_id"] == alice["uid"]
    assert sorted(t["name"] for t in alice["web"].get("/api/tokens").json()) == ["Bob's Claude", "Claude"]


def test_last_used_is_recorded(alice):
    mcp, tok = alice["mcp"], alice["tok"]
    rpc(mcp, tok, "tools/list")
    first = A.row("select last_used from api_tokens")["last_used"]
    assert first and alice["web"].get("/api/tokens").json()[0]["last_used"] == first
    A.run("update api_tokens set last_used=1")
    rpc(mcp, tok, "tools/list")
    assert A.row("select last_used from api_tokens")["last_used"] >= first  # старая отметка сдвинулась
    A.run("update api_tokens set last_used=%s", (first + 1000,))
    rpc(mcp, tok, "tools/list")
    assert A.row("select last_used from api_tokens")["last_used"] == first + 1000  # свежую не переписываем


def test_token_never_logged(new_client, login, caplog):
    caplog.set_level(logging.DEBUG)
    web = new_client()
    login(web)
    tok = new_token(web)
    call(new_client(), tok, "capture", text="secret thought")
    assert tok not in caplog.text and tok[4:] not in caplog.text and "secret thought" not in caplog.text


# ── /mcp: вход и лимиты ──

def test_mcp_requires_bearer_token(client, alice):
    for headers in ({}, {"authorization": "Basic YWxpY2U6eA=="}, {"authorization": "Bearer "}):
        r = rpc(client, None, "tools/list", headers=headers)
        assert r.status_code == 401 and r.headers["www-authenticate"].startswith("Bearer"), headers
    for bad in ("gtd_wrong", "not-a-gtd-token", alice["tok"] + "x"):
        r = rpc(client, bad, "tools/list")
        assert r.status_code == 401 and 'error="invalid_token"' in r.headers["www-authenticate"]
    # Cookie сессии на сайте для /mcp не годится: только токен
    assert rpc(alice["web"], None, "tools/list").status_code == 401


def test_mcp_rate_limit_per_token(alice, monkeypatch):
    monkeypatch.setattr(M, "MCP_RATE", (3, 60))
    mcp, web = alice["mcp"], alice["web"]
    assert [rpc(mcp, alice["tok"], "tools/list").status_code for _ in range(3)] == [200] * 3
    r = rpc(mcp, alice["tok"], "tools/list")
    assert r.status_code == 429 and r.headers["retry-after"] == "60"
    assert rpc(mcp, new_token(web, "second"), "tools/list").status_code == 200  # у другого токена — свой счёт
    A.run("update auth_limits set since = since - 61 where key like 'mcp:%%'")
    assert rpc(mcp, alice["tok"], "tools/list").status_code == 200  # окно прошло


def test_mcp_wrong_tokens_limited_per_ip(client, monkeypatch):
    monkeypatch.setattr(M, "MCP_BAD_TOKENS", (2, 600))
    assert [rpc(client, "gtd_guess", "tools/list").status_code for _ in range(3)] == [401, 401, 429]


# ── инструменты ──

def test_tools_listed(alice):
    tools = rpc(alice["mcp"], alice["tok"], "tools/list").json()["result"]["tools"]
    assert {t["name"] for t in tools} == {"list_tasks", "list_projects", "list_contexts", "capture",
                                         "complete_task", "move_task", "update_task", "weekly_review"}
    by_name = {t["name"]: t for t in tools}
    assert by_name["list_tasks"]["annotations"]["readOnlyHint"] is True
    assert "ctx" not in by_name["capture"]["inputSchema"]["properties"]  # контекст MCP — не аргумент


def test_capture(alice):
    mcp, tok = alice["mcp"], alice["tok"]
    t = call(mcp, tok, "capture", text="buy milk")
    assert (t["number"], t["title"], t["list"], t["project"], t["contexts"]) == (1, "buy milk", "inbox", None, [])
    assert t["url"].endswith("/i/1")
    t = call(mcp, tok, "capture", text="call the bank tomorrow 10am #Finance @phone")
    assert (t["title"], t["list"], t["project"], t["contexts"]) == ("call the bank", "next", "Finance", ["phone"])
    assert t["due"].endswith("10:00+02:00") or t["due"].endswith("10:00+01:00")
    assert A.row("select source from items where num=2 and user_id=%s", (alice["uid"],))["source"] == "mcp"
    assert A.row("select channel from activity where user_id=%s and actions>0", (alice["uid"],))["channel"] == "mcp"
    assert "Empty task" in call_error(mcp, tok, "capture", text="   ")
    t = call(mcp, tok, "capture", text="pay the bill @phone @Computer")  # SERBITO-423: все контексты
    assert (t["title"], t["list"], t["contexts"]) == ("pay the bill", "next", ["phone", "computer"])


def test_list_tasks(alice):
    mcp, tok, uid = alice["mcp"], alice["tok"], alice["uid"]
    A.capture(uid, "inbox one")
    A.capture(uid, "fix the roof #House @home")
    A.capture(uid, "paint the wall #House @home")
    A.capture(uid, "call mom @phone")
    A.capture(uid, "dentist 2026-12-01 10:00")
    A.mark_done(uid, num=3)
    titles = lambda **kw: [t["title"] for t in call(mcp, tok, "list_tasks", **kw)["tasks"]]
    assert titles() == ["inbox one", "dentist"]
    assert titles(list="next") == ["fix the roof", "call mom"]
    assert titles(list="next", context="@home") == ["fix the roof"] == titles(list="next", context="HOME")
    # SERBITO-423: задача с двумя контекстами — под каждым; несколько в фильтре — любой из них
    A.capture(uid, "pay the bill @phone @computer")
    assert titles(list="next", context="@phone") == ["call mom", "pay the bill"]
    assert titles(list="next", context="computer") == ["pay the bill"]
    assert titles(list="next", context="@home @computer") == ["fix the roof", "pay the bill"]
    assert titles(list="all", project="house") == ["paint the wall", "fix the roof"]
    assert titles(list="done") == ["paint the wall"]
    assert titles(list="scheduled") == ["dentist"]
    assert titles(list="all", query="ROOF") == ["fix the roof"]
    res = call(mcp, tok, "list_tasks", list="all", limit=2)
    assert res["total"] == 6 and len(res["tasks"]) == 2
    assert "No project named" in call_error(mcp, tok, "list_tasks", project="Nope")


def test_list_projects_and_contexts(alice):
    mcp, tok, uid = alice["mcp"], alice["tok"], alice["uid"]
    A.capture(uid, "fix the roof #House @home")
    A.capture(uid, "someday idea #Garden")
    A.capture(uid, "call mom @phone")
    A.capture(uid, "call dad @phone")
    A.capture(uid, "pay the bill @phone @home")  # SERBITO-423: считается в обоих контекстах
    garden = A.item_by_num(uid, 2)
    A.item_patch(uid, garden["id"], {"status": "someday"})
    # Порядок — ручной порядок проектов (SERBITO-391): новые проекты встают в конец, то есть в порядке создания
    assert call(mcp, tok, "list_projects")["projects"] == [
        {"name": "House", "open_tasks": 1, "next_actions": 1, "needs_next_action": False},
        {"name": "Garden", "open_tasks": 0, "next_actions": 0, "needs_next_action": True}]
    assert call(mcp, tok, "list_contexts")["contexts"] == [{"context": "@phone", "open_tasks": 3},
                                                           {"context": "@home", "open_tasks": 2}]


def test_complete_task(alice):
    mcp, tok, uid = alice["mcp"], alice["tok"], alice["uid"]
    A.capture(uid, "write report")
    t = call(mcp, tok, "complete_task", number=1)
    assert t["list"] == "done" and t["completed"]
    assert call(mcp, tok, "complete_task", number=1)["completed"] == t["completed"]  # повтор ничего не меняет
    assert "Task #7 not found" in call_error(mcp, tok, "complete_task", number=7)


def test_move_task(alice):
    mcp, tok, uid = alice["mcp"], alice["tok"], alice["uid"]
    A.capture(uid, "plan the trip")
    t = call(mcp, tok, "move_task", number=1, project="#Vacation_2027")
    assert (t["list"], t["project"]) == ("next", "Vacation 2027")  # из Inbox с проектом — в Next, как в приложении
    t = call(mcp, tok, "move_task", number=1, context="@Laptop", list="waiting")
    assert (t["list"], t["contexts"], t["project"]) == ("waiting", ["laptop"], "Vacation 2027")
    t = call(mcp, tok, "move_task", number=1, project="vacation 2027")  # существующий — без дубля
    assert len(A.rows("select 1 from projects where user_id=%s", (uid,))) == 1
    t = call(mcp, tok, "move_task", number=1, project="", context="")
    assert (t["project"], t["contexts"], t["list"]) == (None, [], "waiting")
    # SERBITO-423: несколько контекстов — списком или в старом аргументе через пробел; оба сразу — ошибка
    assert call(mcp, tok, "move_task", number=1, contexts=["@phone", "Laptop"])["contexts"] == ["phone", "laptop"]
    assert call(mcp, tok, "move_task", number=1, context="@home @car")["contexts"] == ["home", "car"]
    assert call(mcp, tok, "move_task", number=1, contexts=[])["contexts"] == []
    assert "not both" in call_error(mcp, tok, "move_task", number=1, context="@a", contexts=["b"])
    A.capture(uid, "inbox task")
    t = call(mcp, tok, "move_task", number=2, contexts=["phone", "home"])
    assert (t["list"], t["contexts"]) == ("next", ["phone", "home"])  # из Inbox с контекстами — в Next
    assert call(mcp, tok, "move_task", number=1, list="someday")["list"] == "someday"
    assert "Nothing to change" in call_error(mcp, tok, "move_task", number=1)
    assert "not found" in call_error(mcp, tok, "move_task", number=9, list="next")
    r = rpc(mcp, tok, "tools/call", {"name": "move_task", "arguments": {"number": 1, "list": "done!"}})
    assert r.json()["result"]["isError"]  # список — только из перечня


def test_cross_user_isolation(alice, new_client, login):
    """Токен Алисы видит и меняет только её задачи: номера у каждого свои, чужой #1 — не её #1."""
    mcp, tok = alice["mcp"], alice["tok"]
    bob = new_client()
    bob_uid = login(bob, "bob@example.com")
    A.capture(bob_uid, "bob secret #BobProject @bobctx")
    A.capture(bob_uid, "bob inbox")
    for lst in ("inbox", "next", "all"):
        assert call(mcp, tok, "list_tasks", list=lst)["tasks"] == []
    assert call(mcp, tok, "list_tasks", list="all", query="bob")["total"] == 0
    assert call(mcp, tok, "list_projects")["projects"] == []
    assert call(mcp, tok, "list_contexts")["contexts"] == []
    assert "No project named" in call_error(mcp, tok, "list_tasks", project="BobProject")
    assert "not found" in call_error(mcp, tok, "complete_task", number=1)
    assert "not found" in call_error(mcp, tok, "move_task", number=2, list="trash")
    # Свой проект с тем же названием — новый, у Алисы; проект Боба не тронут
    call(mcp, tok, "capture", text="mine")
    t = call(mcp, tok, "move_task", number=1, project="BobProject")
    mine = A.row("select p.user_id from projects p join items i on i.project_id=p.id where i.title='mine'")
    assert t["project"] == "BobProject" and mine["user_id"] == alice["uid"]
    assert [(i["title"], i["status"]) for i in A.rows("select title, status from items where user_id=%s order by num",
                                                     (bob_uid,))] == [("bob secret", "next"), ("bob inbox", "inbox")]


# ── официальный клиент MCP (SDK) против приложения целиком ──

@pytest.mark.parametrize("mode", ["legacy", "auto"])
def test_official_client(alice, mode):
    """Клиент SDK, как у Claude Code: старое рукопожатие (initialize) и новый протокол без сессий."""
    async def run():
        http = httpx2.AsyncClient(transport=httpx2.ASGITransport(app=A.app), base_url="http://localhost:8000",
                                  headers={"authorization": f"Bearer {alice['tok']}"})
        async with http, Client(streamable_http_client("http://localhost:8000/mcp", http_client=http),
                                mode=mode) as c:
            names = {t.name for t in (await c.list_tools()).tools}
            got = await c.call_tool("capture", {"text": "via sdk"})
            listed = await c.call_tool("list_tasks", {})
            return names, got, listed
    names, got, listed = asyncio.run(run())
    assert "capture" in names and not got.is_error
    assert got.structured_content["title"] == "via sdk"
    assert [t["title"] for t in listed.structured_content["tasks"]] == ["via sdk"]


# ── этап 2: update_task и weekly_review ──

def test_update_task(alice):
    mcp, tok, uid = alice["mcp"], alice["tok"], alice["uid"]
    A.capture(uid, "draft the report")
    t = call(mcp, tok, "update_task", number=1, title="write the report", notes="two pages")
    assert (t["title"], t["notes"], t["list"], t["due"]) == ("write the report", "two pages", "inbox", None)
    t = call(mcp, tok, "update_task", number=1, due="2026-12-01")
    assert t["due"].startswith("2026-12-01T09:00")  # дата без времени — 9:00, как при захвате
    t = call(mcp, tok, "update_task", number=1, due="2026-12-02T15:30")
    assert t["due"].startswith("2026-12-02T15:30")
    t = call(mcp, tok, "update_task", number=1, due="tomorrow 10am")
    assert "T10:00" in t["due"]
    assert A.row("select reminded from items where num=1 and user_id=%s", (uid,))["reminded"] == 0
    # Явный срок важнее даты в новом заголовке
    t = call(mcp, tok, "update_task", number=1, title="write the report tomorrow", due="2026-12-03")
    assert t["title"] == "write the report" and t["due"].startswith("2026-12-03")
    assert call(mcp, tok, "update_task", number=1, due="")["due"] is None
    assert "Cannot read the date" in call_error(mcp, tok, "update_task", number=1, due="someday soon")
    # SERBITO-423: @контексты в новом заголовке — все, и заменяют прежние
    t = call(mcp, tok, "update_task", number=1, title="write the report @Office @laptop")
    assert (t["title"], t["contexts"]) == ("write the report", ["office", "laptop"])
    assert "Nothing to change" in call_error(mcp, tok, "update_task", number=1)
    assert "Empty title" in call_error(mcp, tok, "update_task", number=1, title="  ")
    assert "not found" in call_error(mcp, tok, "update_task", number=5, notes="x")


def test_update_task_waiting_for(alice):
    mcp, tok, uid = alice["mcp"], alice["tok"], alice["uid"]
    A.capture(uid, "contract signed")
    A.item_patch(uid, A.item_by_num(uid, 1)["id"], {"notes": "scan it"})
    t = call(mcp, tok, "update_task", number=1, waiting_for="  Bob   from legal ")
    assert (t["list"], t["notes"]) == ("waiting", "Waiting for: Bob from legal\nscan it")
    t = call(mcp, tok, "update_task", number=1, waiting_for="Анна")  # строка заменяется, а не копится
    assert t["notes"] == "Ждём: Анна\nscan it"
    A.run("update users set lang='en' where id=%s", (uid,))
    assert call(mcp, tok, "update_task", number=1, waiting_for="Анна")["notes"] == "Waiting for: Анна\nscan it"
    t = call(mcp, tok, "update_task", number=1, waiting_for="")
    assert (t["notes"], t["list"]) == ("scan it", "waiting")
    t = call(mcp, tok, "update_task", number=1, notes="new notes", waiting_for="Carol")
    assert t["notes"] == "Waiting for: Carol\nnew notes"


def test_weekly_review(alice):
    mcp, tok, uid = alice["mcp"], alice["tok"], alice["uid"]
    now = int(A.time.time())
    A.capture(uid, "inbox one")
    A.capture(uid, "inbox two")
    A.capture(uid, "fix the roof #House @home")       # 3: next в House
    A.capture(uid, "pick a color #Kitchen")           # 4: Kitchen без next
    A.capture(uid, "answer from Bob")                 # 5: waiting, давно
    A.capture(uid, "answer from Ann")                 # 6: waiting, недавно
    A.capture(uid, "pay rent")                        # 7: просрочено
    A.capture(uid, "dentist")                         # 8: через 2 дня
    A.capture(uid, "idea")                            # 9: someday
    A.capture(uid, "old overdue but done")            # 10: done — не в обзоре
    st = lambda n, s: A.run("update items set status=%s where user_id=%s and num=%s", (s, uid, n))
    st(4, "waiting"), st(5, "waiting"), st(6, "waiting"), st(9, "someday"), st(10, "done")
    A.run("update items set created=%s where user_id=%s and num=5", (now - 10 * 86400, uid))
    A.run("update items set remind_at=%s where user_id=%s and num in (7, 10)", (now - 3600, uid))
    A.run("update items set remind_at=%s where user_id=%s and num=8", (now + 2 * 86400, uid))
    r = call(mcp, tok, "weekly_review")
    assert r["inbox_count"] == 4  # inbox one, inbox two, pay rent, dentist
    assert r["projects_without_next_action"] == [{"name": "Kitchen", "open_tasks": 1}]
    assert [t["number"] for t in r["overdue"]] == [7]
    assert [(t["number"], t["waiting_over_a_week"]) for t in r["waiting"]] == [(5, True), (4, False), (6, False)]
    assert [t["number"] for t in r["upcoming_7_days"]] == [8]
    assert r["someday_count"] == 1
