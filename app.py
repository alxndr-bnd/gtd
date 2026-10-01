"""GTD for free — веб-UI + Telegram-бот (захват задач и напоминания)."""
import asyncio
import gzip
import hashlib
import hmac
import html
import ipaddress
import json
import logging
import os
import re
import secrets
import smtplib
import sys
import time
from email.message import EmailMessage
from contextlib import asynccontextmanager
from datetime import date, datetime, time as dtime, timedelta
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

import httpx
import psycopg
import sentry_sdk
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from starlette.datastructures import Headers, MutableHeaders
from starlette.exceptions import HTTPException as StarletteHTTPException

import pages

log = logging.getLogger("gtd")
# Одноразовые токены и nonce в адресе (/auth?t=…) — в журнал запросов uvicorn только без значений (SERBITO-360, GTD-10)
SECRET_PARAMS_RE = re.compile(r"([?&](?:t|nonce|token|code|credential)=)[^&#\s\"']+")


class ScrubQuery(logging.Filter):
    """Фильтр журнала запросов: значения секретных параметров URL → [Filtered]. Строка uvicorn.access —
    '%s - "%s %s HTTP/%s" %d', путь с query string — среди args."""
    def filter(self, record):
        if isinstance(record.args, tuple):
            record.args = tuple(SECRET_PARAMS_RE.sub(r"\1[Filtered]", a) if isinstance(a, str) else a
                                for a in record.args)
        elif isinstance(record.msg, str):
            record.msg = SECRET_PARAMS_RE.sub(r"\1[Filtered]", record.msg)
        return True


# Cloud Logging понимает JSON-строку в stdout: severity — уровень записи, message — текст (SERBITO-336).
# Без этого всё, что пишет приложение, приходит без уровня, и ни фильтр по ошибкам, ни алерт не работают
SEVERITY = {"DEBUG": "DEBUG", "INFO": "INFO", "WARNING": "WARNING", "ERROR": "ERROR", "CRITICAL": "CRITICAL"}


class CloudJson(logging.Formatter):
    def format(self, record):
        msg = record.getMessage()
        if record.exc_info:  # трейсбек — в том же message: так ошибку подхватывает Error Reporting
            msg += "\n" + self.formatException(record.exc_info)
        return json.dumps({"severity": SEVERITY.get(record.levelname, "DEFAULT"), "message": msg,
                           "logger": record.name}, ensure_ascii=False)


def setup_logging() -> bool:
    """JSON с severity в stdout — в Cloud Run (K_SERVICE задаёт сама платформа) или при LOG_FORMAT=json;
    локально — обычные читаемые строки. uvicorn настраивает свои логгеры до импорта приложения — перенастраиваем
    и их, чтобы журнал запросов и ошибки сервера тоже шли с уровнем. Возвращает, включён ли JSON."""
    json_logs = os.getenv("LOG_FORMAT", "json" if os.getenv("K_SERVICE") else "text") == "json"
    if json_logs:
        h = logging.StreamHandler(sys.stdout)
        h.setFormatter(CloudJson())
        logging.getLogger().handlers[:] = [h]
        logging.getLogger().setLevel(logging.INFO)
        for name in ("uvicorn", "uvicorn.access"):
            lg = logging.getLogger(name)
            lg.handlers[:], lg.propagate = [h], False
    else:
        logging.basicConfig(level=logging.INFO)
    access = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, ScrubQuery) for f in access.filters):
        access.addFilter(ScrubQuery())
    # httpx на INFO пишет полный URL каждого запроса, а в URL Telegram API зашит токен бота — в логи он не должен попадать
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    return json_logs


setup_logging()

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
BASE_URL = os.getenv("BASE_URL", "https://gtd.serbito.rs").rstrip("/")
TZ = ZoneInfo(os.getenv("TZ", "Europe/Belgrade"))
# DEV=1 — вход без пароля (/dev-login) и коды входа в логе. На публичном инстансе это вход в чужой аккаунт, поэтому
# при https-адресе или в Cloud Run он не включается, даже если переменную передали (SERBITO-360, GTD-12)
DEV = os.getenv("DEV", "") == "1" and not BASE_URL.startswith("https://") and not os.getenv("K_SERVICE")
if os.getenv("DEV", "") == "1" and not DEV:
    log.warning("DEV=1 игнорируется: инстанс публичный (https BASE_URL или Cloud Run)")
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
# Значения параметров в URL: в них одноразовые токены входа (/auth?t=…), nonce и т.п. (SERBITO-346, GTD-2)
QUERY_RE = re.compile(r"([?&][^=&#\s\"'?]+=)[^&#\s\"']+")
# Какие заголовки запроса можно отдавать в Sentry. Остальные — нет: cookie с сессией, секрет вебхука Telegram
# (X-Telegram-Bot-Api-Secret-Token), секрет крона (X-Cron-Secret), адреса клиента (XFF, CF-Connecting-IP)
SENTRY_HEADERS = {"host", "user-agent", "accept", "accept-language", "content-type", "content-length",
                  "origin", "referer"}
FILTERED = "[Filtered]"


def sentry_scrub(event, hint=None):
    """Чистит событие, транзакцию или breadcrumb перед отправкой в Sentry: токен бота → «bot<redacted>»,
    значения параметров URL и сами секреты вебхука и крона → [Filtered] во всех строках; у запроса — только
    безопасные заголовки, без cookie и query string."""
    known = [x for x in (CRON_SECRET, TOKEN and webhook_secret()) if x]

    def clean(v):
        if isinstance(v, str):
            v = QUERY_RE.sub(lambda m: m.group(1) + FILTERED, BOT_TOKEN_RE.sub("bot<redacted>", v))
            for x in known:
                v = v.replace(x, FILTERED)
            return v
        if isinstance(v, dict):
            return {k: clean(x) for k, x in v.items()}
        if isinstance(v, (list, tuple)):
            return [clean(x) for x in v]
        return v
    event = clean(event)
    req = event.get("request") if isinstance(event, dict) else None
    if isinstance(req, dict):
        for k in ("cookies", "env", "data"):
            req.pop(k, None)
        if req.get("query_string"):
            qs = req["query_string"]
            req["query_string"] = "&".join(p.split("=", 1)[0] + "=" + FILTERED for p in qs.split("&") if p) \
                if isinstance(qs, str) else FILTERED
        if isinstance(req.get("headers"), dict):
            req["headers"] = {k: v for k, v in req["headers"].items() if k.lower() in SENTRY_HEADERS}
    return event


init_sentry()
DATABASE_URL = os.getenv("DATABASE_URL", "")
COOKIE_SECURE = BASE_URL.startswith("https")

STATUSES = ("inbox", "next", "waiting", "someday", "reference", "done", "trash")
DONE_MODES = ("hide", "list", "section")  # как проект показывает выполненные задачи (SERBITO-327)

# ───────────────────────── DB ─────────────────────────
if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL не задан. Локально: своя база, postgresql://localhost/gtd "
        "(cp .env.example .env; uvicorn … --env-file .env). Docker Compose задаёт его сам; "
        "прод — docs/deploy-gcp.md"
    )

# prepare_threshold=None: без серверных prepared statements. С ними миграция схемы под работающим
# сервером (новая колонка в items → другой набор у select i.*) роняет запросы «cached plan must not
# change result type» (Sentry GTD-1, деплой добавляет колонки, пока старая ревизия ещё обслуживает)
# check: перед выдачей пул проверяет соединение и заменяет мёртвое. Cloud SQL (через сокет /cloudsql) закрывает
# простаивающие соединения, а пул отдавал такое запросу — 500 у Cloud Scheduler на /tasks/reminders (Sentry GTD-3).
# max_idle/max_lifetime: простоявшие 5 минут и прожившие 30 пул закрывает сам, не дожидаясь обрыва снаружи
_pool = ConnectionPool(DATABASE_URL, min_size=1, max_size=5,
                       kwargs={"row_factory": dict_row, "prepare_threshold": None},
                       check=ConnectionPool.check_connection, max_idle=300, max_lifetime=1800, open=True)

# Схема и миграции: идемпотентны, выполняются при каждом старте
SCHEMA = """
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
-- Чек-лист первого запуска на сайте больше не показывать: закрыл крестиком или выполнил все пункты
alter table users add column if not exists checklist_hidden boolean not null default false;
-- Аналитика использования: только факт активности (кто/день/канал/сколько действий) — без содержимого
create table if not exists activity(
  user_id bigint not null, day date not null, channel text not null, actions integer not null default 0,
  primary key (user_id, day, channel));
-- Язык интерфейса (SERBITO-259): null — авто (сайт — по браузеру, бот — по Telegram), иначе 'ru' или 'en'
alter table users add column if not exists lang text;
-- language_code из последнего апдейта Telegram: на нём «авто»-пользователю приходят напоминания (в них апдейта нет)
alter table users add column if not exists tg_lang text;
-- Выполненные задачи в проекте (SERBITO-327): hide — скрыть, list — в общем списке, section — отдельным блоком.
-- Существующие проекты получают list — так они выглядели до этого; новые по умолчанию скрывают выполненные
alter table projects add column if not exists done_mode text not null default 'list';
alter table projects alter column done_mode set default 'hide';
-- Ручной порядок (SERBITO-328): position — место задачи в её списке (status), ppos — в её проекте; меньше — выше.
-- Дробные числа с зазором 1024: перестановка меняет одну строку (середина между соседями). Бэкфилл сохраняет
-- прежний порядок — по времени создания, старые сверху; новые задачи дальше встают в конец (max + 1024)
alter table items add column if not exists position double precision;
alter table items add column if not exists ppos double precision;
update items i set position = x.p from (
  select id, coalesce((select max(position) from items m where m.user_id = t.user_id), 0)
             + 1024 * row_number() over (partition by user_id order by created, id) p
  from items t where position is null) x
where i.id = x.id;
update items i set ppos = x.p from (
  select id, coalesce((select max(ppos) from items m where m.user_id = t.user_id), 0)
             + 1024 * row_number() over (partition by user_id order by created, id) p
  from items t where ppos is null) x
where i.id = x.id;
create index if not exists items_user_status_pos on items(user_id, status, position);
-- Лимиты входа по коду (SERBITO-346): n событий (писем, неверных кодов, проверок) по ключу за окно с момента
-- since. В базе, а не в памяти инстанса: общие для всех инстансов Cloud Run и переживают перезапуск
create table if not exists auth_limits(key text primary key, n integer not null, since bigint not null);
-- Сессии (SERBITO-360, GTD-5): в базе только sha256 от cookie — утёкший дамп не даёт войти; seen — последняя
-- активность, сессия живёт SESSION_TTL с неё (скользящий срок). Старая таблица sessions хранила сами токены:
-- переносим их хешами и очищаем её. Саму таблицу не удаляем, пока ревизия до 0.20 может ещё работать при
-- выкатке; то, что она успеет записать, переедет при следующем старте
create table if not exists user_sessions(
  token_hash text primary key, user_id bigint not null, created bigint not null, seen bigint not null);
create index if not exists user_sessions_user on user_sessions(user_id);
with moved as (delete from sessions returning token, user_id, created)
insert into user_sessions(token_hash, user_id, created, seen)
  select encode(sha256(convert_to(token, 'UTF8')), 'hex'), user_id, coalesce(created, 0),
         extract(epoch from now())::bigint from moved where user_id is not null
on conflict do nothing;
-- Вход через бота по ссылке (GTD-3): число для сверки, откуда запрос (IP, браузер — показываем в Telegram)
-- и хеш cookie браузера, начавшего вход: забрать сессию может только он. Строка живёт 10 минут
alter table tg_logins add column if not exists code integer;
alter table tg_logins add column if not exists ip text;
alter table tg_logins add column if not exists ua text;
alter table tg_logins add column if not exists browser text;
"""

with _pool.connection() as _c:
    _c.execute(SCHEMA)


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


# ───────────────────────── Язык ─────────────────────────
# Правило выбора языка — pages.lang_of / pages.pick_lang (одно на сайт, бот, письма, 404 и манифест)
LANGS, RU_LANGS = pages.LANGS, pages.RU_LANGS


def req_lang(request: Request, uid: int | None = None) -> str:
    """Язык ответа сервера: явная настройка пользователя > Accept-Language (SPA шлёт в нём выбранный язык)."""
    uid = uid or session_user(request)
    u = row("select lang from users where id=%s", (uid,)) if uid else None
    return (u and u["lang"]) or pages.pick_lang(request.headers.get("accept-language"))


def bot_lang(u, code=None) -> str:
    """Язык бота: явная настройка > language_code этого апдейта > последний известный из Telegram > русский."""
    if u and u.get("lang"):
        return u["lang"]
    return pages.lang_of(code or (u or {}).get("tg_lang"))


# Тексты бота, писем и ошибок API, которые видит пользователь. Ключи в обоих языках одинаковые (тест)
TEXTS = {
    "ru": {
        "help": "Просто пришли мысль — она попадёт в Inbox.\n\n"
                "Умный захват:\n"
                "• «Позвонить в банк завтра в 10:00» → напоминание\n"
                "• «через 2 часа проверить деплой»\n"
                "• «в пятницу отчёт #Клиент_X @работа» → сразу в Next, проект и контекст\n\n"
                "/inbox — что в инбоксе\n/next — следующие действия\n/done 12 — закрыть задачу №12\n"
                "/login — ссылка для входа в веб-интерфейс\n"
                "/email you@example.com — привязать почту: входить на сайте по коду или через Google\n"
                "/about — что такое GTD",
        "ready": "GTD-бот готов.\n\n",
        "start": "Пришли любую мысль — она попадёт во Входящие, а разберёшь потом.\n\n"
                 "• «позвонить маме завтра в 10:00» → напомню\n"
                 "• «отчёт #Работа @комп» → сразу в проект и контекст\n\n"
                 "Все команды — /help",
        "about": "GTD (Getting Things Done) — метод Дэвида Аллена из его книги «Getting Things Done» "
                 "(по-русски — «Как привести дела в порядок»). Голова — для идей, а не для хранения: всё, что требует "
                 "внимания, сразу записываешь во Входящие, а потом решаешь, что это и какой следующий конкретный шаг. "
                 "Шаги ложатся в списки — Next, Waiting, проекты, Someday, — и раз в неделю ты их пересматриваешь, "
                 "так что ничего не теряется.\n\nПодробнее: {url}/about",
        "first_task": "Готово! Можно добавить срок — «завтра в 10:00», — а разобрать всё удобнее на сайте: {url}",
        "btn_email": "📧 Добавить email", "btn_about": "ℹ️ Как это работает", "btn_site": "🌐 Открыть сайт",
        "btn_done": "✅ Готово", "btn_snooze": "💤 +1ч", "btn_next": "⏭ Next",
        "btn_confirm": "✅ Подтвердить", "btn_merge": "🔗 Объединить", "btn_nomerge": "Не сейчас",
        "text_only": "Пока понимаю только текст.",
        "empty": "Пусто 🎉",
        "done_usage": "Использование: /done 12",
        "done_ok": "✅ Готово: {ref} {title}",
        "not_found_task": "Не нашёл такую задачу",
        "login_link": "Вход (10 минут, одноразовая):\n{url}",
        "cb_done": "✅ Готово", "cb_snooze": "💤 Напомню через час", "cb_next": "⏭ В Next", "cb_missing": "Не найдено",
        "tg_link_stale": "Ссылка устарела — нажми кнопку на сайте ещё раз",
        "tg_confirm_link": "привязать этот Telegram к аккаунту GTD {who}",
        "tg_confirm_login": "войти в GTD в браузере",
        "tg_confirm": "Подтвердить: {what}?\n\nЗапрос из браузера: {device}, IP {ip}.{merge}\n\nНажми число, которое "
                      "показано на сайте. Если ты сам сейчас не нажимал кнопку на сайте — ничего не нажимай: "
                      "это чужая попытка войти в твой аккаунт.",
        "tg_merge_warn": "\n\nУ этого Telegram уже есть свой аккаунт с задачами ({items}). После подтверждения сайт "
                         "предложит объединить аккаунты — тот, кто в этом браузере, увидит твои задачи.",
        "tg_wrong_code": "Число не совпало — вход отменён. Нажми кнопку на сайте ещё раз",
        "device_unknown": "неизвестный браузер",
        "tg_linked": "✅ Telegram привязан — вернись в браузер",
        "tg_link_merge": "У этого Telegram уже есть свой аккаунт с задачами — подтверди объединение в браузере",
        "tg_login_ok": "✅ Вход подтверждён — вернись в браузер",
        "email_now": "Сейчас привязана {email} — пришли другой адрес, чтобы сменить.\n\n",
        "email_ask": "Пришли адрес почты — вышлю на него код. С этой почтой можно будет входить на сайте "
                     "по коду или через Google.",
        "email_sent": "Код отправлен на {email}. Пришли его сюда — 6 цифр.",
        "email_linked": "✅ Почта {email} привязана. На сайте можно входить по коду на неё или через Google "
                        "с этим адресом: {url}",
        "merge_no": "Ок, не объединяю. Передумаешь — снова /email",
        "merge_ok": "✅ Аккаунты объединены",
        "merge_stale_bot": "Предложение устарело — начни заново с /email",
        "merge_offer": "{what} уже у другого аккаунта: задач — {items}, проектов — {projects}. Объединить его "
                       "с этим? Всё окажется в одном аккаунте, и войти можно будет любым способом.",
        "what_email_addr": "Почта {email}", "what_email": "Эта почта", "what_google": "Этот Google-аккаунт",
        "what_tg": "Этот Telegram",
        # Ошибки API и входа
        "bad_email": "Неверный адрес почты",
        "email_off": "Вход по почте не настроен",
        "code_cooldown": "Код уже отправлен — новый можно запросить через минуту",
        "too_many": "Слишком много запросов — попробуй через 10 минут",
        "smtp_fail": "Не удалось отправить письмо — попробуй позже",
        "code_expired": "Код устарел — запроси новый",
        "code_wrong": "Неверный код",
        "too_many_day": "Слишком много писем с кодом за сутки — попробуй завтра или войди другим способом",
        "code_locked": "Слишком много неверных кодов — вход по коду на этот адрес закрыт на сутки. "
                       "Войди другим способом или попробуй завтра",
        "email_busy": "Вход по почте сейчас недоступен — войди другим способом или попробуй позже",
        "merge_missing": "Аккаунт для объединения не найден",
        "merge_stale": "Предложение устарело — привяжи способ входа ещё раз",
        "google_off": "Вход через Google не настроен",
        "google_fail": "Google не подтвердил вход — попробуй ещё раз",
        "tg_off": "Telegram-бот выключен",
        "tg_widget_fail": "Telegram не подтвердил вход — попробуй ещё раз",
        "tg_widget_stale": "Вход через Telegram устарел — нажми кнопку ещё раз",
        "project_empty": "Название не может быть пустым",
        "project_exists": "Проект «{title}» уже есть",
        "task_missing": "Задача не найдена",
        # Письмо с кодом
        "mail_subject": "Код входа в GTD: {code}",
        "mail_body": "Код входа в GTD: {code}\n\nДействует 10 минут. Если ты не входил — просто проигнорируй письмо.",
    },
    "en": {
        "help": "Just send me a thought — it lands in your Inbox.\n\n"
                "Smart capture:\n"
                "• “Call the bank tomorrow at 10am” → a reminder\n"
                "• “in 2 hours check the deploy”\n"
                "• “report on friday #Client_X @work” → straight to Next, with a project and a context\n\n"
                "/inbox — what's in your Inbox\n/next — next actions\n/done 12 — complete task #12\n"
                "/login — a sign-in link for the website\n"
                "/email you@example.com — link an email to sign in on the site with a code or Google\n"
                "/about — what GTD is",
        "ready": "The GTD bot is ready.\n\n",
        "start": "Send me any thought — it lands in your Inbox, and you sort it out later.\n\n"
                 "• “call mom tomorrow at 10:00” → I'll remind you\n"
                 "• “report #Work @computer” → straight into a project and a context\n\n"
                 "All commands — /help",
        "about": "GTD (Getting Things Done) is David Allen's method from his book “Getting Things Done”. "
                 "Your head is for having ideas, not holding them: capture everything that needs attention into "
                 "the Inbox right away, then decide what it is and what the next concrete step is. "
                 "Steps go into lists — Next, Waiting, projects, Someday — and once a week you review them, "
                 "so nothing slips through.\n\nMore: {url}/en/about",
        "first_task": "Got it! You can add a time — “tomorrow at 10:00” — and sorting everything out is easier "
                      "on the website: {url}/en/",
        "btn_email": "📧 Add email", "btn_about": "ℹ️ How it works", "btn_site": "🌐 Open the website",
        "btn_done": "✅ Done", "btn_snooze": "💤 +1h", "btn_next": "⏭ Next",
        "btn_confirm": "✅ Confirm", "btn_merge": "🔗 Merge", "btn_nomerge": "Not now",
        "text_only": "I only understand text for now.",
        "empty": "Empty 🎉",
        "done_usage": "Usage: /done 12",
        "done_ok": "✅ Done: {ref} {title}",
        "not_found_task": "Couldn't find that task",
        "login_link": "Sign-in link (one-time, valid for 10 minutes):\n{url}",
        "cb_done": "✅ Done", "cb_snooze": "💤 I'll remind you in an hour", "cb_next": "⏭ Moved to Next",
        "cb_missing": "Not found",
        "tg_link_stale": "This link has expired — press the button on the website again",
        "tg_confirm_link": "link this Telegram to the GTD account {who}",
        "tg_confirm_login": "sign in to GTD in your browser",
        "tg_confirm": "Confirm: {what}?\n\nRequested from: {device}, IP {ip}.{merge}\n\nTap the number shown on the "
                      "website. If you didn't just press the button on the website yourself, don't tap anything: "
                      "someone else is trying to get into your account.",
        "tg_merge_warn": "\n\nThis Telegram already has its own account with tasks ({items}). After you confirm, the "
                         "website will offer to merge the accounts — whoever uses that browser will see your tasks.",
        "tg_wrong_code": "The number didn't match — sign-in cancelled. Press the button on the website again",
        "device_unknown": "unknown browser",
        "tg_linked": "✅ Telegram linked — go back to your browser",
        "tg_link_merge": "This Telegram already has its own account with tasks — confirm the merge in your browser",
        "tg_login_ok": "✅ Sign-in confirmed — go back to your browser",
        "email_now": "{email} is linked now — send another address to change it.\n\n",
        "email_ask": "Send me your email address and I'll email you a code. With this email you can sign in on "
                     "the website with a code or with Google.",
        "email_sent": "Code sent to {email}. Send it here — 6 digits.",
        "email_linked": "✅ {email} is linked. On the website you can sign in with a code sent to it or with Google "
                        "using this address: {url}/en/",
        "merge_no": "OK, not merging. Changed your mind? Send /email again",
        "merge_ok": "✅ Accounts merged",
        "merge_stale_bot": "This offer has expired — start again with /email",
        "merge_offer": "{what} already belongs to another account (tasks: {items}, projects: {projects}). Merge it "
                       "with this one? Everything ends up in one account, and any sign-in method will open it.",
        "what_email_addr": "The email {email}", "what_email": "This email", "what_google": "This Google account",
        "what_tg": "This Telegram",
        "bad_email": "Invalid email address",
        "email_off": "Email sign-in isn't set up",
        "code_cooldown": "A code has already been sent — you can request a new one in a minute",
        "too_many": "Too many requests — try again in 10 minutes",
        "smtp_fail": "Couldn't send the email — try again later",
        "code_expired": "The code has expired — request a new one",
        "code_wrong": "Wrong code",
        "too_many_day": "Too many sign-in codes requested today — try again tomorrow or sign in another way",
        "code_locked": "Too many wrong codes — signing in with a code to this address is locked for 24 hours. "
                       "Sign in another way or try again tomorrow",
        "email_busy": "Email sign-in is temporarily unavailable — sign in another way or try again later",
        "merge_missing": "The account to merge wasn't found",
        "merge_stale": "This offer has expired — link the sign-in method again",
        "google_off": "Google sign-in isn't set up",
        "google_fail": "Google didn't confirm the sign-in — try again",
        "tg_off": "The Telegram bot is off",
        "tg_widget_fail": "Telegram didn't confirm the sign-in — try again",
        "tg_widget_stale": "The Telegram sign-in has expired — press the button again",
        "project_empty": "The name can't be empty",
        "project_exists": "Project “{title}” already exists",
        "task_missing": "Task not found",
        "mail_subject": "Your GTD sign-in code: {code}",
        "mail_body": "Your GTD sign-in code: {code}\n\nIt's valid for 10 minutes. If you didn't try to sign in, "
                     "just ignore this email.",
    },
}


def tr(lang: str, key: str, **kw) -> str:
    return TEXTS[lang if lang in TEXTS else "ru"][key].format(**kw)


# ───────────────────────── Парсинг захвата ─────────────────────────
WEEKDAYS = {"понедельник": 0, "вторник": 1, "сред": 2, "четверг": 3, "пятниц": 4,
            "суббот": 5, "воскресень": 6, "monday": 0, "tuesday": 1, "wednesday": 2,
            "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6}
# «next monday», «this friday» — тот же ближайший день недели, что и «on monday»
WD_RE = (r"(?:\b(?:в|во|на|on)\s+)?(?:\b(?:next|this)\s+)?\b(понедельник|вторник|сред[ауы]|четверг|пятниц[ауы]|"
         r"суббот[ауы]|воскресенье|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b")
MONTHS_EN = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
MON_RE = (r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sept?(?:ember)?|"
          r"oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?")
ON = r"(?:\bon\s+)?"  # «pay rent on 24 oct», «on 2026-10-24»: предлог уходит вместе с датой


def parse_when(text: str, now: datetime):
    """Возвращает (очищенный_текст, epoch|None). Понимает рус/англ: «через 2 часа» / «in 2 hours»,
    «завтра в 10:00» / «tomorrow 10am», «в пятницу» / «on friday», «next monday», «24.10 12:00» / «24 oct 12:00»,
    «2026-10-24», «в 15:30» / «at 3pm», «day after tomorrow».

    Дата через точку — всегда с месяцем из двух цифр. С двузначным днём («24.10», «01.09») или годом
    («5.10.2026») — всегда дата. С однозначным днём («1.10») — только если рядом признак даты: начало
    текста («1.02 оплата»), предлог перед ней («к», «до», «на», «в», «с», «по», «by», «on», «until»,
    «till») или время сразу после («1.10 12:00», «1.10 в 12:00»). Иначе это число: «версия 1.05»,
    «курс 1.10» напоминаний не ставят."""
    found = False

    def cut(m):
        nonlocal text, found
        text = text[: m.start()] + " " + text[m.end():]
        found = True

    m = re.search(r"\b(?:через|in)\s+(\d+)\s*(мин\w*|min\w*|час\w*|ч|hour\w*|hr\w*|h|дн\w*|день|day\w*|недел\w*|week\w*)\b",
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

    hm = tm = None
    # 12-часовое время: «10am», «at 3 pm», «9:30pm»; 12am — полночь, 12pm — полдень
    m = re.search(r"(?:\bat\s+)?\b(\d{1,2})(?::(\d{2}))?\s*([ap])\.?m\b\.?", text, re.I)
    if m and 1 <= int(m.group(1)) <= 12 and int(m.group(2) or 0) < 60:
        hm, tm = (int(m.group(1)) % 12 + (12 if m.group(3).lower() == "p" else 0), int(m.group(2) or 0)), m
    m = None if hm else re.search(r"(?:\b(?:в|at|к)\s+)?\b(\d{1,2}):(\d{2})\b", text, re.I)
    if m and int(m.group(1)) < 24 and int(m.group(2)) < 60:
        hm, tm = (int(m.group(1)), int(m.group(2))), m
    timed_dm = None  # позиция «1.10» прямо перед временем: текст до вырезанного времени не сдвигается
    if tm:
        p = re.search(r"\b\d\.\d{2}\s+$", text[: tm.start()])
        timed_dm = p and p.start()
        cut(tm)

    base, explicit_dm = None, False
    m = re.search(ON + r"\b(\d{4})-(\d{2})-(\d{2})\b", text)
    if m:
        try:
            base = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            cut(m)
        except ValueError:
            pass
    if base is None:
        # Месяц — две цифры, однозначный день — только с признаком даты (правило — в docstring)
        m = next((m for m in re.finditer(r"\b(\d{1,2})\.(\d{2})(?:\.(\d{4}))?\b", text)
                  if len(m.group(1)) == 2 or m.group(3) or m.start() == timed_dm
                  or re.fullmatch(r"(?s)\s*|.*\b(?:к|до|на|в|с|по|by|on|until|till)\s+", text[: m.start()], re.I)),
                 None)
        if m:
            try:
                y = int(m.group(3)) if m.group(3) else now.year
                base = date(y, int(m.group(2)), int(m.group(1)))
                explicit_dm = not m.group(3)
                cut(m)
            except ValueError:
                pass
    if base is None:
        # Английский месяц словом: «24 oct», «24th of October», «oct 24», «October 24, 2027»
        m = (re.search(ON + r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?" + MON_RE + r"(?:,?\s+(\d{4}))?(?!\w)", text, re.I)
             or re.search(ON + r"\b" + MON_RE + r"\s+(\d{1,2})(?:st|nd|rd|th)?\b(?:,?\s+(\d{4})\b)?", text, re.I))
        if m:
            g = m.groups()
            day, mon = (g[0], g[1]) if g[0].isdigit() else (g[1], g[0])
            try:
                base = date(int(g[2]) if g[2] else now.year, MONTHS_EN.index(mon[:3].lower()) + 1, int(day))
                explicit_dm = not g[2]
                cut(m)
            except ValueError:
                pass
    if base is None:
        m = re.search(r"\b(послезавтра|завтра|сегодня|today|day after tomorrow|tomorrow)\b", text, re.I)
        if m:
            k = m.group(1).lower()
            off = 2 if k in ("послезавтра", "day after tomorrow") else 1 if k in ("завтра", "tomorrow") else 0
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


def parse_task(uid: int, raw: str) -> dict:
    """Разбор строки задачи: текст [@контекст] [#проект] [когда] -> {title, context, project_id, remind_at}.
    Один разбор на поле захвата (capture) и заголовок в карточке (PATCH /api/items, SERBITO-298).
    #Проект_Имя — проект пользователя по названию (без учёта регистра, «_» — пробел), нет — создаётся.
    Токены вырезаются из заголовка; остались одни токены — заголовок остаётся как был введён."""
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
    title, remind = parse_when(text, datetime.now(TZ))
    return {"title": title or raw.strip(), "context": ctx, "project_id": proj_id, "remind_at": remind}


# Позиция «в конец» (SERBITO-328): ниже самой нижней задачи списка (status) или проекта — как было до ручного
# порядка, когда списки шли по времени создания. Параметры: user_id, значение
END_POS = "(select coalesce(max(position), 0) + 1024 from items where user_id=%s and status=%s)"
END_PPOS = "(select coalesce(max(ppos), 0) + 1024 from items where user_id=%s and project_id=%s)"


def capture(uid: int, raw: str, source: str = "web") -> dict:
    """Умный захват: текст [@контекст] [#проект] [когда] -> задача."""
    track(uid, "telegram" if source == "telegram" else "web")
    p = parse_task(uid, raw)
    title, ctx, proj_id, remind = p["title"], p["context"], p["project_id"], p["remind_at"]
    status = "next" if (ctx or proj_id) else "inbox"
    iid = run(
        # Номер — из счётчика пользователя, атомарно в одной команде (изменяющий подзапрос — только в WITH)
        # Новая задача — в конец своего списка и своего проекта
        "with seq as (update users set item_seq=item_seq+1 where id=%s returning item_seq) "
        "insert into items(user_id,num,title,status,project_id,context,remind_at,source,created,position,ppos) "
        f"select %s, seq.item_seq, %s,%s,%s,%s,%s,%s,%s,{END_POS},{END_PPOS} from seq returning id",
        (uid, uid, title, status, proj_id, ctx, remind, source, int(time.time()), uid, status, uid, proj_id),
    )
    return item_get(uid, iid)


def project_by_title(uid: int, title: str) -> int:
    # Регистр сравниваем в Python: lower() в Postgres для кириллицы зависит от локали базы
    key = title.casefold()
    for r in rows("select id, title from projects where user_id=%s", (uid,)):
        if r["title"].casefold() == key:
            return r["id"]
    return run("insert into projects(user_id,title,created) values(%s,%s,%s) returning id", (uid, title, int(time.time())))


# Проект — только свой: user_id в join, чтобы чужой project_id не раскрыл чужое название
ITEM_SQL = ("select i.*, p.title as project from items i "
            "left join projects p on p.id=i.project_id and p.user_id=i.user_id ")


def item_get(uid, iid):
    return row(ITEM_SQL + "where i.user_id=%s and i.id=%s", (uid, iid))


def item_by_num(uid, num):
    """По номеру, который видит пользователь (#N в боте и в ссылке /i/N)."""
    return row(ITEM_SQL + "where i.user_id=%s and i.num=%s", (uid, num))


def fmt_ts(ts, lang="ru"):
    """«25.09 14:00» / «25 Sep 14:00» — месяц словом, чтобы не путать день и месяц; strftime("%b") зависит от локали."""
    d = datetime.fromtimestamp(ts, TZ)
    if lang == "en":
        return f"{d.day} {MONTHS_EN[d.month - 1].title()} {d:%H:%M}"
    return d.strftime("%d.%m %H:%M")


def describe(it, lang="ru") -> str:
    bits = []
    if it.get("remind_at"):
        bits.append("⏰ " + fmt_ts(it["remind_at"], lang))
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
        # Текст ошибок httpx содержит URL запроса, а в нём токен — в логи только без него
        log.warning("tg %s failed: %s", method, BOT_TOKEN_RE.sub("bot<redacted>", str(e)))
        return None


def tg_user(tg_id: int, name: str, code=None):
    """Пользователь по Telegram (новый — создаём). code — language_code апдейта: запоминаем для напоминаний."""
    u = row("select * from users where tg_id=%s", (tg_id,))
    if not u:
        uid = run("insert into users(tg_id,name,created) values(%s,%s,%s) returning id", (tg_id, name, int(time.time())))
        u = row("select * from users where id=%s", (uid,))
    if code and u["tg_lang"] != code:
        run("update users set tg_lang=%s where id=%s", (code, u["id"]))
        u["tg_lang"] = code
    return u


def tg_known(frm: dict):
    """Уже известный боту пользователь (без создания) и язык ответа ему."""
    u = row("select * from users where tg_id=%s", (frm.get("id"),))
    return u, bot_lang(u, frm.get("language_code"))


# ── Профиль бота (SERBITO-257, 259): описание до Start, короткое (профиль, репосты) и меню команд.
# Язык текста → (описание ≤512, короткое ≤120). Telegram показывает вариант по language_code клиента, а ""
# — всем прочим; поэтому "" — английский, а языки, которым по правилу pages.lang_of положен русский, — явно
BOT_PROFILE = {
    "ru": ("Записывай задачи и мысли в один тап — разберёшь потом. Работает по методу GTD Дэвида Аллена: "
           "Входящие, следующие действия, проекты, напоминания. Всё синхронизируется с сайтом gtd.serbito.rs. "
           "Бесплатно.",
           "GTD в Telegram: записывай задачи в один тап, напоминания и проекты. gtd.serbito.rs"),
    "en": ("Capture tasks and ideas in one tap — sort them out later. Built on David Allen's GTD method: "
           "Inbox, next actions, projects, reminders. Everything syncs with the website gtd.serbito.rs. Free.",
           "GTD in Telegram: capture tasks in one tap, reminders and projects. gtd.serbito.rs"),
}
BOT_COMMANDS = {
    "ru": [{"command": "inbox", "description": "Инбокс"}, {"command": "next", "description": "Следующие действия"},
           {"command": "done", "description": "Закрыть задачу: /done 12"},
           {"command": "login", "description": "Ссылка для входа в веб"},
           {"command": "email", "description": "Привязать почту"},
           {"command": "about", "description": "Что такое GTD"},
           {"command": "help", "description": "Помощь"}],
    "en": [{"command": "inbox", "description": "Inbox"}, {"command": "next", "description": "Next actions"},
           {"command": "done", "description": "Complete a task: /done 12"},
           {"command": "login", "description": "Sign-in link for the website"},
           {"command": "email", "description": "Link an email"},
           {"command": "about", "description": "What GTD is"},
           {"command": "help", "description": "Help"}],
}
PROFILE_CODES = ("", *RU_LANGS)  # language_code профиля в Telegram; язык текста для каждого — profile_lang


def profile_lang(code: str) -> str:
    return pages.lang_of(code) if code else "en"


# Кнопки постоянной клавиатуры прежних версий (были только русские): нажатие — не задача
BTN_START, BTN_EMAIL = "🚀 Начать", "📧 Добавить email"
NO_PREVIEW = {"link_preview_options": {"is_disabled": True}}


def kb_start(lang="ru"):
    """Кнопки — под приветствием (inline), а не постоянной клавиатурой: поле ввода остаётся свободным.
    «Открыть сайт» — только с https: ссылку на http://localhost Telegram отвергнет вместе с сообщением."""
    kb = [[{"text": tr(lang, "btn_about"), "callback_data": "about"},
           {"text": tr(lang, "btn_email"), "callback_data": "addemail"}]]
    if BASE_URL.startswith("https://"):
        kb.append([{"text": tr(lang, "btn_site"), "url": BASE_URL + ("/en/" if lang == "en" else "")}])
    return {"inline_keyboard": kb}
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


def kb_item(iid, lang="ru"):
    return {"inline_keyboard": [[
        {"text": tr(lang, "btn_done"), "callback_data": f"done:{iid}"},
        {"text": tr(lang, "btn_snooze"), "callback_data": f"snz:{iid}"},
        {"text": tr(lang, "btn_next"), "callback_data": f"next:{iid}"},
    ]]}


def tg_login_get(nonce):
    return row("select * from tg_logins where nonce=%s and expires>%s and status='pending'",
               (nonce, int(time.time())))


def mask_email(email: str) -> str:
    """a***@example.com: в сообщении бота видно, чей это аккаунт, но не весь адрес."""
    name, _, domain = email.partition("@")
    return f"{name[:1]}***@{domain}"


def account_label(uid) -> str:
    """Как назвать аккаунт в подтверждениях: почта (скрытая) или имя."""
    u = row("select email, name from users where id=%s", (uid,)) if uid else None
    if not u:
        return ""
    return mask_email(u["email"]) if u["email"] else (u["name"] or "")


def code_choices(code: int) -> list:
    """Кнопки сверки: верное число и два случайных других, вперемешку."""
    got = {code}
    while len(got) < 3:
        got.add(10 + secrets.randbelow(90))
    out = list(got)
    secrets.SystemRandom().shuffle(out)
    return out


async def tg_login_prompt(chat, frm, nonce, lang):
    """/start <nonce> — пришли с кнопки «Войти через Telegram» на сайте. Одного нажатия мало (SERBITO-360, GTD-3):
    сайт показал число, здесь из трёх кнопок верна одна, — чужую ссылку вслепую не подтвердить. В тексте — откуда
    запрос (браузер и IP) и к какому аккаунту привязка, а если у этого Telegram свои задачи — что их увидят."""
    r = tg_login_get(nonce)
    if not r or r["code"] is None:  # строки до сверки числом (выкатка) — просто «устарела»
        await tg("sendMessage", chat_id=chat, text=tr(lang, "tg_link_stale") + ".")
        return
    merge = ""
    if r["link_user_id"]:
        what = tr(lang, "tg_confirm_link", who=account_label(r["link_user_id"]))
        mine = row("select id from users where tg_id=%s", (frm["id"],))
        if mine and mine["id"] != r["link_user_id"] and has_data(mine["id"]):
            merge = tr(lang, "tg_merge_warn", items=account_stats(mine["id"])["items"])
    else:
        what = tr(lang, "tg_confirm_login")
    text = tr(lang, "tg_confirm", what=what, device=r["ua"] or tr(lang, "device_unknown"), ip=r["ip"] or "?",
              merge=merge)
    await tg("sendMessage", chat_id=chat, text=text, reply_markup={"inline_keyboard": [[
        {"text": str(n), "callback_data": f"tgok:{nonce}:{n}"} for n in code_choices(r["code"])]]})


async def tg_login_confirm(cb, data):
    """Нажата кнопка с числом. Попытка одна: не то число — ссылка сгорает (иначе перебор трёх кнопок).
    Строку «забираем» атомарно (pending → busy), так что два нажатия не подтвердят вход дважды."""
    frm, msg = cb["from"], cb.get("message", {})
    _, lang = tg_known(frm)
    nonce, _, picked = data.partition(":")
    r = row("update tg_logins set status='busy' where nonce=%s and expires>%s and status='pending' and code is not null "
            "returning *", (nonce, int(time.time())))
    if not r:
        note = tr(lang, "tg_link_stale")
    elif picked != str(r["code"]):
        run("update tg_logins set status='denied' where nonce=%s", (nonce,))
        note = tr(lang, "tg_wrong_code")
    elif r["link_user_id"]:
        # Привязка по ссылке — без молчаливого объединения: иначе пустой аккаунт того, кто прислал ссылку, вместе
        # с его сессиями переехал бы в аккаунт жертвы. Объединение — только явно, предложением в браузере
        res = link_identity(r["link_user_id"], "tg_id", frm["id"], auto_merge=False)
        run("update tg_logins set status=%s, user_id=%s, merge=%s where nonce=%s",
            ("ok" if res["ok"] else "merge", r["link_user_id"], res.get("merge"), nonce))
        note = tr(lang, "tg_linked" if res["ok"] else "tg_link_merge")
    else:
        u = tg_user(frm["id"], frm.get("first_name", ""), frm.get("language_code"))
        run("update tg_logins set status='ok', user_id=%s where nonce=%s", (u["id"], nonce))
        note = tr(lang, "tg_login_ok")
    await tg("answerCallbackQuery", callback_query_id=cb["id"], text=note)
    if msg:
        await tg("editMessageText", chat_id=msg["chat"]["id"], message_id=msg["message_id"], text=note)


async def tg_email_ask(chat, u, lang, reply_markup=None):
    """Кнопка «Добавить email» или /email без адреса: ждём адрес следующим сообщением."""
    run("insert into tg_email_links(tg_id,email,expires) values(%s,null,%s) on conflict(tg_id) do update "
        "set email=null, expires=excluded.expires", (u["tg_id"], int(time.time()) + 600))
    now = tr(lang, "email_now", email=u["email"]) if u.get("email") else ""
    await tg("sendMessage", chat_id=chat, text=now + tr(lang, "email_ask"), reply_markup=reply_markup)


async def tg_email_start(chat, u, email, lang):
    """/email адрес — шлём код на почту; пришедшие потом 6 цифр сверяем в tg_email_verify."""
    if not email:
        await tg_email_ask(chat, u, lang)
        return
    try:
        send_code(email, f"tg:{u['tg_id']}", lang)
    except AuthError as e:
        await tg("sendMessage", chat_id=chat, text=e.text(lang))
        return
    run("insert into tg_email_links(tg_id,email,expires) values(%s,%s,%s) on conflict(tg_id) do update "
        "set email=excluded.email, expires=excluded.expires", (u["tg_id"], email, int(time.time()) + 600))
    await tg("sendMessage", chat_id=chat, text=tr(lang, "email_sent", email=email))


async def tg_email_verify(chat, u, email, code, lang):
    try:
        check_code(email, code, f"tg:{u['tg_id']}")
    except AuthError as e:
        await tg("sendMessage", chat_id=chat, text=e.text(lang))
        return
    run("delete from tg_email_links where tg_id=%s", (u["tg_id"],))
    res = link_identity(u["id"], "email", email)
    if res["ok"]:
        await tg("sendMessage", chat_id=chat, text=tr(lang, "email_linked", email=email, url=BASE_URL))
        return
    await tg("sendMessage", chat_id=chat, text=merge_text(res, tr(lang, "what_email_addr", email=email), lang),
             reply_markup={"inline_keyboard": [[
                 {"text": tr(lang, "btn_merge"), "callback_data": f"merge:{res['merge']}"},
                 {"text": tr(lang, "btn_nomerge"), "callback_data": f"nomerge:{res['merge']}"}]]})


async def tg_merge_answer(cb, token, accept):
    u, lang = tg_known(cb["from"])
    if not accept:
        run("delete from merge_offers where token=%s and keep_uid=%s", (token, u["id"] if u else None))
        note = tr(lang, "merge_no")
    elif u and merge_apply(token, u["id"]):
        note = tr(lang, "merge_ok")
    else:
        note = tr(lang, "merge_stale_bot")
    await tg("answerCallbackQuery", callback_query_id=cb["id"], text=note)
    msg = cb.get("message", {})
    if msg:
        await tg("editMessageText", chat_id=msg["chat"]["id"], message_id=msg["message_id"], text=note)


def login_url(uid: int, lang: str) -> str:
    """Одноразовая ссылка входа из бота; английскому пользователю — сразу в английское приложение."""
    tok = secrets.token_urlsafe(24)
    run("insert into login_tokens(token,user_id,expires) values(%s,%s,%s)", (tok, uid, int(time.time()) + 600))
    return f"{BASE_URL}/auth?t={tok}" + ("&lang=en" if lang == "en" else "")


async def handle_message(msg):
    chat = msg["chat"]["id"]
    frm = msg.get("from", {})
    if not frm.get("id"):  # посты каналов, анонимные админы групп: не от человека — аккаунт не заводим
        return
    # Бот работает только в личке (SERBITO-360, GTD-8): в группе ссылка входа из /login, задачи и коды видны всем
    if msg["chat"].get("type") != "private":
        return
    text = (msg.get("text") or msg.get("caption") or "").strip()
    cmd, _, arg = text.partition(" ")
    cmd = cmd.split("@")[0].lower()
    if cmd == "/start" and arg.strip():
        await tg_login_prompt(chat, frm, arg.strip(), tg_known(frm)[1])
        return
    u = tg_user(frm.get("id"), frm.get("first_name", ""), frm.get("language_code"))
    lang = bot_lang(u)
    if text.startswith("/"):
        track(u["id"], "telegram")
    if not text:
        await tg("sendMessage", chat_id=chat, text=tr(lang, "text_only"))
        return
    pending = row("select email from tg_email_links where tg_id=%s and expires>%s", (u["tg_id"], int(time.time())))
    if pending and pending["email"] is None and not text.startswith("/") and text not in (BTN_START, BTN_EMAIL):
        if EMAIL_RE.match(text.lower()):  # ждали адрес после «Добавить email»
            await tg_email_start(chat, u, text.lower(), lang)
            return
        run("delete from tg_email_links where tg_id=%s", (u["tg_id"],))  # передумал — это обычная задача
    if pending and pending["email"] and re.fullmatch(r"\d{6}", text):  # код из письма, только если ждём его
        await tg_email_verify(chat, u, pending["email"], text, lang)
        return
    if text == BTN_EMAIL:  # нажали кнопку старой постоянной клавиатуры — отвечаем и убираем её
        await tg_email_ask(chat, u, lang, KB_REMOVE)
    elif text == BTN_START:
        await tg("sendMessage", chat_id=chat, text=tr(lang, "ready") + tr(lang, "help"), reply_markup=KB_REMOVE)
    elif cmd == "/start":
        await tg("sendMessage", chat_id=chat, text=tr(lang, "start"), reply_markup=kb_start(lang))
    elif cmd == "/help":
        await tg("sendMessage", chat_id=chat, text=tr(lang, "ready") + tr(lang, "help"), reply_markup=kb_start(lang))
    elif cmd == "/about":
        await tg("sendMessage", chat_id=chat, text=tr(lang, "about", url=BASE_URL), **NO_PREVIEW)
    elif cmd == "/login":
        await tg("sendMessage", chat_id=chat, text=tr(lang, "login_link", url=login_url(u["id"], lang)))
    elif cmd == "/email":
        await tg_email_start(chat, u, arg.strip().lower(), lang)
    elif cmd in ("/inbox", "/next"):
        st = cmd[1:]
        its = rows(ITEM_SQL + "where i.user_id=%s and i.status=%s order by i.position nulls last, i.id limit 20",
                   (u["id"], st))
        body = "\n".join(f"{item_ref(i)} {html.escape(i['title'])} {html.escape(describe(i, lang))}".strip()
                         for i in its) or tr(lang, "empty")
        await tg("sendMessage", chat_id=chat, text=f"{st.upper()}:\n{body}", **HTML_MSG)
    elif cmd == "/done":
        try:
            iid = int(arg.strip().lstrip("#"))
        except ValueError:
            await tg("sendMessage", chat_id=chat, text=tr(lang, "done_usage"))
            return
        it = mark_done(u["id"], num=iid)
        if it:
            await tg("sendMessage", chat_id=chat, text=tr(lang, "done_ok", ref=item_ref(it), title=html.escape(it["title"])),
                     **HTML_MSG)
        else:
            await tg("sendMessage", chat_id=chat, text=tr(lang, "not_found_task"))
    elif text.startswith("/"):
        await tg("sendMessage", chat_id=chat, text=tr(lang, "help"))
    else:
        it = capture(u["id"], text, "telegram")
        where = "Next" if it["status"] == "next" else "Inbox"
        await tg("sendMessage", chat_id=chat,
                 text=f"✓ {where} {item_ref(it)}: {html.escape(it['title'])}\n{html.escape(describe(it, lang))}".strip(),
                 reply_markup=kb_item(it["id"], lang), **HTML_MSG)
        # Подсказка — один раз на аккаунт: только к самой первой его задаче. Номер 1 выдаёт счётчик
        # users.item_seq, который не убывает (и при объединении аккаунтов тоже), — поэтому повтора не будет,
        # а у кого задачи уже были (с сайта или раньше в боте), подсказки нет
        if it["num"] == 1:
            await tg("sendMessage", chat_id=chat, text=tr(lang, "first_task", url=BASE_URL), **NO_PREVIEW)


def mark_done(uid, iid=None, num=None):
    """Закрывает задачу по id (кнопки) или по номеру (/done N); возвращает её или None."""
    it = item_get(uid, iid) if iid is not None else item_by_num(uid, num)
    if not it:
        return None
    run("update items set status='done', completed_at=%s where id=%s", (int(time.time()), it["id"]))
    return it


CB_ITEM_ACTS = ("done", "snz", "next")  # кнопки под задачей: «act:<id задачи>»


async def handle_callback(cb):
    """Кнопки бота. На любое нажатие отвечаем answerCallbackQuery — иначе у пользователя крутится часики;
    кнопки прежних версий и испорченные данные — просто «Не найдено», без исключения."""
    act, _, sid = (cb.get("data") or "").partition(":")
    if (cb.get("message") or {}).get("chat", {}).get("type", "private") != "private":  # кнопки в группах — мимо
        await tg("answerCallbackQuery", callback_query_id=cb["id"])
        return
    if act == "tgok":
        await tg_login_confirm(cb, sid)
        return
    if act in ("merge", "nomerge"):
        await tg_merge_answer(cb, sid, act == "merge")
        return
    frm = cb["from"]
    if act in ("about", "help", "addemail"):  # кнопки под приветствием («help» — у приветствий прежних версий)
        chat = (cb.get("message") or {}).get("chat", {}).get("id", frm["id"])
        _, lang = tg_known(frm)
        await tg("answerCallbackQuery", callback_query_id=cb["id"])
        if act == "about":
            await tg("sendMessage", chat_id=chat, text=tr(lang, "about", url=BASE_URL), **NO_PREVIEW)
        elif act == "help":
            await tg("sendMessage", chat_id=chat, text=tr(lang, "help"))
        else:
            await tg_email_ask(chat, tg_user(frm["id"], frm.get("first_name", ""), frm.get("language_code")), lang)
        return
    u, lang = tg_known(frm)
    it = u and act in CB_ITEM_ACTS and sid.isdigit() and item_get(u["id"], int(sid))
    if not it:
        await tg("answerCallbackQuery", callback_query_id=cb["id"], text=tr(lang, "cb_missing"))
        return
    track(u["id"], "telegram")
    iid = it["id"]
    msg = cb.get("message", {})
    if act == "done":
        mark_done(u["id"], iid)
        note = tr(lang, "cb_done")
    elif act == "snz":
        run("update items set remind_at=%s, reminded=0 where id=%s", (int(time.time()) + 3600, iid))
        note = tr(lang, "cb_snooze")
    elif act == "next":
        run(f"update items set status='next', position={END_POS} where id=%s and status<>'next'", (u["id"], "next", iid))
        note = tr(lang, "cb_next")
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
    """На старте: имя бота, профиль (описания и меню команд) и (в проде) вебхук. Идемпотентно — на каждом
    холодном старте; шаги независимы и идут параллельно, ошибка любого только пишется в лог — старт не падает."""
    steps = [bot_username()]
    if WEBHOOK:  # локальный сервер ходит в того же бота: неопубликованные тексты не должны попасть в прод
        steps += [bot_profile(), tg("setWebhook", url=f"{BASE_URL}/tg/webhook", secret_token=webhook_secret(),
                                    allowed_updates=["message", "callback_query"])]
    for r in await asyncio.gather(*steps, return_exceptions=True):
        if isinstance(r, BaseException):  # только тип: в тексте ошибки httpx бывает URL с токеном
            log.warning("bot setup step failed: %s", type(r).__name__)


async def bot_username():
    global BOT_USERNAME
    me = await tg("getMe")
    BOT_USERNAME = me.get("username", "") if isinstance(me, dict) else ""


def _field(r, key):
    return r.get(key) if isinstance(r, dict) else None


async def bot_profile():
    """Описание до Start, короткое описание и меню команд живут в коде, а не руками в @BotFather.
    Холодных стартов много (Cloud Run спит без трафика), поэтому сначала читаем текущее — параллельно,
    по каждому language_code из PROFILE_CODES, — и ставим только то, что разошлось. Не прочиталось (None) —
    просто ставим: вызовы идемпотентны."""
    codes, n = PROFILE_CODES, len(PROFILE_CODES)
    cur = await asyncio.gather(*(tg(m, language_code=c) for m in ("getMyCommands", "getMyDescription",
                                                                   "getMyShortDescription") for c in codes))
    sets = []
    for i, code in enumerate(codes):
        lang = profile_lang(code)
        (desc, short), cmds = BOT_PROFILE[lang], BOT_COMMANDS[lang]
        if cur[i] != cmds:
            sets.append(tg("setMyCommands", commands=cmds, language_code=code))
        if _field(cur[n + i], "description") != desc:
            sets.append(tg("setMyDescription", description=desc, language_code=code))
        if _field(cur[2 * n + i], "short_description") != short:
            sets.append(tg("setMyShortDescription", short_description=short, language_code=code))
    await asyncio.gather(*sets)


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
    due = rows("select i.*, u.tg_id, u.lang, u.tg_lang from items i join users u on u.id=i.user_id "
               "where i.remind_at is not null and i.reminded=0 and i.remind_at<=%s "
               "and i.status not in ('done','trash')", (int(time.time()),))
    sent = 0
    for it in due:
        # Атомарно «забираем» напоминание: два параллельных запуска не пришлют его дважды
        if run("update items set reminded=1 where id=%s and reminded=0 returning id", (it["id"],)) is None:
            continue
        if TOKEN and it["tg_id"]:
            await tg("sendMessage", chat_id=it["tg_id"], text=f"⏰ {item_ref(it)} {html.escape(it['title'])}",
                     reply_markup=kb_item(it["id"], bot_lang(it)), **HTML_MSG)
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
# /docs, /redoc и /openapi.json — только локально (SERBITO-360, GTD-9): публичной карте API на проде незачем быть
app = FastAPI(lifespan=lifespan, **({} if DEV else {"docs_url": None, "redoc_url": None, "openapi_url": None}))

# ── Сжатие ответов (SERBITO-349). Cloud Run отдаёт ответы как есть, а index.html с инлайн-SPA — ~111 КБ на каждое
# открытие. Сжимаем gzip только текст (HTML, JSON, JS, CSS, XML, SVG) от GZIP_MIN байт; уже сжатое (PNG, ответ
# с Content-Encoding), частичное (206) и потоковое (тело несколькими кусками — FileResponse, StreamingResponse)
# идёт как есть. Starlette GZipMiddleware не подошёл: он сжимает и потоковые ответы.
# Слой — самый внутренний (добавлен первым): HEAD, который HeadAsGet превращает в GET, получает те же
# Content-Encoding и Content-Length, что и GET.
GZIP_MIN = 1024
GZIP_TYPES = ("text/", "application/json", "application/javascript", "application/manifest+json",
              "application/xml", "image/svg+xml")


def accepts_gzip(value: str) -> bool:
    """Accept-Encoding разрешает gzip: «gzip» (или, если его нет, «*») и не с q=0."""
    ok = {}
    for part in value.split(","):
        name, _, params = part.partition(";")
        q = params.replace(" ", "").lower()
        try:
            ok[name.strip().lower()] = not (q.startswith("q=") and float(q[2:]) == 0)
        except ValueError:
            ok[name.strip().lower()] = False
    return ok.get("gzip", ok.get("*", False))


class Gzip:
    def __init__(self, asgi):
        self.asgi = asgi

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.asgi(scope, receive, send)
        gz = accepts_gzip(Headers(scope=scope).get("accept-encoding", ""))
        held = None  # http.response.start ждёт первого куска тела: только по нему ясно, сжимать ли

        async def send_z(msg):
            nonlocal held
            if msg["type"] == "http.response.start":
                held = msg
                return
            if held is None:
                return await send(msg)
            start, held = held, None
            body, h = msg.get("body", b""), MutableHeaders(raw=list(start.get("headers", [])))
            if (msg["type"] == "http.response.body" and not msg.get("more_body") and len(body) >= GZIP_MIN
                    and start["status"] not in (204, 206, 304) and "content-encoding" not in h
                    and h.get("content-type", "").lower().startswith(GZIP_TYPES)):
                h.add_vary_header("Accept-Encoding")  # кэши не отдадут сжатое тому, кто его не просил
                if gz:
                    body = gzip.compress(body, compresslevel=6, mtime=0)
                    h["content-encoding"], h["content-length"] = "gzip", str(len(body))
                    msg = {**msg, "body": body}
                start = {**start, "headers": h.raw}
            await send(start)
            await send(msg)

        await self.asgi(scope, receive, send_z)


app.add_middleware(Gzip)

# HEAD отвечаем как GET, только без тела. Мониторинги аптайма и превью ссылок (Slack и др.) сперва шлют HEAD,
# а FastAPI сам его не добавляет — без этого на HEAD / был 405 и сайт считался лежащим.
# API и ссылки входа — только GET: HEAD от бота-превью не должен сжечь одноразовый токен /auth.
NO_HEAD = {"/auth", "/dev-login"}


class HeadAsGet:
    def __init__(self, asgi):
        self.asgi = asgi

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] != "HEAD" or scope["path"].startswith("/api/") or scope["path"] in NO_HEAD:
            return await self.asgi(scope, receive, send)

        async def headers_only(msg):
            # Статус и заголовки (в т.ч. Content-Length) — как у GET; тело выбрасываем, закрывая ответ пустым куском
            if msg["type"] == "http.response.body":
                if msg.get("more_body"):
                    return
                msg = {"type": "http.response.body", "body": b""}
            await send(msg)

        # Копия scope, а не правка: сервер (uvicorn) по своему scope знает, что запрос HEAD, и тела не ждёт
        await self.asgi(dict(scope, method="GET"), receive, headers_only)


app.add_middleware(HeadAsGet)

# ── Защита запросов и заголовки ответов (SERBITO-360)
# GTD-6: запрос, меняющий данные, принимаем только со своей страницы. SameSite=Lax не спасает от соседних
# поддоменов *.serbito.rs — для браузера это тот же «сайт», и cookie уходит. Поэтому: Sec-Fetch-Site (современные
# браузеры) — только same-origin; Origin, если есть, — ровно наш хост. Без обоих заголовков — не браузер (curl,
# тесты): CSRF — атака через браузер жертвы, их пропускаем. Тело /api — только JSON: форма с чужого сайта может
# прислать text/plain, а fetch с application/json на чужой origin браузер без CORS не отправит
UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}
BASE_ORIGIN = "{0.scheme}://{0.netloc}".format(urlsplit(BASE_URL))


def cross_site(headers: dict) -> bool:
    site = headers.get("sec-fetch-site")
    if site and site not in ("same-origin", "none"):
        return True
    origin = headers.get("origin")
    if origin is None:
        return False
    # Свой — это хост запроса или BASE_URL: reverse proxy у self-host может подменять Host на внутренний адрес
    u = urlsplit(origin)
    return u.scheme not in ("http", "https") or (u.netloc != headers.get("host", "") and origin != BASE_ORIGIN)


def json_body_ok(headers: dict) -> bool:
    has_body = headers.get("content-length", "0") not in ("", "0") or "transfer-encoding" in headers
    return not has_body or headers.get("content-type", "").split(";")[0].strip().lower() == "application/json"


def sentry_csp_uri() -> str:
    """Отчёты CSP — в тот же проект Sentry (security endpoint из DSN); без DSN — только в консоль браузера."""
    u = urlsplit(SENTRY_DSN)
    if not (u.username and u.hostname and u.path.strip("/")):
        return ""
    return f"{u.scheme}://{u.hostname}/api/{u.path.strip('/')}/security/?sentry_key={u.username}"


# GTD-9. CSP пока только report-only: SPA и страницы живут на inline-скриптах, стилях и обработчиках, внешние —
# Google Sign-In, виджет Telegram, GA и Cloudflare. Нарушения видны в консоли и в Sentry, не ломая сайт; когда
# отчёты затихнут — перевести в Content-Security-Policy. frame-ancestors (нас нельзя встроить во фрейм) — сразу
CSP_REPORT_ONLY = "; ".join([
    "default-src 'self'",
    "script-src 'self' 'unsafe-inline' https://accounts.google.com https://telegram.org "
    "https://www.googletagmanager.com https://static.cloudflareinsights.com",
    "style-src 'self' 'unsafe-inline' https://accounts.google.com",
    "img-src 'self' data: https:",
    "connect-src 'self' https://accounts.google.com https://*.google-analytics.com https://*.analytics.google.com "
    "https://*.googletagmanager.com https://cloudflareinsights.com",
    "frame-src https://accounts.google.com https://oauth.telegram.org",
    "font-src 'self' data:",
    "object-src 'none'", "base-uri 'self'", "form-action 'self'", "frame-ancestors 'none'",
    *([f"report-uri {sentry_csp_uri()}"] if sentry_csp_uri() else []),
])
SECURITY_HEADERS = [
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"strict-origin-when-cross-origin"),
    (b"x-frame-options", b"DENY"),
    (b"content-security-policy", b"frame-ancestors 'none'"),
    (b"content-security-policy-report-only", CSP_REPORT_ONLY.encode()),
    (b"permissions-policy", b"camera=(), microphone=(), geolocation=(), payment=(), usb=()"),
    # HSTS — только с https: по http браузер его всё равно игнорирует
    *([(b"strict-transport-security", b"max-age=31536000; includeSubDomains")] if COOKIE_SECURE else []),
]


class Guard:
    """Чистый ASGI-слой снаружи приложения: отбивает чужие запросы (GTD-6), добавляет заголовки безопасности
    каждому ответу (GTD-9) и продлевает cookie сессии, если session_user сдвинул её срок (GTD-5)."""
    def __init__(self, asgi):
        self.asgi = asgi

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.asgi(scope, receive, send)
        state = scope.setdefault("state", {})  # тот же dict, что request.state в обработчике

        async def send_h(msg):
            if msg["type"] == "http.response.start":
                hs = list(msg.get("headers", []))
                have = {k.lower() for k, _ in hs}
                hs += [(k, v) for k, v in SECURITY_HEADERS if k not in have]
                sid = state.get("sid_refresh")
                # Ответ сам ставит или стирает sid (вход, выход) — его cookie главнее продления
                if sid and not any(k.lower() == b"set-cookie" and v.startswith(b"sid=") for k, v in hs):
                    hs.append((b"set-cookie", sid_cookie(sid).encode("latin-1")))
                msg = {**msg, "headers": hs}
            await send(msg)

        path = scope["path"]
        if scope["method"] in UNSAFE and (path.startswith("/api/") or path == "/auth"):
            headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
            if cross_site(headers):
                return await JSONResponse({"detail": "cross-site request"}, 403)(scope, receive, send_h)
            if path.startswith("/api/") and not json_body_ok(headers):
                return await JSONResponse({"detail": "application/json required"}, 415)(scope, receive, send_h)
        await self.asgi(scope, receive, send_h)


app.add_middleware(Guard)

# ── Сессии (GTD-5): в cookie — случайный токен, в базе — его sha256. Срок скользящий: SESSION_TTL с последней
# активности; отметку seen и cookie обновляем не чаще раза в сутки, чтобы не писать в базу на каждый запрос
SESSION_TTL = 90 * 86400
SESSION_TOUCH = 86400


def token_hash(tok: str) -> str:
    return hashlib.sha256(tok.encode()).hexdigest()


def sid_cookie(tok: str) -> str:
    """Заголовок Set-Cookie для сессии — тот же, что ставит set_session."""
    r = Response()
    r.set_cookie("sid", tok, max_age=SESSION_TTL, httponly=True, samesite="lax", secure=COOKIE_SECURE)
    return r.headers["set-cookie"]


def session_user(request: Request) -> int | None:
    sid = request.cookies.get("sid")
    if not sid:
        return None
    now, h = int(time.time()), token_hash(sid)
    r = row("select user_id, seen from user_sessions where token_hash=%s and seen>%s", (h, now - SESSION_TTL))
    if not r:
        return None
    if r["seen"] < now - SESSION_TOUCH:
        run("update user_sessions set seen=%s where token_hash=%s", (now, h))
        request.state.sid_refresh = sid
    return r["user_id"]


def current_user(request: Request) -> int:
    uid = session_user(request)
    if not uid:
        raise HTTPException(401, "auth required")
    track(uid, "web", 0)
    return uid


def set_session(resp, uid: int):
    tok, now = secrets.token_urlsafe(32), int(time.time())
    run("delete from user_sessions where seen<%s", (now - SESSION_TTL,))
    run("insert into user_sessions(token_hash,user_id,created,seen) values(%s,%s,%s,%s)", (token_hash(tok), uid, now, now))
    resp.headers.append("set-cookie", sid_cookie(tok))
    return resp


def clear_session(resp):
    resp.delete_cookie("sid", httponly=True, samesite="lax", secure=COOKIE_SECURE)
    return resp


def start_session(uid: int) -> RedirectResponse:
    return set_session(RedirectResponse("/", status_code=303), uid)


# ───────────────────────── Аккаунты ─────────────────────────
# Один аккаунт = одна строка users; способы входа — её колонки tg_id / email / google_sub.
# Google и код на почту с одинаковым адресом попадают в один аккаунт; Telegram привязывается из «Аккаунта».
IDENTITIES = ("tg_id", "email", "google_sub")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
# Лимиты входа по коду (SERBITO-346). Код — 6 цифр, 10⁶ вариантов: без общего бюджета на адрес его можно
# подобрать, перезапрашивая код и угадывая снова. «Отправитель» — IP на сайте или Telegram-аккаунт в боте
DAY = 86400
CODE_TRIES = 5                  # неверных попыток на один код — потом он сгорает
EMAIL_FAILS = 20                # неверных кодов на адрес за сутки (переживает перезапрос кода) — потом блокировка
EMAIL_SENDS = 10                # писем с кодом на один адрес за сутки — от засыпания ящика письмами
SENDER_SENDS = ((5, 600), (30, DAY))  # писем с кодом от одного отправителя: за 10 минут и за сутки
SENDER_GUESSES = (30, 600)      # проверок кода от одного отправителя за 10 минут (по всем адресам)
# Общий потолок писем в сутки (SERBITO-360, GTD-4): аккаунт Brevo общий с serbito, и его дневная квота (300 на
# бесплатном плане) — одна на двоих. Лимиты на адрес и на отправителя не спасают от рассылки со многих IP на
# многие адреса; потолок оставляет serbito запас, а на 80 % и при упоре пишет ошибку — она уходит алертом в Sentry
EMAIL_DAILY_CAP = int(os.getenv("EMAIL_DAILY_CAP", "200"))


def limit_hit(key: str, window: int) -> int:
    """Атомарно засчитывает событие по ключу и возвращает, сколько их с начала окна (вместе с этим).
    Окно фиксированное: начинается с первого события и через window секунд обнуляется. Один upsert —
    параллельные запросы не проскочат мимо счётчика (строка блокируется на время обновления)."""
    now = int(time.time())
    return run("insert into auth_limits(key,n,since) values(%s,1,%s) on conflict(key) do update set "
               "n = case when auth_limits.since > %s then auth_limits.n + 1 else 1 end, "
               "since = case when auth_limits.since > %s then auth_limits.since else excluded.since end "
               "returning n", (key, now, now - window, now - window))


def limit_count(key: str, window: int) -> int:
    r = row("select n from auth_limits where key=%s and since>%s", (key, int(time.time()) - window))
    return r["n"] if r else 0


# Откуда пришёл запрос (SERBITO-346). Модель доверия:
# - Каждый прокси ДОПИСЫВАЕТ в конец X-Forwarded-For адрес, от которого получил запрос; всё левее пишет сам
#   клиент и может подделать. Поэтому клиент — самый правый адрес после того, как справа пропущены наши
#   собственные прокси. Левый край заголовка не читаем никогда.
# - Прод — Cloud Run с domain mapping: Google Front End дописывает реальный адрес клиента, а к контейнеру
#   подключается с link-local 169.254.x.x. За внешним балансировщиком Google добавил бы справа ещё и свой
#   адрес («клиент, балансировщик»): прокси GFE (35.191.0.0/16, 130.211.0.0/22) и link-local пропускаем, адрес
#   правила балансировщика (или своего reverse proxy с публичным IP) задаётся в TRUSTED_PROXIES.
# - Заголовок читаем, только если само соединение пришло от прокси: loopback, частная или link-local сеть,
#   TRUSTED_PROXIES. uvicorn, открытый наружу без прокси, берёт адрес соединения — XFF клиента игнорируется.
# - Cloudflare: сейчас DNS only, запросы идут мимо него. CF-Connecting-IP верим, только если найденный адрес
#   клиента — из сетей Cloudflare, т.е. запрос действительно пришёл через его прокси; иначе этот заголовок
#   подделывается так же, как XFF. Список сетей — https://www.cloudflare.com/ips/
def _nets(cidrs) -> tuple:
    return tuple(ipaddress.ip_network(c.strip(), strict=False) for c in cidrs if c.strip())


TRUSTED_PROXIES = _nets(os.getenv("TRUSTED_PROXIES", "").split(","))
GOOGLE_PROXIES = _nets(["35.191.0.0/16", "130.211.0.0/22"])
CLOUDFLARE = _nets([
    "173.245.48.0/20", "103.21.244.0/22", "103.22.200.0/22", "103.31.4.0/22", "141.101.64.0/18",
    "108.162.192.0/18", "190.93.240.0/20", "188.114.96.0/20", "197.234.240.0/22", "198.41.128.0/17",
    "162.158.0.0/15", "104.16.0.0/13", "104.24.0.0/14", "172.64.0.0/13", "131.0.72.0/22",
    "2400:cb00::/32", "2606:4700::/32", "2803:f800::/32", "2405:b500::/32", "2405:8100::/32",
    "2a06:98c0::/29", "2c0f:f248::/32"])


def _ip(s: str):
    try:
        return ipaddress.ip_address(s.strip())
    except ValueError:
        return None


def _within(ip, nets) -> bool:
    return any(ip.version == n.version and ip in n for n in nets)


def client_ip(request: Request) -> str:
    """Адрес клиента для лимитов — по модели доверия выше."""
    peer = request.client.host if request.client else ""
    ip = _ip(peer)
    if not ip or not (ip.is_private or ip.is_loopback or ip.is_link_local or _within(ip, TRUSTED_PROXIES)):
        return peer
    for hop in reversed(",".join(request.headers.getlist("x-forwarded-for")).split(",")):
        a = _ip(hop)
        if a is None:
            break
        ip = a
        if not (a.is_loopback or a.is_link_local or _within(a, GOOGLE_PROXIES + TRUSTED_PROXIES)):
            break
    if _within(ip, CLOUDFLARE):
        ip = _ip(request.headers.get("cf-connecting-ip", "")) or ip
    return str(ip)


def ip_key(ip: str) -> str:
    """Ключ лимита по IP. IPv6 — целиком /64: столько адресов у абонента обычно есть, по одному — не лимит."""
    a = _ip(ip)
    return str(ipaddress.ip_network(f"{a}/64", strict=False)) if a and a.version == 6 else ip


class AuthError(Exception):
    """Ошибка входа/привязки: текст — ключ TEXTS, язык выбирает тот, кто показывает (сайт или бот)."""
    def __init__(self, status: int, key: str):
        super().__init__(key)
        self.status, self.key = status, key

    def text(self, lang: str) -> str:
        return tr(lang, self.key)


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
            run("delete from user_sessions where user_id=%s", (other["id"],))
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
            raise AuthError(400, "merge_missing")
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
        for table in ("items", "user_sessions", "login_tokens"):
            c.execute(f"update {table} set user_id=%s where user_id=%s", (keep, drop))
        c.execute("update tg_logins set link_user_id=%s where link_user_id=%s", (keep, drop))
        moved = {f: d[f] for f in IDENTITIES if d[f] and not k[f]}
        # Язык: свой выбор keep важнее; язык Telegram — вместе с Telegram
        moved.update({f: d[f] for f in ("lang", "tg_lang") if d[f] and not k[f] and (f == "lang" or "tg_id" in moved)})
        c.execute("delete from merge_offers where keep_uid=%s or drop_uid=%s", (drop, drop))
        c.execute("delete from users where id=%s", (drop,))  # освобождает уникальные tg_id/email/google_sub
        for f, v in moved.items():
            c.execute(f"update users set {f}=%s where id=%s", (v, keep))
    log.info("merged account %s into %s", drop, keep)


def account_stats(uid: int) -> dict:
    return row("select (select count(*) from items where user_id=%s and status<>'trash') items, "
               "(select count(*) from projects where user_id=%s and status='active') projects", (uid, uid))


def link_identity(uid: int, field: str, value, auto_merge: bool = True) -> dict:
    """Привязать способ входа к uid. {"ok": True} — готово; иначе — предложение объединить аккаунты.
    Если свой аккаунт ещё пустой и ничего не теряется, просто переезжаем в тот, где уже есть данные —
    кроме auto_merge=False (привязка по ссылке в бота, где подтверждает не тот, кто в браузере)."""
    other = attach(uid, field, value)
    if other is None:
        return {"ok": True}
    me_, them = row("select * from users where id=%s", (uid,)), row("select * from users where id=%s", (other,))
    if auto_merge and not has_data(uid) and not any(me_[f] and them[f] for f in IDENTITIES):
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


def merge_text(offer: dict, what: str, lang: str) -> str:
    return tr(lang, "merge_offer", what=what, items=offer["items"], projects=offer["projects"])


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


def send_code(email: str, rate_key: str, lang: str = "ru"):
    """Одноразовый код на почту (письмо — на языке lang). Кулдаун минута на адрес; лимиты писем на адрес
    и на отправителя rate_key («ip:…» или «tg:…») — см. SENDER_SENDS, EMAIL_SENDS."""
    if len(email) > 254 or not EMAIL_RE.match(email):
        raise AuthError(400, "bad_email")
    if not (SMTP_PASSWORD or DEV):
        raise AuthError(400, "email_off")
    now = int(time.time())
    if limit_count("fail:" + email, DAY) >= EMAIL_FAILS:
        raise AuthError(429, "code_locked")  # новый код всё равно не примем — и письмо не шлём
    prev = row("select sent from email_codes where email=%s", (email,))
    if prev and prev["sent"] > now - 60:
        raise AuthError(429, "code_cooldown")
    for (limit, window), key in zip(SENDER_SENDS, ("send:", "send-day:")):
        if limit_hit(key + rate_key, window) > limit:
            raise AuthError(429, "too_many" if window < DAY else "too_many_day")
    if limit_hit("mail-day:" + email, DAY) > EMAIL_SENDS:
        raise AuthError(429, "too_many_day")
    sent = limit_hit("mail-all", DAY)
    if sent > EMAIL_DAILY_CAP:
        if sent == EMAIL_DAILY_CAP + 1:  # алерт один раз за окно, а не на каждую попытку
            log.error("email: достигнут суточный потолок писем (%s), вход по почте закрыт до конца окна", EMAIL_DAILY_CAP)
        raise AuthError(503, "email_busy")
    if sent == EMAIL_DAILY_CAP * 8 // 10:
        log.error("email: отправлено %s писем за сутки — 80%% потолка %s", sent, EMAIL_DAILY_CAP)
    code = f"{secrets.randbelow(10 ** 6):06d}"
    run("delete from email_codes where expires<%s", (now,))
    run("delete from auth_limits where since<%s", (now - DAY,))
    # Новый код — новые CODE_TRIES попыток; суточный бюджет адреса (fail:…) при этом не обнуляется
    run("insert into email_codes(email,code_hash,expires,attempts,sent) values(%s,%s,%s,0,%s) "
        "on conflict(email) do update set code_hash=excluded.code_hash, expires=excluded.expires, "
        "attempts=0, sent=excluded.sent", (email, code_hash(email, code), now + 600, now))
    if not SMTP_PASSWORD:
        log.warning("DEV: код входа для %s — %s", email, code)
        return
    try:
        send_email(email, tr(lang, "mail_subject", code=code), tr(lang, "mail_body", code=code))
    except (smtplib.SMTPException, OSError) as e:
        # Без адреса и текста ошибки: SMTPRecipientsRefused и ответы сервера содержат адрес получателя (GTD-10)
        log.warning("smtp failed: %s %s", type(e).__name__, getattr(e, "smtp_code", ""))
        run("delete from email_codes where email=%s", (email,))
        raise AuthError(502, "smtp_fail")


def check_code(email: str, code: str, rate_key: str):
    """Сверяет код. Каждый шаг — атомарный запрос, так что параллельные догадки не обходят лимиты:
    не больше CODE_TRIES попыток на код, EMAIL_FAILS неверных на адрес за сутки (потом вход по коду на адрес
    закрыт), SENDER_GUESSES проверок от отправителя. Верный код одноразовый."""
    now = int(time.time())
    limit, window = SENDER_GUESSES
    if limit_hit("guess:" + rate_key, window) > limit:
        raise AuthError(429, "too_many")
    # Попытка засчитывается до сравнения и только если она ещё есть: update … where attempts<N returning
    r = row("update email_codes set attempts=attempts+1 where email=%s and expires>%s and attempts<%s "
            "returning code_hash", (email, now, CODE_TRIES))
    if not r:
        raise AuthError(429, "code_locked") if limit_count("fail:" + email, DAY) >= EMAIL_FAILS \
            else AuthError(400, "code_expired")
    # Бюджет адреса тоже резервируем до сравнения: сравнений не больше EMAIL_FAILS, сколько бы их ни шло разом
    fails = limit_hit("fail:" + email, DAY)
    if fails > EMAIL_FAILS:
        run("delete from email_codes where email=%s", (email,))
        raise AuthError(429, "code_locked")
    if not hmac.compare_digest(r["code_hash"], code_hash(email, code)):
        if fails >= EMAIL_FAILS:
            run("delete from email_codes where email=%s", (email,))
            raise AuthError(429, "code_locked")
        raise AuthError(400, "code_wrong")
    run("update auth_limits set n=n-1 where key=%s and n>0", ("fail:" + email,))  # верный код — не неудача
    # Удаляем именно этот код: из двух одновременных верных попыток войдёт одна
    if not run("delete from email_codes where email=%s and code_hash=%s returning 1", (email, r["code_hash"])):
        raise AuthError(400, "code_expired")


def link_response(res: dict, what: str, lang: str):
    """what — ключ TEXTS: какой способ входа уже у другого аккаунта."""
    if res["ok"]:
        return {"ok": True}
    return JSONResponse({**res, "detail": merge_text(res, tr(lang, what), lang)}, status_code=409)


def auth_fail(e: AuthError, request: Request):
    return HTTPException(e.status, e.text(req_lang(request)))


@app.post("/api/auth/google")
def auth_google(body: dict, request: Request):
    if not GOOGLE_CLIENT_ID:
        raise auth_fail(AuthError(400, "google_off"), request)
    d = google_verify(body.get("credential") or "")
    if not d:
        raise auth_fail(AuthError(400, "google_fail"), request)
    email = d["email"].lower()
    uid = session_user(request) if body.get("link") else None
    if uid:
        res = link_identity(uid, "google_sub", d["sub"])
        if res["ok"]:
            uid = session_user(request)  # после переезда сессия уже в другом аккаунте
            if not row("select email from users where id=%s", (uid,))["email"]:
                attach(uid, "email", email)
        return link_response(res, "what_google", req_lang(request))
    return set_session(JSONResponse({"ok": True}), google_user(d["sub"], email, d.get("name", "")))


@app.post("/api/auth/email/start")
def auth_email_start(body: dict, request: Request):
    try:
        send_code((body.get("email") or "").strip().lower(), "ip:" + ip_key(client_ip(request)), req_lang(request))
    except AuthError as e:
        raise auth_fail(e, request)
    return {"ok": True}


@app.post("/api/auth/email/verify")
def auth_email_verify(body: dict, request: Request):
    email = (body.get("email") or "").strip().lower()
    try:
        check_code(email, (body.get("code") or "").strip(), "ip:" + ip_key(client_ip(request)))
    except AuthError as e:
        raise auth_fail(e, request)
    uid = session_user(request) if body.get("link") else None
    if uid:
        return link_response(link_identity(uid, "email", email), "what_email", req_lang(request))
    return set_session(JSONResponse({"ok": True}), email_user(email))


@app.post("/api/auth/merge")
def auth_merge(body: dict, request: Request, uid: int = Depends(current_user)):
    try:
        ok = merge_apply(body.get("token") or "", uid)
    except AuthError as e:
        raise auth_fail(e, request)
    if not ok:
        raise auth_fail(AuthError(400, "merge_stale"), request)
    return {"ok": True}


# Вход через бота по ссылке (GTD-3). Браузер, начавший вход, получает cookie tgl (в базе — её хеш): забрать
# сессию опросом может только он, даже если nonce из ссылки попал к кому-то ещё
TG_COOKIE = "tgl"
TG_STARTS = (20, 600)  # начатых входов через бота от одного IP за 10 минут


def device_label(ua: str) -> str:
    """«Chrome, macOS» из User-Agent — для текста подтверждения в боте. Сам User-Agent не храним."""
    ua = ua or ""
    browser = next((name for key, name in (("Edg/", "Edge"), ("OPR/", "Opera"), ("YaBrowser", "Yandex Browser"),
                                             ("Firefox/", "Firefox"), ("Chrome/", "Chrome"), ("CriOS", "Chrome"),
                                             ("Safari/", "Safari")) if key in ua), "")
    system = next((name for key, name in (("iPhone", "iOS"), ("iPad", "iPadOS"), ("Android", "Android"),
                                            ("Windows", "Windows"), ("Mac OS X", "macOS"), ("CrOS", "ChromeOS"),
                                            ("Linux", "Linux")) if key in ua), "")
    return ", ".join(x for x in (browser, system) if x)


@app.post("/api/auth/tg/start")
def auth_tg_start(body: dict, request: Request):
    if not (TOKEN and BOT_USERNAME):
        raise auth_fail(AuthError(400, "tg_off"), request)
    ip = client_ip(request)
    limit, window = TG_STARTS
    if limit_hit("tg-start:" + ip_key(ip), window) > limit:
        raise auth_fail(AuthError(429, "too_many"), request)
    nonce, code = secrets.token_urlsafe(16), 10 + secrets.randbelow(90)
    now = int(time.time())
    run("delete from tg_logins where expires<%s", (now,))
    link_uid = session_user(request) if body.get("link") else None
    browser = request.cookies.get(TG_COOKIE) or secrets.token_urlsafe(16)
    run("insert into tg_logins(nonce,link_user_id,expires,code,ip,ua,browser) values(%s,%s,%s,%s,%s,%s,%s)",
        (nonce, link_uid, now + 600, code, ip, device_label(request.headers.get("user-agent", "")), token_hash(browser)))
    resp = JSONResponse({"nonce": nonce, "code": code, "url": f"https://t.me/{BOT_USERNAME}?start={nonce}"})
    resp.set_cookie(TG_COOKIE, browser, max_age=600, path="/api/auth/tg", httponly=True, samesite="strict",
                    secure=COOKIE_SECURE)
    return resp


# ── Telegram Login Widget (SERBITO-292): вход в один клик на сайте. Данные приходят от браузера, поэтому верим
# им только после проверки подписи: https://core.telegram.org/widgets/login#checking-authorization
# Подпись виджета старше 10 минут не принимаем, и каждую — только раз (GTD-11): перехваченный payload не повторить
TG_WIDGET_MAX_AGE = 10 * 60
TG_WIDGET_SKEW = 5 * 60  # часы Telegram могут чуть убегать вперёд наших


def tg_widget_verify(data) -> dict:
    """Проверяет payload виджета: data-check-string — отсортированные строки key=value без hash, подпись —
    HMAC-SHA256 с ключом SHA256(токена бота). Возвращает {"id", "first_name"}; иначе AuthError."""
    if not TOKEN or not isinstance(data, dict):
        raise AuthError(400, "tg_widget_fail")
    got = data.get("hash")
    fields = {k: v for k, v in data.items() if k != "hash"}
    # Виджет присылает строки и числа; прочее (null, списки, bool) в его ответе не бывает
    if not isinstance(got, str) or not got or any(
            isinstance(v, bool) or not isinstance(v, (str, int)) for v in fields.values()):
        raise AuthError(400, "tg_widget_fail")
    check = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
    want = hmac.new(hashlib.sha256(TOKEN.encode()).digest(), check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(want.encode(), got.encode()):
        raise AuthError(400, "tg_widget_fail")
    try:
        tg_id, auth_date = int(fields["id"]), int(fields["auth_date"])
    except (KeyError, ValueError):
        raise AuthError(400, "tg_widget_fail")
    now = time.time()
    if not now - TG_WIDGET_MAX_AGE <= auth_date <= now + TG_WIDGET_SKEW:
        raise AuthError(400, "tg_widget_stale")
    # Одноразовость: подпись уже видели в пределах срока её жизни — это повтор
    if limit_hit("tg-widget:" + got, TG_WIDGET_MAX_AGE + TG_WIDGET_SKEW) > 1:
        raise AuthError(400, "tg_widget_stale")
    return {"id": tg_id, "first_name": str(fields.get("first_name", ""))}


@app.post("/api/auth/tg/widget")
def auth_tg_widget(body: dict, request: Request):
    """Вход или привязка через виджет — те же пути, что у подтверждения в боте (tg_login_confirm)."""
    if not TOKEN:
        raise auth_fail(AuthError(400, "tg_off"), request)
    try:
        d = tg_widget_verify(body.get("auth"))
    except AuthError as e:
        raise auth_fail(e, request)
    uid = session_user(request) if body.get("link") else None
    if uid:
        return link_response(link_identity(uid, "tg_id", d["id"]), "what_tg", req_lang(request))
    return set_session(JSONResponse({"ok": True}), tg_user(d["id"], d["first_name"])["id"])


@app.post("/api/auth/tg/poll")
def auth_tg_poll(body: dict, request: Request):
    """Опрос страницы, ждущей подтверждения в боте. nonce — в теле, а не в адресе: не оседает в журналах запросов.
    Чужой браузер (нет cookie tgl, с которой начинали) видит «устарело», как и несуществующий nonce."""
    nonce, browser = str(body.get("nonce") or ""), request.cookies.get(TG_COOKIE, "")
    r = row("select * from tg_logins where nonce=%s", (nonce,))
    if not r or r["expires"] < time.time() or not browser or not hmac.compare_digest(r["browser"] or "", token_hash(browser)):
        return {"status": "expired"}
    if r["status"] in ("pending", "busy"):
        return {"status": "pending"}
    run("delete from tg_logins where nonce=%s", (nonce,))
    if r["status"] == "denied":
        return {"status": "denied"}
    if r["status"] == "merge":
        o = row("select drop_uid from merge_offers where token=%s", (r["merge"],))
        if not o:
            return {"status": "expired"}
        stats = account_stats(o["drop_uid"])
        lang = req_lang(request)
        return {"status": "merge", "merge": r["merge"], **stats, "detail": merge_text(stats, tr(lang, "what_tg"), lang)}
    if r["status"] != "ok" or r["link_user_id"]:
        return {"status": r["status"]}
    return set_session(JSONResponse({"status": "ok"}), r["user_id"])


UNDO_CHOICES = (5, 10, 30)


@app.get("/api/me")
def me(uid: int = Depends(current_user)):
    u = row("select name, email, tg_id, google_sub, undo_seconds, lang from users where id=%s", (uid,))
    n = row("select count(*) n from user_sessions where user_id=%s and seen>%s", (uid, int(time.time()) - SESSION_TTL))["n"]
    return {"name": u["name"], "email": u["email"], "tg": bool(u["tg_id"]), "google": bool(u["google_sub"]),
            "undo_seconds": u["undo_seconds"], "lang": u["lang"], "admin": uid in ADMIN_USER_IDS, "sessions": n}


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
    """Настройки пользователя: время на «Отменить» — 5, 10 или 30 секунд; скрыть чек-лист первого запуска;
    язык интерфейса — ru, en или null (авто: сайт — по браузеру, бот — по Telegram)."""
    if "undo_seconds" in body:
        if body["undo_seconds"] not in UNDO_CHOICES:
            raise HTTPException(400, "undo_seconds: 5, 10 или 30")
        run("update users set undo_seconds=%s where id=%s", (body["undo_seconds"], uid))
    if "checklist_hidden" in body:
        if not isinstance(body["checklist_hidden"], bool):
            raise HTTPException(400, "checklist_hidden: true или false")
        run("update users set checklist_hidden=%s where id=%s", (body["checklist_hidden"], uid))
    if "lang" in body:
        if body["lang"] is not None and body["lang"] not in LANGS:
            raise HTTPException(400, "lang: ru, en или null")
        run("update users set lang=%s where id=%s", (body["lang"], uid))
    return me(uid)


# Страницы входа по ссылке: не кешировать, адрес с токеном не отдавать в Referer
AUTH_HEADERS = {"X-Robots-Tag": "noindex", "Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}


def auth_stale_page(request: Request, lang: str) -> HTMLResponse:
    """Ссылку открывают в браузере из Telegram — страница с выходом, а не сырой JSON."""
    page = pages.auth_stale(lang if lang in LANGS else req_lang(request), BOT_USERNAME)
    return HTMLResponse(page, status_code=400, headers=AUTH_HEADERS)


@app.get("/auth")
def auth(t: str, request: Request, lang: str = ""):
    """Ссылка входа из /login в боте. Сама ссылка не входит (SERBITO-360, GTD-7): иначе чужую ссылку можно
    подсунуть, и человек незаметно окажется в аккаунте злоумышленника и будет записывать задачи ему. Страница
    показывает, в чей аккаунт вход, и входит только по кнопке (POST). Заодно превью ссылок и префетч не сжигают
    одноразовый токен."""
    r = row("select user_id from login_tokens where token=%s and expires>%s", (t, int(time.time())))
    if not r:
        return auth_stale_page(request, lang)
    cur = session_user(request)
    page_lang = lang if lang in LANGS else req_lang(request, r["user_id"])
    return HTMLResponse(pages.auth_confirm(page_lang, account_label(r["user_id"]), t, lang if lang in LANGS else "",
                                           account_label(cur) if cur and cur != r["user_id"] else ""),
                        headers=AUTH_HEADERS)


@app.post("/auth")
async def auth_confirm(request: Request):
    """Кнопка «Войти» со страницы /auth: форма со своей страницы (чужой Origin отбивает Guard)."""
    form = parse_qs((await request.body()).decode("utf-8", "replace"))
    t, lang = form.get("t", [""])[0], form.get("lang", [""])[0]
    uid = run("delete from login_tokens where token=%s and expires>%s returning user_id", (t, int(time.time())))
    if not uid:
        return auth_stale_page(request, lang)
    chosen = row("select lang from users where id=%s", (uid,))["lang"] or lang
    return set_session(RedirectResponse("/en/" if chosen == "en" else "/", status_code=303), uid)


@app.get("/dev-login")
def dev_login():
    if not DEV:
        raise HTTPException(404)
    u = row("select id from users limit 1")
    uid = u["id"] if u else run("insert into users(tg_id,name,created) values(0,'dev',%s) returning id", (int(time.time()),))
    return start_session(uid)


@app.get("/api/config")
def config(request: Request):
    # user — чтобы фронт сразу знал, показывать ли вход, без заведомого 401 на /api/counts;
    # lang — язык браузера по его Accept-Language (SPA шлёт этот запрос без своего заголовка): «Авто» на сайте
    return {"bot": BOT_USERNAME if TOKEN else "", "dev": DEV, "google": GOOGLE_CLIENT_ID,
            "email": bool(SMTP_PASSWORD or DEV), "user": bool(session_user(request)),
            "lang": pages.pick_lang(request.headers.get("accept-language")),
            "version": pages.VERSION_LABEL}  # работающая версия — в меню рядом с «Что нового» (SERBITO-329)


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
    """Выход: сессия удаляется из базы, cookie — из браузера."""
    run("delete from user_sessions where token_hash=%s", (token_hash(request.cookies.get("sid", "")),))
    return clear_session(JSONResponse({"ok": True}))


@app.post("/api/logout/all")
def logout_all(uid: int = Depends(current_user)):
    """«Выйти на всех устройствах» в «Аккаунте» (GTD-5): все сессии аккаунта, включая эту."""
    run("delete from user_sessions where user_id=%s", (uid,))
    return clear_session(JSONResponse({"ok": True}))


@app.get("/api/counts")
def counts(uid: int = Depends(current_user)):
    c = {r["status"]: r["n"] for r in rows(
        "select status, count(*) n from items where user_id=%s group by status", (uid,))}
    checklist = onboarding(uid, c)
    c["scheduled"] = row("select count(*) n from items where user_id=%s and remind_at is not null "
                         "and status not in ('done','trash')", (uid,))["n"]
    c["projects"] = row("select count(*) n from projects where user_id=%s and status='active'", (uid,))["n"]
    c["onboarding"] = checklist
    return c


def onboarding(uid: int, by_status: dict) -> dict:
    """Чек-лист первого запуска (SERBITO-258): пункты отмечаются сами по данным.
    capture — записано ≥3 задач за всё время (счётчик item_seq: удаление галочку не снимает);
    process — хотя бы одна задача не во Входящих (любой другой список, Готово и корзина тоже);
    telegram — к аккаунту привязан Telegram. hidden — карточку больше не показывать: закрыл
    крестиком или выполнил всё; запоминаем навсегда, чтобы она не вернулась, если пункт «откатится»."""
    u = row("select item_seq, tg_id, checklist_hidden from users where id=%s", (uid,))
    steps = {"capture": u["item_seq"] >= 3, "process": any(n for s, n in by_status.items() if s != "inbox"),
             "telegram": bool(u["tg_id"])}
    if all(steps.values()) and not u["checklist_hidden"]:
        run("update users set checklist_hidden=true where id=%s", (uid,))
    return {**steps, "hidden": u["checklist_hidden"] or all(steps.values())}


DUE_DAYS = 3  # «Скоро срок» на экране Inbox (SERBITO-326): сегодня и ещё три дня вперёд


def due_cutoff(now: datetime | None = None) -> int:
    """Граница «Скоро срок»: полночь в часовом поясе приложения (TZ) после третьего дня от сегодняшнего.
    Сегодня понедельник — попадает всё до конца четверга; просроченное — всегда."""
    day = (now or datetime.now(TZ)).astimezone(TZ).date() + timedelta(days=DUE_DAYS + 1)
    return int(datetime.combine(day, dtime(0), TZ).timestamp())


@app.get("/api/items")
def list_items(status: str = "inbox", project_id: int | None = None, done: bool = False,
               uid: int = Depends(current_user)):
    where, args = "i.user_id=%s", [uid]
    if status == "scheduled":
        where += " and i.remind_at is not null and i.status not in ('done','trash')"
        order = "i.remind_at"
    elif status == "due":
        # remind_at — срок задачи. Сначала просроченные (самые давние выше), потом ближайшие
        where += " and i.remind_at is not null and i.remind_at < %s and i.status not in ('done','trash')"
        args.append(due_cutoff())
        order = "i.remind_at, i.id"
    else:
        order = "i.completed_at desc" if status == "done" else "i.position nulls last, i.id"
        if status != "all":
            where += " and i.status=%s"
            args.append(status)
    if project_id:
        where += " and i.project_id=%s"
        args.append(project_id)
        if status == "all":  # экран проекта: без корзины; выполненные — только если он их показывает (done=1)
            where += " and i.status not in ('trash','done')" if not done else " and i.status<>'trash'"
            order = "i.ppos nulls last, i.id"  # свой порядок у проекта
    return rows(f"{ITEM_SQL} where {where} order by {order} limit 500", args)


@app.post("/api/capture")
def api_capture(body: dict, uid: int = Depends(current_user)):
    text = (body.get("text") or "").strip()
    if not text:
        raise HTTPException(400, "empty")
    return capture(uid, text, "web")


@app.patch("/api/items/{iid}")
def patch_item(iid: int, body: dict, uid: int = Depends(current_user)):
    cur = item_get(uid, iid)
    if not cur:
        raise HTTPException(404)
    track(uid, "web")
    # Заголовок из карточки разбираем как строку захвата (SERBITO-298): #проект, @контекст и дата из
    # заголовка перекрывают значения полей, без токена — остаётся выбранное в полях. Только если заголовок
    # изменился: иначе повторное сохранение вернуло бы, например, снятое вручную напоминание («завтра»).
    title = body.get("title")
    if isinstance(title, str) and title.strip() and title.strip() != cur["title"]:
        p = parse_task(uid, title)
        body = {**body, **{k: v for k, v in p.items() if v is not None}}
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
        if k == "project_id" and v and not row("select 1 from projects where id=%s and user_id=%s", (v, uid)):
            raise HTTPException(404, "project not found")
        # Перенос в другой список или проект — в конец его (SERBITO-328)
        if k == "status" and v != cur["status"]:
            sets.append(f"position={END_POS}")
            args += [uid, v]
        if k == "project_id" and v and v != cur["project_id"]:
            sets.append(f"ppos={END_PPOS}")
            args += [uid, v]
        sets.append(f"{k}=%s")
        args.append(v or None if k in ("context", "project_id", "remind_at") else v)
    if sets:
        run(f"update items set {', '.join(sets)} where id=%s and user_id=%s", (*args, iid, uid))
    return item_get(uid, iid)


MOVE_SCOPES = {"list": ("position", "status"), "project": ("ppos", "project_id")}


def _between(prev, nxt, col):
    """Позиция между соседями: середина; у края — на 1024 дальше. None — места нет (зазор исчерпан,
    позиции совпали или соседи не по порядку): список надо перенумеровать."""
    a, b = prev and prev[col], nxt and nxt[col]
    if (prev and a is None) or (nxt and b is None):
        return None
    if prev and nxt:
        m = (a + b) / 2
        return m if a < m < b else None
    return b - 1024 if nxt else a + 1024


@app.post("/api/items/{iid}/move")
def move_item(iid: int, body: dict, uid: int = Depends(current_user)):
    """Ручной порядок (SERBITO-328): задачу поставили между prev (над ней) и next (под ней) — соседями на экране,
    в списке (scope=list) или в проекте (scope=project). Обычно меняется одна строка — позиция задачи."""
    scope = body.get("scope", "list")
    if scope not in MOVE_SCOPES:
        raise HTTPException(400, "bad scope")
    col, key = MOVE_SCOPES[scope]
    it = item_get(uid, iid)
    if not it:
        raise HTTPException(404)
    if it[key] is None:
        raise HTTPException(400, "no project")

    def neighbours():
        got = {}
        for side in ("prev", "next"):
            v = body.get(side)
            if v is None:
                continue
            if not isinstance(v, int) or v == iid:
                raise HTTPException(400, "bad " + side)
            n = item_get(uid, v)  # чужая задача не найдётся
            if not n:
                raise HTTPException(404)
            if n[key] != it[key]:
                raise HTTPException(400, "not in the same " + scope)
            got[side] = n
        return got.get("prev"), got.get("next")

    prev, nxt = neighbours()
    if not prev and not nxt:
        return it
    pos = _between(prev, nxt, col)
    if pos is None:  # редко: перенумеровать список с зазорами в текущем порядке и попробовать ещё раз
        run(f"update items i set {col} = x.rn * 1024 from (select id, row_number() over "
            f"(order by {col} nulls last, id) rn from items where user_id=%s and {key}=%s) x "
            "where i.id = x.id", (uid, it[key]))
        prev, nxt = neighbours()
        pos = _between(prev, nxt, col)
        if pos is None:  # соседи не по порядку — экран устарел
            raise HTTPException(409, "stale order")
    track(uid, "web")
    run(f"update items set {col}=%s where id=%s and user_id=%s", (pos, iid, uid))
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
def patch_project(pid: int, body: dict, request: Request, uid: int = Depends(current_user)):
    project_get(uid, pid)
    if "title" in body:
        title = (body.get("title") or "").strip()
        if not title:
            raise HTTPException(400, tr(req_lang(request, uid), "project_empty"))
        if any(r["id"] != pid and r["title"].casefold() == title.casefold()
               for r in rows("select id, title from projects where user_id=%s", (uid,))):
            raise HTTPException(409, tr(req_lang(request, uid), "project_exists", title=title))
        run("update projects set title=%s where id=%s and user_id=%s", (title, pid, uid))
    if "done_mode" in body:
        if body["done_mode"] not in DONE_MODES:
            raise HTTPException(400, "bad done_mode")
        run("update projects set done_mode=%s where id=%s and user_id=%s", (body["done_mode"], pid, uid))
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
def get_item_by_num(num: int, request: Request, uid: int = Depends(current_user)):
    it = item_by_num(uid, num)  # номера у каждого свои: чужую задачу по ссылке не открыть
    if not it:
        raise HTTPException(404, tr(req_lang(request, uid), "task_missing"))
    return it


# dataLayer, gtag() и consent default (Consent Mode v2) объявляет pages.CONSENT_HEAD — он всегда стоит перед этим тегом
GA_SNIPPET = """<script async src="https://www.googletagmanager.com/gtag/js?id={id}"></script>
<script>
  gtag("js", new Date());
  // Страницы шлёт фронт сам (gaPage): только раздел вида /inbox и заголовок «GTD — раздел».
  // Ни названий задач, ни их номеров из /i/N, ни user_id — поэтому свой page_view выключен.
  // cookie_domain 'none' — _ga только на gtd.serbito.rs, не общий .serbito.rs с соседними продуктами (SERBITO-319)
  gtag("config", "{id}", {{ send_page_view: false, cookie_domain: "none" }});
</script>"""


STATIC = os.path.join(os.path.dirname(__file__), "static")


def ga_snippet(request: Request) -> str:
    """GA не задан (self-hosted копия) — ни GA, ни баннера согласия. Задан — согласие на cookie (SERBITO-319)
    на любом хосте, чтобы баннер был виден и локально, а за ним тег GA — только на боевом домене.
    Порядок важен: consent default раньше gtag('config')."""
    if not GA_ID:
        return ""
    return pages.CONSENT_HEAD + (GA_SNIPPET.format(id=GA_ID) if request.url.hostname == GA_HOST else "")


def signin_methods() -> list[str]:
    """Способы входа, которые покажет лендинг, — те же условия, что /api/config отдаёт SPA."""
    on = {"google": GOOGLE_CLIENT_ID, "email": SMTP_PASSWORD or DEV, "bot": BOT_USERNAME if TOKEN else ""}
    return [m for m in ("google", "email", "bot") if on[m]]


def app_page(request: Request, lang: str = "ru", landing: bool = False) -> HTMLResponse:
    """Страница приложения. GA-сниппет — статично в HTML (чтобы Google видел тег), только на боевом домене.
    landing — гостю вместо пустого экрана входа: SEO-теги и текст лендинга прямо в HTML (pages.py)."""
    with open(os.path.join(STATIC, "index.html"), encoding="utf-8") as f:
        page = f.read().replace("<!--GA-->", ga_snippet(request)).replace("<!--ICONS-->", pages.ICONS)
    page = page.replace('<html lang="ru">', f'<html lang="{lang}">')  # SPA потом поставит язык интерфейса
    if landing:
        page = (page.replace("<title>GTD</title>", pages.head(BASE_URL, lang, "home"))
                .replace("<!--LANDING-->", pages.landing(lang, consent=bool(GA_ID), signin=signin_methods(), dev=DEV)))
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


@app.get("/privacy")
def privacy_page(request: Request):
    return HTMLResponse(pages.privacy(BASE_URL, "ru", ga_snippet(request)))


@app.get("/en/privacy")
def privacy_page_en(request: Request):
    return HTMLResponse(pages.privacy(BASE_URL, "en", ga_snippet(request)))


@app.get("/changes")
def changes_ru(request: Request):
    return HTMLResponse(pages.changes(BASE_URL, "ru", ga_snippet(request)))


@app.get("/en/changes")
def changes_en(request: Request):
    return HTMLResponse(pages.changes(BASE_URL, "en", ga_snippet(request)))


@app.get("/robots.txt")
def robots_txt():
    return PlainTextResponse(pages.robots(BASE_URL))


@app.get("/sitemap.xml")
def sitemap_xml():
    return Response(pages.sitemap(BASE_URL), media_type="application/xml")


# Файлы из static/ по корневым адресам: превью ссылок и иконки (браузеры и Google ищут их в корне).
# Генерируются scripts/og_image.py и scripts/icons.py
ROOT_FILES = {
    "og.png": "image/png", "og-en.png": "image/png",  # превью ссылки 1200×630, русская и английская
    "favicon.ico": "image/x-icon", "favicon.svg": "image/svg+xml", "apple-touch-icon.png": "image/png",
    "icon-192.png": "image/png", "icon-512.png": "image/png", "icon-maskable-512.png": "image/png",
}


def root_file(request: Request):
    name = request.url.path.lstrip("/")
    return FileResponse(os.path.join(STATIC, name), media_type=ROOT_FILES[name],
                        headers={"Cache-Control": "public, max-age=86400"})


for _name in ROOT_FILES:
    app.add_api_route("/" + _name, root_file, methods=["GET"], include_in_schema=False)


@app.get("/manifest.webmanifest", include_in_schema=False)
def web_manifest(request: Request):
    """Манифест для «добавить на экран»: один файл на сайт, описание — на языке браузера (ru по умолчанию)."""
    return JSONResponse(pages.manifest(pages.pick_lang(request.headers.get("accept-language"))),
                        media_type="application/manifest+json",
                        headers={"Vary": "Accept-Language", "Cache-Control": "public, max-age=86400"})


@app.exception_handler(StarletteHTTPException)
async def not_found_page(request: Request, exc: StarletteHTTPException):
    """404 в браузере — страница с логотипом и ссылками вместо сырого JSON. /api/* и запросы без text/html
    в Accept (fetch, curl, боты) получают JSON, как раньше; остальные ошибки — тоже."""
    path = request.url.path
    if exc.status_code != 404 or (path + "/").startswith("/api/") or "text/html" not in request.headers.get("accept", ""):
        return await http_exception_handler(request, exc)
    en = path == "/en" or path.startswith("/en/")
    lang = "en" if en else pages.pick_lang(request.headers.get("accept-language"))
    return HTMLResponse(pages.not_found(lang), status_code=404, headers={"X-Robots-Tag": "noindex"})


@app.get("/i/{num:int}")  # не число (/i/abc) — маршрут не совпадёт, и браузер получит страницу 404, а не 422
def item_page(num: int, request: Request):
    """Ссылка на задачу: та же страница, фронт откроет карточку после входа. Данные — только через API.
    Страница личная и без входа пустая — поисковикам не индексировать."""
    r = app_page(request)
    r.headers["X-Robots-Tag"] = "noindex"
    return r
