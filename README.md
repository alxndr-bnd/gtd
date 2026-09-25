# GTD for free — gtd.serbito.rs

Веб-UI + Telegram-бот. FastAPI + SQLite, один контейнер, без внешних зависимостей.

## Деплой (VPS с Docker)
1. DNS: A-запись `gtd.serbito.rs` → IP сервера.
2. @BotFather → `/newbot` → токен.
3. `cp .env.example .env`, вписать `TELEGRAM_BOT_TOKEN`.
4. `docker compose up -d --build` (Caddy сам выпустит TLS).
5. Написать боту `/start` (первый пользователь = владелец), затем `/login` → ссылка входа в UI.

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
