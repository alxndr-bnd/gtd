"""GTD for free — веб-UI + Telegram-бот (захват задач и напоминания)."""
import asyncio
import logging
import os
import re
import secrets
import sqlite3
import threading
import time
from contextlib import asynccontextmanager
from datetime import date, datetime, time as dtime, timedelta
from zoneinfo import ZoneInfo

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse

log = logging.getLogger("gtd")
logging.basicConfig(level=logging.INFO)

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
BASE_URL = os.getenv("BASE_URL", "https://gtd.serbito.rs").rstrip("/")
TZ = ZoneInfo(os.getenv("TZ", "Europe/Belgrade"))
ALLOWED = {int(x) for x in os.getenv("ALLOWED_TG_IDS", "").split(",") if x.strip()}
DEV = os.getenv("DEV", "") == "1"
DB_PATH = os.getenv("DB_PATH", "data/gtd.db")
COOKIE_SECURE = BASE_URL.startswith("https")

STATUSES = ("inbox", "next", "waiting", "someday", "reference", "done", "trash")

# ───────────────────────── DB ─────────────────────────
os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
_db = sqlite3.connect(DB_PATH, check_same_thread=False)
_db.row_factory = sqlite3.Row
_lock = threading.RLock()

_db.executescript(
    """
create table if not exists users(
  id integer primary key, tg_id integer unique, name text, created integer);
create table if not exists sessions(
  token text primary key, user_id integer, created integer);
create table if not exists login_tokens(
  token text primary key, user_id integer, expires integer);
create table if not exists projects(
  id integer primary key, user_id integer, title text, status text default 'active', created integer);
create table if not exists items(
  id integer primary key, user_id integer, title text, notes text default '',
  status text default 'inbox', project_id integer, context text,
  remind_at integer, reminded integer default 0, source text default 'web',
  created integer, completed_at integer);
create index if not exists items_user_status on items(user_id, status);
"""
)


def run(sql, args=()):
    with _lock:
        cur = _db.execute(sql, args)
        _db.commit()
        return cur.lastrowid


def rows(sql, args=()):
    with _lock:
        return [dict(r) for r in _db.execute(sql, args).fetchall()]


def row(sql, args=()):
    r = rows(sql, args)
    return r[0] if r else None


# ───────────────────────── Парсинг захвата ─────────────────────────
WEEKDAYS = {"понедельник": 0, "вторник": 1, "сред": 2, "четверг": 3, "пятниц": 4,
            "суббот": 5, "воскресень": 6, "monday": 0, "tuesday": 1, "wednesday": 2,
            "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6}
WD_RE = (r"(?:\b(?:в|во|на|on)\s+)?\b(понедельник|вторник|сред[ауы]|четверг|пятниц[ауы]|"
         r"суббот[ауы]|воскресенье|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b")


def parse_when(text: str, now: datetime):
    """Возвращает (очищенный_текст, epoch|None). Понимает рус/англ: «через 2 часа»,
    «завтра в 10:00», «в пятницу», «24.10 12:00», «2026-10-24», «в 15:30»."""
    found = False

    def cut(m):
        nonlocal text, found
        text = text[: m.start()] + " " + text[m.end():]
        found = True

    m = re.search(r"\b(?:через|in)\s+(\d+)\s*(мин\w*|min\w*|час\w*|ч|hour\w*|h|дн\w*|день|day\w*|недел\w*|week\w*)\b",
                  text, re.I)
    if m:
        n, u = int(m.group(1)), m.group(2).lower()
        if u.startswith(("мин", "min")):
            d = timedelta(minutes=n)
        elif u.startswith(("ч", "h")):
            d = timedelta(hours=n)
        elif u.startswith(("дн", "ден", "d")):
            d = timedelta(days=n)
        else:
            d = timedelta(weeks=n)
        cut(m)
        return _clean(text), int((now + d).timestamp())

    hm = None
    m = re.search(r"(?:\b(?:в|at|к)\s+)?\b(\d{1,2}):(\d{2})\b", text)
    if m and int(m.group(1)) < 24 and int(m.group(2)) < 60:
        hm = (int(m.group(1)), int(m.group(2)))
        cut(m)

    base, explicit_dm = None, False
    m = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", text)
    if m:
        try:
            base = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            cut(m)
        except ValueError:
            pass
    if base is None:
        m = re.search(r"\b(\d{1,2})\.(\d{1,2})(?:\.(\d{4}))?\b", text)
        if m:
            try:
                y = int(m.group(3)) if m.group(3) else now.year
                base = date(y, int(m.group(2)), int(m.group(1)))
                explicit_dm = not m.group(3)
                cut(m)
            except ValueError:
                pass
    if base is None:
        m = re.search(r"\b(послезавтра|завтра|сегодня|today|tomorrow)\b", text, re.I)
        if m:
            k = m.group(1).lower()
            off = 2 if k == "послезавтра" else 1 if k in ("завтра", "tomorrow") else 0
            base = now.date() + timedelta(days=off)
            cut(m)
    if base is None:
        m = re.search(WD_RE, text, re.I)
        if m:
            w = m.group(1).lower()
            wd = next(v for k, v in WEEKDAYS.items() if w.startswith(k))
            base = now.date() + timedelta(days=(wd - now.weekday() - 1) % 7 + 1)
            cut(m)

    if base is None and hm:
        base = now.date()
        if datetime.combine(base, dtime(*hm), TZ) <= now:
            base += timedelta(days=1)
    if base is None:
        return _clean(text), None
    dt = datetime.combine(base, dtime(*(hm or (9, 0))), TZ)
    if explicit_dm and dt < now:
        dt = dt.replace(year=dt.year + 1)
    return _clean(text), int(dt.timestamp())


def _clean(t: str) -> str:
    t = re.sub(r"^\s*(напомни(?:ть)?(?:\s+мне)?|remind(?:\s+me)?(?:\s+to)?)\b[\s,:-]*(?:про|о|об|что|to)?\s+",
               "", t, flags=re.I)
    return re.sub(r"\s+", " ", t).strip(" ,.-—")


def capture(uid: int, raw: str, source: str = "web") -> dict:
    """Умный захват: текст [@контекст] [#проект] [когда] -> задача."""
    now = datetime.now(TZ)
    text = raw.strip()
    ctx = None
    m = re.search(r"(?<!\S)@([\w-]+)", text)
    if m:
        ctx = m.group(1).lower()
        text = text[: m.start()] + text[m.end():]
    proj_id = None
    m = re.search(r"(?<!\S)#([\w-]+)", text)
    if m:
        proj_id = project_by_title(uid, m.group(1).replace("_", " "))
        text = text[: m.start()] + text[m.end():]
    title, remind = parse_when(text, now)
    title = title or raw.strip()
    status = "next" if (ctx or proj_id) else "inbox"
    iid = run(
        "insert into items(user_id,title,status,project_id,context,remind_at,source,created) values(?,?,?,?,?,?,?,?)",
        (uid, title, status, proj_id, ctx, remind, source, int(time.time())),
    )
    return item_get(uid, iid)


def project_by_title(uid: int, title: str) -> int:
    r = row("select id from projects where user_id=? and lower(title)=lower(?)", (uid, title))
    if r:
        return r["id"]
    return run("insert into projects(user_id,title,created) values(?,?,?)", (uid, title, int(time.time())))


ITEM_SQL = "select i.*, p.title as project from items i left join projects p on p.id=i.project_id "


def item_get(uid, iid):
    return row(ITEM_SQL + "where i.user_id=? and i.id=?", (uid, iid))


def fmt_ts(ts):
    return datetime.fromtimestamp(ts, TZ).strftime("%d.%m %H:%M")


def describe(it) -> str:
    bits = []
    if it.get("remind_at"):
        bits.append("⏰ " + fmt_ts(it["remind_at"]))
    if it.get("context"):
        bits.append("@" + it["context"])
    if it.get("project"):
        bits.append("#" + it["project"])
    return "  ".join(bits)


# ───────────────────────── Telegram ─────────────────────────
_client: httpx.AsyncClient | None = None
BOT_USERNAME = ""


async def tg(method, **params):
    try:
        r = await _client.post(f"https://api.telegram.org/bot{TOKEN}/{method}", json=params, timeout=65)
        data = r.json()
        if not data.get("ok"):
            log.warning("tg %s: %s", method, data)
        return data.get("result")
    except Exception as e:  # noqa: BLE001
        log.warning("tg %s failed: %s", method, e)
        return None


def tg_user(tg_id: int, name: str):
    u = row("select * from users where tg_id=?", (tg_id,))
    if u:
        return u
    allowed = (tg_id in ALLOWED) if ALLOWED else (row("select count(*) c from users")["c"] == 0)
    if not allowed:
        return None
    uid = run("insert into users(tg_id,name,created) values(?,?,?)", (tg_id, name, int(time.time())))
    return row("select * from users where id=?", (uid,))


HELP = (
    "Просто пришли мысль — она попадёт в Inbox.\n\n"
    "Умный захват:\n"
    "• «Позвонить в банк завтра в 10:00» → напоминание\n"
    "• «через 2 часа проверить деплой»\n"
    "• «в пятницу отчёт #Клиент_X @работа» → сразу в Next, проект и контекст\n\n"
    "/inbox — что в инбоксе\n/next — следующие действия\n/done 12 — закрыть задачу №12\n"
    "/login — ссылка для входа в веб-интерфейс"
)


def kb_item(iid, done_only=False):
    return {"inline_keyboard": [[
        {"text": "✅ Готово", "callback_data": f"done:{iid}"},
        {"text": "💤 +1ч", "callback_data": f"snz:{iid}"},
        {"text": "⏭ Next", "callback_data": f"next:{iid}"},
    ]]}


async def handle_message(msg):
    chat = msg["chat"]["id"]
    frm = msg.get("from", {})
    u = tg_user(frm.get("id"), frm.get("first_name", ""))
    if not u:
        await tg("sendMessage", chat_id=chat, text="Доступ закрыт.")
        return
    text = (msg.get("text") or msg.get("caption") or "").strip()
    if not text:
        await tg("sendMessage", chat_id=chat, text="Пока понимаю только текст.")
        return
    cmd, _, arg = text.partition(" ")
    cmd = cmd.split("@")[0].lower()
    if cmd in ("/start", "/help"):
        await tg("sendMessage", chat_id=chat, text="GTD-бот готов.\n\n" + HELP)
    elif cmd == "/login":
        tok = secrets.token_urlsafe(24)
        run("insert into login_tokens values(?,?,?)", (tok, u["id"], int(time.time()) + 600))
        await tg("sendMessage", chat_id=chat, text=f"Вход (10 минут, одноразовая):\n{BASE_URL}/auth?t={tok}")
    elif cmd in ("/inbox", "/next"):
        st = cmd[1:]
        its = rows(ITEM_SQL + "where i.user_id=? and i.status=? order by i.created limit 20", (u["id"], st))
        body = "\n".join(f"#{i['id']} {i['title']} {describe(i)}".strip() for i in its) or "Пусто 🎉"
        await tg("sendMessage", chat_id=chat, text=f"{st.upper()}:\n{body}")
    elif cmd == "/done":
        try:
            iid = int(arg.strip().lstrip("#"))
        except ValueError:
            await tg("sendMessage", chat_id=chat, text="Использование: /done 12")
            return
        ok = mark_done(u["id"], iid)
        await tg("sendMessage", chat_id=chat, text="✅ Готово" if ok else "Не нашёл такую задачу")
    elif text.startswith("/"):
        await tg("sendMessage", chat_id=chat, text=HELP)
    else:
        it = capture(u["id"], text, "telegram")
        where = "Next" if it["status"] == "next" else "Inbox"
        await tg("sendMessage", chat_id=chat, text=f"✓ {where} #{it['id']}: {it['title']}\n{describe(it)}".strip(),
                 reply_markup=kb_item(it["id"]))


def mark_done(uid, iid):
    it = item_get(uid, iid)
    if not it:
        return False
    run("update items set status='done', completed_at=? where id=?", (int(time.time()), iid))
    return True


async def handle_callback(cb):
    u = row("select * from users where tg_id=?", (cb["from"]["id"],))
    if not u:
        return
    act, _, sid = cb["data"].partition(":")
    iid = int(sid)
    it = item_get(u["id"], iid)
    msg = cb.get("message", {})
    if not it:
        await tg("answerCallbackQuery", callback_query_id=cb["id"], text="Не найдено")
        return
    if act == "done":
        mark_done(u["id"], iid)
        note = "✅ Готово"
    elif act == "snz":
        run("update items set remind_at=?, reminded=0 where id=?", (int(time.time()) + 3600, iid))
        note = "💤 Напомню через час"
    elif act == "next":
        run("update items set status='next' where id=?", (iid,))
        note = "⏭ В Next"
    else:
        return
    await tg("answerCallbackQuery", callback_query_id=cb["id"], text=note)
    if msg:
        await tg("editMessageText", chat_id=msg["chat"]["id"], message_id=msg["message_id"],
                 text=f"{note}: {it['title']}")


async def poll_loop():
    global BOT_USERNAME
    me = await tg("getMe")
    BOT_USERNAME = (me or {}).get("username", "")
    await tg("setMyCommands", commands=[
        {"command": "inbox", "description": "Инбокс"}, {"command": "next", "description": "Следующие действия"},
        {"command": "done", "description": "Закрыть задачу: /done 12"},
        {"command": "login", "description": "Ссылка для входа в веб"}, {"command": "help", "description": "Помощь"}])
    offset = 0
    while True:
        ups = await tg("getUpdates", offset=offset, timeout=30, allowed_updates=["message", "callback_query"])
        if ups is None:
            await asyncio.sleep(5)
            continue
        for up in ups:
            offset = up["update_id"] + 1
            try:
                if "message" in up:
                    await handle_message(up["message"])
                elif "callback_query" in up:
                    await handle_callback(up["callback_query"])
            except Exception:  # noqa: BLE001
                log.exception("update failed")


async def reminder_loop():
    while True:
        await asyncio.sleep(15)
        try:
            due = rows("select i.*, u.tg_id from items i join users u on u.id=i.user_id "
                       "where i.remind_at is not null and i.reminded=0 and i.remind_at<=? "
                       "and i.status not in ('done','trash')", (int(time.time()),))
            for it in due:
                run("update items set reminded=1 where id=?", (it["id"],))
                if TOKEN and it["tg_id"]:
                    await tg("sendMessage", chat_id=it["tg_id"], text=f"⏰ {it['title']}",
                             reply_markup=kb_item(it["id"]))
        except Exception:  # noqa: BLE001
            log.exception("reminder loop")


@asynccontextmanager
async def lifespan(app):
    global _client
    _client = httpx.AsyncClient()
    tasks = [asyncio.create_task(reminder_loop())]
    if TOKEN:
        tasks.append(asyncio.create_task(poll_loop()))
    else:
        log.warning("TELEGRAM_BOT_TOKEN не задан — бот выключен")
    yield
    for t in tasks:
        t.cancel()
    await _client.aclose()


# ───────────────────────── Web / API ─────────────────────────
app = FastAPI(lifespan=lifespan)


def current_user(request: Request) -> int:
    sid = request.cookies.get("sid")
    r = row("select user_id from sessions where token=?", (sid,)) if sid else None
    if not r:
        raise HTTPException(401, "auth required")
    return r["user_id"]


def start_session(uid: int) -> RedirectResponse:
    tok = secrets.token_urlsafe(32)
    run("insert into sessions values(?,?,?)", (tok, uid, int(time.time())))
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie("sid", tok, max_age=60 * 60 * 24 * 90, httponly=True, samesite="lax", secure=COOKIE_SECURE)
    return resp


@app.get("/auth")
def auth(t: str):
    r = row("select * from login_tokens where token=? and expires>?", (t, int(time.time())))
    if not r:
        return JSONResponse({"error": "Ссылка устарела. Отправь /login боту ещё раз."}, status_code=400)
    run("delete from login_tokens where token=?", (t,))
    return start_session(r["user_id"])


@app.get("/dev-login")
def dev_login():
    if not DEV:
        raise HTTPException(404)
    u = row("select id from users limit 1")
    uid = u["id"] if u else run("insert into users(tg_id,name,created) values(0,'dev',?)", (int(time.time()),))
    return start_session(uid)


@app.get("/api/config")
def config():
    return {"bot": BOT_USERNAME, "dev": DEV}


@app.post("/api/logout")
def logout(request: Request):
    run("delete from sessions where token=?", (request.cookies.get("sid", ""),))
    return {"ok": True}


@app.get("/api/counts")
def counts(uid: int = Depends(current_user)):
    c = {r["status"]: r["n"] for r in rows(
        "select status, count(*) n from items where user_id=? group by status", (uid,))}
    c["scheduled"] = row("select count(*) n from items where user_id=? and remind_at is not null "
                         "and status not in ('done','trash')", (uid,))["n"]
    c["projects"] = row("select count(*) n from projects where user_id=? and status='active'", (uid,))["n"]
    return c


@app.get("/api/items")
def list_items(status: str = "inbox", project_id: int | None = None, uid: int = Depends(current_user)):
    where, args = "i.user_id=?", [uid]
    if status == "scheduled":
        where += " and i.remind_at is not null and i.status not in ('done','trash')"
        order = "i.remind_at"
    else:
        order = "i.completed_at desc" if status == "done" else "i.created"
        if status != "all":
            where += " and i.status=?"
            args.append(status)
    if project_id:
        where += " and i.project_id=?"
        args.append(project_id)
    return rows(f"{ITEM_SQL} where {where} order by {order} limit 500", args)


@app.post("/api/capture")
def api_capture(body: dict, uid: int = Depends(current_user)):
    text = (body.get("text") or "").strip()
    if not text:
        raise HTTPException(400, "empty")
    return capture(uid, text, "web")


@app.patch("/api/items/{iid}")
def patch_item(iid: int, body: dict, uid: int = Depends(current_user)):
    if not item_get(uid, iid):
        raise HTTPException(404)
    sets, args = [], []
    for k in ("title", "notes", "status", "project_id", "context", "remind_at"):
        if k not in body:
            continue
        v = body[k]
        if k == "status":
            if v not in STATUSES:
                raise HTTPException(400, "bad status")
            sets.append("completed_at=?")
            args.append(int(time.time()) if v == "done" else None)
        if k == "remind_at":
            sets.append("reminded=0")
        if k == "context" and v:
            v = str(v).lstrip("@").lower()
        sets.append(f"{k}=?")
        args.append(v or None if k in ("context", "project_id", "remind_at") else v)
    if sets:
        run(f"update items set {', '.join(sets)} where id=? and user_id=?", (*args, iid, uid))
    return item_get(uid, iid)


@app.delete("/api/items/{iid}")
def delete_item(iid: int, uid: int = Depends(current_user)):
    run("delete from items where id=? and user_id=?", (iid, uid))
    return {"ok": True}


@app.get("/api/projects")
def list_projects(uid: int = Depends(current_user)):
    return rows(
        "select p.*, "
        "(select count(*) from items i where i.project_id=p.id and i.status in ('inbox','next','waiting')) open_count, "
        "(select count(*) from items i where i.project_id=p.id and i.status='next') next_count "
        "from projects p where p.user_id=? and p.status='active' order by p.title", (uid,))


@app.post("/api/projects")
def create_project(body: dict, uid: int = Depends(current_user)):
    title = (body.get("title") or "").strip()
    if not title:
        raise HTTPException(400, "empty")
    return {"id": project_by_title(uid, title)}


@app.patch("/api/projects/{pid}")
def patch_project(pid: int, body: dict, uid: int = Depends(current_user)):
    for k in ("title", "status"):
        if k in body:
            run(f"update projects set {k}=? where id=? and user_id=?", (body[k], pid, uid))
    return {"ok": True}


@app.get("/")
def index():
    return FileResponse(os.path.join(os.path.dirname(__file__), "static", "index.html"))
