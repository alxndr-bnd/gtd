"""GTD for free — веб-UI + Telegram-бот (захват задач и напоминания)."""
import asyncio
import hashlib
import hmac
import html
import logging
import os
import re
import secrets
import smtplib
import time
from email.message import EmailMessage
from contextlib import asynccontextmanager
from datetime import date, datetime, time as dtime, timedelta
from zoneinfo import ZoneInfo

import httpx
import psycopg
import sentry_sdk
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response

import pages

log = logging.getLogger("gtd")
logging.basicConfig(level=logging.INFO)
# httpx на INFO пишет полный URL каждого запроса, а в URL Telegram API зашит токен бота — в логи он не должен попадать
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
BASE_URL = os.getenv("BASE_URL", "https://gtd.serbito.rs").rstrip("/")
TZ = ZoneInfo(os.getenv("TZ", "Europe/Belgrade"))
DEV = os.getenv("DEV", "") == "1"
# Кто видит «Статистику» — id аккаунтов, а не почты: конфиг лежит в публичном репозитории
ADMIN_USER_IDS = {int(x) for x in os.getenv("ADMIN_USER_IDS", "").split(",") if x.strip()}
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
# Почта — через Brevo SMTP relay, тот же аккаунт, что у serbito
SMTP_HOST = os.getenv("SMTP_HOST", "smtp-relay.brevo.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
EMAIL_FROM = os.getenv("EMAIL_FROM", '"GTD" <info@serbito.rs>')
SENTRY_DSN = os.getenv("SENTRY_DSN", "")
# Google Analytics 4: поток данных для gtd.serbito.rs. Пусто — GA не подключается
GA_ID = os.getenv("GA_MEASUREMENT_ID", "")
GA_HOST = "gtd.serbito.rs"
# Будильник напоминаний: Cloud Scheduler раз в минуту шлёт POST /tasks/reminders с этим секретом
CRON_SECRET = os.getenv("CRON_SECRET", "")
# Прод (https): Telegram сам шлёт апдейты вебхуком — инстанс спит, пока никто не пишет.
# Локально (http): long polling, чтобы бот работал без публичного адреса.
WEBHOOK = BASE_URL.startswith("https://")


def init_sentry() -> bool:
    """Ошибки — в Sentry (проект gtd). Только если задан DSN: локально и в тестах молчит.
    log.exception из фоновых циклов бота и напоминаний тоже уходит туда (logging-интеграция)."""
    if not SENTRY_DSN:
        return False
    sentry_sdk.init(dsn=SENTRY_DSN, release=os.getenv("SENTRY_RELEASE") or None,
                    environment="production" if os.getenv("K_SERVICE") else "development",
                    send_default_pii=False, traces_sample_rate=0.1,
                    # Данные пользователей в Sentry не уходят: ни локальные переменные кадров (там бывают
                    # названия задач), ни тела запросов; токен бота вычищается из всего, что осталось
                    include_local_variables=False, max_request_body_size="never",
                    before_send=sentry_scrub, before_send_transaction=sentry_scrub,
                    before_breadcrumb=lambda crumb, hint: None if crumb.get("category") in ("httpx", "httplib") else sentry_scrub(crumb, hint))
    return True


BOT_TOKEN_RE = re.compile(r"bot\d+:[A-Za-z0-9_-]{20,}")


def sentry_scrub(event, hint=None):
    """Рекурсивно заменяет токен бота на «bot<redacted>» во всех строках события."""
    def clean(v):
        if isinstance(v, str):
            return BOT_TOKEN_RE.sub("bot<redacted>", v)
        if isinstance(v, dict):
            return {k: clean(x) for k, x in v.items()}
        if isinstance(v, list):
            return [clean(x) for x in v]
        return v
    return clean(event)


init_sentry()
DATABASE_URL = os.getenv("DATABASE_URL", "")
COOKIE_SECURE = BASE_URL.startswith("https")

STATUSES = ("inbox", "next", "waiting", "someday", "reference", "done", "trash")

# ───────────────────────── DB ─────────────────────────
if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL не задан. Локально: postgresql://gtd:ПАРОЛЬ@127.0.0.1:5433/gtd "
        "(через cloud-sql-proxy). В Cloud Run: postgresql://gtd:ПАРОЛЬ@/gtd"
        "?host=/cloudsql/serbito:europe-west1:serbitodb"
    )

# prepare_threshold=None: без серверных prepared statements. С ними миграция схемы под работающим
# сервером (новая колонка в items → другой набор у select i.*) роняет запросы «cached plan must not
# change result type» (Sentry GTD-1, деплой добавляет колонки, пока старая ревизия ещё обслуживает)
_pool = ConnectionPool(DATABASE_URL, min_size=1, max_size=5,
                       kwargs={"row_factory": dict_row, "prepare_threshold": None}, open=True)

with _pool.connection() as _c:
    _c.execute(
        """
create table if not exists users(
  id bigserial primary key, tg_id bigint unique, name text, created bigint);
create table if not exists sessions(
  token text primary key, user_id bigint, created bigint);
create table if not exists login_tokens(
  token text primary key, user_id bigint, expires bigint);
create table if not exists projects(
  id bigserial primary key, user_id bigint, title text, status text default 'active', created bigint);
create table if not exists items(
  id bigserial primary key, user_id bigint, title text, notes text default '',
  status text default 'inbox', project_id bigint, context text,
  remind_at bigint, reminded integer default 0, source text default 'web',
  created bigint, completed_at bigint);
create index if not exists items_user_status on items(user_id, status);
alter table users add column if not exists email text;
alter table users add column if not exists google_sub text;
create unique index if not exists users_email on users(email);
create unique index if not exists users_google_sub on users(google_sub);
create table if not exists email_codes(
  email text primary key, code_hash text, expires bigint, attempts integer default 0, sent bigint);
create table if not exists tg_logins(
  nonce text primary key, link_user_id bigint, user_id bigint, status text default 'pending', expires bigint);
alter table tg_logins add column if not exists merge text;
create table if not exists merge_offers(
  token text primary key, keep_uid bigint, drop_uid bigint, field text, value text, expires bigint);
create table if not exists tg_email_links(
  tg_id bigint primary key, email text, expires bigint);
-- Свой номер задачи у каждого пользователя (#1, #2…) из счётчика users.item_seq: номера не
-- переиспользуются, поэтому ссылка /i/N никогда не откроет другую задачу. Бэкфилл — один раз.
alter table items add column if not exists num bigint;
alter table users add column if not exists item_seq bigint not null default 0;
update items i set num = x.n from (
  select id, coalesce((select max(num) from items m where m.user_id = t.user_id), 0)
             + row_number() over (partition by user_id order by id) n
  from items t where num is null) x
where i.id = x.id;
update users u set item_seq = coalesce((select max(num) from items i where i.user_id = u.id), 0)
where item_seq < coalesce((select max(num) from items i where i.user_id = u.id), 0);
create unique index if not exists items_user_num on items(user_id, num);
-- Настройки пользователя: сколько секунд живёт «Отменить» после действия с задачей
alter table users add column if not exists undo_seconds integer not null default 30;
-- Аналитика использования: только факт активности (кто/день/канал/сколько действий) — без содержимого
create table if not exists activity(
  user_id bigint not null, day date not null, channel text not null, actions integer not null default 0,
  primary key (user_id, day, channel));
"""
    )


def run(sql, args=()):
    """Выполняет запрос. Для insert ... returning id возвращает id, иначе None."""
    with _pool.connection() as conn:
        cur = conn.execute(sql, args)
        if cur.description:
            r = cur.fetchone()
            return next(iter(r.values())) if r else None
        return None


def rows(sql, args=()):
    with _pool.connection() as conn:
        return conn.execute(sql, args).fetchall()


def row(sql, args=()):
    r = rows(sql, args)
    return r[0] if r else None


_seen_today: set = set()


def track(uid, channel: str, n: int = 1):
    """Аналитика: пользователь uid был активен сегодня в канале web/telegram (+n действий).
    Пишем только факт и счётчик — ни названий задач, ни текста, ни IP. n=0 — просто заходил;
    такие отметки дедуплицируются в памяти, чтобы не писать в базу на каждый запрос."""
    if not uid:
        return
    day = datetime.now(TZ).date()
    if n == 0:
        if (uid, day, channel) in _seen_today:
            return
        _seen_today.add((uid, day, channel))
    run("insert into activity(user_id,day,channel,actions) values(%s,%s,%s,%s) on conflict(user_id,day,channel) "
        "do update set actions = activity.actions + excluded.actions", (uid, day, channel, n))


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
        # Месяц — две цифры: «24.10», «1.02»; иначе «версия 1.2» становится 1 февраля
        m = re.search(r"\b(\d{1,2})\.(\d{2})(?:\.(\d{4}))?\b", text)
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
    track(uid, "telegram" if source == "telegram" else "web")
    title, remind = parse_when(text, now)
    title = title or raw.strip()
    status = "next" if (ctx or proj_id) else "inbox"
    iid = run(
        # Номер — из счётчика пользователя, атомарно в одной команде (изменяющий подзапрос — только в WITH)
        "with seq as (update users set item_seq=item_seq+1 where id=%s returning item_seq) "
        "insert into items(user_id,num,title,status,project_id,context,remind_at,source,created) "
        "select %s, seq.item_seq, %s,%s,%s,%s,%s,%s,%s from seq returning id",
        (uid, uid, title, status, proj_id, ctx, remind, source, int(time.time())),
    )
    return item_get(uid, iid)


def project_by_title(uid: int, title: str) -> int:
    # Регистр сравниваем в Python: lower() в Postgres для кириллицы зависит от локали базы
    key = title.casefold()
    for r in rows("select id, title from projects where user_id=%s", (uid,)):
        if r["title"].casefold() == key:
            return r["id"]
    return run("insert into projects(user_id,title,created) values(%s,%s,%s) returning id", (uid, title, int(time.time())))


ITEM_SQL = "select i.*, p.title as project from items i left join projects p on p.id=i.project_id "


def item_get(uid, iid):
    return row(ITEM_SQL + "where i.user_id=%s and i.id=%s", (uid, iid))


def item_by_num(uid, num):
    """По номеру, который видит пользователь (#N в боте и в ссылке /i/N)."""
    return row(ITEM_SQL + "where i.user_id=%s and i.num=%s", (uid, num))


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
    u = row("select * from users where tg_id=%s", (tg_id,))
    if u:
        return u
    uid = run("insert into users(tg_id,name,created) values(%s,%s,%s) returning id", (tg_id, name, int(time.time())))
    return row("select * from users where id=%s", (uid,))


HELP = (
    "Просто пришли мысль — она попадёт в Inbox.\n\n"
    "Умный захват:\n"
    "• «Позвонить в банк завтра в 10:00» → напоминание\n"
    "• «через 2 часа проверить деплой»\n"
    "• «в пятницу отчёт #Клиент_X @работа» → сразу в Next, проект и контекст\n\n"
    "/inbox — что в инбоксе\n/next — следующие действия\n/done 12 — закрыть задачу №12\n"
    "/login — ссылка для входа в веб-интерфейс\n"
    "/email you@example.com — привязать почту: входить на сайте по коду или через Google"
)


BTN_START, BTN_EMAIL = "🚀 Начать", "📧 Добавить email"
# Кнопки — под приветствием (inline), а не постоянной клавиатурой: поле ввода остаётся свободным
KB_START = {"inline_keyboard": [[{"text": BTN_START, "callback_data": "help"},
                                 {"text": BTN_EMAIL, "callback_data": "addemail"}]]}
# Постоянную клавиатуру прежних версий Telegram убирает только ответом с remove_keyboard
KB_REMOVE = {"remove_keyboard": True}


def webhook_secret() -> str:
    """Секрет заголовка X-Telegram-Bot-Api-Secret-Token — выводим из токена, отдельный не нужен."""
    return hashlib.sha256(f"gtd-webhook:{TOKEN}".encode()).hexdigest()


# Ответы бота с кликабельным «#N» — ссылкой на задачу на сайте (открывается после входа)
HTML_MSG = {"parse_mode": "HTML", "link_preview_options": {"is_disabled": True}}


def item_url(num: int) -> str:
    return f"{BASE_URL}/i/{num}"


def item_ref(it: dict) -> str:
    return f'<a href="{item_url(it["num"])}">#{it["num"]}</a>'


def kb_item(iid, done_only=False):
    return {"inline_keyboard": [[
        {"text": "✅ Готово", "callback_data": f"done:{iid}"},
        {"text": "💤 +1ч", "callback_data": f"snz:{iid}"},
        {"text": "⏭ Next", "callback_data": f"next:{iid}"},
    ]]}


def tg_login_get(nonce):
    return row("select * from tg_logins where nonce=%s and expires>%s and status='pending'",
               (nonce, int(time.time())))


async def tg_login_prompt(chat, nonce):
    """/start <nonce> — пришли с кнопки «Войти через Telegram» на сайте. Просим подтвердить явно:
    иначе чужую ссылку можно подсунуть жертве и получить сессию в её аккаунт."""
    r = tg_login_get(nonce)
    if not r:
        await tg("sendMessage", chat_id=chat, text="Ссылка устарела — нажми кнопку на сайте ещё раз.")
        return
    what = "привязать этот Telegram к аккаунту GTD" if r["link_user_id"] else "войти в GTD в браузере"
    await tg("sendMessage", chat_id=chat,
             text=f"Подтвердить: {what}?\n\nЖми, только если сам только что нажал кнопку на сайте.",
             reply_markup={"inline_keyboard": [[{"text": "✅ Подтвердить", "callback_data": f"tgok:{nonce}"}]]})


async def tg_login_confirm(cb, nonce):
    frm, msg = cb["from"], cb.get("message", {})
    r = tg_login_get(nonce)
    if not r:
        note = "Ссылка устарела — нажми кнопку на сайте ещё раз"
    elif r["link_user_id"]:
        res = link_identity(r["link_user_id"], "tg_id", frm["id"])
        run("update tg_logins set status=%s, user_id=%s, merge=%s where nonce=%s",
            ("ok" if res["ok"] else "merge", r["link_user_id"], res.get("merge"), nonce))
        note = ("✅ Telegram привязан — вернись в браузер" if res["ok"]
                else "У этого Telegram уже есть свой аккаунт с задачами — подтверди объединение в браузере")
    else:
        u = tg_user(frm["id"], frm.get("first_name", ""))
        run("update tg_logins set status='ok', user_id=%s where nonce=%s", (u["id"], nonce))
        note = "✅ Вход подтверждён — вернись в браузер"
    await tg("answerCallbackQuery", callback_query_id=cb["id"], text=note)
    if msg:
        await tg("editMessageText", chat_id=msg["chat"]["id"], message_id=msg["message_id"], text=note)


async def tg_email_ask(chat, u, reply_markup=None):
    """Кнопка «Добавить email» или /email без адреса: ждём адрес следующим сообщением."""
    run("insert into tg_email_links(tg_id,email,expires) values(%s,null,%s) on conflict(tg_id) do update "
        "set email=null, expires=excluded.expires", (u["tg_id"], int(time.time()) + 600))
    now = f"Сейчас привязана {u['email']} — пришли другой адрес, чтобы сменить.\n\n" if u.get("email") else ""
    await tg("sendMessage", chat_id=chat, text=f"{now}Пришли адрес почты — вышлю на него код. С этой почтой "
             "можно будет входить на сайте по коду или через Google.", reply_markup=reply_markup)


async def tg_email_start(chat, u, email):
    """/email адрес — шлём код на почту; пришедшие потом 6 цифр сверяем в tg_email_verify."""
    if not email:
        await tg_email_ask(chat, u)
        return
    try:
        send_code(email, f"tg:{u['tg_id']}")
    except AuthError as e:
        await tg("sendMessage", chat_id=chat, text=e.detail)
        return
    run("insert into tg_email_links(tg_id,email,expires) values(%s,%s,%s) on conflict(tg_id) do update "
        "set email=excluded.email, expires=excluded.expires", (u["tg_id"], email, int(time.time()) + 600))
    await tg("sendMessage", chat_id=chat, text=f"Код отправлен на {email}. Пришли его сюда — 6 цифр.")


async def tg_email_verify(chat, u, email, code):
    try:
        check_code(email, code)
    except AuthError as e:
        await tg("sendMessage", chat_id=chat, text=e.detail)
        return
    run("delete from tg_email_links where tg_id=%s", (u["tg_id"],))
    res = link_identity(u["id"], "email", email)
    if res["ok"]:
        await tg("sendMessage", chat_id=chat, text=f"✅ Почта {email} привязана. На сайте можно входить "
                 f"по коду на неё или через Google с этим адресом: {BASE_URL}")
        return
    await tg("sendMessage", chat_id=chat, text=merge_text(res, f"Почта {email}"),
             reply_markup={"inline_keyboard": [[{"text": "🔗 Объединить", "callback_data": f"merge:{res['merge']}"},
                                                {"text": "Не сейчас", "callback_data": f"nomerge:{res['merge']}"}]]})


async def tg_merge_answer(cb, token, accept):
    u = row("select id from users where tg_id=%s", (cb["from"]["id"],))
    if not accept:
        run("delete from merge_offers where token=%s and keep_uid=%s", (token, u["id"] if u else None))
        note = "Ок, не объединяю. Передумаешь — снова /email"
    elif u and merge_apply(token, u["id"]):
        note = "✅ Аккаунты объединены"
    else:
        note = "Предложение устарело — начни заново с /email"
    await tg("answerCallbackQuery", callback_query_id=cb["id"], text=note)
    msg = cb.get("message", {})
    if msg:
        await tg("editMessageText", chat_id=msg["chat"]["id"], message_id=msg["message_id"], text=note)


async def handle_message(msg):
    chat = msg["chat"]["id"]
    frm = msg.get("from", {})
    text = (msg.get("text") or msg.get("caption") or "").strip()
    cmd, _, arg = text.partition(" ")
    cmd = cmd.split("@")[0].lower()
    if cmd == "/start" and arg.strip():
        await tg_login_prompt(chat, arg.strip())
        return
    u = tg_user(frm.get("id"), frm.get("first_name", ""))
    if text.startswith("/"):
        track(u["id"], "telegram")
    if not text:
        await tg("sendMessage", chat_id=chat, text="Пока понимаю только текст.")
        return
    pending = row("select email from tg_email_links where tg_id=%s and expires>%s", (u["tg_id"], int(time.time())))
    if pending and pending["email"] is None and not text.startswith("/") and text not in (BTN_START, BTN_EMAIL):
        if EMAIL_RE.match(text.lower()):  # ждали адрес после «Добавить email»
            await tg_email_start(chat, u, text.lower())
            return
        run("delete from tg_email_links where tg_id=%s", (u["tg_id"],))  # передумал — это обычная задача
    if pending and pending["email"] and re.fullmatch(r"\d{6}", text):  # код из письма, только если ждём его
        await tg_email_verify(chat, u, pending["email"], text)
        return
    if text == BTN_EMAIL:  # нажали кнопку старой постоянной клавиатуры — отвечаем и убираем её
        await tg_email_ask(chat, u, KB_REMOVE)
    elif text == BTN_START:
        await tg("sendMessage", chat_id=chat, text="GTD-бот готов.\n\n" + HELP, reply_markup=KB_REMOVE)
    elif cmd in ("/start", "/help"):
        await tg("sendMessage", chat_id=chat, text="GTD-бот готов.\n\n" + HELP, reply_markup=KB_START)
    elif cmd == "/login":
        tok = secrets.token_urlsafe(24)
        run("insert into login_tokens(token,user_id,expires) values(%s,%s,%s)", (tok, u["id"], int(time.time()) + 600))
        await tg("sendMessage", chat_id=chat, text=f"Вход (10 минут, одноразовая):\n{BASE_URL}/auth?t={tok}")
    elif cmd == "/email":
        await tg_email_start(chat, u, arg.strip().lower())
    elif cmd in ("/inbox", "/next"):
        st = cmd[1:]
        its = rows(ITEM_SQL + "where i.user_id=%s and i.status=%s order by i.created limit 20", (u["id"], st))
        body = "\n".join(f"{item_ref(i)} {html.escape(i['title'])} {html.escape(describe(i))}".strip()
                         for i in its) or "Пусто 🎉"
        await tg("sendMessage", chat_id=chat, text=f"{st.upper()}:\n{body}", **HTML_MSG)
    elif cmd == "/done":
        try:
            iid = int(arg.strip().lstrip("#"))
        except ValueError:
            await tg("sendMessage", chat_id=chat, text="Использование: /done 12")
            return
        it = mark_done(u["id"], num=iid)
        if it:
            await tg("sendMessage", chat_id=chat, text=f"✅ Готово: {item_ref(it)} {html.escape(it['title'])}", **HTML_MSG)
        else:
            await tg("sendMessage", chat_id=chat, text="Не нашёл такую задачу")
    elif text.startswith("/"):
        await tg("sendMessage", chat_id=chat, text=HELP)
    else:
        it = capture(u["id"], text, "telegram")
        where = "Next" if it["status"] == "next" else "Inbox"
        await tg("sendMessage", chat_id=chat,
                 text=f"✓ {where} {item_ref(it)}: {html.escape(it['title'])}\n{html.escape(describe(it))}".strip(),
                 reply_markup=kb_item(it["id"]), **HTML_MSG)


def mark_done(uid, iid=None, num=None):
    """Закрывает задачу по id (кнопки) или по номеру (/done N); возвращает её или None."""
    it = item_get(uid, iid) if iid is not None else item_by_num(uid, num)
    if not it:
        return None
    run("update items set status='done', completed_at=%s where id=%s", (int(time.time()), it["id"]))
    return it


async def handle_callback(cb):
    act, _, sid = cb["data"].partition(":")
    if act == "tgok":
        await tg_login_confirm(cb, sid)
        return
    if act in ("merge", "nomerge"):
        await tg_merge_answer(cb, sid, act == "merge")
        return
    if act in ("help", "addemail"):  # кнопки под приветствием
        chat = (cb.get("message") or {}).get("chat", {}).get("id", cb["from"]["id"])
        await tg("answerCallbackQuery", callback_query_id=cb["id"])
        if act == "help":
            await tg("sendMessage", chat_id=chat, text=HELP)
        else:
            await tg_email_ask(chat, tg_user(cb["from"]["id"], cb["from"].get("first_name", "")))
        return
    u = row("select * from users where tg_id=%s", (cb["from"]["id"],))
    if not u:
        return
    track(u["id"], "telegram")
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
        run("update items set remind_at=%s, reminded=0 where id=%s", (int(time.time()) + 3600, iid))
        note = "💤 Напомню через час"
    elif act == "next":
        run("update items set status='next' where id=%s", (iid,))
        note = "⏭ В Next"
    else:
        return
    await tg("answerCallbackQuery", callback_query_id=cb["id"], text=note)
    if msg:
        await tg("editMessageText", chat_id=msg["chat"]["id"], message_id=msg["message_id"],
                 text=f"{note}: {it['title']}")


async def dispatch(up: dict):
    try:
        if "message" in up:
            await handle_message(up["message"])
        elif "callback_query" in up:
            await handle_callback(up["callback_query"])
    except Exception:  # noqa: BLE001
        log.exception("update failed")


async def bot_setup():
    """На старте: имя бота, меню команд и (в проде) вебхук. Идемпотентно — на каждом холодном старте."""
    global BOT_USERNAME
    me = await tg("getMe")
    BOT_USERNAME = (me or {}).get("username", "")
    await tg("setMyCommands", commands=[
        {"command": "inbox", "description": "Инбокс"}, {"command": "next", "description": "Следующие действия"},
        {"command": "done", "description": "Закрыть задачу: /done 12"},
        {"command": "login", "description": "Ссылка для входа в веб"},
        {"command": "email", "description": "Привязать почту"},
        {"command": "help", "description": "Помощь"}])
    if WEBHOOK:
        await tg("setWebhook", url=f"{BASE_URL}/tg/webhook", secret_token=webhook_secret(),
                 allowed_updates=["message", "callback_query"])


async def poll_loop():
    """Только локально. Если у бота уже вебхук прода — не трогаем его, иначе увели бы апдейты."""
    info = await tg("getWebhookInfo")
    if (info or {}).get("url"):
        log.warning("у бота настроен вебхук %s — локальный polling не запускаю", info["url"])
        return
    offset = 0
    while True:
        ups = await tg("getUpdates", offset=offset, timeout=30, allowed_updates=["message", "callback_query"])
        if ups is None:
            await asyncio.sleep(5)
            continue
        for up in ups:
            offset = up["update_id"] + 1
            await dispatch(up)


async def send_due_reminders():
    due = rows("select i.*, u.tg_id from items i join users u on u.id=i.user_id "
               "where i.remind_at is not null and i.reminded=0 and i.remind_at<=%s "
               "and i.status not in ('done','trash')", (int(time.time()),))
    sent = 0
    for it in due:
        # Атомарно «забираем» напоминание: два параллельных запуска не пришлют его дважды
        if run("update items set reminded=1 where id=%s and reminded=0 returning id", (it["id"],)) is None:
            continue
        if TOKEN and it["tg_id"]:
            await tg("sendMessage", chat_id=it["tg_id"], text=f"⏰ {item_ref(it)} {html.escape(it['title'])}",
                     reply_markup=kb_item(it["id"]), **HTML_MSG)
            sent += 1
    return sent


async def reminder_loop():
    while True:
        await asyncio.sleep(15)
        try:
            await send_due_reminders()
        except Exception:  # noqa: BLE001
            log.exception("reminder loop")


@asynccontextmanager
async def lifespan(app):
    global _client
    _client = httpx.AsyncClient()
    tasks = []
    if TOKEN:
        try:
            await asyncio.wait_for(bot_setup(), 15)
        except TimeoutError:
            log.warning("Telegram не ответил на старте — бот поднимется со следующим холодным стартом")
        if not WEBHOOK:
            tasks.append(asyncio.create_task(poll_loop()))
    else:
        log.warning("TELEGRAM_BOT_TOKEN не задан — бот выключен")
    if not WEBHOOK:  # в проде напоминания будит Cloud Scheduler через /tasks/reminders
        tasks.append(asyncio.create_task(reminder_loop()))
    yield
    for t in tasks:
        t.cancel()
    await _client.aclose()
    _pool.close()


# ───────────────────────── Web / API ─────────────────────────
app = FastAPI(lifespan=lifespan)


def session_user(request: Request) -> int | None:
    sid = request.cookies.get("sid")
    r = row("select user_id from sessions where token=%s", (sid,)) if sid else None
    return r["user_id"] if r else None


def current_user(request: Request) -> int:
    uid = session_user(request)
    if not uid:
        raise HTTPException(401, "auth required")
    track(uid, "web", 0)
    return uid


def set_session(resp, uid: int):
    tok = secrets.token_urlsafe(32)
    run("insert into sessions(token,user_id,created) values(%s,%s,%s)", (tok, uid, int(time.time())))
    resp.set_cookie("sid", tok, max_age=60 * 60 * 24 * 90, httponly=True, samesite="lax", secure=COOKIE_SECURE)
    return resp


def start_session(uid: int) -> RedirectResponse:
    return set_session(RedirectResponse("/", status_code=303), uid)


# ───────────────────────── Аккаунты ─────────────────────────
# Один аккаунт = одна строка users; способы входа — её колонки tg_id / email / google_sub.
# Google и код на почту с одинаковым адресом попадают в один аккаунт; Telegram привязывается из «Аккаунта».
IDENTITIES = ("tg_id", "email", "google_sub")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_hits: dict[str, list[float]] = {}


def rate_ok(key: str, limit: int, window: int) -> bool:
    """Скользящее окно в памяти инстанса — от массовой рассылки кодов, не от целевой атаки."""
    now = time.time()
    hits = [t for t in _hits.get(key, []) if t > now - window]
    _hits[key] = hits
    if len(hits) >= limit:
        return False
    hits.append(now)
    return True


def client_ip(request: Request) -> str:
    return request.headers.get("x-forwarded-for", request.client.host if request.client else "").split(",")[0].strip()


class AuthError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status, self.detail = status, detail


def has_data(uid: int) -> bool:
    return row("select exists(select 1 from items where user_id=%s) "
               "or exists(select 1 from projects where user_id=%s) b", (uid, uid))["b"]


def attach(uid: int, field: str, value) -> int | None:
    """Привязывает способ входа к аккаунту uid. Если он уже у другого аккаунта: пустой (без задач
    и проектов) — забираем, опустевший без способов входа удаляем; с данными — ничего не меняем
    и возвращаем его id, чтобы вызывающий предложил объединить аккаунты."""
    assert field in IDENTITIES
    other = row(f"select * from users where {field}=%s", (value,))
    if other and other["id"] != uid:
        if has_data(other["id"]):
            return other["id"]
        run(f"update users set {field}=null where id=%s", (other["id"],))
        if not any(other[f] for f in IDENTITIES if f != field):
            run("delete from sessions where user_id=%s", (other["id"],))
            run("delete from login_tokens where user_id=%s", (other["id"],))
            run("delete from users where id=%s", (other["id"],))
    run(f"update users set {field}=%s where id=%s", (value, uid))
    return None


def merge_accounts(keep: int, drop: int):
    """Всё из drop переезжает в keep, drop удаляется — одной транзакцией. Одноимённые проекты
    склеиваются; сессии drop продолжают работать уже в keep; способы входа, которых у keep нет,
    он получает от drop."""
    with _pool.connection() as c, c.transaction():
        k = c.execute("select * from users where id=%s for update", (keep,)).fetchone()
        d = c.execute("select * from users where id=%s for update", (drop,)).fetchone()
        if not k or not d or keep == drop:
            raise AuthError(400, "Аккаунт для объединения не найден")
        mine = {p["title"].casefold(): p["id"]
                for p in c.execute("select id, title from projects where user_id=%s", (keep,)).fetchall()}
        for p in c.execute("select id, title from projects where user_id=%s", (drop,)).fetchall():
            same = mine.get(p["title"].casefold())
            if same:
                c.execute("update items set project_id=%s where project_id=%s", (same, p["id"]))
                c.execute("delete from projects where id=%s", (p["id"],))
            else:
                c.execute("update projects set user_id=%s where id=%s", (keep, p["id"]))
        # Задачи drop получают следующие номера keep — иначе #N двух аккаунтов столкнулись бы.
        # Сперва уводим в минус: уникальность (user_id, num) проверяется построчно, прямо в процессе
        c.execute("update items set num = -num where user_id=%s", (drop,))
        c.execute("update items i set num = k.item_seq + x.rn from users k, (select id, row_number() over "
                  "(order by id) rn from items where user_id=%s) x where k.id=%s and i.id=x.id",
                  (drop, keep))
        c.execute("update users set item_seq = item_seq + (select count(*) from items where user_id=%s) "
                  "where id=%s", (drop, keep))
        for table in ("items", "sessions", "login_tokens"):
            c.execute(f"update {table} set user_id=%s where user_id=%s", (keep, drop))
        c.execute("update tg_logins set link_user_id=%s where link_user_id=%s", (keep, drop))
        moved = {f: d[f] for f in IDENTITIES if d[f] and not k[f]}
        c.execute("delete from merge_offers where keep_uid=%s or drop_uid=%s", (drop, drop))
        c.execute("delete from users where id=%s", (drop,))  # освобождает уникальные tg_id/email/google_sub
        for f, v in moved.items():
            c.execute(f"update users set {f}=%s where id=%s", (v, keep))
    log.info("merged account %s into %s", drop, keep)


def account_stats(uid: int) -> dict:
    return row("select (select count(*) from items where user_id=%s and status<>'trash') items, "
               "(select count(*) from projects where user_id=%s and status='active') projects", (uid, uid))


def link_identity(uid: int, field: str, value) -> dict:
    """Привязать способ входа к uid. {"ok": True} — готово; иначе — предложение объединить аккаунты.
    Если свой аккаунт ещё пустой и ничего не теряется, просто переезжаем в тот, где уже есть данные."""
    other = attach(uid, field, value)
    if other is None:
        return {"ok": True}
    me_, them = row("select * from users where id=%s", (uid,)), row("select * from users where id=%s", (other,))
    if not has_data(uid) and not any(me_[f] and them[f] for f in IDENTITIES):
        merge_accounts(other, uid)
        return {"ok": True}
    tok = secrets.token_urlsafe(16)
    now = int(time.time())
    run("delete from merge_offers where expires<%s", (now,))
    run("insert into merge_offers(token,keep_uid,drop_uid,field,value,expires) values(%s,%s,%s,%s,%s,%s)",
        (tok, uid, other, field, str(value), now + 600))
    return {"ok": False, "merge": tok, **account_stats(other)}


def merge_apply(token: str, keep_uid: int) -> bool:
    """Подтверждение объединения. Подтверждает только тот, кто его начал (keep)."""
    o = row("select * from merge_offers where token=%s and expires>%s", (token, int(time.time())))
    if not o or o["keep_uid"] != keep_uid:
        return False
    run("delete from merge_offers where token=%s", (token,))
    merge_accounts(o["keep_uid"], o["drop_uid"])
    value = int(o["value"]) if o["field"] == "tg_id" else o["value"]
    run(f"update users set {o['field']}=%s where id=%s", (value, o["keep_uid"]))  # тот способ, ради которого всё
    return True


def merge_text(offer: dict, what: str) -> str:
    return (f"{what} уже у другого аккаунта: задач — {offer['items']}, проектов — {offer['projects']}. "
            "Объединить его с этим? Всё окажется в одном аккаунте, и войти можно будет любым способом.")


def email_user(email: str) -> int:
    u = row("select id from users where email=%s", (email,))
    if u:
        return u["id"]
    return run("insert into users(email,name,created) values(%s,%s,%s) returning id",
               (email, email.split("@")[0], int(time.time())))


def google_user(sub: str, email: str, name: str) -> int:
    u = row("select id from users where google_sub=%s", (sub,)) or row("select id from users where email=%s", (email,))
    if u:
        run("update users set google_sub=%s where id=%s", (sub, u["id"]))
        return u["id"]
    return run("insert into users(google_sub,email,name,created) values(%s,%s,%s,%s) returning id",
               (sub, email, name or email.split("@")[0], int(time.time())))


def google_verify(credential: str) -> dict | None:
    """Проверка ID-токена Sign in with Google: подпись и срок проверяет tokeninfo, остальное — мы."""
    try:
        r = httpx.get("https://oauth2.googleapis.com/tokeninfo", params={"id_token": credential}, timeout=10)
    except httpx.HTTPError as e:
        log.warning("google tokeninfo failed: %s", e)
        return None
    d = r.json() if r.status_code == 200 else {}
    if (d.get("aud") != GOOGLE_CLIENT_ID or d.get("iss") not in ("accounts.google.com", "https://accounts.google.com")
            or str(d.get("email_verified")).lower() != "true" or not d.get("email")):
        return None
    return d


def code_hash(email: str, code: str) -> str:
    return hashlib.sha256(f"{email}:{code}".encode()).hexdigest()


def send_email(to: str, subject: str, body: str):
    m = EmailMessage()
    m["From"], m["To"], m["Subject"] = EMAIL_FROM, to, subject
    m.set_content(body)
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as s:
        s.starttls()
        s.login(SMTP_USER, SMTP_PASSWORD)
        s.send_message(m)


def send_code(email: str, rate_key: str):
    """Одноразовый код на почту. Кулдаун минута на адрес, 5 писем за 10 минут на rate_key."""
    if len(email) > 254 or not EMAIL_RE.match(email):
        raise AuthError(400, "Неверный адрес почты")
    if not (SMTP_PASSWORD or DEV):
        raise AuthError(400, "Вход по почте не настроен")
    now = int(time.time())
    prev = row("select sent from email_codes where email=%s", (email,))
    if prev and prev["sent"] > now - 60:
        raise AuthError(429, "Код уже отправлен — новый можно запросить через минуту")
    if not rate_ok(rate_key, 5, 600):
        raise AuthError(429, "Слишком много запросов — попробуй через 10 минут")
    code = f"{secrets.randbelow(10 ** 6):06d}"
    run("delete from email_codes where expires<%s", (now,))
    run("insert into email_codes(email,code_hash,expires,attempts,sent) values(%s,%s,%s,0,%s) "
        "on conflict(email) do update set code_hash=excluded.code_hash, expires=excluded.expires, "
        "attempts=0, sent=excluded.sent", (email, code_hash(email, code), now + 600, now))
    if not SMTP_PASSWORD:
        log.warning("DEV: код входа для %s — %s", email, code)
        return
    try:
        send_email(email, f"Код входа в GTD: {code}",
                   f"Код входа в GTD: {code}\n\nДействует 10 минут. Если ты не входил — просто проигнорируй письмо.")
    except (smtplib.SMTPException, OSError) as e:
        log.warning("smtp to %s failed: %s", email, e)
        run("delete from email_codes where email=%s", (email,))
        raise AuthError(502, "Не удалось отправить письмо — попробуй позже")


def check_code(email: str, code: str):
    """Сверяет код; 5 неверных попыток — и код сгорает. Верный код одноразовый."""
    r = row("select * from email_codes where email=%s and expires>%s", (email, int(time.time())))
    if not r or r["attempts"] >= 5:
        raise AuthError(400, "Код устарел — запроси новый")
    if not hmac.compare_digest(r["code_hash"], code_hash(email, code)):
        run("update email_codes set attempts=attempts+1 where email=%s", (email,))
        raise AuthError(400, "Неверный код")
    run("delete from email_codes where email=%s", (email,))


def link_response(res: dict, what: str):
    if res["ok"]:
        return {"ok": True}
    return JSONResponse({**res, "detail": merge_text(res, what)}, status_code=409)


@app.post("/api/auth/google")
def auth_google(body: dict, request: Request):
    if not GOOGLE_CLIENT_ID:
        raise HTTPException(400, "Вход через Google не настроен")
    d = google_verify(body.get("credential") or "")
    if not d:
        raise HTTPException(400, "Google не подтвердил вход — попробуй ещё раз")
    email = d["email"].lower()
    uid = session_user(request) if body.get("link") else None
    if uid:
        res = link_identity(uid, "google_sub", d["sub"])
        if res["ok"]:
            uid = session_user(request)  # после переезда сессия уже в другом аккаунте
            if not row("select email from users where id=%s", (uid,))["email"]:
                attach(uid, "email", email)
        return link_response(res, "Этот Google-аккаунт")
    return set_session(JSONResponse({"ok": True}), google_user(d["sub"], email, d.get("name", "")))


@app.post("/api/auth/email/start")
def auth_email_start(body: dict, request: Request):
    try:
        send_code((body.get("email") or "").strip().lower(), "ip:" + client_ip(request))
    except AuthError as e:
        raise HTTPException(e.status, e.detail)
    return {"ok": True}


@app.post("/api/auth/email/verify")
def auth_email_verify(body: dict, request: Request):
    email = (body.get("email") or "").strip().lower()
    try:
        check_code(email, (body.get("code") or "").strip())
    except AuthError as e:
        raise HTTPException(e.status, e.detail)
    uid = session_user(request) if body.get("link") else None
    if uid:
        return link_response(link_identity(uid, "email", email), "Эта почта")
    return set_session(JSONResponse({"ok": True}), email_user(email))


@app.post("/api/auth/merge")
def auth_merge(body: dict, uid: int = Depends(current_user)):
    try:
        ok = merge_apply(body.get("token") or "", uid)
    except AuthError as e:
        raise HTTPException(e.status, e.detail)
    if not ok:
        raise HTTPException(400, "Предложение устарело — привяжи способ входа ещё раз")
    return {"ok": True}


@app.post("/api/auth/tg/start")
def auth_tg_start(body: dict, request: Request):
    if not (TOKEN and BOT_USERNAME):
        raise HTTPException(400, "Telegram-бот выключен")
    nonce = secrets.token_urlsafe(16)
    now = int(time.time())
    run("delete from tg_logins where expires<%s", (now,))
    link_uid = session_user(request) if body.get("link") else None
    run("insert into tg_logins(nonce,link_user_id,expires) values(%s,%s,%s)", (nonce, link_uid, now + 600))
    return {"nonce": nonce, "url": f"https://t.me/{BOT_USERNAME}?start={nonce}"}


@app.get("/api/auth/tg/poll")
def auth_tg_poll(nonce: str):
    r = row("select * from tg_logins where nonce=%s", (nonce,))
    if not r or r["expires"] < time.time():
        return {"status": "expired"}
    if r["status"] == "pending":
        return {"status": "pending"}
    run("delete from tg_logins where nonce=%s", (nonce,))
    if r["status"] == "merge":
        o = row("select drop_uid from merge_offers where token=%s", (r["merge"],))
        if not o:
            return {"status": "expired"}
        stats = account_stats(o["drop_uid"])
        return {"status": "merge", "merge": r["merge"], **stats, "detail": merge_text(stats, "Этот Telegram")}
    if r["status"] != "ok" or r["link_user_id"]:
        return {"status": r["status"]}
    return set_session(JSONResponse({"status": "ok"}), r["user_id"])


UNDO_CHOICES = (5, 10, 30)


@app.get("/api/me")
def me(uid: int = Depends(current_user)):
    u = row("select name, email, tg_id, google_sub, undo_seconds from users where id=%s", (uid,))
    return {"name": u["name"], "email": u["email"], "tg": bool(u["tg_id"]), "google": bool(u["google_sub"]),
            "undo_seconds": u["undo_seconds"], "admin": uid in ADMIN_USER_IDS}


@app.get("/api/admin/stats")
def admin_stats(uid: int = Depends(current_user)):
    """Сколько людей пользуется GTD. Только агрегаты: ни названий задач, ни почт, ни имён, ни id.
    Не владельцу — 404, будто эндпоинта нет."""
    if uid not in ADMIN_USER_IDS:
        raise HTTPException(404)
    today = datetime.now(TZ).date()
    one = lambda sql, *a: row(sql, a)["n"]
    active = lambda days: one("select count(distinct user_id) n from activity where day > %s",
                              today - timedelta(days=days))
    since = lambda days: int((datetime.now(TZ) - timedelta(days=days)).timestamp())
    daily = {r["day"]: r for r in rows(
        "select day, count(distinct user_id) users, sum(actions) actions from activity where day > %s group by day",
        (today - timedelta(days=30),))}
    return {
        "users": {"total": one("select count(*) n from users"),
                  "new_7d": one("select count(*) n from users where created >= %s", since(7)),
                  "new_30d": one("select count(*) n from users where created >= %s", since(30)),
                  "with_email": one("select count(*) n from users where email is not null"),
                  "with_google": one("select count(*) n from users where google_sub is not null"),
                  "with_telegram": one("select count(*) n from users where tg_id is not null and tg_id <> 0")},
        "active": {"day": active(1), "week": active(7), "month": active(30),
                   "web_30d": one("select count(distinct user_id) n from activity where day > %s and channel='web'",
                                  today - timedelta(days=30)),
                   "telegram_30d": one("select count(distinct user_id) n from activity where day > %s "
                                       "and channel='telegram'", today - timedelta(days=30))},
        "tasks": {"total": one("select count(*) n from items"),
                  "created_7d": one("select count(*) n from items where created >= %s", since(7)),
                  "created_30d": one("select count(*) n from items where created >= %s", since(30))},
        "daily": [{"day": str(d), "users": daily[d]["users"] if d in daily else 0,
                   "actions": int(daily[d]["actions"]) if d in daily else 0}
                  for d in (today - timedelta(days=i) for i in range(29, -1, -1))],
    }


@app.patch("/api/me")
def patch_me(body: dict, uid: int = Depends(current_user)):
    """Настройки пользователя. Пока одна: время на «Отменить» — 5, 10 или 30 секунд."""
    if "undo_seconds" in body:
        if body["undo_seconds"] not in UNDO_CHOICES:
            raise HTTPException(400, "undo_seconds: 5, 10 или 30")
        run("update users set undo_seconds=%s where id=%s", (body["undo_seconds"], uid))
    return me(uid)


@app.get("/auth")
def auth(t: str):
    r = row("select * from login_tokens where token=%s and expires>%s", (t, int(time.time())))
    if not r:
        return JSONResponse({"error": "Ссылка устарела. Отправь /login боту ещё раз."}, status_code=400)
    run("delete from login_tokens where token=%s", (t,))
    return start_session(r["user_id"])


@app.get("/dev-login")
def dev_login():
    if not DEV:
        raise HTTPException(404)
    u = row("select id from users limit 1")
    uid = u["id"] if u else run("insert into users(tg_id,name,created) values(0,'dev',%s) returning id", (int(time.time()),))
    return start_session(uid)


@app.get("/api/config")
def config(request: Request):
    # user — чтобы фронт сразу знал, показывать ли вход, без заведомого 401 на /api/counts
    return {"bot": BOT_USERNAME if TOKEN else "", "dev": DEV, "google": GOOGLE_CLIENT_ID,
            "email": bool(SMTP_PASSWORD or DEV), "user": bool(session_user(request))}


@app.post("/tg/webhook")
async def tg_webhook(request: Request):
    """Апдейты от Telegram. Проверяем секретный заголовок, который задали в setWebhook."""
    got = request.headers.get("x-telegram-bot-api-secret-token", "")
    if not TOKEN or not hmac.compare_digest(got, webhook_secret()):
        raise HTTPException(403)
    await dispatch(await request.json())
    return {"ok": True}


@app.post("/tasks/reminders")
async def cron_reminders(request: Request):
    """Будильник от Cloud Scheduler (раз в минуту): рассылает наступившие напоминания."""
    got = request.headers.get("x-cron-secret", "")
    if not CRON_SECRET or not hmac.compare_digest(got, CRON_SECRET):
        raise HTTPException(403)
    return {"sent": await send_due_reminders()}


STARTED = str(time.time())


@app.get("/api/dev/version")
def dev_version():
    """Только локально (DEV): версия кода для live reload. Меняется, когда сервер перезапустился
    после правки .py (--reload) или поменялся файл в static/ — страница сама обновится."""
    if not DEV:
        raise HTTPException(404)
    static = os.path.join(os.path.dirname(__file__), "static")
    newest = max(os.path.getmtime(os.path.join(static, f)) for f in os.listdir(static))
    return {"v": f"{STARTED}:{newest}"}


@app.get("/api/contexts")
def list_contexts(uid: int = Depends(current_user)):
    """Контексты пользователя для подсказок @ — самые частые первыми."""
    return [r["context"] for r in rows(
        "select context, count(*) n from items where user_id=%s and context is not null and status<>'trash' "
        "group by context order by n desc, context", (uid,))]


@app.post("/api/logout")
def logout(request: Request):
    run("delete from sessions where token=%s", (request.cookies.get("sid", ""),))
    return {"ok": True}


@app.get("/api/counts")
def counts(uid: int = Depends(current_user)):
    c = {r["status"]: r["n"] for r in rows(
        "select status, count(*) n from items where user_id=%s group by status", (uid,))}
    c["scheduled"] = row("select count(*) n from items where user_id=%s and remind_at is not null "
                         "and status not in ('done','trash')", (uid,))["n"]
    c["projects"] = row("select count(*) n from projects where user_id=%s and status='active'", (uid,))["n"]
    return c


@app.get("/api/items")
def list_items(status: str = "inbox", project_id: int | None = None, uid: int = Depends(current_user)):
    where, args = "i.user_id=%s", [uid]
    if status == "scheduled":
        where += " and i.remind_at is not null and i.status not in ('done','trash')"
        order = "i.remind_at"
    else:
        order = "i.completed_at desc" if status == "done" else "i.created"
        if status != "all":
            where += " and i.status=%s"
            args.append(status)
    if project_id:
        where += " and i.project_id=%s"
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
    track(uid, "web")
    sets, args = [], []
    for k in ("title", "notes", "status", "project_id", "context", "remind_at"):
        if k not in body:
            continue
        v = body[k]
        if k == "status":
            if v not in STATUSES:
                raise HTTPException(400, "bad status")
            sets.append("completed_at=%s")
            args.append(int(time.time()) if v == "done" else None)
        if k == "remind_at":
            sets.append("reminded=0")
        if k == "context" and v:
            v = str(v).lstrip("@").lower()
        sets.append(f"{k}=%s")
        args.append(v or None if k in ("context", "project_id", "remind_at") else v)
    if sets:
        run(f"update items set {', '.join(sets)} where id=%s and user_id=%s", (*args, iid, uid))
    return item_get(uid, iid)


@app.delete("/api/items/{iid}")
def delete_item(iid: int, uid: int = Depends(current_user)):
    track(uid, "web")
    run("delete from items where id=%s and user_id=%s", (iid, uid))
    return {"ok": True}


@app.get("/api/projects")
def list_projects(uid: int = Depends(current_user)):
    return rows(
        "select p.*, "
        "(select count(*) from items i where i.project_id=p.id and i.status in ('inbox','next','waiting')) open_count, "
        "(select count(*) from items i where i.project_id=p.id and i.status='next') next_count, "
        "(select count(*) from items i where i.project_id=p.id) total_count "
        "from projects p where p.user_id=%s and p.status='active' order by p.title", (uid,))


@app.post("/api/projects")
def create_project(body: dict, uid: int = Depends(current_user)):
    title = (body.get("title") or "").strip()
    if not title:
        raise HTTPException(400, "empty")
    return {"id": project_by_title(uid, title)}


def project_get(uid: int, pid: int) -> dict:
    p = row("select * from projects where id=%s and user_id=%s", (pid, uid))
    if not p:
        raise HTTPException(404)
    return p


@app.patch("/api/projects/{pid}")
def patch_project(pid: int, body: dict, uid: int = Depends(current_user)):
    project_get(uid, pid)
    if "title" in body:
        title = (body.get("title") or "").strip()
        if not title:
            raise HTTPException(400, "Название не может быть пустым")
        if any(r["id"] != pid and r["title"].casefold() == title.casefold()
               for r in rows("select id, title from projects where user_id=%s", (uid,))):
            raise HTTPException(409, f"Проект «{title}» уже есть")
        run("update projects set title=%s where id=%s and user_id=%s", (title, pid, uid))
    if "status" in body:
        if body["status"] not in ("active", "done"):
            raise HTTPException(400, "bad status")
        run("update projects set status=%s where id=%s and user_id=%s", (body["status"], pid, uid))
    return project_get(uid, pid)


@app.delete("/api/projects/{pid}")
def delete_project(pid: int, items: str = "keep", uid: int = Depends(current_user)):
    """items=keep — задачи остаются, просто без проекта; items=delete — удаляются вместе с проектом."""
    if items not in ("keep", "delete"):
        raise HTTPException(400, "items: keep или delete")
    project_get(uid, pid)
    with _pool.connection() as c, c.transaction():
        if items == "delete":
            n = c.execute("delete from items where project_id=%s and user_id=%s", (pid, uid)).rowcount
        else:
            n = c.execute("update items set project_id=null where project_id=%s and user_id=%s", (pid, uid)).rowcount
        c.execute("delete from projects where id=%s and user_id=%s", (pid, uid))
    return {"ok": True, "items": items, "affected": n}


@app.get("/api/items/n/{num}")
def get_item_by_num(num: int, uid: int = Depends(current_user)):
    it = item_by_num(uid, num)  # номера у каждого свои: чужую задачу по ссылке не открыть
    if not it:
        raise HTTPException(404, "Задача не найдена")
    return it


GA_SNIPPET = """<script async src="https://www.googletagmanager.com/gtag/js?id={id}"></script>
<script>
  window.dataLayer = window.dataLayer || [];
  function gtag(){{ dataLayer.push(arguments); }}
  gtag("js", new Date());
  // Страницы шлёт фронт сам (gaPage): только раздел вида /inbox и заголовок «GTD — раздел».
  // Ни названий задач, ни их номеров из /i/N, ни user_id — поэтому свой page_view выключен.
  gtag("config", "{id}", {{ send_page_view: false }});
</script>"""


STATIC = os.path.join(os.path.dirname(__file__), "static")


def ga_snippet(request: Request) -> str:
    return GA_SNIPPET.format(id=GA_ID) if GA_ID and request.url.hostname == GA_HOST else ""


def app_page(request: Request, lang: str = "ru", landing: bool = False) -> HTMLResponse:
    """Страница приложения. GA-сниппет — статично в HTML (чтобы Google видел тег), только на боевом домене.
    landing — гостю вместо пустого экрана входа: SEO-теги и текст лендинга прямо в HTML (pages.py)."""
    with open(os.path.join(STATIC, "index.html"), encoding="utf-8") as f:
        page = f.read().replace("<!--GA-->", ga_snippet(request))
    if landing:
        page = (page.replace('<html lang="ru">', f'<html lang="{lang}">')
                .replace("<title>GTD</title>", pages.head(BASE_URL, lang, "home"))
                .replace("<!--LANDING-->", pages.landing(lang)))
    return HTMLResponse(page)


@app.get("/")
def index(request: Request):
    # Вошёл — сразу приложение, как раньше; гость и поисковик видят лендинг
    return app_page(request, landing=not session_user(request))


@app.get("/en/")
def index_en(request: Request):
    return app_page(request, "en", landing=not session_user(request))


@app.get("/about")
def about_page(request: Request):
    return HTMLResponse(pages.about(BASE_URL, "ru", ga_snippet(request)))


@app.get("/en/about")
def about_page_en(request: Request):
    return HTMLResponse(pages.about(BASE_URL, "en", ga_snippet(request)))


@app.get("/robots.txt")
def robots_txt():
    return PlainTextResponse(pages.robots(BASE_URL))


@app.get("/sitemap.xml")
def sitemap_xml():
    return Response(pages.sitemap(BASE_URL), media_type="application/xml")


@app.get("/og.png")
@app.get("/og-en.png")
def og_image(request: Request):
    """Картинка превью ссылки (1200×630): og.png — русская, og-en.png — английская."""
    return FileResponse(os.path.join(STATIC, request.url.path.lstrip("/")), media_type="image/png",
                        headers={"Cache-Control": "public, max-age=86400"})


@app.get("/i/{num}")
def item_page(num: int, request: Request):
    """Ссылка на задачу: та же страница, фронт откроет карточку после входа. Данные — только через API.
    Страница личная и без входа пустая — поисковикам не индексировать."""
    r = app_page(request)
    r.headers["X-Robots-Tag"] = "noindex"
    return r
