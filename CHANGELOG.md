# Changelog

What changed in GTD for its users, version by version, newest first. The same list, in Russian or English,
is on the site: [gtd.serbito.rs/en/changes](https://gtd.serbito.rs/en/changes).

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions are the release tags
`vX.Y.Z`. Every entry has an English line and its Russian translation right under it, because the site
shows the page in both languages. `changelog.py` parses this file for the site and the release script, and
`tests/test_changelog.py` rejects anything else:

- `## [Unreleased]` stays on top; write new entries there. The release script turns it into
  `## [X.Y.Z] - YYYY-MM-DD` (see "Releasing" in the README) and refuses to release without entries.
- Sections: `### Added`, `### Changed`, `### Fixed`, `### Security`.
- An entry is one line `- English text`, followed by one line `  - RU: Русский текст`.
- Write for users: what they can now do or what got fixed, 1–5 short lines per release, no internal jargon.

## [Unreleased]

### Security
- Sign-in codes sent by email are better protected against guessing: after too many wrong codes, signing in with a code to that address is paused for 24 hours, and the number of code emails per day is limited.
  - RU: Коды входа из письма надёжнее защищены от подбора: после слишком многих неверных кодов вход по коду на этот адрес закрывается на сутки, а число писем с кодом в день ограничено.
- Error reports no longer include sign-in links, cookies or service secrets.
  - RU: В отчёты об ошибках больше не попадают ссылки для входа, cookie и служебные секреты.

## [0.18.0] - 2026-09-28

### Added
- The Inbox now shows what to do next: tasks that are overdue or due in the next 3 days, then your Next actions. Overdue dates are marked in red.
  - RU: Во «Входящих» теперь видно, что делать дальше: просроченные задачи и задачи со сроком в ближайшие 3 дня, а за ними — Next. Просроченные даты выделены красным.
- Choose how a project shows completed tasks: hide them, keep them in the list, or show them in a separate collapsible section.
  - RU: В проекте можно выбрать, как показывать выполненные задачи: скрыть, оставить в списке или вынести в отдельный сворачиваемый блок.
- Reorder tasks by drag and drop in Inbox, Next, Waiting, Someday and projects — with the mouse, by holding a task on a phone, or with Alt+↑/↓.
  - RU: Задачи можно переставлять перетаскиванием во «Входящих», Next, Waiting, Someday и в проектах — мышью, долгим нажатием на телефоне или Alt+↑/↓.

## [0.17.0] - 2026-09-28

### Added
- A "What's new" page with the history of changes; the current version is shown at the bottom of every page and in the app menu.
  - RU: Страница «Что нового» с историей изменений; текущая версия — внизу каждой страницы и в меню приложения.

## [0.16.0] - 2026-09-27

### Fixed
- Link previews and uptime monitors that check the site with a HEAD request no longer get an error.
  - RU: Превью ссылок и мониторинги доступности, которые проверяют сайт HEAD-запросом, больше не получают ошибку.

## [0.15.0] - 2026-09-27

### Added
- A cookie banner: accept or decline Google Analytics cookies, and change your mind any time via "Cookie settings".
  - RU: Баннер cookie: cookie Google Analytics можно принять или отклонить, а передумать — в «Настройках cookie».

## [0.14.0] - 2026-09-27

### Added
- The title in a task card understands #project, @context and dates, with the same suggestions as the capture field.
  - RU: Название в карточке задачи понимает #проект, @контекст и даты — с теми же подсказками, что и поле захвата.

### Changed
- Click anywhere on a task to open it.
  - RU: Задача открывается кликом в любом месте карточки.

## [0.13.0] - 2026-09-26

### Added
- Sign in with Telegram in one click on a computer, or link Telegram in Account; on phones the bot link works as before.
  - RU: Вход через Telegram в один клик на компьютере, привязка Telegram в «Аккаунте»; на телефоне — как раньше, через бота.

## [0.12.0] - 2026-09-26

### Added
- A privacy policy: what we store, what analytics receive and how to delete your account.
  - RU: Политика конфиденциальности: что мы храним, что получает аналитика и как удалить аккаунт.
- GTD is open source (MIT): the site links to the code on GitHub and to a guide for running your own copy.
  - RU: GTD — открытый код (MIT): на сайте есть ссылки на код на GitHub и инструкция, как поднять свою копию.

## [0.11.0] - 2026-09-26

### Added
- The browser tab shows the current section and the number of tasks in the Inbox, never task text.
  - RU: Во вкладке браузера — раздел и число задач во Входящих, но никогда не текст задач.

### Fixed
- An expired sign-in link from the bot opens a page that explains what to do next.
  - RU: Устаревшая ссылка входа из бота открывает страницу, где объяснено, что делать дальше.
- Old buttons in the bot always respond instead of spinning.
  - RU: Старые кнопки в боте всегда отвечают, а не крутятся бесконечно.
- Numbers like "version 1.05" are no longer taken for a date, and a capitalized time like "At 18:30" is understood.
  - RU: Числа вроде «версия 1.05» больше не принимаются за дату, а «В 10:00» с большой буквы понимается как время.

## [0.10.0] - 2026-09-26

### Added
- GTD in English: the site, the Telegram bot and sign-in emails. The language is picked automatically and can be changed in Account.
  - RU: GTD на английском: сайт, Telegram-бот и письма для входа. Язык выбирается сам, сменить его можно в «Аккаунте».
- Capture understands English dates and times: "tomorrow at 10am", "next monday", "in 2 hours".
  - RU: Захват понимает даты и время по-английски: «tomorrow at 10am», «next monday», «in 2 hours».

### Fixed
- Empty lists (Next, Waiting, Someday, Reference, Calendar) failed to open.
  - RU: Пустые списки (Next, Waiting, Someday, Reference, Календарь) не открывались.

## [0.9.0] - 2026-09-26

### Added
- Install GTD on your phone's home screen like an app.
  - RU: GTD можно поставить на экран телефона как приложение.
- A clear "Page not found" page instead of a bare error.
  - RU: Понятная страница «Страница не найдена» вместо голой ошибки.

## [0.8.0] - 2026-09-26

### Changed
- New logo and app icons; the site now uses the logo's teal colour.
  - RU: Новый логотип и иконки; сайт теперь в бирюзовом цвете логотипа.

## [0.7.0] - 2026-09-26

### Added
- A home page for visitors and a "How it works" page about the GTD method, in Russian and English.
  - RU: Главная страница для гостей и страница «Как это работает» о методе GTD — на русском и английском.
- A getting-started checklist, and hints with examples in empty lists.
  - RU: Чек-лист первых шагов и подсказки с примерами в пустых списках.
- The bot explains how to start (/start, /about) and gives a tip after your first task.
  - RU: Бот объясняет, как начать (/start, /about), и подсказывает после первой задачи.
- Hotkeys C and N jump to the capture field.
  - RU: Горячие клавиши C и N — сразу к полю захвата.

### Security
- Fixed a flaw that could reveal the name of another user's project.
  - RU: Закрыта уязвимость, через которую можно было увидеть название чужого проекта.

## [0.6.0] - 2026-09-26

### Added
- Every task has its own number (#1, #2…) and a link that opens it.
  - RU: У каждой задачи свой номер (#1, #2…) и ссылка, которая её открывает.
- On phones: a slide-out menu, bigger buttons and an Undo toast (5, 10 or 30 seconds, set in Account).
  - RU: На телефоне — выезжающее меню, крупные кнопки и «Отменить» (5, 10 или 30 секунд, настраивается в «Аккаунте»).
- Unsaved text in the capture field survives a page reload; the lists can be navigated with the keyboard.
  - RU: Недописанный текст в поле захвата не пропадает при перезагрузке; по спискам можно ходить с клавиатуры.

### Fixed
- Actions no longer fail for a moment right after an update.
  - RU: Действия больше не сбоят на короткое время сразу после обновления.

### Security
- Error reports no longer contain request contents or personal data.
  - RU: Отчёты об ошибках больше не содержат содержимого запросов и личных данных.

## [0.5.0] - 2026-09-25

### Changed
- The capture field sits in the middle of the Inbox and keeps focus after adding, so you can capture several tasks in a row.
  - RU: Поле захвата — в центре Inbox и остаётся в фокусе после добавления: можно записать несколько задач подряд.
- The GTD logo in the top left opens the Inbox.
  - RU: Логотип GTD слева вверху открывает Входящие.

## [0.4.0] - 2026-09-25

### Added
- Rename projects.
  - RU: Проекты можно переименовывать.
- Delete a project together with its tasks, or keep the tasks without a project.
  - RU: Проект можно удалить вместе с задачами или оставить задачи без проекта.

## [0.3.0] - 2026-09-25

### Added
- Bot buttons to get started and to add an email.
  - RU: Кнопки бота «Начать» и «Добавить email».

### Changed
- Reminders are checked every minute and never arrive twice.
  - RU: Напоминания проверяются каждую минуту и никогда не приходят дважды.

## [0.2.0] - 2026-09-25

### Added
- Link an email to your account right from the bot with /email.
  - RU: Почту можно привязать к аккаунту прямо из бота командой /email.
- Linking a sign-in method that already has its own tasks offers to merge the two accounts.
  - RU: Если у привязываемого способа входа уже есть свои задачи, GTD предложит объединить аккаунты.
- The capture field suggests your existing @contexts and #projects.
  - RU: Поле захвата подсказывает уже существующие @контексты и #проекты.

### Fixed
- Projects with Cyrillic names are always found; "version 1.2" in a task is no longer read as 1 February.
  - RU: Проекты с русскими названиями находятся всегда; «версия 1.2» в задаче больше не считается 1 февраля.

## [0.1.0] - 2026-09-25

### Added
- Capture tasks on the site or by messaging the Telegram bot, then sort them into Next, Waiting, Someday, Reference and projects, with reminders and a weekly review.
  - RU: Записывай задачи на сайте или сообщением Telegram-боту, потом разбирай по Next, Waiting, Someday, Reference и проектам — с напоминаниями и еженедельным обзором.
- Sign in with Google, a one-time email code or Telegram; methods with the same email share one account.
  - RU: Вход через Google, одноразовый код на почту или Telegram; способы входа с одной почтой — один аккаунт.

[Unreleased]: https://github.com/alxndr-bnd/gtd/compare/v0.18.0...HEAD
[0.18.0]: https://github.com/alxndr-bnd/gtd/compare/v0.17.0...v0.18.0
[0.17.0]: https://github.com/alxndr-bnd/gtd/compare/v0.16.0...v0.17.0
[0.16.0]: https://github.com/alxndr-bnd/gtd/compare/v0.15.0...v0.16.0
[0.15.0]: https://github.com/alxndr-bnd/gtd/compare/v0.14.0...v0.15.0
[0.14.0]: https://github.com/alxndr-bnd/gtd/compare/v0.13.0...v0.14.0
[0.13.0]: https://github.com/alxndr-bnd/gtd/compare/v0.12.0...v0.13.0
[0.12.0]: https://github.com/alxndr-bnd/gtd/compare/v0.11.0...v0.12.0
[0.11.0]: https://github.com/alxndr-bnd/gtd/compare/v0.10.0...v0.11.0
[0.10.0]: https://github.com/alxndr-bnd/gtd/compare/v0.9.0...v0.10.0
[0.9.0]: https://github.com/alxndr-bnd/gtd/compare/v0.8.0...v0.9.0
[0.8.0]: https://github.com/alxndr-bnd/gtd/compare/v0.7.0...v0.8.0
[0.7.0]: https://github.com/alxndr-bnd/gtd/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/alxndr-bnd/gtd/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/alxndr-bnd/gtd/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/alxndr-bnd/gtd/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/alxndr-bnd/gtd/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/alxndr-bnd/gtd/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/alxndr-bnd/gtd/releases/tag/v0.1.0
