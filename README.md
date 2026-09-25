# GTD for free — gtd.serbito.rs

Веб-UI + Telegram-бот. FastAPI + PostgreSQL, один контейнер.

## Локальный запуск
Один раз: `python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt`,
`cp .env.example .env` (пароль в `DATABASE_URL` — только для запуска на боевой базе), `createdb gtd`.

Для быстрых итераций — локальная база, автоперезагрузка на каждую правку:
```bash
DATABASE_URL=postgresql://localhost/gtd .venv/bin/uvicorn app:app --reload --reload-exclude .venv --env-file .env
```
На **боевой** базе — та же команда без `DATABASE_URL=…`: берётся из `.env`, напрямую по публичному IP
`serbitodb`. Работает, пока твой IP в Authorized networks инстанса (сейчас там `home`). Сменился IP —
добавить новый (`gcloud sql instances patch` перезаписывает весь список!) или поднять
`cloud-sql-proxy serbito:europe-west1:serbitodb --port 5433 --gcloud-auth` и ходить на `127.0.0.1:5433`.

С `DEV=1` на экране входа есть кнопка Dev login (первый пользователь), а без `SMTP_PASSWORD`
код входа по почте пишется в лог сервера вместо письма.
`--reload-exclude .venv` обязателен: без него watcher видит `.venv` и сервер перезапускается по кругу.

## Тесты
```bash
.venv/bin/python -m pytest
```
Временная база в локальном Postgres (`brew services start postgresql@17`), создаётся и удаляется сама;
Telegram, Google и почта — заглушки. Гоняются в `release_minor.sh` перед тегом.

## Релиз
```bash
scripts/release_minor.sh "Что поменялось" [новый_файл …]
```
Как в serbito: тесты → `git add -u` (новые файлы — только явно) → коммит → следующий тег `vX.Y.0` → push.
Тег запускает `.github/workflows/deploy.yml`: сборка образа → Trivy (HIGH/CRITICAL с фиксом
блокируют) → новая ревизия без трафика → прогрев до 200 на `/api/config` → переключение трафика.
Не ответила — работает прежняя ревизия. Авторизация в GCP keyless (WIF), ключей нигде нет.

Ошибки — в Sentry (`nohandoff/gtd`, DSN в секрете `gtd-sentry-dsn`, релиз `gtd@X.Y.Z` из тега).
Зависимости обновляет Dependabot (`.github/dependabot.yml`: pip, docker, actions — раз в неделю).

Бот в проде — на **вебхуке** (`/tg/webhook`, секретный заголовок выводится из токена), напоминания
будит **Cloud Scheduler** `gtd-reminders` раз в минуту (`POST /tasks/reminders` с `X-Cron-Secret`).
Фоновых циклов нет, поэтому `min-instances=0` и CPU только на время запросов: без трафика сервис спит.
Локально (http `BASE_URL`) бот работает long polling'ом и сам отказывается, если у бота уже стоит
вебхук прода — чтобы не увести апдейты.

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

Google и код на один адрес — один аккаунт. Остальное привязывается в «👤 Аккаунт» или в боте:
`/email you@example.com` → код на почту → прислать код боту.

Если способ входа уже у другого аккаунта: пустой — забираем молча (и сами переезжаем в аккаунт
с данными, если свой пустой и ничего не теряется); с задачами — предлагаем **объединить** (в вебе —
кнопкой, в боте — «🔗 Объединить»). Подтверждает только тот, кто начал; объединение одной транзакцией:
задачи, проекты (одноимённые склеиваются), сессии и недостающие способы входа переезжают в один аккаунт.
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
- Кнопки под полем ввода: 🚀 Начать и 📧 Добавить email
- `/inbox`, `/next`, `/done 12`, `/login`, `/email`
- Любой, кто напишет боту, получает свой аккаунт

## UI
Inbox · Next (фильтр по @контексту) · Waiting · Календарь/напоминания · Проекты (⚠ без next action) · Someday · Reference · Готово · Weekly Review.

## Дальше (идеи)
Голосовые → текст, повторяющиеся задачи, вебхук вместо long polling, экспорт/импорт.
