"""Веб-API: задачи, проекты, контексты, счётчики, изоляция пользователей."""
import time

import app as A


def test_requires_auth(client):
    for path in ("/api/counts", "/api/items", "/api/projects", "/api/contexts", "/api/me"):
        assert client.get(path).status_code == 401, path
    assert client.post("/api/capture", json={"text": "x"}).status_code == 401


def test_config_reports_session(client, login):
    assert client.get("/api/config").json()["user"] is False
    login(client)
    assert client.get("/api/config").json()["user"] is True


def test_index_served(client):
    r = client.get("/")
    assert r.status_code == 200 and "<title>GTD</title>" in r.text


def test_capture_and_list(client, login):
    login(client)
    assert client.post("/api/capture", json={"text": "  "}).status_code == 400
    a = client.post("/api/capture", json={"text": "мысль"}).json()
    b = client.post("/api/capture", json={"text": "звонок @телефон"}).json()
    assert [i["id"] for i in client.get("/api/items?status=inbox").json()] == [a["id"]]
    assert [i["id"] for i in client.get("/api/items?status=next").json()] == [b["id"]]
    assert len(client.get("/api/items?status=all").json()) == 2


def test_patch_item(client, login):
    login(client)
    iid = client.post("/api/capture", json={"text": "задача"}).json()["id"]

    done = client.patch(f"/api/items/{iid}", json={"status": "done"}).json()
    assert done["status"] == "done" and done["completed_at"]
    back = client.patch(f"/api/items/{iid}", json={"status": "next"}).json()
    assert back["completed_at"] is None
    assert client.patch(f"/api/items/{iid}", json={"status": "weird"}).status_code == 400

    it = client.patch(f"/api/items/{iid}", json={"title": "новое", "notes": "n", "context": "@Дом"}).json()
    assert (it["title"], it["notes"], it["context"]) == ("новое", "n", "дом")
    assert client.patch(f"/api/items/{iid}", json={"context": ""}).json()["context"] is None

    A.run("update items set reminded=1 where id=%s", (iid,))
    it = client.patch(f"/api/items/{iid}", json={"remind_at": int(time.time()) + 60}).json()
    assert it["remind_at"] and it["reminded"] == 0

    pid = client.post("/api/projects", json={"title": "П"}).json()["id"]
    assert client.patch(f"/api/items/{iid}", json={"project_id": pid}).json()["project"] == "П"


def test_delete_item(client, login):
    login(client)
    iid = client.post("/api/capture", json={"text": "удалить"}).json()["id"]
    assert client.delete(f"/api/items/{iid}").json() == {"ok": True}
    assert client.get("/api/items?status=all").json() == []


def test_scheduled_view_ordered_and_skips_done(client, login):
    uid = login(client)
    late = A.capture(uid, "позже 2026-12-01")
    soon = A.capture(uid, "раньше 2026-11-01")
    done = A.capture(uid, "сделано 2026-10-01")
    A.run("update items set status='done' where id=%s", (done["id"],))
    assert [i["id"] for i in client.get("/api/items?status=scheduled").json()] == [soon["id"], late["id"]]


def test_counts(client, login):
    uid = login(client)
    A.capture(uid, "a")
    A.capture(uid, "b @x")
    A.capture(uid, "c завтра #Проект")
    c = client.get("/api/counts").json()
    assert (c["inbox"], c["next"], c["scheduled"], c["projects"]) == (1, 2, 1, 1)


def test_projects(client, login):
    uid = login(client)
    assert client.post("/api/projects", json={"title": " "}).status_code == 400
    pid = client.post("/api/projects", json={"title": "Ремонт"}).json()["id"]
    assert client.post("/api/projects", json={"title": "ремонт"}).json()["id"] == pid  # без дублей
    A.capture(uid, "купить краску #Ремонт")
    A.capture(uid, "идея про плитку #Ремонт")
    A.run("update items set status='someday' where title like 'идея%%'")
    p = client.get("/api/projects").json()[0]
    assert (p["title"], p["open_count"], p["next_count"]) == ("Ремонт", 1, 1)
    items = client.get(f"/api/items?status=all&project_id={pid}").json()
    assert len(items) == 2
    client.patch(f"/api/projects/{pid}", json={"status": "done"})
    assert client.get("/api/projects").json() == []


def test_contexts_by_frequency_without_trash(client, login):
    uid = login(client)
    for t in ("a @дом", "b @работа", "c @работа", "d @старое"):
        A.capture(uid, t)
    A.run("update items set status='trash' where context='старое'")
    A.capture(A.email_user("bob@example.com"), "чужое @секрет")
    assert client.get("/api/contexts").json() == ["работа", "дом"]


def test_users_are_isolated(new_client, login):
    alice, bob = new_client(), new_client()
    login(alice, "alice@example.com")
    login(bob, "bob@example.com")
    iid = alice.post("/api/capture", json={"text": "секрет"}).json()["id"]
    assert bob.get("/api/items?status=all").json() == []
    assert bob.patch(f"/api/items/{iid}", json={"title": "взлом"}).status_code == 404
    bob.delete(f"/api/items/{iid}")
    assert alice.get("/api/items?status=all").json()[0]["title"] == "секрет"


def test_logout(client, login):
    login(client)
    assert client.post("/api/logout").json() == {"ok": True}
    assert client.get("/api/counts").status_code == 401


def test_dev_login_creates_user(client):
    r = client.get("/dev-login", follow_redirects=False)
    assert r.status_code == 303 and client.get("/api/me").status_code == 200


def test_dev_login_disabled_in_prod(client, monkeypatch):
    monkeypatch.setattr(A, "DEV", False)
    assert client.get("/dev-login").status_code == 404


# ── Проекты: переименование и удаление ──

def test_rename_project(client, login):
    uid = login(client)
    pid = client.post("/api/projects", json={"title": "Ремонт"}).json()["id"]
    other = client.post("/api/projects", json={"title": "Отпуск"}).json()["id"]
    assert client.patch(f"/api/projects/{pid}", json={"title": "  Ремонт кухни "}).json()["title"] == "Ремонт кухни"
    assert client.patch(f"/api/projects/{pid}", json={"title": " "}).status_code == 400
    r = client.patch(f"/api/projects/{pid}", json={"title": "отпуск"})
    assert r.status_code == 409 and "уже есть" in r.json()["detail"]
    assert client.patch(f"/api/projects/{pid}", json={"title": "ремонт КУХНИ"}).status_code == 200  # свой — можно
    assert client.patch(f"/api/projects/{other}", json={"status": "weird"}).status_code == 400
    A.capture(uid, "плитка #ремонт_кухни")  # захват находит переименованный проект
    assert A.row("select count(*) n from projects where user_id=%s", (uid,))["n"] == 2


def test_delete_project_keep_items(client, login):
    uid = login(client)
    a = A.capture(uid, "купить краску #Ремонт")
    A.capture(uid, "без проекта")
    pid = a["project_id"]
    assert client.get("/api/projects").json()[0]["total_count"] == 1
    r = client.delete(f"/api/projects/{pid}").json()  # по умолчанию задачи остаются
    assert r == {"ok": True, "items": "keep", "affected": 1}
    it = client.get("/api/items?status=all").json()
    assert len(it) == 2 and all(i["project_id"] is None for i in it)
    assert client.get("/api/projects").json() == []


def test_delete_project_with_items(client, login):
    uid = login(client)
    for t in ("a #Ремонт", "b #Ремонт", "c #Отпуск"):
        A.capture(uid, t)
    pid = A.row("select id from projects where title='Ремонт'")["id"]
    assert client.delete(f"/api/projects/{pid}?items=delete").json()["affected"] == 2
    assert [i["title"] for i in client.get("/api/items?status=all").json()] == ["c"]
    assert [p["title"] for p in client.get("/api/projects").json()] == ["Отпуск"]
    assert client.delete(f"/api/projects/{pid}").status_code == 404
    assert client.delete(f"/api/projects/{pid}?items=all").status_code == 400


def test_foreign_project_untouchable(new_client, login):
    alice, bob = new_client(), new_client()
    uid = login(alice, "alice@example.com")
    login(bob, "bob@example.com")
    pid = A.capture(uid, "секрет #Личное")["project_id"]
    assert bob.patch(f"/api/projects/{pid}", json={"title": "взлом"}).status_code == 404
    assert bob.delete(f"/api/projects/{pid}?items=delete").status_code == 404
    assert A.row("select title from projects")["title"] == "Личное" and A.row("select count(*) n from items")["n"] == 1
