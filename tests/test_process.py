"""Разбор Inbox по шагам (SERBITO-603): каждое решение GTD (корзина, Someday, Reference, сделано сейчас, Waiting,
проект со следующим действием, дата, Next с контекстом), проверки ввода, счётчик заходов для статистики, MCP."""
import time

import app as A
from test_mcp import rpc


def inbox(uid, text="разобрать почту"):
    return A.capture(uid, text)["id"]


def proc(client, iid, **body):
    return client.post(f"/api/items/{iid}/process", json=body)


def item(iid):
    return A.row("select title, notes, status, project_id, contexts, remind_at, completed_at from items where id=%s",
                 (iid,))


def test_not_actionable_branches(client, login):
    uid = login(client)
    for action in ("trash", "someday", "reference"):
        iid = inbox(uid, action)
        r = proc(client, iid, action=action)
        assert r.status_code == 200, r.text
        assert item(iid)["status"] == action


def test_done_now(client, login):
    uid = login(client)
    iid = inbox(uid)
    assert proc(client, iid, action="done").json()["status"] == "done"
    assert item(iid)["completed_at"]


def test_waiting_with_who_and_follow_up(client, login):
    uid = login(client)
    iid = inbox(uid)
    A.run("update items set notes='старая заметка' where id=%s", (iid,))
    follow = int(time.time()) + 3 * 86400
    r = proc(client, iid, action="waiting", who="  Марко  ", follow_up=follow)
    assert r.status_code == 200, r.text
    it = item(iid)
    assert (it["status"], it["remind_at"]) == ("waiting", follow)
    assert it["notes"] == "Ждём: Марко\nстарая заметка"
    # Без даты — без напоминания; метка — на языке аккаунта
    A.run("update users set lang='en' where id=%s", (uid,))
    other = inbox(uid, "invoice")
    proc(client, other, action="waiting", who="Anna")
    assert (item(other)["notes"], item(other)["remind_at"]) == ("Waiting for: Anna", None)


def test_waiting_needs_who(client, login):
    uid = login(client)
    iid = inbox(uid)
    for body in ({}, {"who": "  "}, {"who": 5}):
        assert proc(client, iid, action="waiting", **body).status_code == 400
    assert proc(client, iid, action="waiting", who="Anna", follow_up="tomorrow").status_code == 400
    assert item(iid)["status"] == "inbox"


def test_new_project_needs_next_action(client, login):
    uid = login(client)
    iid = inbox(uid, "ремонт кухни")
    assert proc(client, iid, action="project", project="Ремонт кухни").status_code == 400
    assert proc(client, iid, action="project", project="Ремонт кухни", next_action="  ").status_code == 400
    assert proc(client, iid, action="project", next_action="позвонить Марко").status_code == 400  # нет проекта
    assert A.rows("select 1 from projects") == []  # без следующего действия проект не создаётся
    r = proc(client, iid, action="project", project="Ремонт кухни", next_action="позвонить Марко",
             contexts=["телефон"])
    assert r.status_code == 200, r.text
    p = A.row("select id, status from projects where user_id=%s and title='Ремонт кухни'", (uid,))
    assert p["status"] == "active"
    it = item(iid)
    assert (it["title"], it["status"], it["project_id"], it["contexts"]) == ("позвонить Марко", "next", p["id"],
                                                                            ["телефон"])
    assert not it["notes"]  # прежний текст — это название проекта: в заметки не дублируем
    assert client.get("/api/projects").json()[0]["next_count"] == 1


def test_existing_project_keeps_old_text_in_notes(client, login):
    uid = login(client)
    pid = A.project_by_title(uid, "Отпуск")
    iid = inbox(uid, "что-то с визой")
    r = proc(client, iid, action="project", project_id=pid, next_action="записаться в консульство")
    assert r.status_code == 200, r.text
    it = item(iid)
    assert (it["title"], it["project_id"], it["status"]) == ("записаться в консульство", pid, "next")
    assert it["notes"] == "что-то с визой"


def test_project_picker_skips_archived_and_foreign(client, new_client, login):
    uid = login(client)
    pid = A.project_by_title(uid, "Старый")
    A.project_archive(uid, pid, "all")
    iid = inbox(uid)
    assert proc(client, iid, action="project", project_id=pid, next_action="шаг").status_code == 404
    bob = new_client()
    bob_pid = A.project_by_title(login(bob, "bob@example.com"), "Чужой")
    assert proc(client, iid, action="project", project_id=bob_pid, next_action="шаг").status_code == 404
    assert proc(client, iid, action="project", project_id="1", next_action="шаг").status_code == 400
    assert item(iid)["status"] == "inbox"


def test_dated(client, login):
    uid = login(client)
    iid = inbox(uid)
    when = int(time.time()) + 86400
    assert proc(client, iid, action="dated").status_code == 400
    assert proc(client, iid, action="dated", remind_at=-5).status_code == 400
    r = proc(client, iid, action="dated", remind_at=when)
    assert r.status_code == 200, r.text
    assert (item(iid)["status"], item(iid)["remind_at"]) == ("next", when)
    assert [i["id"] for i in client.get("/api/items?status=scheduled").json()] == [iid]


def test_next_with_and_without_context(client, login):
    uid = login(client)
    a, b = inbox(uid, "купить батарейки"), inbox(uid, "прочитать статью")
    assert proc(client, a, action="next", contexts=["@магазин"]).status_code == 200
    assert (item(a)["status"], item(a)["contexts"]) == ("next", ["магазин"])
    assert proc(client, b, action="next").status_code == 200
    assert (item(b)["status"], item(b)["contexts"]) == ("next", [])
    assert proc(client, inbox(uid), action="next", contexts=5).status_code == 400


def test_only_inbox_tasks_and_own(client, new_client, login):
    uid = login(client)
    iid = inbox(uid)
    assert proc(client, iid, action="bogus").status_code == 400
    assert proc(client, iid, action="someday").status_code == 200
    assert proc(client, iid, action="trash").status_code == 409  # уже разобрана (например, в другой вкладке)
    bob = new_client()
    login(bob, "bob@example.com")
    assert proc(bob, inbox(uid), action="trash").status_code == 404
    assert proc(client, 999999, action="trash").status_code == 404


def test_runs_count_processed_items_for_stats(client, new_client, login, monkeypatch):
    uid = login(client)
    ids = [inbox(uid, f"t{n}") for n in range(3)]
    run = proc(client, ids[0], action="trash").json()["run"]
    assert proc(client, ids[1], action="someday", run=run).json()["run"] == run
    bob = new_client()
    bob_uid = login(bob, "bob@example.com")
    bob_item = inbox(bob_uid)
    assert proc(bob, bob_item, action="trash", run=run).json()["run"] != run  # чужой заход — свой новый
    assert proc(client, ids[2], action="next", run="x").json()["run"] not in (run, None)
    assert [(r["user_id"], r["items"]) for r in A.rows("select * from process_runs order by id")] == \
        [(uid, 2), (bob_uid, 1), (uid, 1)]
    monkeypatch.setattr(A, "ADMIN_USER_IDS", {uid})
    assert client.get("/api/admin/stats").json()["process"] == {"runs": 3, "items": 4}


def test_process_view_is_an_app_page(client):
    for path in ("/process", "/en/process"):
        r = client.get(path)
        assert r.status_code == 200 and r.headers["x-robots-tag"] == "noindex"


def test_mcp_process_inbox_prompt(alice):
    r = rpc(alice["mcp"], alice["tok"], "prompts/list")
    assert r.status_code == 200, r.text
    assert [p["name"] for p in r.json()["result"]["prompts"]] == ["process_inbox"]
    r = rpc(alice["mcp"], alice["tok"], "prompts/get", {"name": "process_inbox"})
    text = r.json()["result"]["messages"][0]["content"]["text"]
    order = ["actionable", "2 minutes", "right person", "more than one step", "fixed date"]
    assert [text.index(w) for w in order] == sorted(text.index(w) for w in order)
    assert "Do not create a project without a next action" in text
    init = rpc(alice["mcp"], alice["tok"], "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                                          "clientInfo": {"name": "t", "version": "1"}})
    assert "process_inbox" in init.json()["result"]["instructions"]
