# Internals

Notes for contributors: how sign-in, languages, public pages and analytics work.

## Accounts and sign-in

Sign-up is open: the first sign-in by any method creates an account.

- **Google** — Sign in with Google; the ID token is checked via `oauth2.googleapis.com/tokeninfo`.
- **Email** — a 6-digit code over SMTP. Valid 10 minutes, 5 attempts, resend after a minute,
  at most 5 emails per 10 minutes per IP. Without `SMTP_PASSWORD` and with `DEV=1` the code goes to the server log.
- **Telegram** — button on the site → bot → “Confirm”; the page picks up the confirmation itself.
  `/login` in the bot works too.

Google and an email code for the same address are one account. Other methods are linked under
“👤 Account” or in the bot: `/email you@example.com` → code by email → send the code to the bot.

If a sign-in method already belongs to another account: an empty one is taken over silently (and we move
into the account with data if ours is empty and nothing is lost); one with tasks — we offer to **merge**
(a button on the web, “🔗 Merge” in the bot). Only the initiator confirms. The merge is one transaction:
tasks, projects (same-name projects are joined), sessions and missing sign-in methods move into one account.

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

## Public pages and UI

App sections: Inbox · Next (filter by @context) · Waiting · Calendar/reminders · Projects (⚠ without a next
action) · Someday · Reference · Done · Weekly Review.

Public pages are `pages.py`; the server renders the text (for search engines): landing for guests on `/` and
`/en/`, “How it works” on `/about` and `/en/about`, `robots.txt`, `sitemap.xml`. Preview images `static/og*.png`
come from `scripts/og_image.py`. The logo is the “Inbox” mark (`pages.mark()`, color `#0F766E` — also the site
accent); favicon and phone icons in `static/` are built by `scripts/icons.py`.
Install to home screen — `/manifest.webmanifest` (`pages.manifest()`, description in the browser language), no
service worker. Unknown address in a browser — 404 page (`pages.not_found()`); `/api/*` and non-`text/html`
requests get JSON.

## Analytics

Nothing leaves the app: no task titles, texts, emails or `user_id`.

- **Statistics** in the app — owner only (`ADMIN_USER_IDS`, account ids): users, DAU/WAU/MAU, channels
  (site/bot), sign-in methods, tasks — all aggregates from the `activity` table (user × day × channel → action count).
- **Google Analytics 4** — only if `GA_MEASUREMENT_ID` is set, and only on the production domain; enhanced
  measurement off. Pages are sent as the section only (`/inbox`, `/next`…; `/i/N` goes as `/inbox`). Events:
  `login` / `link_method` (method), `task_capture`, `about_view` (language); every app event has a `language`
  parameter (ru/en); page titles are always Russian.
- **Sentry** — only if `SENTRY_DSN` is set; no local variables, request bodies or `httpx` breadcrumbs; the bot
  token is scrubbed.

## Ideas

Voice → text, recurring tasks, export/import.
