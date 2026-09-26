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


def test_index_served(client, login):
    r = client.get("/")  # гостю — лендинг с SEO-заголовком и то же приложение
    assert r.status_code == 200 and "<title>GTD онлайн бесплатно" in r.text and "function load()" in r.text
    login(client)
    r = client.get("/")  # вошёл — приложение, как раньше
    assert r.status_code == 200 and "<title>GTD</title>" in r.text and "function load()" in r.text


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


# ── Номера задач и ссылки /i/N ──

def test_numbers_are_per_user_and_never_reused(client, login):
    alice = login(client)
    bob = A.email_user("bob@example.com")
    a1, a2 = A.capture(alice, "a1"), A.capture(alice, "a2")
    b1 = A.capture(bob, "b1")
    assert (a1["num"], a2["num"], b1["num"]) == (1, 2, 1)  # у каждого с единицы
    client.delete(f"/api/items/{a2['id']}")
    assert A.capture(alice, "a3")["num"] == 3  # номер удалённой #2 не переиспользуется


def test_item_link_by_number(new_client, login):
    alice, bob = new_client(), new_client()
    uid = login(alice, "alice@example.com")
    login(bob, "bob@example.com")
    it = A.capture(uid, "секрет")
    assert alice.get(f"/api/items/n/{it['num']}").json()["title"] == "секрет"
    assert bob.get(f"/api/items/n/{it['num']}").status_code == 404  # у Боба своя #1 не существует
    assert new_client().get(f"/api/items/n/{it['num']}").status_code == 401
    page = new_client().get(f"/i/{it['num']}")  # страницу отдаём всем, данные — только владельцу
    assert page.status_code == 200 and "<title>GTD</title>" in page.text


def test_merge_renumbers_moved_tasks(new_client, login, mail):
    c, carol = new_client(), new_client()
    alice = login(c, "alice@example.com")
    for t in ("a1", "a2"):
        c.post("/api/capture", json={"text": t})
    login(carol, "carol@example.com")
    for t in ("c1", "c2"):
        carol.post("/api/capture", json={"text": t})
    c.post("/api/auth/email/start", json={"email": "carol@example.com"})
    code = __import__("conftest").last_code(mail)
    token = c.post("/api/auth/email/verify", json={"email": "carol@example.com", "code": code, "link": True}).json()["merge"]
    assert c.post("/api/auth/merge", json={"token": token}).json() == {"ok": True}
    nums = {i["title"]: i["num"] for i in c.get("/api/items?status=all").json()}
    assert nums == {"a1": 1, "a2": 2, "c1": 3, "c2": 4}
    assert A.capture(alice, "next")["num"] == 5


def test_dev_version_only_in_dev(client, monkeypatch):
    v = client.get("/api/dev/version").json()["v"]
    assert v and client.get("/api/dev/version").json()["v"] == v  # стабильна, пока ничего не менялось
    monkeypatch.setattr(A, "DEV", False)
    assert client.get("/api/dev/version").status_code == 404


def test_undo_seconds_setting(client, login):
    login(client)
    assert client.get("/api/me").json()["undo_seconds"] == 30  # по умолчанию
    for n in (5, 10, 30):
        assert client.patch("/api/me", json={"undo_seconds": n}).json()["undo_seconds"] == n
    for bad in (0, 15, "30", None):
        assert client.patch("/api/me", json={"undo_seconds": bad}).status_code == 400
    assert client.get("/api/me").json()["undo_seconds"] == 30
    assert client.patch("/api/me", json={}).status_code == 200  # без полей — ничего не меняется


def test_google_analytics_only_on_prod_domain(client, monkeypatch):
    assert "googletagmanager" not in client.get("/").text  # ID не задан — GA нет
    monkeypatch.setattr(A, "GA_ID", "G-TEST123")
    prod = client.get("/", headers={"host": "gtd.serbito.rs"}).text
    assert "gtag/js?id=G-TEST123" in prod and "send_page_view: false" in prod and "<!--GA-->" not in prod
    for host in ("localhost:8000", "gtd-488744139718.europe-west1.run.app"):
        page = client.get("/", headers={"host": host}).text
        assert "googletagmanager" not in page and "<!--GA-->" not in page, host
    assert "gtag/js?id=G-TEST123" in client.get("/i/5", headers={"host": "gtd.serbito.rs"}).text


# ── чек-лист первого запуска: пункты отмечаются сами по данным, скрытие хранится на сервере ──

def checklist(c):
    return c.get("/api/counts").json()["onboarding"]


def test_checklist_ticks_from_data(client, login):
    uid = login(client)
    assert checklist(client) == {"capture": False, "process": False, "telegram": False, "hidden": False}
    ids = [client.post("/api/capture", json={"text": t}).json()["id"] for t in ("раз", "два")]
    assert checklist(client)["capture"] is False
    client.post("/api/capture", json={"text": "три"})
    assert checklist(client)["capture"] is True
    client.delete(f"/api/items/{ids[0]}")  # «записал 3» — за всё время: удаление галочку не снимает
    assert checklist(client)["capture"] is True and checklist(client)["process"] is False
    client.patch(f"/api/items/{ids[1]}", json={"status": "someday"})  # разобрал задачу из Inbox
    assert checklist(client)["process"] is True
    A.run("update users set tg_id=777 where id=%s", (uid,))
    assert checklist(client) == {"capture": True, "process": True, "telegram": True, "hidden": True}
    # всё выполнено — карточка скрыта навсегда, даже если пункт потом «откатится»
    client.patch(f"/api/items/{ids[1]}", json={"status": "inbox"})
    assert checklist(client) == {"capture": True, "process": False, "telegram": True, "hidden": True}


def test_checklist_process_rule(client, login):
    login(client)
    client.post("/api/capture", json={"text": "сразу в дело @дом"})  # с @контекстом — мимо Inbox, в Next
    assert checklist(client)["process"] is True
    A.run("update items set status='trash'")  # выбросить из Inbox — тоже разобрать
    assert checklist(client)["process"] is True
    A.run("update items set status='inbox'")
    assert checklist(client)["process"] is False


def test_checklist_telegram_and_dev_user(client):
    client.get("/dev-login")  # dev-пользователь заводится с tg_id=0 — это не Telegram
    assert checklist(client)["telegram"] is False


def test_checklist_dismiss_persists(new_client, login):
    laptop, phone = new_client(), new_client()
    login(laptop)
    login(phone)
    assert client_patch_ok(laptop, {"checklist_hidden": True})
    assert checklist(phone)["hidden"] is True  # закрыл на одном устройстве — не вернётся на другом
    for bad in ("yes", 1, None):
        assert laptop.patch("/api/me", json={"checklist_hidden": bad}).status_code == 400
    assert checklist(laptop)["hidden"] is True


def client_patch_ok(c, body):
    return c.patch("/api/me", json=body).status_code == 200


def test_existing_user_who_did_everything_never_sees_checklist(client, login):
    uid = login(client)
    for t in ("a", "b @x", "c"):
        A.capture(uid, t)
    A.run("update users set tg_id=777 where id=%s", (uid,))
    assert checklist(client)["hidden"] is True  # с первого же запроса


def test_checklist_is_per_user(new_client, login):
    alice, bob = new_client(), new_client()
    a = login(alice, "alice@example.com")
    login(bob, "bob@example.com")
    for t in ("раз", "два", "три @дом"):
        alice.post("/api/capture", json={"text": t})
    A.run("update users set tg_id=777 where id=%s", (a,))
    alice.patch("/api/me", json={"checklist_hidden": True})
    assert checklist(alice)["hidden"] is True
    assert checklist(bob) == {"capture": False, "process": False, "telegram": False, "hidden": False}
    assert bob.patch("/api/me", json={"checklist_hidden": False}).status_code == 200
    assert checklist(alice)["hidden"] is True  # свой флаг Боб меняет только у себя


def test_index_has_checklist_and_empty_states(client):
    page = client.get("/").text
    for s in ("onboarding_step", "Запиши 3 мысли", "Разбери Inbox", "Подключи Telegram-бота", "checklist_hidden"):
        assert s in page, s
