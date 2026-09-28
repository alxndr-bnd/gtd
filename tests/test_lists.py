"""Списки задач: «Скоро срок» на экране Inbox (SERBITO-326), выполненные в проекте (SERBITO-327),
ручной порядок (SERBITO-328)."""
import math
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import app as A
from conftest import bot_callback


def add(client, text, status=None, remind_at=None):
    it = client.post("/api/capture", json={"text": text}).json()
    body = {k: v for k, v in (("status", status), ("remind_at", remind_at)) if v is not None}
    return client.patch(f"/api/items/{it['id']}", json=body).json() if body else it


def ids(client, query):
    return [i["id"] for i in client.get("/api/items?" + query).json()]


# ── SERBITO-326: «Скоро срок» ──

def test_due_cutoff_is_midnight_after_third_day_in_app_tz(monkeypatch):
    monkeypatch.setattr(A, "TZ", ZoneInfo("Europe/Belgrade"))
    mon = datetime(2026, 9, 28, 23, 30, tzinfo=A.TZ)  # понедельник, поздний вечер
    assert A.due_cutoff(mon) == int(datetime(2026, 10, 2, tzinfo=A.TZ).timestamp())  # до конца четверга
    # Тот же момент — это уже вторник в Токио: окно сдвигается на день. День считается в TZ приложения
    monkeypatch.setattr(A, "TZ", ZoneInfo("Asia/Tokyo"))
    assert A.due_cutoff(mon) == int(datetime(2026, 10, 3, tzinfo=ZoneInfo("Asia/Tokyo")).timestamp())
    # Переход на зимнее время внутри окна: граница — всё равно местная полночь
    monkeypatch.setattr(A, "TZ", ZoneInfo("Europe/Belgrade"))
    fri = datetime(2026, 10, 23, 12, tzinfo=A.TZ)
    assert datetime.fromtimestamp(A.due_cutoff(fri), A.TZ) == datetime(2026, 10, 27, tzinfo=A.TZ)


def test_due_soon_order_boundary_and_lists(client, login):
    login(client)
    now, cut = int(time.time()), A.due_cutoff()
    late = add(client, "просрочено давно", "waiting", now - 3 * 86400)
    over = add(client, "просрочено вчера", "next", now - 86400)
    soon = add(client, "скоро", "someday", now + 3600)
    edge = add(client, "в последнюю секунду окна", "reference", cut - 1)
    inbox = add(client, "во входящих", remind_at=now + 60)
    add(client, "сразу за окном", "next", cut)
    add(client, "без срока", "next")
    add(client, "выполнена", "done", now - 60)
    add(client, "в корзине", "trash", now - 60)
    assert ids(client, "status=due") == [late["id"], over["id"], inbox["id"], soon["id"], edge["id"]]


def test_due_soon_is_per_user(new_client, login):
    a, b = new_client(), new_client()
    login(a, "a@example.com")
    login(b, "b@example.com")
    add(a, "срок у a", "next", int(time.time()) + 60)
    assert ids(b, "status=due") == [] and len(ids(a, "status=due")) == 1


def test_due_window_is_three_days(client, login):
    login(client)
    today = datetime.now(A.TZ).replace(hour=12, minute=0, second=0, microsecond=0)
    at = {d: add(client, f"через {d} дн.", "next", int((today + timedelta(days=d)).timestamp()))["id"]
          for d in (0, 3, 4)}
    assert ids(client, "status=due") == [at[0], at[3]]


# ── SERBITO-327: выполненные задачи в проекте ──

def project_items(client, pid, done=False):
    return {i["title"]: i["status"] for i in
            client.get(f"/api/items?status=all&project_id={pid}{'&done=1' if done else ''}").json()}


def test_existing_projects_keep_showing_completed_new_ones_hide(client, login):
    """Миграция: проекты, что были до неё, остаются как были — выполненные в списке; новые их скрывают."""
    uid = login(client)
    A.run("alter table projects drop column done_mode")  # база до SERBITO-327
    old = A.run("insert into projects(user_id,title,created) values(%s,'Старый',0) returning id", (uid,))
    A.run(A.SCHEMA)  # старт новой версии
    new = client.post("/api/projects", json={"title": "Новый"}).json()["id"]
    via_capture = A.capture(uid, "задача #Из_захвата")["project_id"]
    modes = {p["id"]: p["done_mode"] for p in client.get("/api/projects").json()}
    assert modes == {old: "list", new: "hide", via_capture: "hide"}
    A.run(A.SCHEMA)  # повторный старт ничего не меняет
    assert {p["id"]: p["done_mode"] for p in client.get("/api/projects").json()} == modes


def test_done_mode_persists_per_project(new_client, login):
    c = new_client()
    login(c)
    a = c.post("/api/projects", json={"title": "А"}).json()["id"]
    b = c.post("/api/projects", json={"title": "Б"}).json()["id"]
    assert c.patch(f"/api/projects/{a}", json={"done_mode": "section"}).json()["done_mode"] == "section"
    assert c.patch(f"/api/projects/{a}", json={"done_mode": "weird"}).status_code == 400
    assert {p["id"]: p["done_mode"] for p in c.get("/api/projects").json()} == {a: "section", b: "hide"}
    c.patch(f"/api/projects/{b}", json={"done_mode": "list"})
    assert {p["id"]: p["done_mode"] for p in c.get("/api/projects").json()} == {a: "section", b: "list"}
    other = new_client()
    login(other, "bob@example.com")
    assert other.patch(f"/api/projects/{a}", json={"done_mode": "list"}).status_code == 404
    assert A.row("select done_mode from projects where id=%s", (a,))["done_mode"] == "section"


def test_project_items_completed_only_on_request(client, login):
    login(client)
    pid = client.post("/api/projects", json={"title": "П"}).json()["id"]
    for text, status in (("открытая", None), ("ждёт", "waiting"), ("готово", "done"), ("в корзине", "trash")):
        it = client.post("/api/capture", json={"text": text}).json()
        client.patch(f"/api/items/{it['id']}", json={"project_id": pid, **({"status": status} if status else {})})
    assert project_items(client, pid) == {"открытая": "inbox", "ждёт": "waiting"}
    assert project_items(client, pid, done=True) == {"открытая": "inbox", "ждёт": "waiting", "готово": "done"}
    assert len(client.get("/api/items?status=all").json()) == 4  # Weekly Review — по-прежнему всё


# ── SERBITO-328: ручной порядок ──

def titles(client, query):
    return [i["title"] for i in client.get("/api/items?" + query).json()]


def move(client, iid, prev=None, nxt=None, scope="list"):
    return client.post(f"/api/items/{iid}/move", json={"prev": prev, "next": nxt, "scope": scope})


def positions(uid):
    return {r["id"]: (r["position"], r["ppos"]) for r in A.rows("select id, position, ppos from items where user_id=%s",
                                                                  (uid,))}


def test_backfill_keeps_current_order(client, login):
    """До SERBITO-328 списки шли по времени создания, старые сверху, — после миграции порядок тот же."""
    uid = login(client)
    for t, created in (("вторая", 200), ("первая", 100), ("третья", 300)):
        it = client.post("/api/capture", json={"text": t + " #П"}).json()
        A.run("update items set created=%s where id=%s", (created, it["id"]))
    A.run("alter table items drop column position, drop column ppos")  # база до SERBITO-328
    A.run(A.SCHEMA)
    pid = client.get("/api/projects").json()[0]["id"]
    assert titles(client, "status=next") == ["первая", "вторая", "третья"]
    assert titles(client, f"status=all&project_id={pid}") == ["первая", "вторая", "третья"]
    new = client.post("/api/capture", json={"text": "новая #П"}).json()
    assert titles(client, "status=next")[0] == titles(client, f"status=all&project_id={pid}")[0] == new["title"]


def test_new_and_moved_tasks_go_on_top(client, login):
    login(client)
    a = add(client, "a", "next")
    b = add(client, "b", "next")
    assert titles(client, "status=next") == ["b", "a"]  # новые — сверху
    c = add(client, "c")
    assert titles(client, "status=inbox") == ["c"]
    client.patch(f"/api/items/{c['id']}", json={"status": "next"})
    assert titles(client, "status=next") == ["c", "b", "a"]  # перенесённая — наверх нового списка
    client.patch(f"/api/items/{a['id']}", json={"title": "a", "status": "next"})  # тот же список — место прежнее
    assert titles(client, "status=next") == ["c", "b", "a"]
    pid = client.post("/api/projects", json={"title": "П"}).json()["id"]
    for it in (a, b):
        client.patch(f"/api/items/{it['id']}", json={"project_id": pid})
    assert titles(client, f"status=all&project_id={pid}") == ["b", "a"]  # добавленная в проект — наверх проекта


def test_reorder_updates_one_row_and_persists(client, login):
    uid = login(client)
    a, b, c, d = (add(client, t, "next")["id"] for t in "dcba")  # на экране: a b c d
    a, b, c, d = d, c, b, a
    assert titles(client, "status=next") == list("abcd")
    before = positions(uid)
    r = move(client, d, prev=a, nxt=b)
    assert r.status_code == 200, r.text
    after = positions(uid)
    assert [k for k in before if before[k] != after[k]] == [d]  # одна строка
    assert before[a][0] < after[d][0] < before[b][0] and after[d][1] == before[d][1]  # порядок проекта не тронут
    assert titles(client, "status=next") == list("adbc")
    assert move(client, c, nxt=a).status_code == 200  # в самый верх
    assert move(client, a, prev=b).status_code == 200  # в самый низ
    assert titles(client, "status=next") == list("cdba")
    assert move(client, b).json()["id"] == b  # без соседей — ничего не меняется
    assert titles(client, "status=next") == list("cdba")


def test_reorder_when_gap_is_exhausted(client, login):
    uid = login(client)
    a, b, c = (add(client, t, "waiting")["id"] for t in "cba")
    a, c = c, a
    A.run("update items set position=%s where id=%s", (1.0, a))
    A.run("update items set position=%s where id=%s", (math.nextafter(1.0, 2), b))  # между ними места нет
    A.run("update items set position=%s where id=%s", (5.0, c))
    assert titles(client, "status=waiting") == list("abc")
    assert move(client, c, prev=a, nxt=b).status_code == 200  # список перенумерован, задача встала
    assert titles(client, "status=waiting") == list("acb")
    assert len({p for p, _ in positions(uid).values()}) == 3


def test_reorder_checks_ownership_and_list(new_client, login):
    me, other = new_client(), new_client()
    login(me)
    login(other, "bob@example.com")
    a, b = add(me, "a", "next")["id"], add(me, "b", "next")["id"]
    inbox = add(me, "во входящих")["id"]
    theirs = add(other, "чужая", "next")["id"]
    assert move(other, a, nxt=theirs).status_code == 404  # чужую задачу не двигать
    assert move(me, a, nxt=theirs).status_code == 404  # и не ставить рядом с чужой
    assert move(me, a, nxt=inbox).status_code == 400  # сосед из другого списка
    assert move(me, a, nxt=a).status_code == 400
    assert move(me, a, nxt="b").status_code == 400
    assert move(me, a, nxt=b, scope="weird").status_code == 400
    assert move(me, a, nxt=b, scope="project").status_code == 400  # задача без проекта
    assert me.post(f"/api/items/{a}/move", json={}).status_code == 200
    assert new_client().post(f"/api/items/{a}/move", json={}).status_code == 401
    assert titles(me, "status=next") == ["b", "a"] and titles(other, "status=next") == ["чужая"]


def test_project_order_is_separate_from_list_order(client, login):
    login(client)
    x = add(client, "x #П", "next")["id"]
    y = add(client, "y #П", "waiting")["id"]
    z = add(client, "z #П", "next")["id"]
    pid = client.get("/api/projects").json()[0]["id"]
    assert titles(client, f"status=all&project_id={pid}") == ["z", "y", "x"]
    assert move(client, x, nxt=z, scope="project").status_code == 200
    assert titles(client, f"status=all&project_id={pid}") == ["x", "z", "y"]
    assert titles(client, "status=next") == ["z", "x"]  # в Next — прежний порядок
    assert move(client, y, nxt=x, scope="list").status_code == 400  # waiting и next — разные списки
    assert move(client, y, nxt=x, scope="project").status_code == 200  # а проект у них общий


def test_bot_next_button_puts_task_on_top(tg):
    from conftest import bot_message
    bot_message("первая @дом")
    bot_message("разобрать потом")
    uid = A.row("select id from users where tg_id=777")["id"]
    iid = A.row("select id from items where user_id=%s and status='inbox'", (uid,))["id"]
    bot_callback(f"next:{iid}")
    assert [r["title"] for r in A.rows("select title from items where user_id=%s and status='next' "
                                       "order by position", (uid,))] == ["разобрать потом", "первая"]
