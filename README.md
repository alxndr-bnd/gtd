# GTD for free — gtd.serbito.rs

Веб-UI + Telegram-бот. FastAPI + PostgreSQL, один контейнер.

## Локальный запуск
Один раз: `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`,
`cp .env.example .env` и вписать пароль в `DATABASE_URL`. Дальше:

```bash
.venv/bin/uvicorn app:app --reload --reload-exclude .venv --env-file .env
```

База — **боевая** `gtd`, напрямую по публичному IP `serbitodb` (без прокси). Работает, пока твой IP
в Authorized networks инстанса (сейчас там `home`). Сменился IP — добавить новый
(`gcloud sql instances patch` перезаписывает весь список!) или поднять
`cloud-sql-proxy serbito:europe-west1:serbitodb --port 5433 --gcloud-auth` и ходить на `127.0.0.1:5433`.

С `DEV=1` на экране входа есть кнопка Dev login (первый пользователь), а без `SMTP_PASSWORD`
код входа по почте пишется в лог сервера вместо письма.
`--reload-exclude .venv` обязателен: без него watcher видит `.venv` и сервер перезапускается по кругу.

## Релиз
```bash
scripts/release_minor.sh "Что поменялось" [новый_файл …]
```
Как в serbito: `git add -u` (новые файлы — только явно) → коммит → следующий тег `vX.Y.0` → push.
Тег запускает `.github/workflows/deploy.yml`: сборка образа → Trivy (HIGH/CRITICAL с фиксом
блокируют) → новая ревизия без трафика → прогрев до 200 на `/api/config` → переключение трафика.
Не ответила — работает прежняя ревизия. Авторизация в GCP keyless (WIF), ключей нигде нет.

Сервис держит один всегда включённый инстанс (`min=max=1`, CPU без троттлинга): бот — long polling,
напоминания — фоновая задача, без этого они засыпают между запросами.

### Разовая настройка
1. @BotFather → `/newbot` → токен.
2. `scripts/setup_gcp.sh` — Artifact Registry `gtd`, сервисные аккаунты `gtd-deployer` / `gtd-run`,
   роли, секреты `gtd-database-url` и `gtd-telegram-bot-token` (спросит токен; пароль БД возьмёт
   из `.env`), доступ к общим `GOOGLE_CLIENT_ID` / `EMAIL_HOST_PASSWORD`, репо в условие WIF.
3. Google Cloud Console → APIs & Services → Credentials → OAuth-клиент serbito (тот, чей ID в секрете
   `GOOGLE_CLIENT_ID`) → Authorized JavaScript origins: `https://gtd.serbito.rs`,
   `http://localhost:8000`, `http://localhost`.
4. Первый релиз, затем домен:
   `gcloud beta run domain-mappings create --service gtd --domain gtd.serbito.rs --region europe-west1`
   и DNS `CNAME gtd → ghs.googlehosted.com.` (если DNS на Cloudflare — без прокси, иначе Google не выпустит сертификат).

## Аккаунты
Регистрация открыта: первый вход любым способом создаёт аккаунт.
- **Google** — Sign in with Google; ID-токен проверяется через `oauth2.googleapis.com/tokeninfo`.
- **Почта** — 6-значный код через Brevo SMTP (аккаунт serbito, отправитель `info@serbito.rs`).
  10 минут, 5 попыток, повторно — через минуту, не больше 5 писем за 10 минут с одного IP.
- **Telegram** — кнопка на сайте → бот → «Подтвердить»; страница ловит подтверждение сама.
  `/login` в боте тоже работает.

Google и код на один адрес — один аккаунт. Остальное привязывается в «👤 Аккаунт». Способ входа,
занятый другим аккаунтом, забирается, только если тот пустой (без задач и проектов).
Письма идут с общего Brevo serbito: если тариф бесплатный, лимит 300 писем/день — на оба проекта.

## База данных
PostgreSQL 17 на Cloud SQL (`serbitodb`), отдельная база `gtd`. Подключение одной
переменной `DATABASE_URL`: локально через прокси на `127.0.0.1:5433`, в Cloud Run —
через unix-сокет `/cloudsql/...`. Схема создаётся сама при старте.

## Захват в Telegram
- `Позвонить в банк завтра в 10:00` → Inbox + напоминание
- `через 2 часа проверить деплой`, `в пятницу`, `24.10 12:00`, `2026-10-24`
- `отчёт #Клиент_X @работа` → сразу Next, проект и контекст
- Кнопки под сообщением: ✅ Готово / 💤 +1ч / ⏭ Next
- `/inbox`, `/next`, `/done 12`, `/login`
- Любой, кто напишет боту, получает свой аккаунт

## UI
Inbox · Next (фильтр по @контексту) · Waiting · Календарь/напоминания · Проекты (⚠ без next action) · Someday · Reference · Готово · Weekly Review.

## Дальше (идеи)
Голосовые → текст, повторяющиеся задачи, вебхук вместо long polling, экспорт/импорт.
