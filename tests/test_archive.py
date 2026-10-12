"""Архив проектов (SERBITO-602): два режима архива, восстановление, вопрос «Убрать в архив?» после последней задачи,
архив не виден в списках, выборе проекта и напоминаниях; бот и MCP."""
import asyncio
import time

import app as A
from conftest import bot_callback, bot_message, texts
from test_mcp import call, call_error


def project(uid, title):
    return A.row("select * from projects where user_id=%s and title=%s", (uid, title))


def setup_kitchen(uid):
    """#Кухня: две открытые задачи (Next с будущим напоминанием, Waiting с прошедшим) и одна выполненная."""
    now = int(time.time())
    nxt = A.capture(uid, "купить плитку #Кухня")
    wait = A.capture(uid, "смета от Марко #Кухня")
    done = A.capture(uid, "снять размеры #Кухня")
    A.item_patch(uid, wait["id"], {"status": "waiting", "remind_at": now - 3600})
    A.item_patch(uid, nxt["id"], {"remind_at": now + 86400})
    A.item_patch(uid, done["id"], {"status": "done"})
    return project(uid, "Кухня")["id"], nxt["id"], wait["id"], done["id"]


def status(iid):
    return A.row("select status, project_id, remind_at, archived_from from items where id=%s", (iid,))


def test_archive_with_tasks_hides_them_everywhere(client, login):
    uid = login(client)
    pid, nxt, wait, done = setup_kitchen(uid)
    A.capture(uid, "другое дело @дом")
    r = client.post(f"/api/projects/{pid}/archive", json={"mode": "all"})
    assert r.status_code == 200 and sorted(r.json()["items"]) == sorted([nxt, wait])
    assert (status(nxt)["status"], status(nxt)["archived_from"]) == ("archived", "next")
    assert status(done)["status"] == "done"  # история остаётся в проекте
    assert client.get("/api/projects").json() == []
    assert [i["title"] for i in client.get("/api/items?status=next").json()] == ["другое дело"]
    assert client.get("/api/items?status=waiting").json() == []
    assert client.get("/api/items?status=scheduled").json() == []
    assert client.get("/api/items?status=due").json() == []
    c = client.get("/api/counts").json()
    assert (c["projects"], c["archived_projects"], c["scheduled"]) == (0, 1, 0)
    arch = client.get("/api/archive").json()
    assert [(p["title"], p["archive_mode"]) for p in arch] == [("Кухня", "all")]
    assert {i["title"] for i in arch[0]["items"]} == {"купить плитку", "смета от Марко", "снять размеры"}
    assert client.post(f"/api/projects/{pid}/archive", json={"mode": "all"}).status_code == 409


def test_archive_only_project_keeps_tasks_active(client, login):
    uid = login(client)
    pid, nxt, wait, done = setup_kitchen(uid)
    r = client.post(f"/api/projects/{pid}/archive", json={"mode": "project"})
    assert sorted(r.json()["items"]) == sorted([nxt, wait])
    assert (status(nxt)["status"], status(nxt)["project_id"]) == ("next", None)
    assert (status(wait)["status"], status(wait)["project_id"]) == ("waiting", None)
    assert status(done)["project_id"] == pid
    assert client.get("/api/projects").json() == []
    assert client.get("/api/archive").json()[0]["archive_mode"] == "project"
    assert client.post(f"/api/projects/{pid}/archive", json={"mode": "x"}).status_code == 400


def test_restore_returns_tasks_and_only_future_reminders(client, login):
    uid = login(client)
    pid, nxt, wait, done = setup_kitchen(uid)
    other = A.capture(uid, "уже в Next @дом")["id"]
    future = status(nxt)["remind_at"]
    client.post(f"/api/projects/{pid}/archive", json={"mode": "all"})
    r = client.post(f"/api/projects/{pid}/restore", json={})
    assert r.json() == {"ok": True, "restored": 2, "dropped_reminders": 1}
    assert (status(nxt)["status"], status(nxt)["remind_at"], status(nxt)["archived_from"]) == ("next", future, None)
    assert (status(wait)["status"], status(wait)["remind_at"]) == ("waiting", None)  # прошедшее снято
    # вернувшаяся задача встаёт в конец своего списка
    assert [i["id"] for i in client.get("/api/items?status=next").json()] == [other, nxt]
    assert [p["title"] for p in client.get("/api/projects").json()] == ["Кухня"]
    assert client.get("/api/archive").json() == []
    assert client.post(f"/api/projects/{pid}/restore", json={}).status_code == 409


def test_archived_reminders_are_not_sent(tg):
    uid = A.tg_user(777, "Tom")["id"]
    it = A.capture(uid, "позвонить #Дача")
    A.item_patch(uid, it["id"], {"remind_at": int(time.time()) - 5})
    A.project_archive(uid, project(uid, "Дача")["id"], "all")
    assert asyncio.run(A.send_due_reminders()) == 0


def test_foreign_project_cannot_be_archived(new_client, login):
    alice, bob = new_client(), new_client()
    uid = login(alice, "alice@example.com")
    login(bob, "bob@example.com")
    pid = A.capture(uid, "x #Тайна")["project_id"]
    assert bob.post(f"/api/projects/{pid}/archive", json={"mode": "all"}).status_code == 404
    alice.post(f"/api/projects/{pid}/archive", json={"mode": "all"})
    assert bob.post(f"/api/projects/{pid}/restore", json={}).status_code == 404
    assert bob.get("/api/archive").json() == []


def test_old_completed_projects_move_to_archive(client, login):
    """Прежняя кнопка «Завершить» (status done) — это архив без задач; PATCH status по-прежнему работает."""
    uid = login(client)
    pid = A.capture(uid, "x #Старый")["project_id"]
    A.run("update projects set status='done' where id=%s", (pid,))
    A.run(A.SCHEMA)
    p = project(uid, "Старый")
    assert (p["status"], p["archive_mode"]) == ("archived", "project") and p["archived_at"]
    client.patch(f"/api/projects/{pid}", json={"status": "active"})
    assert project(uid, "Старый")["status"] == "active"
    client.patch(f"/api/projects/{pid}", json={"status": "done"})
    assert project(uid, "Старый")["status"] == "archived"
    assert client.get("/api/items?status=next").json()[0]["project_id"] == pid  # задачи не тронуты


# ── вопрос «Убрать в архив?» ──

def test_offer_when_last_task_is_done_or_trashed(client, login):
    uid = login(client)
    a = A.capture(uid, "первое #Отпуск")
    b = A.capture(uid, "второе #Отпуск")
    A.capture(uid, "без проекта")
    pid = a["project_id"]
    r = client.patch(f"/api/items/{a['id']}", json={"status": "done"}).json()
    assert "archive_offer" not in r  # в проекте ещё есть дело
    r = client.patch(f"/api/items/{b['id']}", json={"status": "trash"}).json()
    assert r["archive_offer"] == {"id": pid, "title": "Отпуск"}
    assert project(uid, "Отпуск")["status"] == "active"  # молча не архивируем
    # повторное сохранение закрытой задачи не спрашивает снова
    assert "archive_offer" not in client.patch(f"/api/items/{b['id']}", json={"status": "trash"}).json()
    c = A.capture(uid, "третье #Отпуск")
    assert client.delete(f"/api/items/{c['id']}").json() == {"ok": True, "archive_offer": {"id": pid, "title": "Отпуск"}}
    loose = A.capture(uid, "ничьё")
    assert "archive_offer" not in client.patch(f"/api/items/{loose['id']}", json={"status": "done"}).json()


def test_someday_task_keeps_project_alive(client, login):
    uid = login(client)
    a = A.capture(uid, "сделать #Сад")
    s = A.capture(uid, "идея #Сад")
    A.item_patch(uid, s["id"], {"status": "someday"})
    assert "archive_offer" not in client.patch(f"/api/items/{a['id']}", json={"status": "done"}).json()
    p = client.get("/api/projects").json()[0]
    assert (p["active_count"], p["next_count"]) == (1, 0)


# ── захват и выбор проекта ──

def test_capture_into_archived_project_restores_it(client, login):
    uid = login(client)
    pid, nxt, wait, done = setup_kitchen(uid)
    A.project_archive(uid, pid, "all")
    assert client.get("/api/projects").json() == []  # выбор проекта в захвате (#…) берёт этот список
    it = client.post("/api/capture", json={"text": "заказать фартук #кухня"}).json()
    assert it["project_id"] == pid and it["status"] == "next"
    assert project(uid, "Кухня")["status"] == "active" and status(nxt)["status"] == "next"
    assert len(A.rows("select 1 from projects where user_id=%s", (uid,))) == 1  # без дубля


def test_archived_task_card_saves_without_error(client, login):
    uid = login(client)
    pid, nxt, *_ = setup_kitchen(uid)
    A.project_archive(uid, pid, "all")
    r = client.patch(f"/api/items/{nxt}", json={"title": "купить плитку", "status": "archived", "notes": "серая",
                                               "project_id": pid})
    assert r.status_code == 200 and (r.json()["status"], r.json()["notes"]) == ("archived", "серая")
    assert client.patch(f"/api/items/{nxt}", json={"status": "archived"}).status_code == 200
    other = A.capture(uid, "x")
    assert client.patch(f"/api/items/{other['id']}", json={"status": "archived"}).status_code == 400


# ── бот ──

def test_bot_offers_archive_after_last_task(tg):
    bot_message("купить билеты #Отпуск")
    it = A.row("select * from items")
    bot_message(f"/done {it['num']}")
    method, p = tg[-1]
    assert p["text"] == "В проекте #Отпуск больше нет задач. Убрать его в архив?"
    pid = it["project_id"]
    assert [b["callback_data"] for b in p["reply_markup"]["inline_keyboard"][0]] == [f"arch:{pid}", f"noarch:{pid}"]
    bot_message(f"/done {it['num']}")  # уже выполнена — второй раз не спрашиваем
    assert "архив" not in texts(tg)[-1]

    bot_callback(f"noarch:{pid}")
    assert A.row("select status from projects")["status"] == "active"
    assert texts(tg)[-1] == "Хорошо, проект #Отпуск остаётся в Projects"
    bot_callback(f"arch:{pid}")
    assert A.row("select status, archive_mode from projects") == {"status": "archived", "archive_mode": "all"}
    assert texts(tg)[-1].startswith("🗄 Проект #Отпуск в архиве")
    bot_callback(f"arch:{pid}")  # уже в архиве
    assert ("answerCallbackQuery", {"callback_query_id": "cb", "text": "Не найдено"}) in tg


def test_bot_done_button_offers_archive_in_english(tg):
    bot_message("book tickets #Trip", lang="en")
    it = A.row("select * from items")
    bot_callback(f"done:{it['id']}", lang="en")
    assert texts(tg)[-1] == "Project #Trip has no tasks left. Archive it?"


def test_bot_archive_buttons_ignore_foreign_projects(tg):
    bot_message("моё #Секрет", tg_id=777)
    pid = A.row("select id from projects")["id"]
    A.tg_user(888, "Eve")
    bot_callback(f"arch:{pid}", tg_id=888)
    assert A.row("select status from projects")["status"] == "active"
    assert ("answerCallbackQuery", {"callback_query_id": "cb", "text": "Не найдено"}) in tg


# ── MCP ──

def test_mcp_archive_and_restore(alice):
    mcp, tok, uid = alice["mcp"], alice["tok"], alice["uid"]
    A.capture(uid, "fix the roof #House")
    A.capture(uid, "plant roses #Garden")
    A.capture(uid, "order seeds #Garden")
    r = call(mcp, tok, "archive_project", name="garden")
    assert r == {"name": "Garden", "archived": True, "tasks": "archive", "tasks_archived": 2}
    assert [p["name"] for p in call(mcp, tok, "list_projects")["projects"]] == ["House"]
    listed = call(mcp, tok, "list_projects", include_archived=True)["projects"]
    assert [(p["name"], p["archived"]) for p in listed] == [("House", False), ("Garden", True)]
    assert listed[1]["archived_tasks"] == 2
    assert [t["title"] for t in call(mcp, tok, "list_tasks", list="next")["tasks"]] == ["fix the roof"]
    assert call(mcp, tok, "list_tasks", list="archived")["total"] == 2
    assert call(mcp, tok, "list_tasks", list="all")["total"] == 1
    assert "already archived" in call_error(mcp, tok, "archive_project", name="Garden")
    assert "No project named" in call_error(mcp, tok, "archive_project", name="Nope")

    r = call(mcp, tok, "restore_project", name="Garden")
    assert r == {"name": "Garden", "archived": False, "restored_tasks": 2, "dropped_reminders": 0}
    assert "not archived" in call_error(mcp, tok, "restore_project", name="Garden")

    r = call(mcp, tok, "archive_project", name="House", tasks="keep")
    assert r["tasks_kept_without_project"] == 1
    roof = A.item_by_num(uid, 1)
    assert (roof["status"], roof["project_id"]) == ("next", None)


def test_mcp_complete_last_task_suggests_archive(alice):
    mcp, tok, uid = alice["mcp"], alice["tok"], alice["uid"]
    A.capture(uid, "pack bags #Trip")
    A.capture(uid, "book hotel #Trip")
    assert "project_now_empty" not in call(mcp, tok, "complete_task", number=1)
    r = call(mcp, tok, "move_task", number=2, list="trash")
    assert r["project_now_empty"] == "Trip" and "archive_project" in r["hint"]
    review = call(mcp, tok, "weekly_review")
    assert review["empty_projects"] == ["Trip"]
    call(mcp, tok, "archive_project", name="Trip")
    assert call(mcp, tok, "weekly_review")["empty_projects"] == []


def test_mcp_move_task_to_archived_project_restores_it(alice):
    mcp, tok, uid = alice["mcp"], alice["tok"], alice["uid"]
    A.capture(uid, "old #Blog")
    call(mcp, tok, "archive_project", name="Blog")
    A.capture(uid, "write a post")
    t = call(mcp, tok, "move_task", number=2, project="Blog")
    assert (t["project"], t["list"]) == ("Blog", "next")
    assert A.row("select status from projects")["status"] == "active"
    assert A.item_by_num(uid, 1)["status"] == "next"
