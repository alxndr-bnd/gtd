# GTD — free Getting Things Done app

A free task manager built around David Allen's GTD method: capture everything in a second, clarify it
later, do the next action. On the web and in Telegram, one account for both. Hosted at
**[gtd.serbito.rs](https://gtd.serbito.rs)** · bot [@gtdsrbot](https://t.me/gtdsrbot).

FastAPI + PostgreSQL, one container. English and Russian.

![Landing page](docs/img/landing.png)

![Inbox](docs/img/app-inbox.png)

![Next actions filtered by context](docs/img/app-next.png)

## Features

- **Quick capture** with natural dates: `call mom tomorrow at 10am #Family @phone` → task, reminder, project, context.
- **GTD lists:** Inbox · Next (filter by @context) · Waiting · Calendar/reminders · Projects (⚠ without a next
  action) · Someday · Reference · Done · Weekly Review.
- **Telegram bot:** send a message — it lands in the Inbox; reminders come with ✅ Done / 💤 +1h / ⏭ Next buttons.
- **Sign-in** by email code, Google or Telegram; methods link into one account, accounts can be merged.
- **AI assistants:** connect Claude (or any MCP client) with a personal token — see [Connect Claude](#connect-claude-mcp).
- **Keyboard-first** web app, installable to the phone home screen.
- **Privacy:** analytics never include task content, emails or user ids.

Details for contributors: [docs/internals.md](docs/internals.md). What changed in each version:
[CHANGELOG.md](CHANGELOG.md) (also on the site: [What's new](https://gtd.serbito.rs/en/changes)).

## Connect Claude (MCP)

Claude and other MCP clients can work with your tasks: capture to the Inbox, list and search tasks, projects and
contexts, complete a task, move it to another list, project or context.

1. In the app, open **👤 Account → 🤖 AI assistants (MCP)**. Enter a token name and press **Create token**.
   Copy the token at once: the app shows it only once.
2. Connect the client. The server address is `https://gtd.serbito.rs/mcp` (on your own server — `BASE_URL/mcp`).
   - **Claude Code:** the app shows this command with your token filled in:
     ```bash
     claude mcp add --transport http gtd https://gtd.serbito.rs/mcp --header "Authorization: Bearer gtd_…"
     ```
   - **Other clients** with remote MCP servers: transport “Streamable HTTP”, the address above, and the header
     `Authorization: Bearer gtd_…`.
   - **Clients that start only local servers** (for example, Claude Desktop through its config file): use the
     `mcp-remote` bridge — command `npx`, arguments
     `["mcp-remote", "https://gtd.serbito.rs/mcp", "--header", "Authorization: Bearer gtd_…"]`.
3. Ask, for example: “add ‘call the bank tomorrow 10am’ to my inbox”, “what are my next actions @phone?”,
   “complete #42”, “move #17 to the Renovation project”.

| Tool | What it does |
| --- | --- |
| `capture` | Captures a task, like the app and the bot: dates, `#Project` and `@context` in the text work |
| `list_tasks` | Lists a list (Inbox, Next, Waiting, Scheduled, Someday, Reference, Done, all); filters by project, context, text |
| `list_projects` | Active projects with counts; marks projects without a next action |
| `list_contexts` | Contexts with counts of open tasks |
| `complete_task` | Marks task #N done |
| `move_task` | Moves task #N to a list, a project (created if missing) and/or a context |

A token opens every task of its account. The database keeps only its SHA-256 hash. “👤 Account” shows when each
token was last used; **Revoke** cuts access at once (“Sign out on all devices” does not revoke tokens). The limit is
120 requests a minute per token. Connectors on claude.ai (sign-in with OAuth instead of a token) are planned:
[docs/plans/2026-10-03-mcp-oauth.md](docs/plans/2026-10-03-mcp-oauth.md).

## Run locally

Once: `python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt`, `cp .env.example .env`,
`createdb gtd`. Then — local database, auto-reload on every change:

```bash
.venv/bin/uvicorn app:app --reload --reload-exclude .venv --env-file .env
```

- `.env` points to the **local** database by default. The server applies the schema on startup, and
  unreleased code must not change production. Connecting to production is explicit only — see
  [docs/deploy-gcp.md](docs/deploy-gcp.md#database).
- `--reload-exclude .venv` is required: without it the watcher sees `.venv` and restarts the server in a loop.
- With `DEV=1` the sign-in screen has a **Dev login** button (first user), and without `SMTP_PASSWORD` the email
  sign-in code goes to the server log instead of an email.
- With `DEV=1` live reload works: editing `.py` restarts the server, editing `static/` doesn't, but either way
  the open page refreshes itself within ~1 s (it polls `/api/dev/version`). Text typed in the capture field is
  kept; an open task card postpones the refresh.
- Locally (http `BASE_URL`) the bot uses long polling and won't start if the bot has a production webhook.

Or with Docker: `DEV=1 docker compose up --build` — see [self-hosting](docs/self-host.md).

## Tests

```bash
.venv/bin/playwright install chromium   # once: the browser for the SPA smoke test
.venv/bin/python -m pytest
```

A temporary database in the local Postgres (`brew services start postgresql@17`, or `TEST_PG_URL`) is created
and dropped automatically; Telegram, Google and email are stubbed. `tests/test_browser_smoke.py` opens every
section of the app and the public pages in headless Chromium and fails on any JS error. The release script runs
the tests before tagging.

CI ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)) runs on every pull request and every push to `main`:
the full pytest with the browser smoke, a lock check and an image build.

## Dependencies

Versions are locked with hashes. You edit `requirements*.in`; `requirements*.txt` are generated, do not edit them.

| File | What | Installed by |
|---|---|---|
| `requirements.in` → `requirements.txt` | app dependencies | the Docker image (`pip install --require-hashes`) |
| `requirements-dev.in` → `requirements-dev.txt` | the same versions plus pytest and Playwright | developers and CI |

Update the lock with [uv](https://docs.astral.sh/uv/) (`brew install uv`), then reinstall and commit `.in` and `.txt` together:

```bash
scripts/lock.sh                            # after an edit in requirements*.in
scripts/lock.sh --upgrade-package fastapi  # one package to its latest version
scripts/lock.sh --upgrade                  # all packages
.venv/bin/pip install -r requirements-dev.txt
```

- CI fails if the lock does not match `requirements*.in`.
- Dependabot opens weekly PRs for the lock (ecosystem `uv`), the base image digest and the actions.
- The base image is pinned as `python:3.14-slim@sha256:…`. Dependabot bumps the digest. The deploy still runs
  `apt-get upgrade` once a day, so Debian security fixes do not wait for a new digest. A new Python minor version
  is a manual change: the `FROM` tag and `--python-version` in `scripts/lock.sh`.

## Releasing

1. With every user-facing change, add a line under `## [Unreleased]` in [CHANGELOG.md](CHANGELOG.md):
   `### Added` / `Changed` / `Fixed` / `Security`, one line `- English text` and right under it
   `  - RU: Русский текст`. Write what users can now do or what got fixed — not commit messages.
2. On `main`: `scripts/release_minor.sh "Commit message" [new_file …]`. It picks the next tag `vX.Y.0` and
   **refuses to release** if `[Unreleased]` has no entries. Otherwise it renames `[Unreleased]` to
   `## [X.Y.0] - <today>` (with a fresh empty `[Unreleased]` on top), runs the tests, commits everything
   in one release commit, tags, pushes, and creates the GitHub Release with that version's English entries.
3. The tag deploys ([docs/deploy-gcp.md](docs/deploy-gcp.md)) with `APP_VERSION` from the tag: the footer,
   the app menu and `/changes` show it (`dev` when unset, e.g. locally).
4. The deploy fails for a tag without its `## [X.Y.Z]` section in CHANGELOG.md, so a tag pushed by hand
   cannot ship without notes.

`/changes` and `/en/changes` render CHANGELOG.md, parsed once at startup by `changelog.py`;
`tests/test_changelog.py` checks the format, both languages and that every tag has an entry.

## Deploy

- **Your own server:** [docs/self-host.md](docs/self-host.md) — Docker Compose, app + Postgres, bot optional.
- **Reference instance** (Google Cloud Run, release by tag): [docs/deploy-gcp.md](docs/deploy-gcp.md).

## License

[MIT](LICENSE) © 2026 Alexander Bondarchuk — free to use, modify and distribute, including commercially,
as long as the copyright and license text are kept.

GTD® and Getting Things Done® are trademarks of the David Allen Company. This project is independent and not
affiliated with the author of the method.

## Other projects

Also by No Handoff:
- **Planning Poker** — free planning poker for scrum teams, no sign-up:
  [poker.serbito.rs](https://poker.serbito.rs/?utm_source=github&utm_medium=crosspromo&utm_campaign=readme) ·
  [GitHub](https://github.com/alxndr-bnd/planning-poker)
- **Javi** — delivery notifications for small businesses in Serbia:
  [javi.serbito.rs](https://javi.serbito.rs/?utm_source=github&utm_medium=crosspromo&utm_campaign=readme) ·
  [GitHub](https://github.com/alxndr-bnd/javi)
- **Serbito** — classifieds in Serbia:
  [serbito.rs](https://serbito.rs/?utm_source=github&utm_medium=crosspromo&utm_campaign=readme)

Made by [No Handoff](https://www.linkedin.com/company/nohandoff/)
