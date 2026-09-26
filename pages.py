"""Публичные страницы для гостей и поисковиков: лендинг на входе (/, /en/), «Как это работает»
(/about, /en/about), SEO-теги, robots.txt и sitemap.xml. Текст отдаёт сервер прямо в HTML —
Google индексирует его без JS. Маршруты — в app.py, здесь только содержимое; base — BASE_URL."""
import json

BOT_URL = "https://t.me/gtdsrbot"
GTD_SITE = "https://gettingthingsdone.com"
NOHANDOFF_URL = "https://www.linkedin.com/company/nohandoff/"
# Код открыт под MIT (SERBITO-291): ссылка — в подвале, на лендинге и на /about
REPO_URL = "https://github.com/alxndr-bnd/gtd"
SELF_HOST_URL = REPO_URL + "/blob/main/docs/self-host.md"
# Контактная почта в подвале. Пусто — строки нет: владелец ещё не выбрал адрес
CONTACT_EMAIL = ""
SITE_NAME = "GTD"
BRAND = "#0F766E"  # бирюзовый из логотипа «Входящие»; он же --ac светлой темы в index.html

# Знак: лоток «Входящие» с галочкой. Один источник для шапки, favicon и иконок (scripts/icons.py)
MARK_GLYPH = ('<path d="M13 37h11l3.5 5h9l3.5-5h11v9a5 5 0 0 1-5 5H18a5 5 0 0 1-5-5z" fill="#fff"/>'
              '<path d="M23 20l7 7 12-13" fill="none" stroke="#fff" stroke-width="6" '
              'stroke-linecap="round" stroke-linejoin="round"/>')


def mark(size: int = 0) -> str:
    """Знак в скруглённом квадрате; size — px для встраивания в страницу (0 — без размеров, для файла)."""
    dim = f' width="{size}" height="{size}" aria-hidden="true"' if size else ""
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"{dim}>'
            f'<rect width="64" height="64" rx="14" fill="{BRAND}"/>{MARK_GLYPH}</svg>')


# Иконки и цвет браузера — во <head> всех страниц, и приложения, и публичных
ICONS = "\n".join([
    '<link rel="icon" href="/favicon.ico" sizes="48x48">',
    '<link rel="icon" href="/favicon.svg" type="image/svg+xml">',
    '<link rel="apple-touch-icon" href="/apple-touch-icon.png">',
    f'<meta name="theme-color" content="{BRAND}" media="(prefers-color-scheme: light)">',
    '<meta name="theme-color" content="#12161c" media="(prefers-color-scheme: dark)">',
    f'<meta name="application-name" content="{SITE_NAME}">',
    f'<meta name="apple-mobile-web-app-title" content="{SITE_NAME}">',
    # Установка на экран телефона: манифест + режим без адресной строки на iOS (service worker не нужен)
    '<link rel="manifest" href="/manifest.webmanifest">',
    '<meta name="mobile-web-app-capable" content="yes">',
    '<meta name="apple-mobile-web-app-capable" content="yes">',
    '<meta name="apple-mobile-web-app-status-bar-style" content="default">',
])
LANGS = ("ru", "en")
PATHS = {("ru", "home"): "/", ("ru", "about"): "/about", ("en", "home"): "/en/", ("en", "about"): "/en/about"}

# Другие проекты No Handoff: один список на оба языка (в README — тот же, с UTM для GitHub)
PRODUCTS = [
    ("Planning Poker", "https://poker.serbito.rs/",
     "Бесплатный planning poker для скрам-команд, без регистрации", "Free planning poker for scrum teams, no sign-up"),
    ("Javi", "https://javi.serbito.rs/",
     "Уведомления о доставке для малого бизнеса в Сербии", "Delivery notifications for small businesses in Serbia"),
    ("Serbito", "https://serbito.rs/", "Объявления в Сербии", "Classifieds in Serbia"),
]
FOOTER_UTM = "?utm_source=gtd&utm_medium=crosspromo&utm_campaign=footer"

T = {
    "ru": {
        "home_title": "GTD онлайн бесплатно — Getting Things Done приложение и Telegram-бот",
        "home_desc": "GTD онлайн бесплатно: приложение по методу Getting Things Done Дэвида Аллена. Записывай задачи "
                     "в один тап на сайте или через telegram бот для задач, разбирай Inbox, веди проекты и напоминания.",
        "about_title": "Как работает GTD: 5 шагов метода Getting Things Done — GTD онлайн",
        "about_desc": "Метод GTD Дэвида Аллена за минуту: собрать, обработать, организовать, пересмотреть, делать — "
                      "и где это в приложении. Примеры быстрого захвата, telegram бот для задач, горячие клавиши.",
        "og_alt": "GTD — Записал → Разобрал → Сделал. Бесплатно, на сайте и в Telegram",
        "locale": "ru_RU", "how": "Как это работает", "other_lang": "English", "open": "Открыть GTD",
        "other": "Другие проекты", "made": "Сделано",
        "oss": "Открытый код (MIT)", "oss_note": "Бесплатно и с открытым кодом (MIT)",
        "tm": "GTD® и Getting Things Done® — товарные знаки David Allen Company. "
              "Сервис независимый и не связан с автором метода.",
    },
    "en": {
        "home_title": "GTD online for free — Getting Things Done app and Telegram bot",
        "home_desc": "Free GTD online: a Getting Things Done app based on David Allen's method. Capture tasks in one tap "
                     "on the web or with a Telegram bot for tasks, clear your Inbox, track projects and reminders.",
        "about_title": "How GTD works: the 5 steps of Getting Things Done — GTD online",
        "about_desc": "David Allen's GTD method in a minute: capture, clarify, organize, reflect, engage — and where "
                      "each step lives in the app. Quick-capture examples, a Telegram bot for tasks, hotkeys.",
        "og_alt": "GTD — Capture → Clarify → Do. Free, on the web and in Telegram",
        "locale": "en_US", "how": "How it works", "other_lang": "Русский", "open": "Open GTD",
        "other": "Other projects", "made": "Made by",
        "oss": "Open source (MIT)", "oss_note": "Free and open source (MIT)",
        "tm": "GTD® and Getting Things Done® are trademarks of the David Allen Company. "
              "This service is independent and not affiliated with the author of the method.",
    },
}

# Стили публичных страниц. Переменные цвета (--bg, --ac…) — из index.html; на /about — BASE_CSS
PUBLIC_CSS = """<style>
.pub{max-width:880px;margin:0 auto;padding:18px 16px 32px}
.pub a{color:var(--ac)}
.pub .top{display:flex;align-items:center;gap:16px;margin-bottom:28px;font-size:14px}
.pub .top .brand{flex:1;display:flex;align-items:center;gap:10px;font-weight:700;font-size:18px;color:var(--tx);text-decoration:none}
.pub h1{font-size:30px;line-height:1.2;margin:0 0 12px}
.pub h2{font-size:20px;margin:36px 0 12px}
.pub .lead{font-size:18px;color:var(--mut);margin:0 0 16px}
.pub .note{color:var(--mut);font-size:13px}
.pub .intro{display:grid;grid-template-columns:1fr 380px;gap:28px;align-items:start}
.pub .card{background:var(--card);border:1px solid var(--bd);border-radius:14px;padding:18px 20px}
.pub .signin{text-align:center} .pub .signin h2{margin:0 0 4px}
.pub .steps{list-style:none;margin:0;padding:0;display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:10px}
.pub .steps li{background:var(--card);border:1px solid var(--bd);border-radius:12px;padding:14px 16px}
.pub .steps b{display:block;font-size:17px;margin-bottom:4px}
.pub .steps .where{display:block;color:var(--mut);font-size:13px;margin-top:6px}
.pub .btn{display:inline-block;padding:10px 18px;border-radius:10px;background:var(--ac);color:var(--on-ac);text-decoration:none;font-weight:600}
.pub .ex{list-style:none;padding:0} .pub .ex li{margin:6px 0}
.pub q{background:var(--card);border:1px solid var(--bd);border-radius:6px;padding:1px 6px;-webkit-box-decoration-break:clone;box-decoration-break:clone}
.pub q::before,.pub q::after{content:none}
.pub kbd{font:12.5px ui-monospace,Menlo,monospace;border:1px solid var(--bd);border-bottom-width:2px;border-radius:5px;padding:0 5px;background:var(--card)}
.pub footer{margin-top:48px;padding-top:16px;border-top:1px solid var(--bd);color:var(--mut);font-size:13px}
.pub footer ul{list-style:none;padding:0;margin:6px 0 14px} .pub footer li{margin:3px 0}
@media(max-width:700px){.pub .intro{grid-template-columns:1fr}.pub h1{font-size:25px}}
</style>"""

# /about — отдельная лёгкая страница без JS приложения: цвета те же, что в index.html
BASE_CSS = """<style>
:root{--bg:#f6f7f9;--card:#fff;--tx:#1c2430;--mut:#6b7686;--bd:#e3e7ee;--ac:#0F766E;--on-ac:#fff}
@media(prefers-color-scheme:dark){:root{--bg:#12161c;--card:#1a2029;--tx:#e6eaf0;--mut:#8b96a6;--bd:#2a323e;--ac:#2BA597;--on-ac:#0b1a18}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--tx);font:15px/1.5 system-ui,-apple-system,Segoe UI,sans-serif}
</style>"""

# Cloudflare Web Analytics (без cookies) — как в index.html, только на проде
CF_BEACON = """<script>
if(location.hostname==='gtd.serbito.rs'){
  const s=document.createElement('script'); s.defer=true; s.src='https://static.cloudflareinsights.com/beacon.min.js';
  s.dataset.cfBeacon='{"token": "f8d8fa129e284768aebb986131e987c7"}'; document.head.appendChild(s);
}
</script>"""


def url(base: str, lang: str, page: str) -> str:
    return base + PATHS[(lang, page)]


def head(base: str, lang: str, page: str) -> str:
    """<title>, description, canonical, hreflang, OG, twitter:card и JSON-LD WebApplication."""
    t, me = T[lang], url(base, lang, page)
    title, desc = t[page + "_title"], t[page + "_desc"]
    alt = [f'<link rel="alternate" hreflang="{lg}" href="{url(base, lg, page)}">' for lg in LANGS]
    alt.append(f'<link rel="alternate" hreflang="x-default" href="{url(base, "ru", page)}">')
    other = T["en" if lang == "ru" else "ru"]["locale"]
    ld = {"@context": "https://schema.org", "@type": "WebApplication", "name": SITE_NAME,
          "url": url(base, lang, "home"), "description": t["home_desc"], "applicationCategory": "ProductivityApplication",
          "operatingSystem": "Web, Telegram", "inLanguage": lang,
          "offers": {"@type": "Offer", "price": "0", "priceCurrency": "EUR"}, "sameAs": [BOT_URL]}
    img = f"{base}/og.png" if lang == "ru" else f"{base}/og-en.png"
    return "\n".join([
        f"<title>{title}</title>",
        f'<meta name="description" content="{desc}">',
        f'<link rel="canonical" href="{me}">', *alt,
        '<meta property="og:type" content="website">',
        f'<meta property="og:site_name" content="{SITE_NAME}">',
        f'<meta property="og:title" content="{title}">',
        f'<meta property="og:description" content="{desc}">',
        f'<meta property="og:url" content="{me}">',
        f'<meta property="og:locale" content="{t["locale"]}">',
        f'<meta property="og:locale:alternate" content="{other}">',
        f'<meta property="og:image" content="{img}">',
        '<meta property="og:image:type" content="image/png">',
        '<meta property="og:image:width" content="1200">', '<meta property="og:image:height" content="630">',
        f'<meta property="og:image:alt" content="{t["og_alt"]}">',
        '<meta name="twitter:card" content="summary_large_image">',
        # </script> внутри JSON оборвал бы тег — экранируем «</»
        '<script type="application/ld+json">' + json.dumps(ld, ensure_ascii=False).replace("</", "<\\/") + "</script>",
        PUBLIC_CSS,
    ])


def topbar(lang: str, page: str) -> str:
    t, other = T[lang], "en" if lang == "ru" else "ru"
    link = (f'<a href="{PATHS[(lang, "about")]}">{t["how"]}</a>' if page == "home"
            else f'<a href="{PATHS[(lang, "home")]}">{t["open"]}</a>')
    return (f'<div class="top"><a class="brand" href="{PATHS[(lang, "home")]}">{mark(28)} GTD</a>{link}'
            f'<a href="{PATHS[(other, page)]}" hreflang="{other}" lang="{other}">{t["other_lang"]}</a></div>')


def footer(lang: str) -> str:
    """Другие проекты No Handoff, открытый код, подпись и оговорка о товарном знаке — на всех публичных страницах."""
    t, i = T[lang], 2 if lang == "ru" else 3
    items = "".join(f'<li><a href="{p[1]}{FOOTER_UTM}">{p[0]}</a> — {p[i]}</li>' for p in PRODUCTS)
    contact = f' · <a href="mailto:{CONTACT_EMAIL}">{CONTACT_EMAIL}</a>' if CONTACT_EMAIL else ""
    return (f'<footer><b>{t["other"]}</b><ul>{items}</ul>'
            f'<p>{t["oss"]} · <a href="{REPO_URL}">GitHub</a>{contact}</p>'
            f'<p>{t["made"]} <a href="{NOHANDOFF_URL}">No Handoff</a></p>'
            f'<p>{t["tm"]} <a href="{GTD_SITE}">gettingthingsdone.com</a></p></footer>')


LANDING = {
    "ru": """<h1>GTD онлайн — бесплатно, на сайте и в Telegram</h1>
<p class="lead">Запиши всё, что крутится в голове, за секунду — и разбери потом, по методу Getting Things Done.</p>""",
    "en": """<h1>GTD online — free, on the web and in Telegram</h1>
<p class="lead">Capture everything on your mind in a second, then sort it out later with the Getting Things Done method.</p>""",
}
LANDING_SIGNIN = {
    "ru": ("Вход", "Вход или регистрация — аккаунт создастся сам"),
    "en": ("Sign in", "Sign in or sign up — the account is created automatically"),
}
LANDING_STEPS = {
    "ru": """<h2>Записал → Разобрал → Сделал</h2>
<ol class="steps">
<li><b>1. Записал</b>Мысль, задача, идея — в поле захвата или сообщением боту.
<q>позвонить маме завтра в 10:00</q> — и напоминание уже стоит.</li>
<li><b>2. Разобрал</b>Во Входящих решаешь, что это: следующее действие, проект, ожидание или «когда-нибудь».</li>
<li><b>3. Сделал</b>В Next — только конкретные шаги, по контексту: @комп, @телефон, @дом.
Раз в неделю — обзор, чтобы ничего не потерялось.</li>
</ol>
<p><a href="/about">Подробнее о методе и приложении →</a></p>
<h2>Telegram-бот @gtdsrbot</h2>
<div class="card"><p style="margin-top:0">Пиши задачи боту обычными сообщениями — они попадут во Входящие.
Напоминания приходят в Telegram с кнопками ✅ Готово и 💤 +1ч. Бот и сайт — один аккаунт:
войди на сайте через Telegram или привяжи бота в «Аккаунте».</p>
<a class="btn" href="https://t.me/gtdsrbot">Открыть @gtdsrbot</a></div>""",
    "en": """<h2>Capture → Clarify → Do</h2>
<ol class="steps">
<li><b>1. Capture</b>A thought, task or idea — into the capture field or as a message to the bot.
<q>call mom tomorrow at 10:00</q> — and the reminder is set.</li>
<li><b>2. Clarify</b>In the Inbox you decide what it is: a next action, a project, waiting for someone, or “someday”.</li>
<li><b>3. Do</b>Next holds only concrete steps, filtered by context: @computer, @phone, @home.
A weekly review keeps anything from slipping through.</li>
</ol>
<p><a href="/en/about">More about the method and the app →</a></p>
<h2>Telegram bot @gtdsrbot</h2>
<div class="card"><p style="margin-top:0">Send tasks to the bot as plain messages — they land in your Inbox.
Reminders arrive in Telegram with ✅ Done and 💤 +1h buttons. The bot and the site share one account:
sign in on the site with Telegram or link the bot under “Account”.</p>
<a class="btn" href="https://t.me/gtdsrbot">Open @gtdsrbot</a></div>""",
}


def landing(lang: str) -> str:
    """Лендинг для гостя: вставляется в #root index.html. Кнопки входа JS кладёт в #signin."""
    h, hint = LANDING_SIGNIN[lang]
    oss = f'<p class="note">{T[lang]["oss_note"]} · <a href="{REPO_URL}">GitHub</a></p>'
    return (f'<div class="pub">{topbar(lang, "home")}<div class="intro"><div>{LANDING[lang]}{oss}</div>'
            f'<div class="card signin"><h2>{h}</h2><p class="hint" style="margin:0 0 16px">{hint}</p>'
            f'<div id="signin"></div></div></div>{LANDING_STEPS[lang]}{footer(lang)}</div>')


ABOUT = {
    "ru": """<h1>Как работает GTD</h1>
<p class="lead">GTD (Getting Things Done) — метод Дэвида Аллена из книги «Getting Things Done» (2001; по-русски —
«Как привести дела в порядок»). Голова — для идей, а не для хранения: всё, что требует внимания, сразу записываешь
во Входящие, потом решаешь, что это и какой следующий конкретный шаг.</p>
<h2>Пять шагов метода — и где они в приложении</h2>
<ol class="steps">
<li><b>1. Собрать</b>Всё, что крутится в голове, — сразу записать, не разбирая.
<span class="where">Поле захвата на сайте, Telegram-бот</span></li>
<li><b>2. Обработать</b>По каждой записи: что это, нужно ли действие и какой следующий шаг.
<span class="where">Inbox</span></li>
<li><b>3. Организовать</b>Разложить по спискам: действия, ожидания, проекты, идеи на потом, справка, даты.
<span class="where">Next, Waiting, Проекты, Someday, Reference, Календарь</span></li>
<li><b>4. Пересмотреть</b>Раз в неделю пройтись по всему, чтобы система оставалась честной.
<span class="where">Weekly Review</span></li>
<li><b>5. Делать</b>Выбрать следующее действие по месту и ситуации.
<span class="where">Next с фильтром по @контексту</span></li>
</ol>
<h2>Быстрый захват</h2>
<ul class="ex">
<li><q>позвонить маме завтра в 10:00</q> — задача во Входящих и напоминание завтра в 10:00.</li>
<li><q>отчёт #Работа @комп</q> — сразу в Next, в проект «Работа», контекст @комп.</li>
<li>Сроки понимаются и так: <q>через 2 часа</q>, <q>в пятницу</q>, <q>24.10 12:00</q>.</li>
</ul>
<h2>Telegram-бот</h2>
<p><a href="https://t.me/gtdsrbot">@gtdsrbot</a> принимает задачи обычными сообщениями — с тем же синтаксисом —
и присылает напоминания с кнопками ✅ Готово, 💤 +1ч и ⏭ Next. Бот и сайт — один аккаунт: войди на сайте
через Telegram или привяжи бота в «Аккаунте».</p>
<p><a class="btn" href="https://t.me/gtdsrbot">Открыть @gtdsrbot</a></p>
<h2>Горячие клавиши</h2>
<ul class="ex">
<li><kbd>C</kbd> или <kbd>N</kbd> — к полю захвата (работает и на русской раскладке)</li>
<li><kbd>↑</kbd> <kbd>↓</kbd> — по задачам, <kbd>Enter</kbd> — открыть задачу</li>
<li><kbd>←</kbd> — в меню, <kbd>→</kbd> — обратно к задачам, <kbd>Esc</kbd> — сбросить выбор</li>
</ul>
<p><a class="btn" href="/">Открыть GTD</a></p>""",
    "en": """<h1>How GTD works</h1>
<p class="lead">GTD (Getting Things Done) is David Allen's method from his book “Getting Things Done” (2001).
Your head is for having ideas, not holding them: capture everything that needs attention into an Inbox right away,
then decide what it is and what the next concrete step is.</p>
<h2>The five steps — and where they live in the app</h2>
<ol class="steps">
<li><b>1. Capture</b>Write down everything on your mind right away, without sorting it.
<span class="where">Capture field on the site, Telegram bot</span></li>
<li><b>2. Clarify</b>For each item: what is it, is it actionable, and what is the next step.
<span class="where">Inbox</span></li>
<li><b>3. Organize</b>Put things into lists: actions, waiting-fors, projects, someday ideas, reference, dates.
<span class="where">Next, Waiting, Projects, Someday, Reference, Calendar</span></li>
<li><b>4. Reflect</b>Once a week, go over everything so the system stays trustworthy.
<span class="where">Weekly Review</span></li>
<li><b>5. Engage</b>Pick the next action that fits where you are and what you have at hand.
<span class="where">Next, filtered by @context</span></li>
</ol>
<h2>Quick capture</h2>
<ul class="ex">
<li><q>call mom tomorrow at 10:00</q> — a task in the Inbox and a reminder tomorrow at 10:00.</li>
<li><q>report #Work @computer</q> — straight to Next, in the “Work” project, with the @computer context.</li>
<li>Times and dates are understood too: <q>in 2 hours</q>, <q>tomorrow 10am</q>, <q>next monday</q>, <q>24 oct 12:00</q>.</li>
<li>Russian works too: <q>позвонить маме завтра в 10:00</q>, <q>отчёт #Работа @комп</q>.</li>
</ul>
<h2>Telegram bot</h2>
<p><a href="https://t.me/gtdsrbot">@gtdsrbot</a> takes tasks as plain messages — same syntax — and sends reminders
with ✅ Done, 💤 +1h and ⏭ Next buttons. The bot and the site share one account: sign in on the site with Telegram
or link the bot under “Account”.</p>
<p><a class="btn" href="https://t.me/gtdsrbot">Open @gtdsrbot</a></p>
<h2>Hotkeys</h2>
<ul class="ex">
<li><kbd>C</kbd> or <kbd>N</kbd> — jump to the capture field (works in any keyboard layout)</li>
<li><kbd>↑</kbd> <kbd>↓</kbd> — move between tasks, <kbd>Enter</kbd> — open a task</li>
<li><kbd>←</kbd> — to the menu, <kbd>→</kbd> — back to the tasks, <kbd>Esc</kbd> — clear the selection</li>
</ul>
<p><a class="btn" href="/en/">Open GTD</a></p>""",
}


# Последний раздел /about: как поставить сайт на экран телефона (манифест — /manifest.webmanifest)
INSTALL = {
    "ru": """<h2>GTD на экране телефона</h2>
<p>Сайт ставится как приложение, без магазина: на iPhone — «Поделиться» → «На экран «Домой»»,
на Android — меню ⋮ → «Установить приложение».</p>""",
    "en": """<h2>GTD on your home screen</h2>
<p>Install the site like an app, no store needed: on iPhone, Share → Add to Home Screen;
on Android, the ⋮ menu → Install app.</p>""",
}

# Раздел /about про открытый код; инструкция по self-hosting — только на английском
OSS = {
    "ru": f"""<h2>Открытый код</h2>
<p>GTD — открытый проект под лицензией MIT: код и история изменений — на <a href="{REPO_URL}">GitHub</a>.
Можно посмотреть, как всё устроено, предложить правку или поднять свою копию —
<a href="{SELF_HOST_URL}">инструкция по self-hosting</a> (на английском).</p>""",
    "en": f"""<h2>Open source</h2>
<p>GTD is open source under the MIT license: the code and its history are on <a href="{REPO_URL}">GitHub</a>.
Read how it works, suggest a fix, or run your own copy — see the <a href="{SELF_HOST_URL}">self-hosting guide</a>.</p>""",
}


def about(base: str, lang: str, ga: str) -> str:
    """Страница «Как это работает». ga — GA-сниппет (только на боевом домене) или пусто."""
    path = PATHS[(lang, "about")]
    # Только событие и адрес страницы — никаких данных пользователя (как вся аналитика gtd)
    track = ("<script>function ga(name, params = {}){ if(typeof gtag === 'function') gtag('event', name, params); }\n"
             f"ga('page_view', {{page_location: location.origin + '{path}', page_title: 'GTD — {T[lang]['how']}'}});\n"
             f"ga('about_view', {{language: '{lang}'}});</script>")
    return f"""<!doctype html>
<html lang="{lang}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
{head(base, lang, "about")}
{ICONS}
{ga}
{CF_BEACON}
{BASE_CSS}
</head>
<body>
<div class="pub">{topbar(lang, "about")}{ABOUT[lang]}{OSS[lang]}{INSTALL[lang]}{footer(lang)}</div>
{track}
</body>
</html>"""


def robots(base: str) -> str:
    return "\n".join(["User-agent: *", "Allow: /", "Allow: /about", "Allow: /en/",
                      *(f"Disallow: {p}" for p in ("/api/", "/auth", "/dev-login", "/tg/", "/tasks/", "/i/")),
                      "", f"Sitemap: {base}/sitemap.xml", ""])


def sitemap(base: str) -> str:
    """4 публичных адреса, у каждого — альтернативы ru/en/x-default."""
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:xhtml="http://www.w3.org/1999/xhtml">']
    for (lang, page) in PATHS:
        alts = [(lg, url(base, lg, page)) for lg in LANGS] + [("x-default", url(base, "ru", page))]
        out.append(f"<url><loc>{url(base, lang, page)}</loc>"
                   + "".join(f'<xhtml:link rel="alternate" hreflang="{h}" href="{u}"/>' for h, u in alts) + "</url>")
    out.append("</urlset>")
    return "\n".join(out)


# Язык интерфейса (SERBITO-259) — одно правило на сайт, бот, письма, манифест и 404 (в SPA — его копия pickLang):
# русский — для ru, uk, be и сербского кириллицей (sr, sr-RS, sr-Cyrl; не sr-Latn), английский — для всех
# остальных. Языка нет вовсе — русский, как было до английской версии. Явная настройка в «Аккаунте»
# (users.lang) важнее любого автоопределения.
RU_LANGS = ("ru", "uk", "be", "sr")


def lang_of(code) -> str:
    """Язык интерфейса по коду языка — из браузера (en-US, sr-Latn-RS) или language_code Telegram (en, ru)."""
    parts = str(code or "").strip().lower().replace("_", "-").split("-")
    if not parts[0] or parts[0] == "*":
        return "ru"
    return "ru" if parts[0] in RU_LANGS and not (parts[0] == "sr" and "latn" in parts) else "en"


def pick_lang(accept: str | None) -> str:
    """Язык по Accept-Language: решает самый предпочтительный язык (выше q, при равных — раньше в списке).
    SPA шлёт в этом заголовке уже выбранный язык интерфейса."""
    found = []
    for i, part in enumerate((accept or "").split(",")):
        tag, *params = part.split(";")
        q = next((p.split("=", 1)[1] for p in params if p.strip().startswith("q=")), "1")
        try:
            q = float(q)
        except ValueError:
            continue
        if tag.strip() and q > 0:
            found.append((-q, i, tag.strip()))
    return lang_of(min(found)[2] if found else "")


# Манифест для установки на экран телефона. Файл один на сайт, поэтому язык описания — по Accept-Language
MANIFEST_DESC = {
    "ru": "Бесплатное приложение по методу Getting Things Done: Inbox, следующие действия, проекты и напоминания — "
          "на сайте и в Telegram.",
    "en": "A free Getting Things Done app: Inbox, next actions, projects and reminders — on the web and in Telegram.",
}


def manifest(lang: str) -> dict:
    return {"id": "/", "name": SITE_NAME, "short_name": SITE_NAME, "description": MANIFEST_DESC[lang], "lang": lang,
            "dir": "ltr", "start_url": "/", "scope": "/", "display": "standalone",
            "background_color": "#f6f7f9", "theme_color": BRAND,  # фон — --bg светлой темы, как у страниц
            "icons": [{"src": "/icon-192.png", "sizes": "192x192", "type": "image/png"},
                      {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png"},
                      {"src": "/icon-maskable-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"}]}


# Страница 404 для браузера (неизвестный адрес вне /api/): заголовок, пояснение, «на главную», «как это работает»
NOT_FOUND = {
    "ru": ("Страница не найдена", "Такой страницы нет — возможно, ссылка устарела или в ней опечатка.",
           "Открыть GTD", "Как это работает"),
    "en": ("Page not found", "There is no page at this address — the link may be outdated or mistyped.",
           "Open GTD", "How it works"),
}


def not_found(lang: str) -> str:
    h, text, go, how = NOT_FOUND[lang]
    home, about_ = PATHS[(lang, "home")], PATHS[(lang, "about")]
    return f"""<!doctype html>
<html lang="{lang}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex">
<title>{h} — {SITE_NAME}</title>
{ICONS}
{BASE_CSS}
{PUBLIC_CSS}
</head>
<body>
<div class="pub"><div class="top"><a class="brand" href="{home}">{mark(28)} GTD</a></div>
<h1>{h}</h1>
<p class="lead">{text}</p>
<p><a class="btn" href="{home}">{go}</a>&emsp;<a href="{about_}">{how}</a></p></div>
</body>
</html>"""
