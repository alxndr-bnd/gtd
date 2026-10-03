"""MCP для AI-ассистентов (SERBITO-375): Claude и другие MCP-клиенты читают и меняют задачи владельца токена.

Транспорт — Streamable HTTP на /mcp, без сессий (stateless): Cloud Run засыпает и держит до двух инстансов, так что
в памяти одного инстанса ничего жить не может. Ответы — JSON, без SSE-потока. Вход — `Authorization: Bearer …`:
личный API-токен из «👤 Аккаунта» (gtd_…) или токен доступа OAuth 2.1 (gtdo_…, коннекторы claude.ai, oauth.py).
Без токена — 401 с адресом метаданных ресурса: по нему клиент сам найдёт вход через OAuth.

Каждый инструмент работает только с данными владельца токена: uid берётся из токена, а не из аргументов, и каждый
запрос фильтрует по user_id. Ни токен, ни содержимое задач в журнал не пишутся.
Описания инструментов и тексты ошибок — по-английски: их читает модель, а не человек."""
import logging
import re
import time
from datetime import datetime
from typing import Annotated, Any, Literal

import anyio.to_thread
from fastapi import HTTPException
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp_types import ToolAnnotations
from pydantic import Field
from starlette.requests import Request
from starlette.responses import JSONResponse

import app as A
import oauth

# SDK пишет INFO на каждый запрос (запуск и остановка менеджера) и текст ошибок инструментов — в них бывают
# названия проектов. В журнал — только предупреждения и падения
logging.getLogger("mcp").setLevel(logging.WARNING)

# Лимиты. На токен: Claude делает несколько вызовов на один ответ — минутного бюджета человеку хватает с запасом,
# а зациклившегося агента он остановит. На IP — неверные токены: 256-битный токен не подобрать, но каждая попытка —
# запрос в базу; сверх лимита отвечаем 429, не заглядывая в неё
MCP_RATE = (120, 60)        # запросов на токен в минуту
MCP_BAD_TOKENS = (30, 600)  # неверных токенов с одного IP за 10 минут
LIST_LIMIT = 200
WEEK = 7 * 86400
# «Кого ждём» (update_task, waiting_for) — первая строка заметок: отдельного поля у задачи нет, а заметки видны
# и в приложении, и в боте. Метка — на языке аккаунта; заменяем и снимаем строку с любой из двух
WAITING_MARK = {"ru": "Ждём: ", "en": "Waiting for: "}
WAITING_RE = re.compile(r"^(?:Ждём|Waiting for): .*(?:\n|$)")

server = MCPServer(
    "gtd",
    title="GTD",
    instructions=(
        "GTD task manager (Getting Things Done). Lists: inbox (unprocessed), next (next actions), waiting, someday, "
        "reference, done, trash. A task belongs to at most one project and one @context. Refer to tasks by their "
        "number (#N), which the user also sees in the app. Capture new thoughts with `capture`; it understands "
        "dates (\"tomorrow 10am\", \"завтра в 10:00\"), #Project and @context in the text."),
    website_url=A.BASE_URL,
)
# Защита от DNS rebinding — для серверов на localhost, которые доверяют сети. Здесь каждый запрос несёт Bearer-токен,
# а адрес у копий разный (gtd.serbito.rs, self-host), поэтому проверку Host в SDK выключаем
NO_REBINDING = TransportSecuritySettings(enable_dns_rebinding_protection=False)


# ───────────────────────── Вход ─────────────────────────

def owner(ctx: Context) -> int:
    """Владелец токена: его положил в scope["state"] слой Endpoint до вызова инструмента."""
    req = ctx.request_context.request
    uid = req.scope.get("state", {}).get("mcp_uid") if req is not None else None
    if not uid:  # через /mcp так не бывает: без верного токена запрос до инструментов не доходит
        raise ToolError("not authenticated")
    return uid


def unauthorized(error: str = "") -> JSONResponse:
    return JSONResponse({"detail": "invalid token" if error else "auth required"}, 401,
                        headers={"WWW-Authenticate": oauth.challenge(error)})


def too_many(window: int) -> JSONResponse:
    return JSONResponse({"detail": "too many requests"}, 429, headers={"Retry-After": str(window)})


def authenticate(request: Request) -> tuple[int | None, JSONResponse | None]:
    """(uid, None) или (None, ответ с ошибкой). Ходит в базу — вызывается в рабочем потоке."""
    scheme, _, raw = request.headers.get("authorization", "").partition(" ")
    raw = raw.strip()
    if scheme.lower() != "bearer" or not raw:
        return None, unauthorized()
    bad_key = "mcpbad:" + A.ip_key(A.client_ip(request))
    if A.limit_count(bad_key, MCP_BAD_TOKENS[1]) >= MCP_BAD_TOKENS[0]:
        return None, too_many(MCP_BAD_TOKENS[1])
    tok = A.api_token_user(raw) or oauth.access_token_user(raw)
    if not tok:
        A.limit_hit(bad_key, MCP_BAD_TOKENS[1])
        return None, unauthorized("invalid_token")
    if A.limit_hit(f"mcp:{tok['id']}", MCP_RATE[1]) > MCP_RATE[0]:
        return None, too_many(MCP_RATE[1])
    A.track(tok["user_id"], "mcp", 0)
    return tok["user_id"], None


class Endpoint:
    """ASGI-приложение на /mcp: проверяет токен и лимит, потом отдаёт запрос SDK.

    Менеджер сессий SDK — свой на каждый запрос: сервер без сессий и отвечает JSON, так что после ответа ничего
    не живёт. Заодно /mcp не зависит от lifespan приложения (тесты гоняют TestClient без него). Накладные — доли
    миллисекунды."""

    async def __call__(self, scope, receive, send):
        uid, err = await anyio.to_thread.run_sync(authenticate, Request(scope, receive))
        if err:
            return await err(scope, receive, send)
        scope.setdefault("state", {})["mcp_uid"] = uid
        server.streamable_http_app(stateless_http=True, json_response=True, transport_security=NO_REBINDING)
        manager = server.session_manager  # только что созданный: между этими строками нет await
        async with manager.run():
            await manager.handle_request(scope, receive, send)


endpoint = Endpoint()


# ───────────────────────── Данные ─────────────────────────

def iso(ts) -> str | None:
    return datetime.fromtimestamp(ts, A.TZ).isoformat(timespec="minutes") if ts else None


def task_out(it: dict) -> dict:
    return {"number": it["num"], "title": it["title"], "list": it["status"], "project": it.get("project"),
            "context": it["context"], "due": iso(it["remind_at"]), "notes": it["notes"] or "",
            "created": iso(it["created"]), "completed": iso(it["completed_at"]), "url": A.item_url(it["num"])}


def task_by_number(uid: int, number: int) -> dict:
    it = A.item_by_num(uid, number)
    if not it:
        raise ToolError(f"Task #{number} not found")
    return it


def project_named(uid: int, name: str) -> dict | None:
    key = name.strip().lstrip("#").replace("_", " ").casefold()
    return next((p for p in A.rows("select id, title from projects where user_id=%s", (uid,))
                 if p["title"].casefold() == key), None)


def context_name(value: str) -> str:
    return value.strip().lstrip("@").lower()


def patch(uid: int, it: dict, body: dict) -> dict:
    try:
        return A.item_patch(uid, it["id"], body, channel="mcp")
    except HTTPException as e:
        raise ToolError(str(e.detail)) from None


# ───────────────────────── Инструменты ─────────────────────────

READ = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False)


@server.tool(annotations=READ)
def list_tasks(
    ctx: Context,
    list: Annotated[Literal["inbox", "next", "waiting", "scheduled", "someday", "reference", "done", "all"],
                    Field(description="Which list. scheduled — open tasks with a date; all — every list "
                                      "except trash")] = "inbox",
    project: Annotated[str | None, Field(description="Only this project (name, case-insensitive)")] = None,
    context: Annotated[str | None, Field(description="Only this context, e.g. \"@phone\" or \"phone\"")] = None,
    query: Annotated[str | None, Field(description="Only tasks whose title or notes contain this text")] = None,
    limit: Annotated[int, Field(ge=1, le=LIST_LIMIT)] = 50,
) -> dict[str, Any]:
    """List or search the user's tasks: the Inbox, next actions, a project or a @context.
    Tasks come in the user's own order (done: newest first, scheduled: by date)."""
    uid = owner(ctx)
    where, args = ["i.user_id=%s"], [uid]
    order = "i.position nulls last, i.id"
    if list == "scheduled":
        where.append("i.remind_at is not null and i.status not in ('done','trash')")
        order = "i.remind_at, i.id"
    elif list == "all":
        where.append("i.status<>'trash'")
        order = "i.created desc, i.id desc"
    else:
        where.append("i.status=%s")
        args.append(list)
        if list == "done":
            order = "i.completed_at desc nulls last, i.id desc"
    if project:
        p = project_named(uid, project)
        if not p:
            raise ToolError(f"No project named {project!r}. Call list_projects to see them")
        where.append("i.project_id=%s")
        args.append(p["id"])
    if context:
        where.append("i.context=%s")
        args.append(context_name(context))
    found = A.rows(f"{A.ITEM_SQL} where {' and '.join(where)} order by {order}", args)
    if query:  # без учёта регистра — в Python: lower() в Postgres для кириллицы зависит от локали базы
        q = query.casefold()
        found = [it for it in found if q in it["title"].casefold() or q in (it["notes"] or "").casefold()]
    return {"tasks": [task_out(it) for it in found[:limit]], "total": len(found)}


@server.tool(annotations=READ)
def list_projects(ctx: Context) -> dict[str, Any]:
    """The user's active projects with counts of open tasks. In GTD every project needs a next action:
    needs_next_action marks projects without one."""
    uid = owner(ctx)
    ps = A.rows(
        "select p.title, "
        "(select count(*) from items i where i.project_id=p.id and i.user_id=p.user_id "
        " and i.status in ('inbox','next','waiting')) open, "
        "(select count(*) from items i where i.project_id=p.id and i.user_id=p.user_id and i.status='next') next "
        "from projects p where p.user_id=%s and p.status='active' order by p.title", (uid,))
    return {"projects": [{"name": p["title"], "open_tasks": p["open"], "next_actions": p["next"],
                          "needs_next_action": p["next"] == 0} for p in ps]}


@server.tool(annotations=READ)
def list_contexts(ctx: Context) -> dict[str, Any]:
    """The user's @contexts (where or with what a task can be done) with counts of open tasks, most used first."""
    uid = owner(ctx)
    cs = A.rows("select context, count(*) n from items where user_id=%s and context is not null "
                "and status not in ('done','trash') group by context order by n desc, context", (uid,))
    return {"contexts": [{"context": "@" + c["context"], "open_tasks": c["n"]} for c in cs]}


@server.tool(annotations=WRITE)
def capture(
    ctx: Context,
    text: Annotated[str, Field(min_length=1, max_length=2000,
                               description="The task as the user would type it, e.g. \"call the bank tomorrow "
                                           "10am #Finance @phone\"")],
) -> dict[str, Any]:
    """Capture a task into the Inbox, the same way as the app and the Telegram bot do. Understands a date or time
    (sets a reminder), #Project and @context in the text; with a project or a context the task goes to Next."""
    uid = owner(ctx)
    if not text.strip():
        raise ToolError("Empty task")
    return task_out(A.capture(uid, text, "mcp"))


@server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True,
                                         openWorldHint=False))
def complete_task(ctx: Context, number: Annotated[int, Field(description="Task number, #N")]) -> dict[str, Any]:
    """Mark a task as done."""
    uid = owner(ctx)
    it = task_by_number(uid, number)
    return task_out(it if it["status"] == "done" else patch(uid, it, {"status": "done"}))


@server.tool(annotations=WRITE)
def move_task(
    ctx: Context,
    number: Annotated[int, Field(description="Task number, #N")],
    list: Annotated[Literal["inbox", "next", "waiting", "someday", "reference", "trash"] | None,
                    Field(description="Move to this list")] = None,
    project: Annotated[str | None, Field(description="Put into this project (created if missing); "
                                                     "\"\" — remove from its project")] = None,
    context: Annotated[str | None, Field(description="Set this @context; \"\" — remove the context")] = None,
) -> dict[str, Any]:
    """Move a task to another list, project or @context. An Inbox task given a project or a context goes to Next,
    as in the app."""
    uid = owner(ctx)
    it = task_by_number(uid, number)
    body: dict[str, Any] = {}
    if list is not None:
        body["status"] = list
    if project is not None:
        name = project.strip().lstrip("#").replace("_", " ").strip()
        body["project_id"] = A.project_by_title(uid, name) if name else None
    if context is not None:
        body["context"] = context_name(context) or None
    if not body:
        raise ToolError("Nothing to change: give list, project or context")
    if list is None and it["status"] == "inbox" and (body.get("project_id") or body.get("context")):
        body["status"] = "next"
    return task_out(patch(uid, it, body))


def parse_due(value: str) -> int:
    """Срок задачи: ISO (2026-10-24, 2026-10-24T15:30, с поясом или без) или как в поле захвата
    («tomorrow 10am», «в пятницу»). Дата без времени — 9:00, как при захвате."""
    v = value.strip()
    try:
        dt = datetime.fromisoformat(v)
    except ValueError:
        dt = None
    if dt is not None:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
            dt = dt.replace(hour=9)
        return int((dt if dt.tzinfo else dt.replace(tzinfo=A.TZ)).timestamp())
    rest, ts = A.parse_when(v, datetime.now(A.TZ))
    if ts is None or rest:
        raise ToolError(f"Cannot read the date {value!r}. Use ISO 8601 (2026-10-24 or 2026-10-24T15:30) "
                        "or words like \"tomorrow 10am\"")
    return ts


def waiting_notes(uid: int, notes: str, who: str) -> str:
    """Заметки с новой строкой «Ждём: …» (who пустой — без неё)."""
    rest = WAITING_RE.sub("", notes or "", count=1)
    if not who:
        return rest
    lang = (A.row("select lang from users where id=%s", (uid,)) or {}).get("lang") \
        or ("ru" if re.search("[а-яё]", who, re.I) else "en")
    return WAITING_MARK[lang] + who + ("\n" + rest if rest else "")


@server.tool(annotations=WRITE)
def update_task(
    ctx: Context,
    number: Annotated[int, Field(description="Task number, #N")],
    title: Annotated[str | None, Field(max_length=500, description="New title. Like in the app, #Project, @context "
                                                                 "and a date in the title are applied too")] = None,
    notes: Annotated[str | None, Field(max_length=10000, description="New notes (replace the old ones); "
                                                                    "\"\" — clear")] = None,
    due: Annotated[str | None, Field(description="Due date and reminder: ISO 8601 (2026-10-24, 2026-10-24T15:30) or "
                                                 "words (\"tomorrow 10am\", \"next friday\"); a date without time "
                                                 "means 09:00; \"\" — remove the date. The task then shows in "
                                                 "Scheduled")] = None,
    waiting_for: Annotated[str | None, Field(max_length=200, description="Who or what the task waits for. Moves the "
                                                                         "task to Waiting and puts \"Waiting for: …\" "
                                                                         "as the first line of its notes; \"\" — "
                                                                         "remove that line")] = None,
) -> dict[str, Any]:
    """Change a task: title, notes, due date (reminder) or who it is waiting for. Only the given fields change.
    Use move_task for the list, project or context, and complete_task to finish it."""
    uid = owner(ctx)
    it = task_by_number(uid, number)
    body: dict[str, Any] = {}
    if title is not None:
        if not title.strip():
            raise ToolError("Empty title")
        body["title"] = title.strip()
    if notes is not None:
        body["notes"] = notes
    if waiting_for is not None:
        who = " ".join(waiting_for.split())
        body["notes"] = waiting_notes(uid, body.get("notes", it["notes"]), who)
        if who:
            body["status"] = "waiting"
    remind = None if due is None else parse_due(due) if due.strip() else 0
    if not body and due is None:
        raise ToolError("Nothing to change: give title, notes, due or waiting_for")
    if body:
        it = patch(uid, it, body)
    if due is not None:  # отдельно и после: явный срок важнее даты, найденной в новом заголовке
        it = patch(uid, it, {"remind_at": remind or None})
    return task_out(it)


@server.tool(annotations=READ)
def weekly_review(ctx: Context) -> dict[str, Any]:
    """GTD Weekly Review in one call: Inbox count, projects without a next action, overdue tasks, everything in
    Waiting (waiting_over_a_week marks the old ones), reminders for the next 7 days and the Someday count.
    Read-only: suggest changes to the user, then make them with the other tools."""
    uid = owner(ctx)
    now = int(time.time())
    counts = {r["status"]: r["n"] for r in A.rows(
        "select status, count(*) n from items where user_id=%s group by status", (uid,))}
    stalled = A.rows(
        "select p.title, (select count(*) from items i where i.project_id=p.id and i.user_id=p.user_id "
        " and i.status in ('inbox','waiting')) open from projects p where p.user_id=%s and p.status='active' "
        "and not exists (select 1 from items i where i.project_id=p.id and i.user_id=p.user_id and i.status='next') "
        "order by p.title", (uid,))
    open_sql = f"{A.ITEM_SQL} where i.user_id=%s and i.status not in ('done','trash') and i.remind_at is not null "
    overdue = A.rows(open_sql + "and i.remind_at<%s order by i.remind_at, i.id limit %s", (uid, now, LIST_LIMIT))
    soon = A.rows(open_sql + "and i.remind_at>=%s and i.remind_at<%s order by i.remind_at, i.id limit %s",
                  (uid, now, now + WEEK, LIST_LIMIT))
    waiting = A.rows(f"{A.ITEM_SQL} where i.user_id=%s and i.status='waiting' order by i.created, i.id limit %s",
                     (uid, LIST_LIMIT))
    return {
        "inbox_count": counts.get("inbox", 0),
        "projects_without_next_action": [{"name": p["title"], "open_tasks": p["open"]} for p in stalled],
        "overdue": [task_out(it) for it in overdue],
        "waiting": [{**task_out(it), "waiting_over_a_week": (it["created"] or now) < now - WEEK} for it in waiting],
        "upcoming_7_days": [task_out(it) for it in soon],
        "someday_count": counts.get("someday", 0),
    }
