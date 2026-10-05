"""Веб-API: задачи, проекты, контексты, счётчики, изоляция пользователей."""
import re
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

    # Прежнее поле context (одна строка) по-прежнему работает (SERBITO-423)
    it = client.patch(f"/api/items/{iid}", json={"title": "новое", "notes": "n", "context": "@Дом"}).json()
    assert (it["title"], it["notes"], it["contexts"]) == ("новое", "n", ["дом"])
    assert client.patch(f"/api/items/{iid}", json={"context": ""}).json()["contexts"] == []
    assert client.patch(f"/api/items/{iid}", json={"context": None}).json()["contexts"] == []

    A.run("update items set reminded=1 where id=%s", (iid,))
    it = client.patch(f"/api/items/{iid}", json={"remind_at": int(time.time()) + 60}).json()
    assert it["remind_at"] and it["reminded"] == 0

    pid = client.post("/api/projects", json={"title": "П"}).json()["id"]
    assert client.patch(f"/api/items/{iid}", json={"project_id": pid}).json()["project"] == "П"


def card_save(client, iid, **fields):
    """Сохранение карточки: SPA шлёт все поля формы разом, как в openEdit."""
    body = {"title": "", "notes": "", "status": "inbox", "project_id": None, "contexts": [], "remind_at": None, **fields}
    r = client.patch(f"/api/items/{iid}", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def test_card_title_parsed_like_capture(client, login):
    """SERBITO-298: #проект, @контекст и дата в заголовке карточки — как в поле захвата."""
    uid = login(client)
    iid = client.post("/api/capture", json={"text": "черновик"}).json()["id"]
    it = card_save(client, iid, title="позвонить в банк завтра в 10:00 @Телефон #Новый_Проект")
    same = A.capture(uid, "позвонить в банк завтра в 10:00 @Телефон #Новый_Проект")
    assert (it["title"], it["contexts"], it["project"]) == ("позвонить в банк", ["телефон"], "Новый Проект")
    assert it["remind_at"] == same["remind_at"] and it["reminded"] == 0
    assert it["project_id"] == same["project_id"]  # проект создан один раз и переиспользован захватом
    assert it["status"] == "inbox"  # список — из поля «Список», токены его не меняют
    assert A.row("select count(*) n from projects where user_id=%s", (uid,))["n"] == 1


def test_card_title_token_overrides_fields(client, login):
    login(client)
    a = client.post("/api/projects", json={"title": "Дом"}).json()["id"]
    b = client.post("/api/projects", json={"title": "Клиент X"}).json()["id"]
    iid = client.post("/api/capture", json={"text": "задача"}).json()["id"]
    it = card_save(client, iid, title="задача #клиент_x @Работа @комп", project_id=a, contexts=["дом"])
    assert (it["title"], it["project_id"], it["contexts"]) == ("задача", b, ["работа", "комп"])


def test_card_title_without_tokens_keeps_fields(client, login):
    login(client)
    pid = client.post("/api/projects", json={"title": "Дом"}).json()["id"]
    iid = client.post("/api/capture", json={"text": "задача"}).json()["id"]
    when = int(time.time()) + 3600
    it = card_save(client, iid, title="новое название", project_id=pid, contexts=["@Дача", "дом"], remind_at=when)
    assert (it["title"], it["project_id"], it["contexts"], it["remind_at"]) == \
        ("новое название", pid, ["дача", "дом"], when)
    # Только #проект в заголовке: контексты и напоминание из полей остаются
    it = card_save(client, iid, title="новое название #Дом", contexts=["дача", "дом"], remind_at=when)
    assert (it["title"], it["project_id"], it["contexts"], it["remind_at"]) == \
        ("новое название", pid, ["дача", "дом"], when)


def test_card_unchanged_title_not_reparsed(client, login):
    """Заголовок не меняли — не разбираем: снятое в карточке напоминание не возвращается из «завтра»,
    выбранный контекст не перекрывается токеном, оставшимся в заголовке из одних токенов."""
    login(client)
    it = client.post("/api/capture", json={"text": "завтра"}).json()
    assert it["title"] == "завтра" and it["remind_at"]
    assert card_save(client, it["id"], title="завтра")["remind_at"] is None
    it = client.post("/api/capture", json={"text": "@дом"}).json()
    assert card_save(client, it["id"], title="@дом", contexts=["работа"])["contexts"] == ["работа"]


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
    A.run("update items set status='trash' where 'старое' = any(contexts)")
    A.capture(A.email_user("bob@example.com"), "чужое @секрет")
    assert client.get("/api/contexts").json() == ["работа", "дом"]


def test_task_with_several_contexts(client, login):
    """SERBITO-423: задача с двумя контекстами считается в каждом из них; PATCH contexts заменяет весь список."""
    uid = login(client)
    two = A.capture(uid, "оплатить счёт @телефон @комп")
    A.capture(uid, "позвонить @телефон")
    assert client.get("/api/contexts").json() == ["телефон", "комп"]
    it = client.patch(f"/api/items/{two['id']}", json={"contexts": ["@Комп", "дом", "комп"]}).json()
    assert it["contexts"] == ["комп", "дом"]
    assert client.patch(f"/api/items/{two['id']}", json={"contexts": "@дача @лес"}).json()["contexts"] == ["дача", "лес"]
    assert client.patch(f"/api/items/{two['id']}", json={"contexts": []}).json()["contexts"] == []
    for bad in (5, ["a", 1], {"a": "b"}):
        assert client.patch(f"/api/items/{two['id']}", json={"contexts": bad}).status_code == 400
    assert client.get("/api/contexts").json() == ["телефон"]


def test_old_single_context_migrates_once():
    """SERBITO-423: старая колонка context при старте переезжает в contexts списком из одного; повторный старт
    не возвращает контекст, который потом сняли."""
    uid = A.email_user("alice@example.com")
    iid = A.capture(uid, "старая задача")["id"]
    A.run("update items set context='дом', contexts='{}' where id=%s", (iid,))  # так её оставила прежняя ревизия
    A.run(A.SCHEMA)
    assert A.row("select context, contexts from items where id=%s", (iid,)) == {"context": None, "contexts": ["дом"]}
    A.run("update items set contexts='{}' where id=%s", (iid,))
    A.run(A.SCHEMA)
    assert A.row("select contexts from items where id=%s", (iid,))["contexts"] == []


def test_users_are_isolated(new_client, login):
    alice, bob = new_client(), new_client()
    login(alice, "alice@example.com")
    login(bob, "bob@example.com")
    iid = alice.post("/api/capture", json={"text": "секрет"}).json()["id"]
    assert bob.get("/api/items?status=all").json() == []
    assert bob.patch(f"/api/items/{iid}", json={"title": "взлом"}).status_code == 404
    bob.delete(f"/api/items/{iid}")
    assert alice.get("/api/items?status=all").json()[0]["title"] == "секрет"


def test_dev_login_creates_user(client):
    r = client.get("/dev-login", follow_redirects=False)
    assert r.status_code == 303 and client.get("/api/me").status_code == 200


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



def test_cannot_attach_foreign_project(new_client, login):
    alice, bob = new_client(), new_client()
    uid = login(alice, "alice@example.com")
    login(bob, "bob@example.com")
    pid = A.capture(uid, "секрет #Личное")["project_id"]
    iid = bob.post("/api/capture", json={"text": "моё"}).json()["id"]
    assert bob.patch(f"/api/items/{iid}", json={"project_id": pid}).status_code == 404
    it = bob.get("/api/items?status=all").json()[0]
    assert it["project_id"] is None and it["project"] is None
    A.run("update items set project_id=%s where id=%s", (pid, iid))  # даже если id попал в базу — названия не видно
    assert bob.get("/api/items?status=all").json()[0]["project"] is None

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
    for t in ("c1 @дом @комп", "c2"):
        carol.post("/api/capture", json={"text": t})
    c.post("/api/auth/email/start", json={"email": "carol@example.com"})
    code = __import__("conftest").last_code(mail)
    token = c.post("/api/auth/email/verify", json={"email": "carol@example.com", "code": code, "link": True}).json()["merge"]
    assert c.post("/api/auth/merge", json={"token": token}).json() == {"ok": True}
    nums = {i["title"]: i["num"] for i in c.get("/api/items?status=all").json()}
    assert nums == {"a1": 1, "a2": 2, "c1": 3, "c2": 4}
    assert A.capture(alice, "next")["num"] == 5
    # Контексты переезжают вместе с задачей (SERBITO-423)
    assert A.item_by_num(alice, 3)["contexts"] == ["дом", "комп"]
    assert c.get("/api/contexts").json() == ["дом", "комп"]


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


def test_dnd_tip_seen_is_per_user_and_server_side(new_client, login):
    """Подсказка о перестановке (SERBITO-390): закрыл на одном устройстве — не вернётся на другом; флаг свой у каждого."""
    laptop, phone, bob = new_client(), new_client(), new_client()
    login(laptop)
    login(phone)
    login(bob, "bob@example.com")
    assert phone.get("/api/me").json()["dnd_tip_seen"] is False
    assert laptop.patch("/api/me", json={"dnd_tip_seen": True}).json()["dnd_tip_seen"] is True
    assert phone.get("/api/me").json()["dnd_tip_seen"] is True
    assert bob.get("/api/me").json()["dnd_tip_seen"] is False
    for bad in ("yes", 1, None):
        assert laptop.patch("/api/me", json={"dnd_tip_seen": bad}).status_code == 400
    assert laptop.get("/api/me").json()["dnd_tip_seen"] is True


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
    page = client.get(re.search(r'src="(/app\.[0-9a-f]+\.js)"', page).group(1)).text  # код SPA — в бандле (SERBITO-444)
    for s in ("onboarding_step", "Запиши 3 мысли", "Разбери Inbox", "Подключи Telegram-бота", "checklist_hidden"):
        assert s in page, s


def test_capture_into_project(client, login, new_client):
    """SERBITO-354 (item 3): поле захвата на странице проекта кладёт задачу в этот проект (как #проект в тексте —
    сразу в Next). Явный #другой в тексте важнее страницы. Чужой или несуществующий проект — 404, задача не создана."""
    login(client)
    pid = client.post("/api/projects", json={"title": "Ремонт кухни"}).json()["id"]
    it = client.post("/api/capture", json={"text": "выбрать плитку завтра в 10:00", "project_id": pid}).json()
    assert (it["project_id"], it["project"], it["status"], it["title"]) == (pid, "Ремонт кухни", "next", "выбрать плитку")
    assert it["remind_at"]
    other = client.post("/api/capture", json={"text": "купить краску #Дача", "project_id": pid}).json()
    assert other["project"] == "Дача" and other["project_id"] != pid
    stranger = new_client()
    login(stranger, "bob@example.com")
    n = A.row("select count(*) n from items")["n"]
    assert stranger.post("/api/capture", json={"text": "x", "project_id": pid}).status_code == 404
    assert stranger.post("/api/capture", json={"text": "x", "project_id": "1"}).status_code == 400
    assert A.row("select count(*) n from items")["n"] == n
