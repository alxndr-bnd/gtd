"""Списки задач: «Скоро срок» на экране Inbox (SERBITO-326)."""
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import app as A


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
