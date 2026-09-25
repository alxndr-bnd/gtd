# GTD for free — gtd.serbito.rs

Веб-UI + Telegram-бот. FastAPI + PostgreSQL, один контейнер.

## Локальный запуск
1. `python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt`
2. Прокси к Cloud SQL: `cloud-sql-proxy serbito:europe-west1:serbitodb --port 5433`
3. `cp .env.example .env`, вписать пароль в `DATABASE_URL`.
4. `DEV=1 BASE_URL=http://localhost:8000 uvicorn app:app --reload --reload-exclude "$PWD/.venv"`

`--reload-exclude` обязателен: без него watcher видит `.venv` и сервер перезапускается по кругу.
С `DEV=1` авторизация не нужна — логинит в первого пользователя.

## Деплой (Cloud Run)
1. DNS: `gtd.serbito.rs` → Cloud Run domain mapping.
2. @BotFather → `/newbot` → токен.
3. Секреты в Secret Manager, база — `gtd` на инстансе `serbitodb`.
4. `gcloud run deploy gtd --source . --region europe-west1 --add-cloudsql-instances serbito:europe-west1:serbitodb`
5. Написать боту `/start` (первый пользователь = владелец), затем `/login` → ссылка входа в UI.

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

## UI
Inbox · Next (фильтр по @контексту) · Waiting · Календарь/напоминания · Проекты (⚠ без next action) · Someday · Reference · Готово · Weekly Review.

## Дальше (идеи)
Голосовые → текст, повторяющиеся задачи, вебхук вместо long polling, экспорт/импорт.
