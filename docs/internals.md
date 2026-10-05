# Internals

Notes for contributors: how sign-in, languages, public pages and analytics work.

## Accounts and sign-in

Sign-up is open: the first sign-in by any method creates an account.

- **Google** — Sign in with Google; the ID token is checked via `oauth2.googleapis.com/tokeninfo`.
- **Email** — a 6-digit code over SMTP. Valid 10 minutes, 5 attempts, resend after a minute,
  at most 5 emails per 10 minutes per IP, and `EMAIL_DAILY_CAP` emails a day in total. Without `SMTP_PASSWORD` and with `DEV=1` the code goes to the server log.
- **Telegram** — button on the site → bot → tap the number the site shows (one of three buttons; a wrong one
  cancels). The bot message says which browser and IP asked and, for linking, which account. Only the browser
  that started (cookie `tgl`) can pick up the session. On gtd.serbito.rs desktop — the Telegram Login Widget
  (a signature is valid 10 minutes and once). The widget uses `data-auth-url`, not `data-onauth` (SERBITO-316):
  it returns to the same page with the signed fields in the fragment `#tgw?…`. The SPA removes the fragment and
  sends the fields to `POST /api/auth/tg/widget`, so they do not reach request logs. The fragment carries
  `_s`, a per-tab secret from `sessionStorage`: a link with someone else's signed fields signs in or links nothing.
  `/login` in the bot sends a link to a page that shows whose account it opens and signs in only with its button (POST).
- **Sessions** — cookie `sid`; the database keeps only its SHA-256. Expire after 90 days without activity
  (sliding). Sign out deletes the session and the cookie; “Sign out on all devices” in “👤 Account” deletes all.
- **Requests** — state-changing `/api/*` and `POST /auth` must be same-origin (`Sec-Fetch-Site`/`Origin`);
  `/api/*` bodies must be `application/json`. Every response carries security headers; the CSP is report-only
  for now (reports go to Sentry when `SENTRY_DSN` is set). `/docs` and `/openapi.json` exist only with `DEV=1`.
- **Known CSP reports** (SERBITO-412). The browser sends them straight to Sentry's security endpoint, not
  through our server, so our code cannot filter them.
  - `script-src` eval (GTD-2): `telegram.org/js/telegram-widget.js` line 6. The Login Widget compiled our
    `data-onauth` callback with `eval`. Fixed in SERBITO-316: the widget now uses `data-auth-url` and calls
    no `eval`. New GTD-2 events after that release are a regression. Many old reports came from local
    headless Chrome runs against prod.
  - `font-src` frontend-cdn.perplexity.ai (GTD-4): the Perplexity browser or extension injects its font into
    the page. Not ours. Filter in Sentry, gtd and serbito projects: Settings → Security Headers → CSP →
    "Use default ignored sources" on, "Additional ignored sources" + `frontend-cdn.perplexity.ai`.
- **Bot** answers only in private chats.

**Sign-in order** (SERBITO-448, owner decision 2026-10-05): Google, the email code, then Telegram — the order
from before; no single filled "primary" button. The audit suggested one primary action (Telegram, since it also
connects the bot); the owner kept the old order. The order lives in `loginScreen` (index.html) and in
`app.signin_methods` (the landing reserves height for it).

Google and an email code for the same address are one account. Other methods are linked under
“👤 Account” or in the bot: `/email you@example.com` → code by email → send the code to the bot.

If a sign-in method already belongs to another account: an empty one is taken over silently (and we move
into the account with data if ours is empty and nothing is lost — except when linking Telegram via the bot
link, where the person confirming isn't the one in the browser); one with tasks — we offer to **merge**
(a button on the web, “🔗 Merge” in the bot). Only the initiator confirms. The merge is one transaction:
tasks, projects (same-name projects are joined), sessions and missing sign-in methods move into one account.

## MCP for AI assistants

`/mcp` — an MCP server for Claude and other clients (SERBITO-375), code in `mcp_server.py` (transport, tools) and
`oauth.py` (OAuth 2.1), user guide in the README.

- **Auth** — `Authorization: Bearer …` with one of two token kinds:
  - **personal API token** `gtd_…` from “👤 Account” (`/api/tokens`, cookie session only). The table `api_tokens`
    keeps the SHA-256 of the token, its name, `created` and `last_used` (written at most once a minute). Revoke
    deletes the row.
  - **OAuth access token** `gtdo_…` (claude.ai custom connectors, ChatGPT, Cursor, VS Code, Claude Code without a
    header). See below.

  The session cookie does not open `/mcp`; no token opens `/api/*`. Without a valid token `/mcp` answers 401 with
  `WWW-Authenticate: Bearer resource_metadata="BASE_URL/.well-known/oauth-protected-resource/mcp", scope="tasks"`.
- **OAuth 2.1** (`oauth.py`, design: [docs/plans/2026-10-03-mcp-oauth.md](plans/2026-10-03-mcp-oauth.md)) — gtd
  is its own authorization server; issuer = `BASE_URL`, resource = `BASE_URL/mcp`, one scope `tasks`.
  - Metadata: `/.well-known/oauth-protected-resource` (also `…/mcp`, RFC 9728) and
    `/.well-known/oauth-authorization-server` (RFC 8414). Only these have CORS `*`. Models from the `mcp` SDK.
  - `POST /register` (RFC 7591): public clients only (`token_endpoint_auth_method` is always `none`). Any client can
    register; the consent screen is the gate. Redirect URIs (`redirect_kind`), 1–10 per client, ≤ 300 chars, ASCII,
    no `#`, `*`, `\`, whitespace or user info:
    - `web` — `https://` with a normal DNS name (at least one dot), not loopback and not gtd's own host;
    - `loopback` — `http://localhost|127.0.0.1|[::1]`, any port (Claude Code, Claude Desktop, VS Code);
    - `app` — a custom scheme from `APP_SCHEMES` only: `cursor://`, `vscode://`, `vscode-insiders://`; a host is
      required, no port. Claude Desktop has no own scheme: it uses the claude.ai callback or loopback.

    `javascript:`, `data:`, `file:` and other schemes are rejected. 10 registrations per IP an hour. Clients without
    connections and unused for 90 days are deleted on the next registration.
  - `GET /authorize`: unknown client or an unregistered redirect URI → error page, never a redirect. The match is
    exact (case, path, query, port). One exception (RFC 8252 §7.3): a loopback URI matches a registered loopback URI
    with any port; scheme, host, path and query still match. `/token` then needs the exact URI used at `/authorize`.
    Other errors go back to the client with `error`, `state` and `iss` (RFC 9207). PKCE S256 only; `resource`, if
    given, must be `/mcp`. Not signed in → a page that saves its own URL in `sessionStorage` (`gtd-oauth-next`) and
    sends the user to sign in; after any sign-in method the SPA (`oauthResume`) returns to `/authorize` (same-site
    path only, 30 minutes). Signed in → consent page: client name, account, return host (`redirect_host`: host, or
    `scheme://host` for an app); Allow / Deny. The client name is self-declared, so every redirect except the two
    Claude callbacks gets a warning (`#oauth-warn`): GTD does not check the name; for loopback, any local program can
    get the code.
  - `POST /authorize` — the consent form: same-origin only (403 otherwise), re-validates every parameter.
    Consent pages use `Referrer-Policy: same-origin` (with `no-referrer` the browser sends `Origin: null` and the
    same-origin check fails); the redirect with the code uses `no-referrer`.
  - `POST /token` (form body): `authorization_code` (code single use, 10 minutes, bound to client, redirect URI,
    PKCE verifier and resource) and `refresh_token` (rotates on every use). Access token 1 hour, refresh token
    90 days. 30 requests per client a minute.
  - Theft signals revoke the whole connection: a reused code, a reused (already rotated) refresh token, a refresh
    token from another client.
  - `POST /revoke` (RFC 7009): always 200; a refresh token revokes the connection, an access token only itself.
  - Tables: `oauth_clients`; `oauth_grants` (one connection per account and client — new consent replaces the old
    one); `oauth_codes` and `oauth_tokens` (SHA-256 only). Tokens live in their own table, not in `api_tokens` as
    the design first said: the personal-token list and its limit stay untouched. Grants and codes move with an
    account merge.
  - “👤 Account → Connected apps”: `GET /api/oauth/apps`, `DELETE /api/oauth/apps/{id}` (cookie session). The list
    drops connections without a live refresh token. `host` comes from the redirect URI the grant used
    (`oauth_grants.redirect_uri`; older grants fall back to the client's first registered URI).
- **Transport** — the official `mcp` SDK (2.x, `MCPServer`), Streamable HTTP, stateless, JSON responses: Cloud
  Run keeps nothing between requests. A fresh SDK session manager serves each request, so `/mcp` needs no app
  lifespan. Both protocol eras work: the `initialize` handshake (2025-xx) and the stateless 2026-07-28 one.
- **Limits** (`auth_limits`) — 120 requests a minute per personal token or OAuth connection, 30 wrong tokens per IP
  in 10 minutes → 429 with `Retry-After`.
- **Isolation** — tools take `uid` from the token, never from arguments; tasks are addressed by the user's own
  number `#N`. Tools: `capture`, `list_tasks`, `list_projects`, `list_contexts`, `complete_task`, `move_task`,
  `update_task`, `weekly_review`.
- **Contexts** (SERBITO-423) — a task has a list of contexts (`items.contexts text[]`). `move_task` takes `contexts`
  (a list) or the old `context` (a string, may hold several: `"@phone @computer"`); both replace the whole list.
  The `list_tasks` context filter matches any of the task's contexts.
- **`update_task`** — title (parsed like the card: `#Project`, `@contexts` — they replace the old ones, a date),
  notes, due date (ISO 8601 or capture words; date only → 09:00; an explicit `due` wins over a date in the new
  title), `waiting_for`. A task has no “waiting for” field: `waiting_for` moves it to Waiting and writes
  `Waiting for: …` / `Ждём: …` (account language) as the first line of the notes; `""` removes that line.
- **`weekly_review`** — read-only: Inbox count, projects without a next action, overdue (open tasks with a past
  date), Waiting (oldest first, `waiting_over_a_week` by creation date — there is no “moved to Waiting” time),
  next 7 days, Someday count.
- **Logs** — no token values, codes or task text; SDK loggers (`mcp.*`) log warnings only.

## Telegram capture

- `Call the bank tomorrow at 10:00` → Inbox + reminder
- `in 2 hours check the deploy`, `on friday`, `24.10 12:00`, `2026-10-24`
- English: `call mom tomorrow at 10:00`, `tomorrow 10am`, `at 3pm`, `on friday`, `next monday`,
  `24 oct 12:00` / `oct 24`, `in 2 hours`, `day after tomorrow`, `remind me to …`;
  Russian: `Позвонить в банк завтра в 10:00`, `через 2 часа`, `в пятницу` (the site parses the same way)
- `report #Client_X @work` → straight to Next, with project and context
- Buttons under a message: ✅ Done / 💤 +1h / ⏭ Next
- `/start` — greeting with examples and buttons “How it works”, “Add email”, “Open the website” (the last one only with an https `BASE_URL`)
- `/inbox`, `/next`, `/done 12`, `/login`, `/email`, `/about`
- Anyone who messages the bot gets an account; the account's very first task gets one hint about the site
- Bot description, short description and command menu live in code (`BOT_PROFILE`, `BOT_COMMANDS`), per
  `language_code`: `""` (everyone else) — English, `ru`/`uk`/`be`/`sr` — Russian

## Language (RU / EN)

One rule for the site, bot, emails, 404 and manifest — `pages.lang_of` / `pages.pick_lang`: Russian for `ru`,
`uk`, `be` and Cyrillic Serbian (`sr`, `sr-RS`, `sr-Cyrl`), English for everything else (including `sr-Latn`);
no language at all — Russian.

- **Site:** setting under “Account” (Auto / Русский / English, `users.lang`, null = auto) > entering via `/en/`
  (remembered in the browser) > browser language (Accept-Language → `/api/config` → `lang`). The SPA sends the
  chosen language in `Accept-Language`; the server uses it for errors and the sign-in email.
- **Bot:** setting under “Account” > `language_code` of the Telegram update > Russian. The last `language_code`
  is stored in `users.tg_lang` — reminders use it. `/login` for an English user links to `/en/`.
- Texts: SPA — the `#i18n` dictionary in `static/index.html`; bot and server — `TEXTS` in `app.py`. ru/en keys
  match and English has no Cyrillic (`tests/test_i18n.py`).
- GTD terms in Russian (owner decision, SERBITO-354): the lists — Inbox, Next, Waiting, Projects, Someday, Reference,
  Weekly Review — are proper nouns, written in English and not declined (“в Inbox”, “из Next”); the sentences around
  them are Russian. Other sections (Календарь, Готово, Аккаунт) are ordinary UI words and are translated. Same rule
  in the app, the public pages and the bot (`test_ru_gtd_terms_are_proper_nouns`).

## Public pages and UI

App sections: Inbox · Next (filter by @context) · Waiting · Calendar/reminders · Projects (⚠ without a next
action) · Someday · Reference · Done · Weekly Review.
Every section has its own address — `/` (Inbox), `/next`, `/projects`, `/p/<id>` (a project), `/account`… and the
same under `/en/` — so Back (button or phone gesture) returns to the previous section instead of leaving the site.
A task card opened from a list is its own history entry `/i/<N>`: Back closes it. Server: `app.APP_VIEWS`.

Manual order: tasks have their own order in each list (`items.position`) and in each project (`items.ppos`);
projects have one order per user (`projects.position`, SERBITO-391), used by the project list and every project
picker. Drag a card with the mouse, hold it or drag it by ⠿ on a phone, or press Alt+↑↓ on the selected card. The ⠿
grip shows on touch screens and on hover or selection with a mouse. A one-time hint above the first list with 2+
tasks explains this; its dismissal is stored per user (`users.dnd_tip_seen`, SERBITO-390).

Public pages are `pages.py`; the server renders the text (for search engines): landing for guests on `/` and
`/en/`, “How it works” on `/about` and `/en/about`, privacy policy on `/privacy`, “What's new” on `/changes`
(rendered from `CHANGELOG.md` via `changelog.py`, parsed once at startup), `robots.txt`, `sitemap.xml`, `llms.txt`
(a short summary with links for AI assistants; Google ignores it). `robots.txt` does not block `/i/<N>`: a blocked
page hides its `X-Robots-Tag: noindex` from crawlers, so app pages stay crawlable and `noindex` (SERBITO-448).
The trailing-slash redirect (`/en` → `/en/`, `/about/` → `/about`) is permanent (308) for `GET`/`HEAD` outside
`/api/`; API paths and other methods keep Starlette's 307 (`app.PermanentSlashRedirect`).
The running version (`APP_VERSION` from the deploy tag, else `dev`) is in every public footer and, via
`/api/config`, in the app menu next to “What's new”; a dot marks a version not yet seen on `/changes`
(localStorage `gtd-seen-version`). Preview images `static/og*.png`
come from `scripts/og_image.py`. The logo is the “Inbox” mark (`pages.mark()`, color `#0F766E` — also the site
accent); favicon and phone icons in `static/` are built by `scripts/icons.py`.
Install to home screen — `/manifest.webmanifest` (`pages.manifest()`, description in the browser language), no
service worker. Phones (SERBITO-448): the cookie banner shows a one-line short text (`CONSENT[lang]["short"]`)
with “Details” and the buttons in one row (~86 px instead of 137); the bot line sits above the sign-in card; links in
the header, the notes and the footer have a 44×44 px tap area (padding plus the same negative margin, so lines do
not move); public text is at least 14 px; grey `--mut` is `#4b5563` / `#a1abb9` (7:1 on the page background).
Unknown address — 404 page (`pages.not_found()`), also for curl and link previews; only `/api/*`
and clients that ask for `application/json` get JSON. `/ru/…` redirects (308) to the same page without the prefix.

## Analytics

Nothing leaves the app: no task titles, texts, emails or `user_id`.

- **Statistics** in the app — owner only (`ADMIN_USER_IDS`, account ids): users, DAU/WAU/MAU, channels
  (site/bot; MCP requests count as the `mcp` channel), sign-in methods, tasks — all aggregates from the `activity` table (user × day × channel → action count).
- **Google Analytics 4** — only if `GA_MEASUREMENT_ID` is set, and only on the production domain; enhanced
  measurement off. Pages are sent as the section only (`/inbox`, `/next`…; `/i/N` goes as `/inbox`). Events:
  `login` / `link_method` (method), `task_capture`, `about_view` (language); every app event has a `language`
  parameter (ru/en); page titles are always Russian.
- **Sentry** — only if `SENTRY_DSN` is set; no local variables, request bodies or `httpx` breadcrumbs; the bot
  token is scrubbed.

## Ideas

Voice → text, recurring tasks, export/import.
