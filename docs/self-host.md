# Self-hosting

Run your own GTD with Docker Compose: the app and PostgreSQL 17, one command.
The hosted version is free at [gtd.serbito.rs](https://gtd.serbito.rs) if you'd rather not.

## Quick try (local)

```bash
git clone https://github.com/alxndr-bnd/gtd && cd gtd
DEV=1 docker compose up --build
```

Open http://localhost:8000 and press **Dev login**. `DEV=1` signs you in without any provider and prints
email sign-in codes to the log — **never expose a `DEV=1` instance to the internet**: anyone could sign in
as the first user.

Stop with `docker compose down` (data stays in the `db` volume) or `docker compose down -v` (wipe it).

## Real setup

Put settings in a `.env` file next to `docker-compose.yml` (Compose reads it automatically), then:

```bash
docker compose up -d --build
```

The schema is created and migrated on startup. The database isn't published on the host; it lives in the
`db` volume. Upgrading = `git pull && docker compose up -d --build`.

At least one sign-in method is required: email (SMTP), Google, or the Telegram bot.

### Variables

| Variable | Default | Purpose |
|---|---|---|
| `BASE_URL` | `http://localhost:8000` | Public URL of the site. `https://…` switches the bot to webhook mode (see below) and sets secure cookies. Set it — the app's own fallback is the reference instance |
| `PORT` | `8000` | Host port. Change `BASE_URL` along with it |
| `POSTGRES_PASSWORD` | `gtd` | Database password (the DB isn't exposed, but change it anyway) |
| `TZ` | `Europe/Belgrade` | Time zone for parsing “tomorrow at 10:00” and for reminders |
| `SMTP_HOST`, `SMTP_PORT` | `smtp-relay.brevo.com`, `587` | SMTP server (STARTTLS) for email sign-in codes |
| `SMTP_USER`, `SMTP_PASSWORD` | — | SMTP login. Email sign-in is on only when `SMTP_PASSWORD` is set (or `DEV=1`) |
| `EMAIL_FROM` | `"GTD" <gtd@localhost>` | Sender of sign-in emails, e.g. `"GTD" <gtd@example.com>` |
| `GOOGLE_CLIENT_ID` | — | Sign in with Google. OAuth client (Web) with your `BASE_URL` in Authorized JavaScript origins |
| `TELEGRAM_BOT_TOKEN` | — | Bot token from @BotFather. Optional: without it the bot is off and the site works as usual |
| `CRON_SECRET` | — | Secret for `POST /tasks/reminders`; needed only in webhook mode |
| `ADMIN_USER_IDS` | — | Comma-separated account ids that see the in-app Statistics |
| `SENTRY_DSN` | — | Send errors to your Sentry |
| `DEV` | — | `1` = dev login and codes in the log. Local only |

`GA_MEASUREMENT_ID` is ignored outside the reference domain, so self-hosted instances send nothing to Google Analytics.

## Telegram bot: polling vs webhook

The mode follows `BASE_URL`, as in `app.py`:

- **`http://…` → long polling.** Works behind NAT, no public address needed. Reminders are sent by a loop
  inside the app every 15 s. If the bot already has a webhook set (e.g. it was used with an https instance),
  polling refuses to start so it doesn't steal updates — remove it with
  `curl "https://api.telegram.org/bot<TOKEN>/deleteWebhook"`.
- **`https://…` → webhook.** On startup the app registers `BASE_URL/tg/webhook` with Telegram (the secret
  header is derived from the token) and syncs the bot's description and command menu. Telegram needs a
  valid certificate on port 443, 80, 88 or 8443 — put a reverse proxy (Caddy, nginx, Traefik) in front of
  the app port. In this mode there's **no in-process reminder loop**: something must call
  `POST /tasks/reminders` with header `X-Cron-Secret: $CRON_SECRET` every minute. The bundled `reminders`
  service does exactly that:

  ```bash
  echo "CRON_SECRET=$(openssl rand -hex 32)" >> .env
  docker compose --profile https up -d --build
  ```

The website runs the same in both modes, with or without the bot.

## Checks

```bash
curl -s localhost:8000/api/config   # {"bot": "...", "google": "...", "email": true, ...}
docker compose logs app             # "TELEGRAM_BOT_TOKEN не задан — бот выключен" = bot off, as intended
```

Production on Google Cloud Run (the reference instance) is described in [deploy-gcp.md](deploy-gcp.md).
