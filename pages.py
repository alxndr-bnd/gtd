"""Публичные страницы для гостей и поисковиков: лендинг на входе (/, /en/), «Как это работает»
(/about, /en/about), политика конфиденциальности (/privacy, /en/privacy), «Что нового» (/changes, /en/changes),
Telegram-бот (/bot, /en/bot), чек-лист Weekly Review (/weekly-review, /en/weekly-review), SEO-теги, robots.txt, llms.txt и sitemap.xml. Текст отдаёт сервер прямо в HTML —
Google индексирует его без JS. Маршруты — в app.py, здесь только содержимое; base — BASE_URL."""
import html
import json
import math
import os
import re

import changelog

# Публичный бот gtd.serbito.rs. Имя видно на сайте — так бота находят и в Telegram (SERBITO-422)
BOT_NAME = "gtdsrbot"
BOT_URL = f"https://t.me/{BOT_NAME}"
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
PATHS = {("ru", "home"): "/", ("ru", "about"): "/about", ("en", "home"): "/en/", ("en", "about"): "/en/about",
         ("ru", "privacy"): "/privacy", ("en", "privacy"): "/en/privacy",
         ("ru", "changes"): "/changes", ("en", "changes"): "/en/changes",
         # Страницы под поисковый запрос (SERBITO-441, SERBITO-446)
         ("ru", "bot"): "/bot", ("en", "bot"): "/en/bot",
         ("ru", "weekly"): "/weekly-review", ("en", "weekly"): "/en/weekly-review"}

# Версия, которая сейчас работает (SERBITO-329): APP_VERSION ставит деплой из тега (v0.16.0 → 0.16.0),
# локально и в self-hosted копии без него — «dev». Показывается в подвале, в меню приложения (/api/config)
# и на /changes
VERSION = re.sub(r"[^\w.+-]", "", os.getenv("APP_VERSION", "")).removeprefix("v")  # только безопасные символы
VERSION_LABEL = f"v{VERSION}" if VERSION else "dev"

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
.pub{max-width:880px;margin:0 auto;padding:18px 16px 32px;overflow-wrap:break-word}
/* <main> публичной страницы (SERBITO-349) — просто обёртка: правило main из index.html — для приложения */
.pub>main{display:block;flex:none;max-width:none;width:auto;margin:0;padding:0}
.pub a{color:var(--ac)}
.pub .top{display:flex;align-items:center;gap:16px;margin-bottom:28px;font-size:14px}
.pub .top .brand{flex:1;display:flex;align-items:center;gap:10px;font-weight:700;font-size:18px;color:var(--tx);text-decoration:none}
.pub h1{font-size:30px;line-height:1.2;margin:0 0 12px}
.pub h2{font-size:20px;margin:36px 0 12px}
.pub .lead{font-size:18px;color:var(--mut);margin:0 0 16px}
.pub .note{color:var(--mut);font-size:14px}
.pub .intro{display:grid;grid-template-columns:1fr 380px;gap:28px;align-items:start}
.pub .card{background:var(--card);border:1px solid var(--bd);border-radius:14px;padding:18px 20px}
.pub .signin{text-align:center} .pub .signin h2{margin:0 0 4px}
/* Поля первой и последней кнопки входа не выходят за блок (SERBITO-448): пока виджет Telegram не загружен,
   поле «или» под ним иначе сдвигало весь блок на 14 px */
.pub #signin{display:flow-root}
.pub .steps{list-style:none;margin:0;padding:0;display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:10px}
.pub .steps li{background:var(--card);border:1px solid var(--bd);border-radius:12px;padding:14px 16px}
.pub .steps b{display:block;font-size:17px;margin-bottom:4px}
.pub .steps .where{display:block;color:var(--mut);font-size:14px;margin-top:6px}
.pub button.btn{border:0;font-family:inherit;font-size:inherit;cursor:pointer}
.pub .btn{display:inline-block;padding:10px 18px;border-radius:10px;background:var(--ac);color:var(--on-ac);text-decoration:none;font-weight:600}
.pub .ex{list-style:none;padding:0} .pub .ex li{margin:6px 0}
.pub q{background:var(--card);border:1px solid var(--bd);border-radius:6px;padding:1px 6px;-webkit-box-decoration-break:clone;box-decoration-break:clone}
.pub q::before,.pub q::after{content:none}
.pub pre{white-space:pre-wrap;font:14px/1.5 ui-monospace,Menlo,monospace;background:var(--card);border:1px solid var(--bd);border-radius:10px;padding:14px 16px;margin:0 0 12px}
.pub table{border-collapse:collapse;width:100%} .pub th,.pub td{border-bottom:1px solid var(--bd);padding:8px 6px;text-align:left;vertical-align:top}
.pub td{overflow-wrap:anywhere} .pub td:first-child{white-space:nowrap;padding-right:12px}
.pub kbd{font:14px ui-monospace,Menlo,monospace;border:1px solid var(--bd);border-bottom-width:2px;border-radius:5px;padding:0 5px;background:var(--card)}
.pub footer{margin-top:48px;padding-top:16px;border-top:1px solid var(--bd);color:var(--mut);font-size:14px}
.pub footer ul{list-style:none;padding:0;margin:6px 0 14px} .pub footer li{margin:3px 0}
.pub .rel h2{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap} .pub .rel h2 .note{font-weight:400}
.pub .rel h3{font-size:14px;text-transform:uppercase;letter-spacing:.04em;color:var(--mut);margin:14px 0 4px}
.pub .rel ul{margin:0;padding-left:20px} .pub .rel li{margin:4px 0}
/* minmax(0,1fr), а не 1fr: колонка не шире экрана, даже если строка почты или кнопка входа шире (320 px, WCAG 1.4.10) */
@media(max-width:700px){.pub .intro{grid-template-columns:minmax(0,1fr)}.pub h1{font-size:25px}}
/* Читается и нажимается на телефоне (SERBITO-448). Текст — не мельче 14 px (подпись, «или» и сообщения блока входа
   в приложении — 13 px), серый --mut — контраст от 7:1. Ссылки шапки, строк под текстом и подвала — зона нажатия
   не меньше 44×44 px: поля внутри ссылки и такие же отрицательные снаружи, поэтому строки не раздвигаются */
.pub .hint,.pub .or,.pub .msg{font-size:14px}
@media(max-width:700px),(pointer:coarse){
  .pub .top a{display:inline-flex;align-items:center;min-height:44px;min-width:44px}
  .pub .note a,.pub .more a,.pub footer a{display:inline-block;padding:12px 4px;margin:-12px -4px}
  .pub footer li a{padding:12px 10px;margin:-12px -10px}
}
</style>"""

# /about — отдельная лёгкая страница без JS приложения: цвета те же, что в index.html
BASE_CSS = """<style>
:root{--bg:#f6f7f9;--card:#fff;--tx:#1c2430;--mut:#4b5563;--bd:#e3e7ee;--ac:#0F766E;--on-ac:#fff}
@media(prefers-color-scheme:dark){:root{--bg:#12161c;--card:#1a2029;--tx:#e6eaf0;--mut:#a1abb9;--bd:#2a323e;--ac:#2BA597;--on-ac:#0b1a18}}
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


# Согласие на cookie аналитики (SERBITO-319, решение владельца SERBITO-285 — одно на все четыре продукта).
# Google Consent Mode v2: до любого gtag('config') — «запрещено» по умолчанию; GA на проде всё равно грузится
# и шлёт пинги без cookie. «Принять» разрешает только analytics_storage: рекламы в gtd нет, и три рекламных
# разрешения (ad_storage, ad_user_data, ad_personalization) всегда denied. Выбор (и дата) — в localStorage
# на 12 месяцев; сохранённый выбор сразу идёт в consent default, чтобы согласившийся не отправил первый пинг
# «запрещено». Баннер и ссылки «Настройки cookie» — только если задан GA_MEASUREMENT_ID (self-hosted копия без GA не спрашивает про cookie, которых нет), зато на любом
# хосте — так их видно и локально; сам gtag.js грузится только на gtd.serbito.rs (app.ga_snippet)
CONSENT_KEY = "gtd-consent"
CONSENT_DAYS = 365
CONSENT = {
    "ru": {"text": "GTD использует cookie Google Analytics, чтобы понимать, какие разделы полезны, — "
                   "без текста задач.",
           "short": "Cookie Google Analytics — без текста задач",
           "more": "Подробнее", "yes": "Принять", "no": "Отклонить", "label": "Cookie", "settings": "Настройки cookie"},
    "en": {"text": "GTD uses Google Analytics cookies to learn which sections are useful — never your task text.",
           "short": "Google Analytics cookies, never your task text",
           "more": "Details", "yes": "Accept", "no": "Decline", "label": "Cookies", "settings": "Cookie settings"},
}
CONSENT_CSS = """<style>
.cc{position:fixed;left:50%;bottom:16px;transform:translateX(-50%);z-index:70;width:min(640px,calc(100vw - 24px));
  display:flex;flex-wrap:wrap;align-items:center;gap:10px 16px;background:var(--card);color:var(--tx);
  border:1px solid var(--bd);border-radius:12px;padding:12px 14px;box-shadow:0 8px 30px #0003;
  font:14px/1.45 system-ui,-apple-system,Segoe UI,sans-serif}
.cc[hidden]{display:none}
.cc p{flex:1 1 280px;margin:0} .cc a{color:var(--ac)} .cc .ccs{display:none}
/* «Принять» и «Отклонить» — одного веса (решение владельца, SERBITO-349): тот же вид и та же ширина */
.cc .ccb{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-left:auto}
.cc button{font:inherit;font-weight:600;min-height:44px;padding:0 16px;border-radius:8px;border:1px solid var(--ac);
  background:var(--card);color:var(--tx);cursor:pointer}
.cc button:focus-visible,.cc a:focus-visible{outline:2px solid var(--ac);outline-offset:2px}
body.cc-open .pub{padding-bottom:calc(var(--cc-h,0px) + 32px)}
body.cc-open .app main{padding-bottom:calc(var(--cc-h,0px) + 24px)}
body.cc-open .toast{bottom:calc(var(--cc-h,0px) + 24px)}
/* Фокус не прячется под баннером (WCAG 2.4.11, SERBITO-349): прокрутка к элементу оставляет снизу место под баннер —
   и у страницы, и у выезжающего меню приложения (nav на телефоне прокручивается сам) */
html.cc-open{scroll-padding-bottom:calc(var(--cc-h,0px) + 16px)}
@media(max-width:700px){html.cc-open nav{scroll-padding-bottom:calc(var(--cc-h,0px) + 16px);padding-bottom:calc(var(--cc-h,0px) + 16px)}}
/* Телефон (SERBITO-448): баннер закрывал до 137 px первого экрана и строку о боте. Теперь — короткий текст в одну
   строку, под ним «Подробнее» и кнопки (~86 px). <p> раскрыт в сетку (display:contents): короткий текст — на всю
   ширину, ссылка — в одну строку с кнопками. Полный текст — на широком экране */
@media(max-width:700px){.cc{display:grid;grid-template-columns:auto 1fr;gap:4px 12px;bottom:8px;padding:8px 12px}
  .cc p{display:contents} .cc .ccl{display:none} .cc .ccs{display:block;grid-column:1/-1}
  .cc a{display:inline-flex;align-items:center;min-height:44px;padding:0 4px;margin:0 -4px}
  .cc .ccb{margin:0} .cc button{padding:0 12px}
  body.cc-kb .cc{display:none}}
</style>"""
CONSENT_JS = """<script>
// Согласие на cookie Google Analytics (SERBITO-319): описание — у CONSENT в pages.py
window.dataLayer = window.dataLayer || [];
function gtag(){ dataLayer.push(arguments); }
(() => {
  const KEY = '__KEY__', TTL = __DAYS__ * 864e5, TXT = __TXT__;
  // Баннер спрашивает только про аналитику: рекламные разрешения — denied при любом выборе
  const state = v => ({ad_storage: 'denied', analytics_storage: v, ad_user_data: 'denied', ad_personalization: 'denied'});
  // Выбор старше 12 месяцев или localStorage недоступен — выбора нет: снова «запрещено» и баннер
  const saved = () => {
    try{ const c = JSON.parse(localStorage.getItem(KEY));
      if(c && (c.v === 'granted' || c.v === 'denied') && Date.now() - c.t < TTL) return c.v; }catch(e){}
    return null;
  };
  const choice = saved();
  gtag('consent', 'default', {...state(choice === 'granted' ? 'granted' : 'denied'), wait_for_update: 500});

  let el = null, drawn = '';
  const lang = () => document.documentElement.lang === 'en' ? 'en' : 'ru';
  function draw(){  // язык баннера — язык страницы; SPA меняет его после входа, тогда и перерисовываем
    const lg = lang(); if(!el || drawn === lg) return;
    const t = TXT[drawn = lg];
    el.setAttribute('aria-label', t.label);
    el.innerHTML = `<p><span class="ccl">${t.text}</span> <span class="ccs">${t.short}</span> `
      + `<a href="${t.privacy}#cookies">${t.more}</a></p><div class="ccb">`
      + `<button type="button" data-cc="granted">${t.yes}</button>`
      + `<button type="button" data-cc="denied">${t.no}</button></div>`;
  }
  // Высота баннера — в --cc-h (на <html>: от неё и scroll-padding): низ страницы, тост «Отменить» и фокус не прячутся под ним
  const root = document.documentElement;
  const fit = () => { if(el && !el.hidden) root.style.setProperty('--cc-h', el.offsetHeight + 'px'); };
  function show(focus){
    if(!el){
      el = document.createElement('div'); el.className = 'cc'; el.id = 'cc'; el.setAttribute('role', 'region');
      document.body.prepend(el);  // первым в порядке Tab; position:fixed — страница не сдвигается
      new MutationObserver(draw).observe(document.documentElement, {attributes: true, attributeFilter: ['lang']});
    }
    draw(); el.hidden = false; document.body.classList.add('cc-open'); root.classList.add('cc-open'); fit();
    if(focus) el.querySelector('button').focus();
  }
  function hide(){ if(el) el.hidden = true; document.body.classList.remove('cc-open'); root.classList.remove('cc-open'); }
  // Отзыв согласия: стираем _ga* на этом хосте (GA пишет их host-only, cookie_domain 'none') и на родительских
  // доменах — там остались cookie, которые GA ставил на .serbito.rs до SERBITO-319
  function dropCookies(){
    const hs = location.hostname.split('.'), doms = [''];
    for(let i = 0; i < hs.length - 1; i++) doms.push('; domain=' + hs.slice(i).join('.'));
    document.cookie.split(';').map(c => c.split('=')[0].trim()).filter(n => n.startsWith('_ga'))
      .forEach(n => doms.forEach(d => { document.cookie = `${n}=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/${d}`; }));
  }
  function choose(v){
    try{ localStorage.setItem(KEY, JSON.stringify({v, t: Date.now()})); }catch(e){}
    gtag('consent', 'update', state(v));
    if(v === 'denied') dropCookies();
    hide();
  }
  document.addEventListener('click', e => {
    const b = e.target.closest('[data-cc]'); if(b) return choose(b.dataset.cc);
    if(e.target.closest('[data-cc-open]')){ e.preventDefault(); show(true); }  // «Настройки cookie»
  });
  addEventListener('resize', fit);
  // Окно вернуло фокус (другое приложение, вкладка) — браузер снова шлёт focusin тому же элементу: это не ход
  // клавиатуры, не крутим (SERBITO-374). Фиксированный элемент прокрутка не откроет — его тоже не трогаем
  let away = null;
  addEventListener('blur', e => { if(e.target === window) away = document.activeElement; });
  const fixed = n => { for(; n && n.nodeType === 1; n = n.parentElement) if(getComputedStyle(n).position === 'fixed') return true; };
  // Фокус (Tab) попал под баннер — докручиваем: scrollIntoView учитывает scroll-padding-bottom выше. Браузер сам
  // прокручивает только к элементу за краем окна, а этот на экране — просто закрыт баннером
  document.addEventListener('focusin', e => {
    const back = e.target === away; away = null;
    if(back || !el || el.hidden || el.contains(e.target) || fixed(e.target)) return;
    const b = el.getBoundingClientRect(), r = e.target.getBoundingClientRect();
    if(r.bottom > b.top && r.top < b.bottom && r.right > b.left && r.left < b.right) e.target.scrollIntoView({block: 'nearest'});
  });
  // Телефон: открыта клавиатура (видимая область сжалась, фокус в поле) — баннер не закрывает поле ввода
  window.visualViewport?.addEventListener('resize', () => document.body.classList.toggle('cc-kb',
    visualViewport.height < innerHeight * .75 && /^(INPUT|TEXTAREA)$/.test(document.activeElement?.tagName)));
  window.gtdConsent = {open: () => show(true)};  // по нему SPA решает, показывать ли «Настройки cookie» в меню
  const start = () => { if(!choice) show(false); };
  document.readyState === 'loading' ? document.addEventListener('DOMContentLoaded', start) : start();
})();
</script>"""
CONSENT_HEAD = CONSENT_CSS + "\n" + (
    CONSENT_JS.replace("__KEY__", CONSENT_KEY).replace("__DAYS__", str(CONSENT_DAYS))
    .replace("__TXT__", json.dumps({lg: {**CONSENT[lg], "privacy": PATHS[(lg, "privacy")]} for lg in LANGS},
                                   ensure_ascii=False).replace("</", "<\\/")))


def url(base: str, lang: str, page: str) -> str:
    return base + PATHS[(lang, page)]


CRUMB = {"about": "how", "privacy": "privacy", "changes": "changes", "bot": "bot", "weekly": "weekly"}  # имя страницы в хлебных крошках — ключ T


def structured_data(base: str, lang: str, page: str, img: str) -> dict:
    """JSON-LD (SERBITO-445): один @graph — кто сделал (Organization), сайт, само приложение и эта страница.
    @id у общих узлов — от русской главной на всех страницах обоих языков: для поисковика это одна сущность,
    а не по копии на каждый язык. На внутренних страницах — ещё хлебные крошки"""
    t, me, home = T[lang], url(base, lang, page), url(base, "ru", "home")
    org, site, app = home + "#org", home + "#website", home + "#app"
    application = {
        "@type": "WebApplication", "@id": app, "name": SITE_NAME, "url": home, "description": t["home_desc"],
        "applicationCategory": "ProductivityApplication", "operatingSystem": "Web, Telegram", "inLanguage": list(LANGS),
        "isAccessibleForFree": True, "license": "https://opensource.org/licenses/MIT", "image": img,
        "author": {"@id": org}, "publisher": {"@id": org},
        "offers": {"@type": "Offer", "price": "0", "priceCurrency": "EUR", "availability": "https://schema.org/InStock"},
        "sameAs": [BOT_URL, REPO_URL]}
    if VERSION:
        application["softwareVersion"] = VERSION
    webpage = {"@type": "WebPage", "@id": me + "#page", "url": me, "name": t[page + "_title"],
               "description": t[page + "_desc"], "inLanguage": lang, "isPartOf": {"@id": site},
               "about": {"@id": app}, "primaryImageOfPage": img}
    if modified(page):
        webpage["dateModified"] = modified(page)
    graph = [
        {"@type": "Organization", "@id": org, "name": "No Handoff", "url": NOHANDOFF_URL,
         "sameAs": [NOHANDOFF_URL, REPO_URL.rsplit("/", 1)[0]]},
        {"@type": "WebSite", "@id": site, "url": home, "name": SITE_NAME, "inLanguage": list(LANGS),
         "publisher": {"@id": org}},
        application, webpage]
    if page != "home":
        graph.append({"@type": "BreadcrumbList", "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": SITE_NAME, "item": url(base, lang, "home")},
            {"@type": "ListItem", "position": 2, "name": t[CRUMB[page]], "item": me}]})
    return {"@context": "https://schema.org", "@graph": graph}


def head(base: str, lang: str, page: str) -> str:
    """<title>, description, canonical, hreflang, OG, twitter:card и JSON-LD."""
    t, me = T[lang], url(base, lang, page)
    title, desc = t[page + "_title"], t[page + "_desc"]
    alt = [f'<link rel="alternate" hreflang="{lg}" href="{url(base, lg, page)}">' for lg in LANGS]
    alt.append(f'<link rel="alternate" hreflang="x-default" href="{url(base, "ru", page)}">')
    other = T["en" if lang == "ru" else "ru"]["locale"]
    img = f"{base}/og.png" if lang == "ru" else f"{base}/og-en.png"
    ld = structured_data(base, lang, page, img)
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
    return (f'<header class="top"><a class="brand" href="{PATHS[(lang, "home")]}">{mark(28)} {SITE_NAME}</a>{link}'
            f'<a href="{PATHS[(other, page)]}" hreflang="{other}" lang="{other}">{t["other_lang"]}</a></header>')


def footer(lang: str, consent: bool = False) -> str:
    """Другие проекты No Handoff, открытый код, подпись и оговорка о товарном знаке — на всех публичных страницах.
    consent — задан GA: тогда и ссылка «Настройки cookie»."""
    t, i = T[lang], 2 if lang == "ru" else 3
    items = "".join(f'<li><a href="{p[1]}{FOOTER_UTM}">{p[0]}</a> — {p[i]}</li>' for p in PRODUCTS)
    contact = f' · <a href="mailto:{CONTACT_EMAIL}">{CONTACT_EMAIL}</a>' if CONTACT_EMAIL else ""
    # Снова открыть баннер согласия (SERBITO-319); без JS — ссылка на раздел политики о cookie
    cookie = (f' · <a href="{PATHS[(lang, "privacy")]}#cookies" data-cc-open>{CONSENT[lang]["settings"]}</a>'
              if consent else "")
    # «Что нового» и работающая версия (SERBITO-329)
    changes = f' · <a href="{PATHS[(lang, "changes")]}">{t["changes"]}</a> · <span class="ver">{VERSION_LABEL}</span>'
    # Справочные страницы (SERBITO-441, SERBITO-446) — ссылки со всех публичных страниц
    guides = " · ".join(f'<a href="{PATHS[(lang, page)]}">{t[key]}</a>'
                        for page, key in (("about", "how"), ("bot", "bot"), ("weekly", "weekly")))
    return (f'<footer><p>{guides}</p><b>{t["other"]}</b><ul>{items}</ul>'
            f'<p>{t["oss"]} · <a href="{REPO_URL}">GitHub</a>{changes}{contact}</p>'
            f'<p>{t["made"]} <a href="{NOHANDOFF_URL}">No Handoff</a> · '
            f'<a href="{PATHS[(lang, "privacy")]}">{t["privacy"]}</a>{cookie}</p>'
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
<li><b>2. Разобрал</b>В Inbox решаешь, что это: следующее действие, проект, ожидание или идея на потом, — и кладёшь
в Next, Projects, Waiting или Someday.</li>
<li><b>3. Сделал</b>В Next — только конкретные шаги, по контексту: @комп, @телефон, @дом.
Раз в неделю — <a href="/weekly-review">Weekly Review</a>, чтобы ничего не потерялось.</li>
</ol>
<p class="more"><a href="/about">Подробнее о методе и приложении →</a></p>
<h2>Telegram-бот @gtdsrbot</h2>
<div class="card"><p style="margin-top:0">Пиши задачи боту обычными сообщениями — они попадут в Inbox.
Напоминания приходят в Telegram с кнопками ✅ Готово и 💤 +1ч. Бот и сайт — один аккаунт:
войди на сайте через Telegram или привяжи бота в «Аккаунте».</p>
<a class="btn" href="https://t.me/gtdsrbot">Открыть @gtdsrbot</a>
<p class="more" style="margin-bottom:0"><a href="/bot">Что умеет бот: сроки, #проекты, @контексты →</a></p></div>""",
    "en": """<h2>Capture → Clarify → Do</h2>
<ol class="steps">
<li><b>1. Capture</b>A thought, task or idea — into the capture field or as a message to the bot.
<q>call mom tomorrow at 10:00</q> — and the reminder is set.</li>
<li><b>2. Clarify</b>In the Inbox you decide what it is: a next action, a project, waiting for someone, or “someday”.</li>
<li><b>3. Do</b>Next holds only concrete steps, filtered by context: @computer, @phone, @home.
A <a href="/en/weekly-review">weekly review</a> keeps anything from slipping through.</li>
</ol>
<p class="more"><a href="/en/about">More about the method and the app →</a></p>
<h2>Telegram bot @gtdsrbot</h2>
<div class="card"><p style="margin-top:0">Send tasks to the bot as plain messages — they land in your Inbox.
Reminders arrive in Telegram with ✅ Done and 💤 +1h buttons. The bot and the site share one account:
sign in on the site with Telegram or link the bot under “Account”.</p>
<a class="btn" href="https://t.me/gtdsrbot">Open @gtdsrbot</a>
<p class="more" style="margin-bottom:0"><a href="/en/bot">What the bot can do: dates, #projects, @contexts →</a></p></div>""",
}


# Место под кнопки входа (SERBITO-349): #signin в HTML пустой, JS заполняет его после /api/config — и лендинг
# прыгал на ~260 px (CLS 0.13–0.17). Высоту резервируем заранее по включённым способам входа (порядок — как
# в loginScreen и app.signin_methods: Google, почта, Telegram). #signin — flow-root: поля крайних
# элементов внутри блока. Замер в Chromium, px: кнопка Google 44; строка почты 48, её нижний отступ 16 больше
# соседнего (+2 перед «или», +6 перед сообщением); Telegram 46 — iframe виджета (запасная кнопка, 40); «или»
# 14+20.3+14 (14 px); строка сообщения 10+14; «Нет способов входа» 15+21.75+15+14; «Dev login» 15+39.75+15.
# Браузерный смоук сверяет резерв с реальной высотой.
SIGNIN_PX = {"google": 44, "email": 48, "bot": 46}
SIGNIN_OR, SIGNIN_MSG, SIGNIN_NONE, SIGNIN_DEV = 48.3, 24, 65.75, 69.75


def signin_height(methods: list[str], dev: bool = False) -> int:
    if not methods:
        return math.ceil(SIGNIN_NONE)
    h = sum(SIGNIN_PX[m] for m in methods) + SIGNIN_OR * (len(methods) - 1) + SIGNIN_MSG
    if "email" in methods:
        h += 6 if methods[-1] == "email" else 2
    return math.ceil(h + (SIGNIN_DEV if dev else 0))


def landing(lang: str, consent: bool = False, signin: list[str] = (), dev: bool = False) -> str:
    """Лендинг для гостя: вставляется в #root index.html. Кнопки входа JS кладёт в #signin; signin — включённые
    способы входа (для резерва высоты), dev — локальная кнопка «Dev login»."""
    h, hint = LANDING_SIGNIN[lang]
    oss = f'<p class="note">{T[lang]["oss_note"]} · <a href="{REPO_URL}">GitHub</a></p>'
    return (f'<div class="pub">{topbar(lang, "home")}<main><div class="intro"><div>{LANDING[lang]}{oss}{BOT_NOTE[lang]}</div>'
            f'<div class="card signin"><h2>{h}</h2><p class="hint" style="margin:0 0 16px">{hint}</p>'
            f'<div id="signin" style="min-height:{signin_height(list(signin), dev)}px"></div>'
            f'{PRIVACY_NOTE[lang]}</div>'
            f'</div>{LANDING_STEPS[lang]}</main>{footer(lang, consent)}</div>')


ABOUT = {
    "ru": """<h1>Как работает GTD</h1>
<p class="lead">GTD (Getting Things Done) — метод Дэвида Аллена из книги «Getting Things Done» (2001; по-русски —
«Как привести дела в порядок»). Голова — для идей, а не для хранения: всё, что требует внимания, сразу записываешь
в Inbox, потом решаешь, что это и какой следующий конкретный шаг.</p>
<h2>Пять шагов метода — и где они в приложении</h2>
<ol class="steps">
<li><b>1. Собрать</b>Всё, что крутится в голове, — сразу записать, не разбирая.
<span class="where">Поле захвата на сайте, Telegram-бот</span></li>
<li><b>2. Обработать</b>По каждой записи: что это, нужно ли действие и какой следующий шаг.
<span class="where">Inbox</span></li>
<li><b>3. Организовать</b>Разложить по спискам: действия, ожидания, проекты, идеи на потом, справка, даты.
<span class="where">Next, Waiting, Projects, Someday, Reference, Календарь</span></li>
<li><b>4. Пересмотреть</b>Раз в неделю пройтись по всему, чтобы система оставалась честной.
<span class="where"><a href="/weekly-review">Weekly Review</a></span></li>
<li><b>5. Делать</b>Выбрать следующее действие по месту и ситуации.
<span class="where">Next с фильтром по @контексту</span></li>
</ol>
<h2>Быстрый захват</h2>
<ul class="ex">
<li><q>позвонить маме завтра в 10:00</q> — задача в Inbox и напоминание завтра в 10:00.</li>
<li><q>отчёт #Работа @комп</q> — сразу в Next, в проект «Работа», контекст @комп.</li>
<li><q>оплатить счёт @телефон @комп</q> — у задачи может быть несколько контекстов:
она видна в Next и под @телефон, и под @комп.</li>
<li>Сроки понимаются и так: <q>через 2 часа</q>, <q>в пятницу</q>, <q>24.10 12:00</q>.</li>
</ul>
<h2>Telegram-бот</h2>
<p><a href="https://t.me/gtdsrbot">@gtdsrbot</a> принимает задачи обычными сообщениями — с тем же синтаксисом —
и присылает напоминания с кнопками ✅ Готово, 💤 +1ч и ⏭ Next. Бот и сайт — один аккаунт: войди на сайте
через Telegram или привяжи бота в «Аккаунте». Все примеры, команды и подключение Claude —
<a href="/bot">на странице бота</a>.</p>
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
<span class="where"><a href="/en/weekly-review">Weekly Review</a></span></li>
<li><b>5. Engage</b>Pick the next action that fits where you are and what you have at hand.
<span class="where">Next, filtered by @context</span></li>
</ol>
<h2>Quick capture</h2>
<ul class="ex">
<li><q>call mom tomorrow at 10:00</q> — a task in the Inbox and a reminder tomorrow at 10:00.</li>
<li><q>report #Work @computer</q> — straight to Next, in the “Work” project, with the @computer context.</li>
<li><q>pay the bill @phone @computer</q> — a task can have several contexts:
in Next it shows under both @phone and @computer.</li>
<li>Times and dates are understood too: <q>in 2 hours</q>, <q>tomorrow 10am</q>, <q>next monday</q>, <q>24 oct 12:00</q>.</li>
<li>Russian works too: <q>позвонить маме завтра в 10:00</q>, <q>отчёт #Работа @комп</q>.</li>
</ul>
<h2>Telegram bot</h2>
<p><a href="https://t.me/gtdsrbot">@gtdsrbot</a> takes tasks as plain messages — same syntax — and sends reminders
with ✅ Done, 💤 +1h and ⏭ Next buttons. The bot and the site share one account: sign in on the site with Telegram
or link the bot under “Account”. All examples, commands and how to connect Claude are
<a href="/en/bot">on the bot page</a>.</p>
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
    """Страница «Как это работает». ga — app.ga_snippet: согласие на cookie и (только на боевом домене) тег GA."""
    path = PATHS[(lang, "about")]
    # Только событие и адрес страницы — никаких данных пользователя (как вся аналитика gtd)
    track = ("<script>function ga(name, params = {}){ if(typeof gtag === 'function') gtag('event', name, params); }\n"
             f"ga('page_view', {{page_location: location.origin + '{path}', page_title: 'GTD — {T[lang]['how']}'}});\n"
             f"ga('about_view', {{language: '{lang}'}});</script>")
    return public_page(base, lang, "about", ga, ABOUT[lang] + OSS[lang] + INSTALL[lang], track)


# Политика конфиденциальности (SERBITO-282). Каждое утверждение сверено с app.py и index.html — меняешь, что
# хранится или куда уходит, правь и этот текст. Адрес для запросов о данных — только здесь, больше нигде
PRIVACY_EMAIL = "alexander.bondarchuk@gmail.com"
PRIVACY_DAY = "2026-09-29"  # редакция политики: дата в тексте, dateModified и lastmod в sitemap
T["ru"].update(privacy="Конфиденциальность", privacy_title="Политика конфиденциальности — GTD онлайн",
               privacy_desc="Какие данные хранит GTD онлайн, что получает аналитика, как дать или отозвать "
                            "согласие на cookie, кто обрабатывает данные и как удалить аккаунт.")
T["en"].update(privacy="Privacy", privacy_title="Privacy policy — GTD online",
               privacy_desc="What data GTD online stores, what analytics receive, how to give or withdraw "
                            "cookie consent, who processes it and how to delete your account.")
# Строка о боте на лендинге (SERBITO-422): имя-ссылка и что он делает. Стоит под текстом, над блоком входа
# (SERBITO-448): под ним на телефоне её закрывал баннер cookie. Тот же текст — на экране входа в приложении
# (bot_line в index.html), там имя бота — из /api/config
BOT_NOTE = {
    "ru": f'<p class="note" style="margin:14px 0 0">Telegram-бот <a href="{BOT_URL}">@{BOT_NAME}</a>: задача — '
          f'одним сообщением, напоминания приходят в чат</p>',
    "en": f'<p class="note" style="margin:14px 0 0">Telegram bot <a href="{BOT_URL}">@{BOT_NAME}</a>: send a task '
          f'as a message, get reminders in the chat</p>',
}
# Ссылка на политику под кнопками входа на лендинге
PRIVACY_NOTE = {
    "ru": '<p class="note" style="margin:14px 0 0">Что мы храним — <a href="/privacy">политика конфиденциальности</a></p>',
    "en": '<p class="note" style="margin:14px 0 0">What we store — <a href="/en/privacy">privacy policy</a></p>',
}
PRIVACY = {
    "ru": """<h1>Политика конфиденциальности</h1>
<p class="lead">GTD — бесплатный менеджер задач от No Handoff. Храним только то, без чего приложение не работает;
данные не продаём, рекламы нет.</p>
<p class="note">Обновлено {date}</p>
<h2>Что мы храним</h2>
<ul class="ex">
<li><b>Аккаунт</b> — смотря как входишь: id, имя и язык из Telegram; адрес почты; id Google-аккаунта, почта
и имя из Google. И настройки: язык интерфейса, время на «Отменить», показывать ли чек-лист.</li>
<li><b>Задачи и проекты</b>: название, заметки, список, контекст, проект, время напоминания и откуда пришла
задача — с сайта или от бота.</li>
<li><b>Счётчик активности</b>: за каждый день и канал (сайт или бот) — сколько было действий. Без текста задач
и без IP-адресов.</li>
<li><b>Вход</b>: cookie <code>sid</code> (httpOnly) держит тебя в аккаунте; сессия истекает через 90 дней без
заходов, в базе — только её хеш. Выход удаляет сессию, «Выйти на всех устройствах» — все. Ссылки и коды входа
живут 10 минут, коды из писем хранятся только в виде хеша. Вход через бота на эти же 10 минут запоминает IP
и название браузера — чтобы показать их в Telegram при подтверждении.</li>
<li><b>Только в браузере</b> (localStorage): выбранный язык, ещё не сохранённые черновики (стираются при выходе)
и твой выбор в баннере cookie.</li>
</ul>
<h2>Аналитика</h2>
<ul class="ex">
<li><b>Google Analytics 4</b> — только на gtd.serbito.rs: какой раздел открыт (Inbox, Next…) и события — вход
и привязка (каким способом), факт записи задачи, шаги чек-листа, просмотр «Как это работает», язык интерфейса.
Текст задач, их номера, почта и id пользователя туда не уходят. Свои cookie GA ставит, только если ты
согласишься (см. ниже), а Google в любом случае получает обычные технические данные браузера (устройство,
примерное местоположение).</li>
<li><b>Cloudflare Web Analytics</b> — общее число посещений, без cookie.</li>
</ul>
<h2 id="cookies">Cookie и согласие</h2>
<ul class="ex">
<li><b>Баннер</b>: на gtd.serbito.rs при первом визите спрашиваем, можно ли Google Analytics ставить cookie, —
«Принять» или «Отклонить». Выбор действует и на сайте, и в приложении.</li>
<li><b>Пока не согласишься</b> (или если отклонишь), GA работает без cookie (Google Consent Mode): уходят
только пинги без идентификатора браузера — какой раздел открыт или какое событие случилось. По ним Google
считает общую статистику, но не узнаёт, что визиты — от одного человека.</li>
<li><b>Если примешь</b>, GA ставит cookie <code>_ga</code> и <code>_ga_…</code> — только для gtd.serbito.rs,
не для других сайтов serbito.rs; по ним видно повторные визиты. Согласие — только на аналитику: рекламы
у нас нет, рекламные разрешения Google всегда выключены.</li>
<li><b>Выбор хранится</b> в localStorage этого браузера 12 месяцев, потом спросим снова. Если браузер
не даёт его сохранить, баннер появится при следующем визите.</li>
<li><b>Передумать</b> можно в любой момент: ссылка «Настройки cookie» внизу каждой страницы и в меню
приложения. При отказе удаляем cookie <code>_ga*</code> этого сайта.</li>
</ul>
<h2>Кто обрабатывает данные</h2>
<ul class="ex">
<li><b>Google Cloud</b> (Cloud Run и Cloud SQL, регион europe-west1, Бельгия) — сервер и база данных.</li>
<li><b>Brevo</b> — отправляет письма с кодом входа.</li>
<li><b>Sentry</b> — отчёты об ошибках: без тел запросов, локальных переменных и персональных данных.</li>
<li><b>Google Analytics</b> и <b>Cloudflare</b> — аналитика, см. выше.</li>
<li><b>Telegram</b> — если пользуешься ботом, сообщения и напоминания идут через Telegram.</li>
<li><b>Google</b> — если входишь через Google, он подтверждает нам твой аккаунт.</li>
</ul>
<h2>Сколько храним</h2>
<p>Пока есть аккаунт. Удалённая задача или проект стирается из базы сразу (Корзина — это просто список,
оттуда можно вернуть). В резервных копиях базы удалённое может оставаться, пока копии не сменятся.</p>
<h2>Удаление аккаунта</h2>
<p>Кнопки удаления в приложении пока нет — напиши на почту ниже и укажи, как входишь (почта, Google
или Telegram). Удалим аккаунт и все его данные в течение 30 дней после запроса.</p>
<h2>Контакты</h2>
<p>Оператор — No Handoff. Вопросы о данных и запросы на удаление:
<a href="mailto:{email}">{email}</a>.</p>""",
    "en": """<h1>Privacy policy</h1>
<p class="lead">GTD is a free task manager by No Handoff. We keep only what the app needs to work;
we don't sell data, and there are no ads.</p>
<p class="note">Updated {date}</p>
<h2>What we store</h2>
<ul class="ex">
<li><b>Account</b> — depending on how you sign in: ID, first name and language from Telegram; email address;
Google account ID, email and name from Google. Plus settings: interface language, undo time, checklist on or off.</li>
<li><b>Tasks and projects</b>: title, notes, list, context, project, reminder time and where the task came from —
the site or the bot.</li>
<li><b>Activity counter</b>: per day and channel (site or bot) — how many actions you made. No task text,
no IP addresses.</li>
<li><b>Sign-in</b>: the <code>sid</code> cookie (httpOnly) keeps you signed in; a session expires after 90 days
without visits, and we store only its hash. Signing out deletes the session, “Sign out on all devices” deletes
all of them. Sign-in links and codes last 10 minutes; email codes are stored only as a hash. Signing in through
the bot keeps the IP address and browser name for the same 10 minutes, to show them in Telegram when you
confirm.</li>
<li><b>In your browser only</b> (localStorage): your language choice, unsaved drafts (erased when you sign out)
and your answer to the cookie banner.</li>
</ul>
<h2>Analytics</h2>
<ul class="ex">
<li><b>Google Analytics 4</b> — only on gtd.serbito.rs: which section is open (Inbox, Next…) and events — sign-in
and account linking (which method), the fact a task was captured, checklist steps, “How it works” views,
interface language. Task text, task numbers, email and user ID are never sent. GA sets its cookies only if you
agree (see below); either way, Google receives standard technical data from your browser (device,
approximate location).</li>
<li><b>Cloudflare Web Analytics</b> — total visits, no cookies.</li>
</ul>
<h2 id="cookies">Cookies and consent</h2>
<ul class="ex">
<li><b>Banner</b>: on gtd.serbito.rs, on your first visit we ask whether Google Analytics may set cookies —
Accept or Decline. The choice applies to both the site and the app.</li>
<li><b>Until you accept</b> (or if you decline), GA runs without cookies (Google Consent Mode): it only receives
pings without a browser identifier — which section is open or which event happened. Google uses them for
overall statistics but can't tell that visits come from the same person.</li>
<li><b>If you accept</b>, GA sets the <code>_ga</code> and <code>_ga_…</code> cookies — for gtd.serbito.rs only,
not for other serbito.rs sites; they show repeat visits. Consent covers analytics only: we have no ads, and
Google's advertising permissions stay off.</li>
<li><b>Your choice is stored</b> in this browser's localStorage for 12 months, then we ask again. If the browser
doesn't let us store it, the banner shows up on your next visit.</li>
<li><b>Change your mind</b> at any time: the “Cookie settings” link at the bottom of every page and in the app
menu. Declining deletes this site's <code>_ga*</code> cookies.</li>
</ul>
<h2>Who processes data</h2>
<ul class="ex">
<li><b>Google Cloud</b> (Cloud Run and Cloud SQL, region europe-west1, Belgium) — server and database.</li>
<li><b>Brevo</b> — sends emails with sign-in codes.</li>
<li><b>Sentry</b> — error reports, without request bodies, local variables or personal data.</li>
<li><b>Google Analytics</b> and <b>Cloudflare</b> — analytics, see above.</li>
<li><b>Telegram</b> — if you use the bot, messages and reminders pass through Telegram.</li>
<li><b>Google</b> — if you sign in with Google, it confirms your account to us.</li>
</ul>
<h2>How long we keep data</h2>
<p>As long as your account exists. A deleted task or project is erased from the database right away (Trash is
just a list you can restore from). Deleted data may remain in database backups until they are replaced.</p>
<h2>Deleting your account</h2>
<p>There is no delete button in the app yet — email us at the address below and say how you sign in (email,
Google or Telegram). We delete the account and all its data within 30 days of the request.</p>
<h2>Contact</h2>
<p>Operator: No Handoff. Questions about your data and deletion requests:
<a href="mailto:{email}">{email}</a>.</p>""",
}


def privacy(base: str, lang: str, ga: str) -> str:
    """Политика конфиденциальности — такая же лёгкая страница, как /about. ga — app.ga_snippet, как у /about."""
    path = PATHS[(lang, "privacy")]
    body = PRIVACY[lang].format(date=human_date(PRIVACY_DAY, lang), email=PRIVACY_EMAIL)
    track = ("<script>if(typeof gtag === 'function') gtag('event', 'page_view', "
             f"{{page_location: location.origin + '{path}', page_title: 'GTD — {T[lang]['privacy']}'}});</script>")
    return public_page(base, lang, "privacy", ga, body, track)


# «Что нового» (SERBITO-329): история версий из CHANGELOG.md. Разбирается один раз при старте (формат и его
# проверки — changelog.py и tests/test_changelog.py); [Unreleased] на сайт не попадает — только вышедшие версии
RELEASES = changelog.released(changelog.load())
CHANGELOG_URL = REPO_URL + "/blob/main/CHANGELOG.md"


def modified(page: str) -> str | None:
    """Когда страница по-настоящему менялась: для JSON-LD dateModified и lastmod в sitemap (SERBITO-445).
    Только там, где дата известна: «Что нового» — последний релиз, политика — её редакция. Главной и /about
    дату не ставим: дата деплоя — неправда, и Google перестаёт верить lastmod сайта"""
    return {"changes": RELEASES[0].date if RELEASES else None, "privacy": PRIVACY_DAY,
            "bot": GUIDES_DAY, "weekly": GUIDES_DAY}.get(page)
SEEN_KEY = "gtd-seen-version"  # localStorage: версия, которую человек уже видел на /changes (точка в меню SPA)
T["ru"].update(changes="Что нового", changes_title="Что нового в GTD — история версий приложения Getting Things Done",
               changes_desc="Что появилось и что исправлено в GTD онлайн: все версии приложения и Telegram-бота, "
                            "новые сверху.")
T["en"].update(changes="What's new", changes_title="What's new in GTD — release history of the Getting Things Done app",
               changes_desc="What's new and what got fixed in GTD online: every version of the app and the Telegram "
                            "bot, newest first.")
CHANGES = {
    # Вступление под заголовком (SERBITO-448): что это за страница и как часто выходят версии
    "ru": {"intro": "Здесь — всё, что менялось в GTD для пользователей: новые возможности и исправления на сайте "
                    "и в Telegram-боте. Новые версии выходят несколько раз в неделю.",
           "lead": "Изменения GTD по версиям, новые сверху.", "now": "Сейчас работает {v}.",
           "dev": "Это локальная сборка (dev).",
           "src": f'Тот же список на английском — <a href="{CHANGELOG_URL}">CHANGELOG.md</a> на GitHub.',
           "sections": {"Added": "Новое", "Changed": "Изменено", "Fixed": "Исправлено", "Security": "Безопасность"},
           "months": ("января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября",
                      "октября", "ноября", "декабря")},
    "en": {"intro": "This page lists every change in GTD that users can see: new features and fixes on the site "
                    "and in the Telegram bot. New versions come out several times a week.",
           "lead": "What changed in GTD, version by version, newest first.", "now": "You are using {v}.",
           "dev": "This is a local build (dev).",
           "src": f'Also on GitHub: <a href="{CHANGELOG_URL}">CHANGELOG.md</a>.',
           "sections": {"Added": "Added", "Changed": "Changed", "Fixed": "Fixed", "Security": "Security"},
           "months": ("January", "February", "March", "April", "May", "June", "July", "August", "September",
                      "October", "November", "December")},
}


def human_date(day: str, lang: str) -> str:
    y, m, d = (int(x) for x in day.split("-"))
    return f"{d} {CHANGES[lang]['months'][m - 1]} {y}"


def entry_html(text: str) -> str:
    """Текст пункта: экранируем, `код` — в <code>. Другой разметки в CHANGELOG.md не ждём."""
    return re.sub(r"`([^`]+)`", r"<code>\1</code>", html.escape(text, quote=False))


def release_html(r: changelog.Release, lang: str) -> str:
    c, i = CHANGES[lang], 1 if lang == "ru" else 0
    secs = "".join(f'<h3>{c["sections"][name]}</h3><ul>'
                   + "".join(f"<li>{entry_html(e[i])}</li>" for e in r.sections[name]) + "</ul>"
                   for name in changelog.SECTIONS if name in r.sections)
    return (f'<section class="rel" id="v{r.version}"><h2>v{r.version} '
            f'<span class="note">{human_date(r.date, lang)}</span></h2>{secs}</section>')


def changes(base: str, lang: str, ga: str) -> str:
    """Страница «Что нового» — такая же лёгкая, как /about. Открыл её — текущая версия считается увиденной."""
    c, path = CHANGES[lang], PATHS[(lang, "changes")]
    now = c["now"].format(v=VERSION_LABEL) if VERSION else c["dev"]
    body = (f'<h1>{T[lang]["changes"]}</h1><p class="lead">{c["intro"]}</p><p>{c["lead"]} {now}</p>'
            + "".join(release_html(r, lang) for r in RELEASES) + f'<p class="note" style="margin-top:32px">{c["src"]}</p>')
    title = json.dumps(f'GTD — {T[lang]["changes"]}', ensure_ascii=False)
    track = (f"<script>try{{ localStorage.setItem('{SEEN_KEY}', '{VERSION_LABEL}'); }}catch(e){{}}\n"
             "if(typeof gtag === 'function') gtag('event', 'page_view', "
             f"{{page_location: location.origin + '{path}', page_title: {title}}});</script>")
    return public_page(base, lang, "changes", ga, body, track)


def public_page(base: str, lang: str, page: str, ga: str, main: str, track: str = "") -> str:
    """Лёгкая публичная страница без JS приложения (как /about): <head> с SEO-тегами, шапка, <main>, подвал.
    ga — app.ga_snippet: согласие на cookie и (только на боевом домене) тег GA; track — свой <script> после body."""
    return f"""<!doctype html>
<html lang="{lang}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
{head(base, lang, page)}
{ICONS}
{ga}
{CF_BEACON}
{BASE_CSS}
</head>
<body>
<div class="pub">{topbar(lang, page)}<main>{main}</main>{footer(lang, bool(ga))}</div>
{track}
</body>
</html>"""


def page_view(lang: str, page: str) -> str:
    """Событие page_view в GA (если тег есть): только адрес и название страницы — никаких данных пользователя."""
    title = json.dumps(f"{SITE_NAME} — {T[lang][CRUMB[page]]}", ensure_ascii=False)
    return ("<script>if(typeof gtag === 'function') gtag('event', 'page_view', "
            f"{{page_location: location.origin + '{PATHS[(lang, page)]}', page_title: {title}}});</script>")


# ── Справочные страницы под поисковые запросы (SERBITO-441, SERBITO-446) ──
# Каждое утверждение сверено с кодом: захват — app.parse_task/parse_when, кнопки и команды бота — app.kb_item и
# app.handle_message, привязка — «Аккаунт» в index.html, MCP — mcp_server.py, шаги обзора — reviewHtml в index.html.
# Меняешь поведение — правь и текст. Примеры захвата проверяет test_bot_page_capture_examples_work, шаги обзора —
# test_weekly_review_page_matches_app. GUIDES_DAY — редакция текста этих страниц: дата на странице, dateModified
# и lastmod в sitemap; правишь текст — ставь новую дату
GUIDES_DAY = "2026-10-05"
T["ru"].update(
    bot="Telegram-бот",
    bot_title=f"Telegram бот для задач и напоминаний, бесплатно — {SITE_NAME}",
    bot_desc="Бесплатный Telegram-бот для задач по методу GTD: пиши задачу сообщением, бот поймёт срок, #проект "
             "и @контекст и напомнит с кнопками ✅ и +1ч.",
    # Имя списка — по-английски (SERBITO-354). Поисковый запрос «еженедельный обзор» — только в title, h1 и description
    weekly="Weekly Review",
    weekly_title=f"Еженедельный обзор GTD: чек-лист на 30 минут и шаблон — {SITE_NAME}",
    weekly_desc="Еженедельный обзор GTD за 30 минут: шесть шагов, как в приложении, пример по минутам и шаблон "
                "чек-листа, который можно скопировать.")
T["en"].update(
    bot="Telegram bot",
    bot_title=f"Telegram bot for tasks and reminders, free — {SITE_NAME}",
    bot_desc="A free Telegram bot for tasks, built on GTD. Send a task as a message: it reads dates, #Project and "
             "@context and reminds you with Done and +1h buttons.",
    weekly="Weekly review",
    weekly_title=f"GTD weekly review template: a 30-minute checklist — {SITE_NAME}",
    weekly_desc="A GTD weekly review in 30 minutes: six steps that match the app, a minute-by-minute example and a "
                "plain-text checklist template you can copy.")
UPDATED = {"ru": "Обновлено", "en": "Updated"}

# Примеры захвата на /bot: (что написать, что получится). Тест прогоняет каждый через настоящий захват
BOT_CAPTURE = {
    "ru": [("позвонить маме завтра в 10:00", "задача в Inbox и напоминание завтра в 10:00."),
           ("через 2 часа забрать посылку", "напоминание через 2 часа."),
           ("в пятницу отчёт #Клиент_X @работа", "сразу в Next: проект «Клиент X», контекст @работа, напоминание "
                                                 "в пятницу в 9:00."),
           ("оплатить счёт @телефон @комп", "два контекста: в Next задача видна и под @телефон, и под @комп."),
           ("24.10 12:00 стоматолог", "напоминание 24 октября в 12:00."),
           ("напомни купить хлеб в 18:30", "задача «купить хлеб»: слово «напомни» бот убирает. Напоминание "
                                           "сегодня в 18:30, а если это время уже прошло — завтра.")],
    "en": [("call mom tomorrow at 10am", "a task in the Inbox and a reminder tomorrow at 10:00."),
           ("in 2 hours pick up the parcel", "a reminder in 2 hours."),
           ("report on friday #Client_X @work", "straight to Next: project “Client X”, context @work, a reminder "
                                                "on Friday at 9:00."),
           ("pay the bill @phone @computer", "two contexts: in Next the task shows under both @phone and @computer."),
           ("dentist 24 oct 12:00", "a reminder on 24 October at 12:00."),
           ("remind me to buy bread at 6:30pm", "the task is “buy bread”: the bot drops “remind me to”. The reminder "
                                                "is today at 18:30, or tomorrow if that time has passed.")],
}

BOT = {
    "ru": """<h1>Telegram-бот для задач: пишешь в чат — задача в Inbox</h1>
<p class="lead"><a href="{bot_url}">@{bot}</a> — бесплатный Telegram-бот для задач по методу GTD (Getting Things Done).
Пиши задачу обычным сообщением: бот поймёт срок, проект и контекст и напомнит в нужное время.
Бот и сайт — один аккаунт.</p>
<p class="note">{updated}</p>
<p><a class="btn" href="{bot_url}">Открыть @{bot}</a></p>
<h2>Как начать за 3 шага?</h2>
<p>Открой бота, напиши задачу, разбирай задачи на сайте. Регистрация не нужна.</p>
<ol class="steps">
<li><b>1. Открой бота</b>Нажми «Открыть @{bot}», затем Start. Аккаунт создастся сам — без почты и пароля.</li>
<li><b>2. Напиши задачу</b>Обычным сообщением: <q>позвонить маме завтра в 10:00</q>. Бот ответит, куда попала
задача — в Inbox или Next, — и поставит напоминание.</li>
<li><b>3. Разбирай на сайте</b>Войди на <a href="{home}">сайте</a> через Telegram — это тот же аккаунт. Там удобно
разбирать Inbox, вести проекты и делать <a href="{weekly}">Weekly Review</a>.</li>
</ol>
<h2>Как записать задачу со сроком, проектом и контекстом?</h2>
<p>Пиши как обычно. Бот сам найдёт в тексте срок, #Проект и @контекст и уберёт их из названия задачи.</p>
<ul class="ex">{examples}</ul>
<p>Что ещё важно знать:</p>
<ul>
<li>Сроки: <q>сегодня</q>, <q>завтра</q>, <q>послезавтра</q>, день недели (<q>в пятницу</q>), <q>через 30 минут</q>,
<q>через 3 дня</q>, <q>через 2 недели</q>, дата (<q>24.10</q>, <q>2026-10-24</q>) и время (<q>10:00</q>).
Английские слова тоже работают: <q>tomorrow 10am</q>.</li>
<li>Дата без времени — напоминание в 9:00. Время без даты — сегодня, а если оно уже прошло — завтра.</li>
<li>#Проект — одно слово, вместо пробела — «_»: <q>#Ремонт_кухни</q>. Такого проекта нет — бот его создаст.</li>
<li>@контекстов может быть несколько. С #проектом или @контекстом задача сразу идёт в Next, без них — в Inbox.</li>
<li>Время считается по часовому поясу {tz}.</li>
<li>Тот же синтаксис работает в поле захвата на сайте.</li>
</ul>
<h2>Как работают напоминания?</h2>
<p>В назначенное время бот присылает сообщение с задачей и тремя кнопками.</p>
<ul class="ex">
<li><b>✅ Готово</b> — закрыть задачу.</li>
<li><b>💤 +1ч</b> — напомнить ещё раз через час.</li>
<li><b>⏭ Next</b> — перенести задачу в Next.</li>
</ul>
<p>Те же кнопки есть под ответом бота на каждую новую задачу. Напоминания приходят только в Telegram: если ты завёл
аккаунт на сайте, привяжи к нему бота.</p>
<h2>Какие команды понимает бот?</h2>
<p>Семь команд. Всё остальное, что ты пишешь, бот записывает как задачу.</p>
<ul class="ex">
<li><code>/inbox</code> — что в Inbox, <code>/next</code> — что в Next (первые 20 задач).</li>
<li><code>/done 12</code> — закрыть задачу №12. Номер бот показывает в ответе на каждую задачу.</li>
<li><code>/login</code> — одноразовая ссылка для входа на сайт. Она работает 10 минут.</li>
<li><code>/email you@example.com</code> — привязать почту, чтобы входить на сайте по коду или через Google.</li>
<li><code>/about</code> — что такое GTD, <code>/help</code> — примеры захвата и список команд.</li>
</ul>
<h2>Как связать бота и аккаунт на сайте?</h2>
<p>Бот и сайт — один аккаунт. Как его связать, зависит от того, с чего ты начал.</p>
<ul class="ex">
<li><b>Начал с бота.</b> На сайте нажми «Войти через Telegram» — откроется тот же аккаунт. Или пришли боту
<code>/login</code> и открой ссылку из ответа.</li>
<li><b>Начал на сайте</b> (Google или почта). Открой «👤 Аккаунт» → Telegram → «Привязать Telegram». В боте нажми
Start, затем кнопку с числом, которое показал сайт.</li>
<li><b>Хочешь входить и по почте.</b> Пришли боту <code>/email</code> и адрес. Код придёт в письме, его нужно
отправить боту.</li>
<li>Если у этого Telegram уже есть свой аккаунт с задачами, {site} предложит объединить аккаунты.</li>
</ul>
<h2 id="claude">Можно ли подключить Claude?</h2>
<p>Да. У {site} есть MCP-сервер, и Claude работает с твоими задачами напрямую.</p>
<p>Claude может записать задачу в Inbox — с тем же синтаксисом, — показать списки, проекты и контексты, закрыть
или изменить задачу, перенести её в другой список или проект и провести Weekly Review.</p>
<ol>
<li>В Claude открой Settings → Connectors → Add custom connector.</li>
<li>Введи адрес <code>{mcp}</code>. Поля OAuth оставь пустыми.</li>
<li>Войди в {site} и нажми Allow.</li>
</ol>
<p>ChatGPT, Cursor, VS Code, Claude Code и другие клиенты — в <a href="{mcp_doc}">инструкции на GitHub</a>
(на английском).</p>
<h2>Что бот хранит?</h2>
<p>Задачи и их поля: срок, проект, контексты. Из Telegram — id, имя и язык. Бот работает только в личных
сообщениях: в группах он молчит. Подробнее — в <a href="{privacy}">политике конфиденциальности</a>.</p>
<p><a class="btn" href="{bot_url}">Открыть @{bot}</a></p>""",
    "en": """<h1>Telegram bot for tasks: send a message, get a task in your Inbox</h1>
<p class="lead"><a href="{bot_url}">@{bot}</a> is a free Telegram bot for tasks, built on the GTD (Getting Things Done)
method. Send a task as a plain message. The bot reads the date, project and context and reminds you on time.
The bot and the website share one account.</p>
<p class="note">{updated}</p>
<p><a class="btn" href="{bot_url}">Open @{bot}</a></p>
<h2>How do I start in 3 steps?</h2>
<p>Open the bot, send a task, sort your tasks on the website. You do not need to sign up.</p>
<ol class="steps">
<li><b>1. Open the bot</b>Tap “Open @{bot}”, then Start. The account is created for you — no email, no password.</li>
<li><b>2. Send a task</b>As a plain message: <q>call mom tomorrow at 10am</q>. The bot tells you where the task went —
Inbox or Next — and sets the reminder.</li>
<li><b>3. Sort it on the website</b>Sign in on the <a href="{home}">website</a> with Telegram — it is the same account.
There you clear the Inbox, track projects and do the <a href="{weekly}">weekly review</a>.</li>
</ol>
<h2>How do I add a date, a project and a context?</h2>
<p>Write as usual. The bot finds the date, #Project and @context in the text and removes them from the task title.</p>
<ul class="ex">{examples}</ul>
<p>Good to know:</p>
<ul>
<li>Dates: <q>today</q>, <q>tomorrow</q>, <q>day after tomorrow</q>, a weekday (<q>on friday</q>, <q>next monday</q>),
<q>in 30 minutes</q>, <q>in 3 days</q>, <q>in 2 weeks</q>, a date (<q>24 oct</q>, <q>oct 24</q>, <q>2026-10-24</q>)
and a time (<q>10am</q>, <q>3:30pm</q>, <q>18:30</q>). Russian works too: <q>завтра в 10:00</q>.</li>
<li>A date without a time sets the reminder at 9:00. A time without a date means today, or tomorrow if that time
has passed.</li>
<li>A #Project is one word; use “_” for a space: <q>#Kitchen_renovation</q>. If the project does not exist, the bot
creates it.</li>
<li>A task can have several @contexts. With a #project or a @context the task goes straight to Next; without them, to
the Inbox.</li>
<li>Times use the {tz} time zone.</li>
<li>The same syntax works in the capture field on the website.</li>
</ul>
<h2>How do reminders work?</h2>
<p>At the set time the bot sends you the task with three buttons.</p>
<ul class="ex">
<li><b>✅ Done</b> — complete the task.</li>
<li><b>💤 +1h</b> — remind you again in an hour.</li>
<li><b>⏭ Next</b> — move the task to Next.</li>
</ul>
<p>The same buttons come with the bot's reply to every new task. Reminders arrive only in Telegram: if you signed up
on the website, link the bot to your account.</p>
<h2>Which commands does the bot know?</h2>
<p>Seven commands. Everything else you send becomes a task.</p>
<ul class="ex">
<li><code>/inbox</code> — what is in your Inbox, <code>/next</code> — your next actions (the first 20 tasks).</li>
<li><code>/done 12</code> — complete task #12. The bot shows the number in its reply to every task.</li>
<li><code>/login</code> — a one-time sign-in link for the website. It works for 10 minutes.</li>
<li><code>/email you@example.com</code> — link an email to sign in on the website with a code or with Google.</li>
<li><code>/about</code> — what GTD is, <code>/help</code> — capture examples and the list of commands.</li>
</ul>
<h2>How do I link the bot and my website account?</h2>
<p>The bot and the website share one account. How you link them depends on where you started.</p>
<ul class="ex">
<li><b>You started in the bot.</b> On the website, press “Sign in with Telegram” — the same account opens. Or send
<code>/login</code> to the bot and open the link it sends.</li>
<li><b>You started on the website</b> (Google or email). Open “👤 Account” → Telegram → “Link Telegram”. In the bot,
tap Start, then the button with the number that the website shows.</li>
<li><b>You also want to sign in with email.</b> Send <code>/email</code> and your address to the bot. Then send the code
from the email back to the bot.</li>
<li>If this Telegram already has its own account with tasks, {site} offers to merge the two accounts.</li>
</ul>
<h2 id="claude">Can I connect Claude?</h2>
<p>Yes. {site} has an MCP server, so Claude can work with your tasks directly.</p>
<p>Claude can capture a task to the Inbox — with the same syntax —, show your lists, projects and contexts, complete
or edit a task, move it to another list or project, and run the weekly review.</p>
<ol>
<li>In Claude, open Settings → Connectors → Add custom connector.</li>
<li>Enter the address <code>{mcp}</code>. Leave the OAuth fields empty.</li>
<li>Sign in to {site} and press Allow.</li>
</ol>
<p>ChatGPT, Cursor, VS Code, Claude Code and other clients: see the <a href="{mcp_doc}">guide on GitHub</a>.</p>
<h2>What does the bot store?</h2>
<p>Your tasks and their fields: date, project, contexts. From Telegram: your ID, first name and language. The bot
works only in private chats; in groups it stays silent. Details are in the <a href="{privacy}">privacy policy</a>.</p>
<p><a class="btn" href="{bot_url}">Open @{bot}</a></p>""",
}


def bot(base: str, lang: str, ga: str, tz: str = "Europe/Belgrade") -> str:
    """Страница Telegram-бота (SERBITO-441). tz — часовой пояс сервиса (app.TZ): по нему бот понимает время."""
    examples = "".join(f"<li><q>{html.escape(text)}</q> — {html.escape(what, quote=False)}</li>"
                       for text, what in BOT_CAPTURE[lang])
    main = BOT[lang].format(
        bot=BOT_NAME, bot_url=BOT_URL, home=PATHS[(lang, "home")], weekly=PATHS[(lang, "weekly")],
        privacy=PATHS[(lang, "privacy")], examples=examples, tz=html.escape(tz), mcp=f"{base}/mcp",
        mcp_doc=REPO_URL + "#connect-claude-mcp", site=SITE_NAME, updated=f"{UPDATED[lang]} {human_date(GUIDES_DAY, lang)}")
    return public_page(base, lang, "bot", ga, main, page_view(lang, "bot"))


# Шаблон обзора простым текстом — его копируют в заметки. Шаги — те же, что в разделе Weekly Review приложения
# (review.s1…s6 в index.html), минуты — как в примере на 30 минут
WEEKLY_TEMPLATE = {
    "ru": """Weekly Review по GTD — чек-лист на 30 минут

[ ] 1. Очистить голову (5 мин)
    Запиши всё, что в голове: дела, идеи, обещания.
[ ] 2. Разобрать Inbox до нуля (10 мин)
    По каждой записи: что это и какой следующий шаг.
[ ] 3. Проекты без следующего действия (5 мин)
    У каждого проекта — хотя бы одна задача в Next.
[ ] 4. В Waiting дольше недели (3 мин)
    Кому пора напомнить? Чего больше не нужно ждать?
[ ] 5. Ближайшие напоминания (4 мин)
    Что впереди? Всё ли на своих местах?
[ ] 6. Someday/Maybe (3 мин)
    Что пора начать? Что больше не интересно?""",
    "en": """GTD weekly review — a 30-minute checklist

[ ] 1. Get clear (5 min)
    Write down everything on your mind: tasks, ideas, promises.
[ ] 2. Inbox to zero (10 min)
    For each item: what is it, and what is the next step?
[ ] 3. Projects without a next action (5 min)
    Every project needs at least one task in Next.
[ ] 4. Waiting for over a week (3 min)
    Who needs a nudge? What can you stop waiting for?
[ ] 5. Upcoming reminders (4 min)
    What is coming up? Is everything in place?
[ ] 6. Someday/Maybe (3 min)
    What is it time to start? What no longer matters?""",
}
COPY = {"ru": ("Скопировать шаблон", "Скопировано ✓"), "en": ("Copy the template", "Copied ✓")}

WEEKLY = {
    "ru": """<h1>Еженедельный обзор GTD: чек-лист на 30 минут</h1>
<p class="lead">Weekly Review — это полчаса раз в неделю, когда ты проходишь по всем своим спискам. Ниже — шесть
шагов, как в разделе Weekly Review в {site}, пример по минутам и шаблон, который можно скопировать.</p>
<p class="note">{updated}</p>
<h2>Зачем нужен еженедельный обзор?</h2>
<p>Чтобы снова доверять своим спискам. За неделю в Inbox копятся записи, проекты теряют следующий шаг, а ожидания
забываются. Тогда голова опять начинает держать всё сама. Обзор возвращает всё на свои места.</p>
<h2>Какие шаги у еженедельного обзора?</h2>
<p>Шесть шагов — те же, что в разделе Weekly Review в {site}. На каждом шаге приложение само показывает, что требует
внимания.</p>
<ol class="steps">
<li><b>1. Очистить голову</b>Запиши всё, что крутится в голове: дела, идеи, обещания. Не разбирай — просто записывай.
<span class="where">Поле захвата сверху или сообщение <a href="{bot}">боту</a></span></li>
<li><b>2. Разобрать Inbox до нуля</b>По каждой записи реши, что это и какой следующий шаг. Разложи по Next, Waiting,
Someday, Reference или удали.
<span class="where">Сколько записей в Inbox — и ссылка на него</span></li>
<li><b>3. Проекты без следующего действия</b>У каждого активного проекта должна быть хотя бы одна задача в Next.
<span class="where">Список проектов без задачи в Next, с пометкой ⚠</span></li>
<li><b>4. В Waiting дольше недели</b>Пройди по ожиданиям: кому пора напомнить, а чего больше не нужно ждать.
<span class="where">Задачи в Waiting, записанные больше недели назад</span></li>
<li><b>5. Ближайшие напоминания</b>Посмотри, что впереди, и перенеси то, что не успеешь.
<span class="where">Восемь ближайших напоминаний, включая просроченные</span></li>
<li><b>6. Someday/Maybe</b>Перечитай идеи на потом. Что пора начать — перенеси в Next или сделай проектом.
<span class="where">Сколько идей в Someday — и ссылка на список</span></li>
</ol>
<h2>Как провести обзор за 30 минут?</h2>
<p>Поставь таймер и иди по шагам по порядку. Вот пример обычной недели: 14 записей в Inbox и пять проектов.</p>
<table>
<thead><tr><th>Минуты</th><th>Шаг и что происходит</th></tr></thead>
<tbody>
<tr><td>0–5</td><td><b>Очистить голову.</b> Записал ещё 6 мыслей: <q>купить фильтр для воды</q>, <q>ответить Ане про отпуск</q>…</td></tr>
<tr><td>5–15</td><td><b>Inbox до нуля.</b> 20 записей: 9 — в Next, 3 — в Someday, 2 — в Reference, 6 удалил.</td></tr>
<tr><td>15–20</td><td><b>Проекты.</b> У «Ремонт кухни» нет шага в Next — добавил <q>выбрать плитку #Ремонт_кухни @магазин</q>.</td></tr>
<tr><td>20–23</td><td><b>Waiting.</b> Счёт от бухгалтера ждём уже 9 дней — написал ему.</td></tr>
<tr><td>23–27</td><td><b>Напоминания.</b> На неделе 4 напоминания, одно перенёс на понедельник.</td></tr>
<tr><td>27–30</td><td><b>Someday.</b> «Курсы испанского» — пора: перенёс в Next.</td></tr>
</tbody>
</table>
<h2>Где взять шаблон еженедельного обзора?</h2>
<p>Скопируй текст ниже в заметки или в календарь. Это те же шесть шагов с флажками и временем.</p>
<pre id="tpl">{template}</pre>
<p><button class="btn" type="button" data-copy="tpl" data-done="{copied}">{copy}</button></p>
<h2>Как часто делать обзор?</h2>
<p>Раз в неделю, в одно и то же время. Например, в пятницу после обеда: неделя ещё свежа в памяти, а в понедельник
ты начинаешь с чистыми списками.</p>
<h2>Что делать, если пропустил неделю?</h2>
<p>Просто начни сейчас. Если времени мало, сделай хотя бы шаги 1 и 2: очисти голову и разбери Inbox. Остальные шаги
сделай в следующий раз.</p>
<h2>Как начать обзор в {site}?</h2>
<p>Открой раздел 🔍 Weekly Review в меню приложения. Шаги уже там, со списками из твоих задач.</p>
<p><a class="btn" href="{review}">Начать обзор в {site}</a></p>
<p>Обзор можно провести и с Claude: у {site} есть MCP-сервер, и Claude соберёт сводку по тем же шагам.
Как подключить — <a href="{bot}#claude">на странице бота</a>.</p>""",
    "en": """<h1>GTD weekly review: a 30-minute checklist and template</h1>
<p class="lead">The weekly review is half an hour once a week when you go over all your lists. Below are six steps
that match the Weekly Review view in {site}, a minute-by-minute example and a template you can copy.</p>
<p class="note">{updated}</p>
<h2>Why do a weekly review?</h2>
<p>So you can trust your lists again. During a week the Inbox fills up, projects lose their next step and waiting-fors
get forgotten. Then your head starts to hold everything again. The review puts things back in place.</p>
<h2>What are the steps of a weekly review?</h2>
<p>Six steps, the same as in the Weekly Review view in {site}. At each step the app shows you what needs attention.</p>
<ol class="steps">
<li><b>1. Get clear</b>Write down everything on your mind: tasks, ideas, promises. Do not sort yet — just write.
<span class="where">The capture field at the top, or a message to the <a href="{bot}">bot</a></span></li>
<li><b>2. Inbox to zero</b>For each item, decide what it is and what the next step is. Move it to Next, Waiting,
Someday or Reference, or delete it.
<span class="where">How many items are in the Inbox, with a link to it</span></li>
<li><b>3. Projects without a next action</b>Every active project needs at least one task in Next.
<span class="where">Projects with no task in Next, marked ⚠</span></li>
<li><b>4. Waiting for over a week</b>Go over what you are waiting for: who needs a nudge, and what you can stop waiting for.
<span class="where">Tasks in Waiting that you added more than a week ago</span></li>
<li><b>5. Upcoming reminders</b>Look at what is coming up and move what you will not get to.
<span class="where">The eight nearest reminders, overdue ones included</span></li>
<li><b>6. Someday/Maybe</b>Read through your ideas for later. Move what is ready to Next, or make it a project.
<span class="where">How many ideas are in Someday, with a link to the list</span></li>
</ol>
<h2>How do I do a weekly review in 30 minutes?</h2>
<p>Set a timer and go through the steps in order. Here is a typical week: 14 items in the Inbox and five projects.</p>
<table>
<thead><tr><th>Minutes</th><th>Step and what happens</th></tr></thead>
<tbody>
<tr><td>0–5</td><td><b>Get clear.</b> Wrote down 6 more thoughts: <q>buy a water filter</q>, <q>answer Anna about the holiday</q>…</td></tr>
<tr><td>5–15</td><td><b>Inbox to zero.</b> 20 items: 9 to Next, 3 to Someday, 2 to Reference, 6 deleted.</td></tr>
<tr><td>15–20</td><td><b>Projects.</b> “Kitchen renovation” has no step in Next — added <q>choose tiles #Kitchen_renovation @store</q>.</td></tr>
<tr><td>20–23</td><td><b>Waiting.</b> The accountant's invoice is 9 days late — sent a reminder.</td></tr>
<tr><td>23–27</td><td><b>Reminders.</b> 4 reminders this week; moved one to Monday.</td></tr>
<tr><td>27–30</td><td><b>Someday.</b> “Spanish classes” — time to start: moved to Next.</td></tr>
</tbody>
</table>
<h2>Where can I get a weekly review template?</h2>
<p>Copy the text below into your notes or your calendar. It has the same six steps, with checkboxes and times.</p>
<pre id="tpl">{template}</pre>
<p><button class="btn" type="button" data-copy="tpl" data-done="{copied}">{copy}</button></p>
<h2>How often should I do a weekly review?</h2>
<p>Once a week, at the same time. For example, on Friday afternoon: the week is still fresh, and on Monday you start
with clean lists.</p>
<h2>What if I skip a week?</h2>
<p>Just start now. If you are short on time, do at least steps 1 and 2: get clear and empty the Inbox. Do the other
steps next time.</p>
<h2>How do I start the review in {site}?</h2>
<p>Open 🔍 Weekly Review in the app menu. The steps are already there, filled with your own tasks.</p>
<p><a class="btn" href="{review}">Start the review in {site}</a></p>
<p>You can also run the review with Claude: {site} has an MCP server, and Claude builds a summary along the same steps.
See <a href="{bot}#claude">how to connect it</a>.</p>""",
}
# Копирование шаблона: без JS текст в <pre> просто выделяется, с JS — кнопка кладёт его в буфер обмена
COPY_JS = """<script>
document.addEventListener('click', e => {
  const b = e.target.closest('[data-copy]'); if(!b) return;
  const el = document.getElementById(b.dataset.copy), label = b.textContent;
  const done = () => { b.textContent = b.dataset.done; setTimeout(() => { b.textContent = label; }, 2000); };
  const select = () => { const r = document.createRange(); r.selectNodeContents(el);
    const s = getSelection(); s.removeAllRanges(); s.addRange(r); };
  if(navigator.clipboard) navigator.clipboard.writeText(el.textContent).then(done, select); else select();
});
</script>"""


def weekly(base: str, lang: str, ga: str) -> str:
    """Чек-лист Weekly Review (SERBITO-446): шаги — как в разделе приложения, пример на 30 минут, шаблон и кнопка
    «Начать обзор» — в раздел /review (гость сначала войдёт)."""
    copy, copied = COPY[lang]
    main = WEEKLY[lang].format(
        bot=PATHS[(lang, "bot")], review="/review" if lang == "ru" else "/en/review",
        template=html.escape(WEEKLY_TEMPLATE[lang], quote=False), copy=copy, copied=copied, site=SITE_NAME,
        updated=f"{UPDATED[lang]} {human_date(GUIDES_DAY, lang)}")
    return public_page(base, lang, "weekly", ga, main, COPY_JS + "\n" + page_view(lang, "weekly"))


def robots(base: str) -> str:
    # /i/N не закрываем (SERBITO-448): адрес под Disallow робот не открывает и не видит его X-Robots-Tag: noindex —
    # а ссылку на него, найденную где-то ещё, Google оставляет в индексе без текста. Пусть читает и не индексирует
    return "\n".join(["User-agent: *", "Allow: /", "Allow: /about", "Allow: /privacy", "Allow: /changes", "Allow: /bot",
                      "Allow: /weekly-review", "Allow: /en/",
                      *(f"Disallow: {p}" for p in ("/api/", "/auth", "/dev-login", "/tg/", "/tasks/")),
                      "", f"Sitemap: {base}/sitemap.xml", ""])


def llms(base: str) -> str:
    """/llms.txt (llmstxt.org, SERBITO-448): что такое GTD и ссылки на главное — для LLM-ассистентов. Google его
    не читает; файл короткий и дешёвый. Только текст: тот же набор страниц, что в sitemap, плюс бот и код."""
    page = lambda lang, p, name: f"- [{name}]({url(base, lang, p)})"  # noqa: E731
    return "\n".join([
        f"# {SITE_NAME}", "",
        "> GTD is a free app for David Allen's Getting Things Done method. Capture tasks on the web or in the "
        f"Telegram bot @{BOT_NAME}, clear the Inbox, track next actions, projects and reminders. Open source (MIT), "
        "in English and Russian.", "",
        "## Pages", "",
        page("en", "home", "Home (English)") + ": sign in with Google, an email code or Telegram",
        page("ru", "home", "Home (Russian)"),
        page("en", "about", "How it works (English)") + ": the 5 GTD steps and where each one lives in the app",
        page("ru", "about", "How it works (Russian)"),
        page("en", "changes", "What's new (English)") + ": release history, newest first",
        page("ru", "changes", "What's new (Russian)"),
        page("en", "bot", "Telegram bot for tasks (English)") + ": capture syntax, reminders, commands, linking, MCP",
        page("ru", "bot", "Telegram bot for tasks (Russian)"),
        page("en", "weekly", "GTD weekly review (English)") + ": the 6-step checklist, a 30-minute example, a template",
        page("ru", "weekly", "GTD weekly review (Russian)"), "",
        "## Elsewhere", "",
        f"- [Telegram bot @{BOT_NAME}]({BOT_URL}): send a task as a message, get reminders in the chat",
        f"- [Source code on GitHub]({REPO_URL}): MIT license, self-hosting guide", ""])


def sitemap(base: str) -> str:
    """Публичные адреса из PATHS, у каждого — альтернативы ru/en/x-default и lastmod, где дата известна."""
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:xhtml="http://www.w3.org/1999/xhtml">']
    for (lang, page) in PATHS:
        alts = [(lg, url(base, lg, page)) for lg in LANGS] + [("x-default", url(base, "ru", page))]
        day = modified(page)
        out.append(f"<url><loc>{url(base, lang, page)}</loc>" + (f"<lastmod>{day}</lastmod>" if day else "")
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
            # Все иконки непрозрачные, бирюзовые во весь холст (SERBITO-392): прозрачные углы macOS показывал чёрными
            "icons": [{"src": f"/icon-{kind}{size}.png", "sizes": f"{size}x{size}", "type": "image/png", "purpose": purpose}
                      for kind, purpose in (("", "any"), ("maskable-", "maskable")) for size in (192, 512)]}


# Страница 404 для браузера (неизвестный адрес вне /api/): заголовок, пояснение, «на главную», «как это работает»
NOT_FOUND = {
    "ru": ("Страница не найдена", "Такой страницы нет — возможно, ссылка устарела или в ней опечатка.",
           "Открыть GTD", "Как это работает"),
    "en": ("Page not found", "There is no page at this address — the link may be outdated or mistyped.",
           "Open GTD", "How it works"),
}


# Устаревшая ссылка входа из бота (/auth): что случилось, «войти на сайте» и, если бот известен, «открыть бота»
AUTH_STALE = {
    "ru": ("Ссылка устарела", "Ссылка входа из бота работает 10 минут и только один раз. "
           "Отправь /login боту ещё раз или войди на сайте.", "Войти", "Открыть бота"),
    "en": ("This link has expired", "A sign-in link from the bot works once and only for 10 minutes. "
           "Send /login to the bot again or sign in on the site.", "Sign in", "Open the bot"),
}


def not_found(lang: str) -> str:
    h, text, go, how = NOT_FOUND[lang]
    return notice(lang, h, text, go, (PATHS[(lang, "about")], how))


def auth_stale(lang: str, bot: str = "") -> str:
    h, text, go, open_bot = AUTH_STALE[lang]
    return notice(lang, h, text, go, (f"https://t.me/{bot}", open_bot) if bot else None)


# Вход по ссылке из бота — только кнопкой (SERBITO-360, GTD-7): видно, в чей аккаунт, и чужую ссылку можно узнать
AUTH_CONFIRM = {
    "ru": ("Вход в GTD", "Войти в аккаунт <b>{who}</b>? Ссылку присылает бот на команду /login. Если ты её "
           "не запрашивал — закрой страницу: кто-то пытается подсунуть тебе свой аккаунт.",
           "Сейчас в этом браузере открыт другой аккаунт — <b>{cur}</b>. Вход переключит на <b>{who}</b>.",
           "Войти", "Отмена"),
    "en": ("Sign in to GTD", "Sign in to the account <b>{who}</b>? The bot sends this link in reply to /login. "
           "If you didn't ask for it, close this page: someone may be trying to slip you their account.",
           "Another account is signed in in this browser — <b>{cur}</b>. Signing in switches to <b>{who}</b>.",
           "Sign in", "Cancel"),
}


def auth_confirm(lang: str, who: str, token: str, link_lang: str = "", current: str = "") -> str:
    """Страница /auth?t=…: в чей аккаунт вход и кнопка «Войти» (POST /auth). current — аккаунт, уже открытый
    в этом браузере, если он другой."""
    h, text, switch, go, cancel = AUTH_CONFIRM[lang]
    who, cur = html.escape(who or "?"), html.escape(current)
    note = f'<p class="note">{switch.format(cur=cur, who=who)}</p>' if current else ""
    home = PATHS[(lang, "home")]
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
<div class="pub"><header class="top"><a class="brand" href="{home}">{mark(28)} GTD</a></header><main>
<h1>{h}</h1>
<p class="lead">{text.format(who=who)}</p>
{note}
<form method="post" action="/auth"><input type="hidden" name="t" value="{html.escape(token)}">
<input type="hidden" name="lang" value="{html.escape(link_lang)}">
<p><button class="btn" type="submit">{go}</button>&emsp;<a href="{home}">{cancel}</a></p></form></main></div>
</body>
</html>"""


def notice(lang: str, h: str, text: str, go: str, link: tuple | None) -> str:
    """Короткая служебная страница: заголовок, пояснение, кнопка на главную и (необязательно) вторая ссылка."""
    home = PATHS[(lang, "home")]
    extra = f'&emsp;<a href="{link[0]}">{link[1]}</a>' if link else ""
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
<div class="pub"><header class="top"><a class="brand" href="{home}">{mark(28)} GTD</a></header><main>
<h1>{h}</h1>
<p class="lead">{text}</p>
<p><a class="btn" href="{home}">{go}</a>{extra}</p></main></div>
</body>
</html>"""
