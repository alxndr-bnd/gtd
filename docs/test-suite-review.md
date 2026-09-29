# Test suite review (SERBITO-364, 2026-09-29)

The pytest gate (`tests/`, including the Playwright smoke `test_browser_smoke.py`) was reviewed for tests that repeat each other, check only source text, or cost time without adding coverage.

## Before / after

| | Before | After |
|---|---|---|
| Tests | 609 | 575 |
| Wall time, full gate (2 runs each, same machine) | 32.4–33.5 s | 26.9–29.4 s |
| Browser smoke share (measured under coverage) | ~22 s | ~15.5 s |
| App coverage (`app.py`, `pages.py`, `changelog.py`, …; lines) | 1621 / 1717 (94.41%) | 1621 / 1717 (94.41%) |

Coverage is identical line by line: no app line lost its only test.

## Removed or changed, by category

**Browser smoke: fixed sleeps and language-independent repeats** (the biggest time cost):
- `test_telegram_fallback_deep_link` slept 2.5 s per language so the login poll could run.
  - It now waits for the poll response and asserts `status == "pending"`, which proves the browser-cookie binding.
  - The check is stricter than before, and the test takes 0.4 s instead of 2.9 s.
- `test_drag_to_reorder_with_mouse` runs once instead of once per language.
  - It asserts no text.
  - The EN Next screen is still rendered by `test_app_sections[en]`.

**Exact duplicates:**
- `test_parse`:
  - 13 of the 16 `test_parses_when` rows, and all of `test_no_date`, were rows of the `RU_GOLDEN` table.
  - The 3 English rows moved to `test_parses_when_english`.
- `test_api`:
  - `test_index_served` is covered by the `test_pages` landing and signed-in tests.
  - `test_logout` is superseded by `test_security::test_logout_clears_cookie_and_session` (SERBITO-360).
  - `test_dev_login_disabled_in_prod` is covered by `test_install_404`.
  - `test_google_analytics_only_on_prod_domain` is covered by the `test_consent` host tests; its `<!--GA-->` placeholder assert moved there.
- `test_bot`: `test_bot_setup_local_has_no_webhook` is covered by `test_bot_setup_local_leaves_profile_alone`, which asserts that `getMe` is the only call.
- `test_head`: HEAD through TestClient repeated the ASGI-level HEAD test for `/`.
- `test_changelog`: `test_versions_newest_first` is redundant because `changelog.parse()` itself rejects versions or dates out of order. Both the parse test and the "newer" malformed case exercise that.

**The same rule tested again through every endpoint:**
- `pick_lang` is table-tested in `test_i18n::test_accept_language`. The two cases only the manifest test had (a Serbian Cyrillic list and a malformed `q`) moved there.
- `test_manifest` (9 cases) and `test_config_reports_browser_language` (6 cases) keep one RU and one EN case each.

**Source greps that the browser smoke runs for real:**
- `test_consent`: the Consent Mode JS literals (denied by default, ads never granted, 12-month TTL) and the app-menu "Cookie settings" template grep. Both are exercised by `test_consent_banner` and `test_consent_on_prod_host`, in RU and EN.
- `test_pages::test_app_menu_links_to_changes` now checks only the RU/EN URLs. The link, the new-version dot and the seen-version key are covered by `test_menu_whats_new_and_version`.

**Shorter code:**
- `email_start` and `email_verify` (two copies) and `texts()` (three copies) now live once in `conftest.py`.
- `fake_tg` and `profile_tg` share one fake.

## Kept on purpose

- Every sign-in, session, rate-limit, CSRF, security-header and secrets-scrubbing test (SERBITO-342..362, GTD-1..14). They stay even where two tests share a setup, for example:
  - the email rate limit in `test_auth` and `test_auth_limits`;
  - `test_smtp_failure_*`, which checks different things;
  - the token-in-log checks for each bot failure path.
- Bug-regression tests:
  - SERBITO-269, -270, -272, -273, -286 and -292;
  - HEAD must not burn a one-time login link.
- `test_capture.py`: a fast unit layer, even though API and bot tests reach the same code.
- The EN bot tests in `test_i18n`, which mirror the RU ones: they are the i18n guard.

## Unsure (listed, not changed)

- `test_api`:
  - `test_dev_login_creates_user` (the smoke logs in through `/dev-login` everywhere);
  - `test_existing_user_who_did_everything_never_sees_checklist` (close to `test_checklist_ticks_from_data`);
  - `test_index_has_checklist_and_empty_states` (grep).
- `test_bot`: the three email-linking tests could be one test parametrized by entry point.
- `test_i18n`:
  - `test_ui_language_mechanism` (grep; `<html lang>` is checked in the smoke);
  - `test_english_public_pages_have_no_russian_ui_note` (checks that a long-removed string is absent).
- `test_consent`:
  - `test_cookie_settings_link_in_every_footer` (the smoke clicks the footer link on three pages);
  - the literal yes/no asserts in `test_banner_texts`.
- `test_pages`:
  - `test_privacy_ga_only_on_prod` and `test_about_ga_event_only_on_prod` overlap the `test_consent` host tests;
  - `test_icons_and_name_on_every_page` and `test_install_404::test_manifest_and_ios_meta_on_every_page` walk the same page list.
- `test_head::test_head_keeps_page_headers`: only the og.png cache-control assert is unique.
- `test_changelog::test_real_changelog_notes_are_english` hardcodes "0.16.0".
- `test_tg_widget::test_spa_uses_widget_with_fallback`: the widget script URL is only grepped.
- Flaky: `test_keyboard_reorder` failed once, under coverage tracing on a loaded machine, with its 5 s save deadline. It passed 5 of 5 on rerun. Not changed.
