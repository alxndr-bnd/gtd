# Production deploy: Google Cloud Run

How the reference instance, [gtd.serbito.rs](https://gtd.serbito.rs), is built, deployed and operated.
To run your own copy on any Docker host, see [self-host.md](self-host.md) instead.

## Overview

| Piece | Where |
|---|---|
| App | Cloud Run service `gtd`, region `europe-west1`, project `serbito` |
| Image | Artifact Registry `europe-west1-docker.pkg.dev/serbito/gtd/gtd:<sha>` |
| Database | Cloud SQL PostgreSQL 17, instance `serbitodb`, database `gtd` |
| Bot | Telegram webhook `/tg/webhook` |
| Reminders | Cloud Scheduler job `gtd-reminders`, every minute |
| Errors | Sentry project `nohandoff/gtd` |
| Dependencies | Dependabot (`.github/dependabot.yml`: pip, docker, actions — weekly) |

## Release

```bash
scripts/release_minor.sh "What changed" [new_file …]
```

Tests → `git add -u` (new files only when listed explicitly) → commit → next tag `vX.Y.0` → push →
GitHub Release with generated notes (skipped with a warning if `gh` is missing or fails).
Releases are cut from `main` only.

The tag triggers `.github/workflows/deploy.yml`:

1. Build the image.
2. Trivy scan — HIGH/CRITICAL vulnerabilities with an available fix, or leaked secrets, block the deploy.
3. Deploy a new revision with no traffic (tag `candidate`).
4. Warm it up until `/api/config` returns 200.
5. Switch 100% of traffic to that revision by name.

If the candidate doesn't answer, the previous revision keeps serving. The first deploy (no service yet)
goes straight to 100% and is then probed. There is no `workflow_dispatch`: unreleased code can't bypass
the tag. GCP auth is keyless (Workload Identity Federation) — no keys anywhere.

## Runtime

- The bot runs on a **webhook** (`/tg/webhook`); the secret header is derived from the bot token.
- Reminders are woken by **Cloud Scheduler** `gtd-reminders` once a minute:
  `POST /tasks/reminders` with `X-Cron-Secret`.
- No background loops, so `min-instances=0` and CPU only during requests: without traffic the service sleeps.
- Locally (http `BASE_URL`) the bot uses long polling and refuses to start if the bot already has the
  production webhook set, so it never steals updates.
- On startup production syncs the bot description, short description and command menu from the code
  (`BOT_PROFILE`, `BOT_COMMANDS`) and changes only what differs; a local server never touches the bot profile.

Configuration:

- Non-secret env vars — `.github/deploy.env.yaml` (`BASE_URL`, `TZ`, `SMTP_USER`, `ADMIN_USER_IDS`,
  `GA_MEASUREMENT_ID`). `SENTRY_RELEASE` is added from the tag (`v0.3.0` → `gtd@0.3.0`).
- Secrets — Secret Manager via `--set-secrets` in the workflow: `gtd-database-url`, `GOOGLE_CLIENT_ID`,
  `EMAIL_HOST_PASSWORD` (→ `SMTP_PASSWORD`), `gtd-sentry-dsn`, `gtd-cron-secret`, and
  `gtd-telegram-bot-token` (optional: while the secret has no enabled version, the service deploys without the bot).

## Database

PostgreSQL 17 on Cloud SQL (`serbitodb`), separate database `gtd`, one variable `DATABASE_URL`.
The schema is created and migrated on startup.

- Cloud Run connects through the unix socket:
  `postgresql://gtd:PASSWORD@/gtd?host=/cloudsql/serbito:europe-west1:serbitodb`
- A local server connects to the production database **only explicitly**:

  ```bash
  DATABASE_URL="$(sed -n 's/^DATABASE_URL_PROD=//p' .env)" .venv/bin/uvicorn app:app --env-file .env
  ```

  This works while your IP is in the Authorized networks of `serbitodb` (currently `home`). If your IP
  changed, add the new one (careful: `gcloud sql instances patch` overwrites the whole list!) or run
  `cloud-sql-proxy serbito:europe-west1:serbitodb --port 5433 --gcloud-auth` and connect to `127.0.0.1:5433`.

Why local-by-default: a local server applies the schema on startup, and unreleased code must not change
production. On 2026-09-25 columns reached the production database before the release and broke a query
in the running revision (Sentry GTD-1).

## Email

Sign-in codes go through Brevo SMTP (serbito account, sender `info@serbito.rs`), shared with serbito:
on the free plan the 300 emails/day limit covers both projects.

## One-time setup

1. @BotFather → `/newbot` → token.
2. `scripts/setup_gcp.sh` — Artifact Registry `gtd`, service accounts `gtd-deployer` / `gtd-run`, roles,
   secrets `gtd-database-url`, `gtd-telegram-bot-token` (asks for the token; takes the DB password from
   `.env`), `gtd-sentry-dsn`, `gtd-cron-secret`, access to the shared `GOOGLE_CLIENT_ID` /
   `EMAIL_HOST_PASSWORD`, the repo in the WIF condition, and the Cloud Scheduler job `gtd-reminders`.
   Idempotent.
3. Google Cloud Console → APIs & Services → Credentials → the serbito OAuth client (the one whose ID is in
   `GOOGLE_CLIENT_ID`) → Authorized JavaScript origins: `https://gtd.serbito.rs`, `http://localhost:8000`,
   `http://localhost`.
4. First release, then the domain:
   `gcloud beta run domain-mappings create --service gtd --domain gtd.serbito.rs --region europe-west1`
   and DNS `CNAME gtd → ghs.googlehosted.com.` (on Cloudflare — DNS only, no proxy, otherwise Google
   won't issue the certificate).

## Monitoring and analytics

- **Sentry** — `nohandoff/gtd`, DSN in secret `gtd-sentry-dsn`, release `gtd@X.Y.Z` from the tag.
- **Google Analytics 4** — Serbito account → property `gtd.serbito.rs` (`G-CP9WBRWGD6`, `GA_MEASUREMENT_ID`).
  The tag loads only on the production domain.
- **Statistics** in the app — for the owner only: `ADMIN_USER_IDS` in `.github/deploy.env.yaml`
  (account id, not email; production owner is id 3).
- **Cloudflare Web Analytics** — visits, no cookies.

What these may and may not collect: see [internals.md](internals.md#analytics).
