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

## [0.33.0] - 2026-10-04

### Added
- A task can have several contexts: "pay the bill @phone @computer" shows the task under both in Next. In the task card, add and remove contexts as chips.
  - RU: У задачи может быть несколько контекстов: «оплатить счёт @телефон @комп» — задача видна в Next под обоими. В карточке задачи контексты добавляются и убираются чипами.
- The sign-in block names the Telegram bot @gtdsrbot with a link and one line on what it does.
  - RU: В блоке входа виден Telegram-бот @gtdsrbot: ссылка и строка о том, что он умеет.

### Changed
- The Telegram bot is easier to find in Telegram search: its name and description now contain the words "GTD", "tasks" and "todo" (and their Russian forms).
  - RU: Telegram-бота проще найти в поиске Telegram: в его имени и описании теперь есть слова «GTD», «задачи», «todo» и «tasks».

## [0.32.0] - 2026-10-04

### Security
- Signing in with the Telegram button on the website no longer depends on code that strict browser security settings block.
  - RU: Вход кнопкой Telegram на сайте больше не зависит от кода, который блокируют строгие настройки безопасности браузера.

## [0.31.0] - 2026-10-03

### Added
- Connect ChatGPT, Cursor, VS Code and other AI assistants to GTD with sign-in, no token. The consent screen shows where access goes.
  - RU: ChatGPT, Cursor, VS Code и другие AI-ассистенты подключаются к GTD через вход, без токена. Экран согласия показывает, куда уходит доступ.

## [0.30.0] - 2026-10-03

### Fixed
- Version 0.29.0 did not start on the server. This version ships all of its changes: the Claude connector, project reorder, the reorder hint and the green macOS icon.
  - RU: Версия 0.29.0 не запустилась на сервере. Эта версия выпускает все её изменения: коннектор для Claude, порядок проектов, подсказку про перестановку и зелёную иконку на macOS.

## [0.29.0] - 2026-10-03

### Added
- Reorder projects by drag & drop, with Alt+↑↓, or by the ⠿ handle on a phone. The project picker uses the same order.
  - RU: Проекты можно переставлять: перетащите мышью, пальцем за ⠿ или Alt+↑↓. В выборе проекта тот же порядок.
- Connect Claude as a custom connector: on claude.ai, Claude Desktop or the phone, add the gtd URL, sign in and press Allow. No token is needed. "Account" lists connected apps, and Disconnect cuts access at once.
  - RU: Claude подключается как свой коннектор: на claude.ai, в Claude Desktop или на телефоне добавьте адрес gtd, войдите и нажмите «Разрешить». Токен не нужен. В «Аккаунте» виден список подключённых приложений, «Отключить» сразу закрывает доступ.
- New AI assistant tools: edit a task (title, notes, due date, who it waits for) and a Weekly Review summary.
  - RU: Новые инструменты для AI-ассистентов: правка задачи (название, заметки, срок, кого ждём) и сводка для Weekly Review.

### Changed
- It is now easy to see that tasks can be reordered: the ⠿ handle shows on hover, and a one-time hint explains drag and Alt+↑↓.
  - RU: Теперь видно, что задачи можно переставлять: при наведении появляется ⠿, а разовая подсказка объясняет перетаскивание и Alt+↑↓.

### Fixed
- The installed app icon on macOS is green again instead of black.
  - RU: Иконка установленного приложения на macOS снова зелёная, а не чёрная.

### Security
- Every deploy now checks that the site sends its security headers. If one is missing, the deploy stops.
  - RU: Каждый деплой теперь проверяет, что сайт отдаёт заголовки безопасности. Если какого-то нет, деплой останавливается.

## [0.28.0] - 2026-10-03

### Added
- Connect Claude and other AI assistants over MCP: create a personal token under "Account", then capture, list, complete and move tasks from the assistant.
  - RU: Подключение Claude и других AI-ассистентов по MCP: создайте личный токен в «Аккаунте» и записывайте, смотрите, закрывайте и переносите задачи прямо из ассистента.

### Security
- MCP tokens are stored only as hashes, show when they were last used, and can be revoked at once.
  - RU: MCP-токены хранятся только в виде хеша, показывают, когда ими пользовались, и отзываются сразу.

## [0.27.0] - 2026-10-03

### Security
- A safety check when you link a sign-in method to your account now always runs.
  - RU: Проверка безопасности при привязке способа входа к аккаунту теперь работает всегда.

## [0.26.0] - 2026-10-02

### Added
- Inbox tasks have a "→ Project" button: pick a project or create a new one right there. The task dialog's project list also has "+ New project".
  - RU: У задач в Inbox есть кнопка «→ Project»: выберите проект или сразу создайте новый. В списке проектов в карточке задачи тоже есть «+ Новый проект».
- A project page has its own "Add a task" field: what you type there goes straight into that project. Elsewhere, a message tells you where a new task went, with Undo.
  - RU: На странице проекта есть своё поле «Добавить задачу»: всё, что вы там пишете, попадает сразу в этот проект. В других разделах сообщение показывает, куда ушла новая задача, и её можно отменить.
- After you move a task from the Inbox to Next, the message offers your usual @contexts: one tap adds one, so the Next filter works.
  - RU: Когда вы переносите задачу из Inbox в Next, сообщение предлагает ваши обычные @контексты: одно нажатие — и контекст добавлен, фильтр в Next работает.
- The "First steps" checklist links to the Telegram bot.
  - RU: В чек-листе «Первые шаги» есть ссылка на Telegram-бота.

### Changed
- The Back button and the phone's back gesture now move between sections, projects and an open task instead of leaving the app. Each section has its own address, so a reload keeps you where you were.
  - RU: Кнопка «Назад» и жест «назад» на телефоне теперь переходят между разделами, проектами и открытой задачей, а не уводят из приложения. У каждого раздела свой адрес, и после перезагрузки вы остаётесь там же.
- The Projects screen has a single labelled "New project" field. Pressing Create with an empty name tells you what's missing, and a new project is confirmed with a message.
  - RU: На экране проектов осталось одно подписанное поле «Новый проект». «Создать» без названия подсказывает, чего не хватает, а созданный проект подтверждается сообщением.
- In Russian, the GTD list names (Inbox, Next, Waiting, Projects, Someday, Reference, Weekly Review) are now used the same way everywhere — in the app, on the site and in the bot — with the text around them in Russian.
  - RU: В русской версии названия списков GTD (Inbox, Next, Waiting, Projects, Someday, Reference, Weekly Review) теперь везде одинаковые — в приложении, на сайте и в боте, а текст вокруг них по-русски.
- On a touch screen, tasks have a ⠿ handle: drag it to reorder right away, without holding. A one-time tip explains how to reorder.
  - RU: На сенсорном экране у задач есть ручка ⠿: потяните за неё, чтобы сразу переставить задачу, без удержания. Разовая подсказка объясняет, как переставлять.
- On a wide screen, the capture button now says "Add" next to its icon.
  - RU: На широком экране у кнопки добавления рядом со значком есть подпись «Добавить».

### Fixed
- On a phone, the checkbox and the task number are easier to tap: each now has a finger-sized area.
  - RU: На телефоне галочку и номер задачи легче нажать: у каждого теперь зона под палец.
- After the "First steps" card goes away, the Inbox no longer leaves an empty gap above the capture field.
  - RU: Когда карточка «Первые шаги» исчезает, над полем ввода в Inbox больше не остаётся пустого места.
- The sign-in buttons are the same width and say the same thing ("Sign in with…"), and the Telegram button no longer looks disabled.
  - RU: Кнопки входа одной ширины и начинаются одинаково («Войти…»), а кнопка Telegram больше не выглядит выключенной.
- A /ru/ address now opens the Russian site instead of a "Page not found" error, and a wrong address always shows a proper "Page not found" page.
  - RU: Адрес /ru/ теперь открывает русский сайт, а не ошибку «Страница не найдена», а неверный адрес всегда показывает нормальную страницу «Страница не найдена».

## [0.25.0] - 2026-10-02

### Fixed
- The home and info pages no longer scroll down a little each time you switch back to the browser while the cookie banner is open.
  - RU: Главная и информационные страницы больше не прокручиваются немного вниз каждый раз, когда вы возвращаетесь в браузер при открытом баннере cookie.

## [0.24.0] - 2026-10-01

### Changed
- The home page loads faster: the Google and Telegram sign-in buttons now load only once the sign-in box is on screen.
  - RU: Главная страница грузится быстрее: кнопки входа через Google и Telegram загружаются, только когда блок входа появился на экране.
- In the cookie banner, Accept and Decline now look the same and are the same size, so declining is as easy as accepting.
  - RU: В баннере cookie кнопки «Принять» и «Отклонить» теперь одинаковые на вид и по размеру — отказаться так же просто, как согласиться.

### Fixed
- The home page fits a narrow screen or 400% zoom without scrolling sideways, and every public page marks its main content and headings properly for screen readers.
  - RU: Главная страница помещается на узком экране и при увеличении 400 % без прокрутки вбок, а у всех публичных страниц основное содержимое и заголовки правильно размечены для экранных чтецов.
- "Sign out" in the account menu now works with the Space key as well as Enter.
  - RU: «Выйти» в меню аккаунта теперь срабатывает и от пробела, а не только от Enter.

## [0.23.0] - 2026-10-01

### Security
- The server now runs with the latest OpenSSL security fixes.
  - RU: Сервер работает с последними исправлениями безопасности OpenSSL.

## [0.22.0] - 2026-10-01

### Fixed
- Reminders keep arriving on time after a brief database hiccup, instead of being delayed until the next attempt.
  - RU: Напоминания приходят вовремя и после короткого сбоя связи с базой, а не откладываются до следующей попытки.

## [0.21.0] - 2026-09-30

### Changed
- Pages open faster: the site now sends them compressed. Grey hint text is a little darker and easier to read.
  - RU: Страницы открываются быстрее — сайт теперь отдаёт их сжатыми. Серый текст подсказок стал чуть темнее и читается легче.

### Fixed
- The app menu works from the keyboard: Tab reaches every section, and on a phone the open menu keeps focus inside and returns it to the menu button when it closes.
  - RU: Меню приложения работает с клавиатуры: Tab доходит до каждого раздела, а на телефоне открытое меню держит фокус внутри и возвращает его на кнопку меню, когда закрывается.
- The task card works with screen readers: every field has a label, the "done" checkbox is named after its task, and closing the card brings you back to the task you opened.
  - RU: Карточка задачи работает с экранным чтецом: у каждого поля есть подпись, галочка «выполнено» называется по своей задаче, а после закрытия карточки ты снова на той задаче, которую открыл.
- The sign-in page no longer jumps when the sign-in buttons appear.
  - RU: Страница входа больше не прыгает, когда появляются кнопки входа.
- The cookie banner no longer hides the button or link you reach with Tab.
  - RU: Баннер cookie больше не закрывает кнопку или ссылку, на которую переходишь по Tab.

## [0.20.0] - 2026-09-29

### Security
- Signing in with the Telegram bot now asks you to tap the number shown on the website, and the bot shows which browser and IP address asked — someone else's link can't get into your account with one tap.
  - RU: Вход через Telegram-бота теперь просит нажать число, показанное на сайте, а бот показывает, из какого браузера и с какого IP пришёл запрос, — чужая ссылка не откроет твой аккаунт одним нажатием.
- A sign-in link from /login in the bot opens a page that shows whose account it is, and signs you in only when you press the button.
  - RU: Ссылка входа из /login в боте открывает страницу, где видно, чей это аккаунт, и входит только по кнопке.
- Sessions end after 90 days without visits, and "Sign out on all devices" in Account signs you out everywhere at once. Signing out also erases unsaved drafts in this browser.
  - RU: Сессия заканчивается через 90 дней без заходов, а «Выйти на всех устройствах» в «Аккаунте» выходит отовсюду сразу. При выходе стираются и несохранённые черновики в этом браузере.
- The bot no longer answers in group chats, so sign-in links and tasks never show up there.
  - RU: Бот больше не отвечает в групповых чатах — ссылки входа и задачи туда не попадают.
- Stronger protection against requests from other websites and against a flood of sign-in emails; security headers on every page.
  - RU: Защита от запросов с чужих сайтов и от потока писем с кодом входа стала строже; на всех страницах — заголовки безопасности.

## [0.19.0] - 2026-09-28

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

[Unreleased]: https://github.com/alxndr-bnd/gtd/compare/v0.33.0...HEAD
[0.33.0]: https://github.com/alxndr-bnd/gtd/compare/v0.32.0...v0.33.0
[0.32.0]: https://github.com/alxndr-bnd/gtd/compare/v0.31.0...v0.32.0
[0.31.0]: https://github.com/alxndr-bnd/gtd/compare/v0.30.0...v0.31.0
[0.30.0]: https://github.com/alxndr-bnd/gtd/compare/v0.29.0...v0.30.0
[0.29.0]: https://github.com/alxndr-bnd/gtd/compare/v0.28.0...v0.29.0
[0.28.0]: https://github.com/alxndr-bnd/gtd/compare/v0.27.0...v0.28.0
[0.27.0]: https://github.com/alxndr-bnd/gtd/compare/v0.26.0...v0.27.0
[0.26.0]: https://github.com/alxndr-bnd/gtd/compare/v0.25.0...v0.26.0
[0.25.0]: https://github.com/alxndr-bnd/gtd/compare/v0.24.0...v0.25.0
[0.24.0]: https://github.com/alxndr-bnd/gtd/compare/v0.23.0...v0.24.0
[0.23.0]: https://github.com/alxndr-bnd/gtd/compare/v0.22.0...v0.23.0
[0.22.0]: https://github.com/alxndr-bnd/gtd/compare/v0.21.0...v0.22.0
[0.21.0]: https://github.com/alxndr-bnd/gtd/compare/v0.20.0...v0.21.0
[0.20.0]: https://github.com/alxndr-bnd/gtd/compare/v0.19.0...v0.20.0
[0.19.0]: https://github.com/alxndr-bnd/gtd/compare/v0.18.0...v0.19.0
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
