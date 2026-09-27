"""Согласие на cookie аналитики (SERBITO-319, решение SERBITO-285): Consent Mode v2 «запрещено» по умолчанию
раньше любого gtag('config'), баннер и «Настройки cookie» на всех публичных страницах и в приложении,
раздел о согласии в политике. Поведение в браузере (Принять/Отклонить, localStorage, cookie _ga) —
в test_browser_smoke.py."""
import json
import re

import pytest

import app as A
import pages as P

PUBLIC = {"/": "ru", "/about": "ru", "/privacy": "ru", "/en/": "en", "/en/about": "en", "/en/privacy": "en"}
PROD = {"host": "gtd.serbito.rs"}
DEFAULT = "gtag('consent', 'default'"


@pytest.mark.parametrize("path", [*PUBLIC, "/i/5"])  # /i/N — страница приложения без лендинга
def test_consent_default_before_ga_config(client, monkeypatch, path):
    monkeypatch.setattr(A, "GA_ID", "G-TEST123")
    h = client.get(path, headers=PROD).text
    assert h.count(DEFAULT) == 1
    # dataLayer/gtag → consent default → gtag.js → config: иначе первый пинг уйдёт без согласия
    assert (h.index("function gtag(){ dataLayer.push(arguments); }") < h.index(DEFAULT)
            < h.index("googletagmanager.com/gtag/js?id=G-TEST123") < h.index('gtag("config", "G-TEST123"'))
    # _ga — host-only на gtd.serbito.rs, не общий .serbito.rs с соседними продуктами
    assert 'gtag("config", "G-TEST123", { send_page_view: false, cookie_domain: "none" });' in h
    assert h.index(DEFAULT) < h.index("</head>")


def test_consent_default_denied_and_ads_never_granted():
    js = P.CONSENT_JS
    # «Принять» — только analytics_storage; три рекламных разрешения denied при любом выборе
    assert ("const state = v => ({ad_storage: 'denied', analytics_storage: v, ad_user_data: 'denied', "
            "ad_personalization: 'denied'});") in js
    assert "state(choice === 'granted' ? 'granted' : 'denied'), wait_for_update: 500" in js  # выбора нет — denied
    assert "gtag('consent', 'update', state(v))" in js
    assert P.CONSENT_DAYS == 365 and f"const KEY = '{P.CONSENT_KEY}', TTL = 365 * 864e5" in P.CONSENT_HEAD


@pytest.mark.parametrize("path", [*PUBLIC, "/i/5"])
def test_banner_everywhere_ga_only_on_prod(client, monkeypatch, path):
    """Баннер и логика согласия — и локально (их можно проверить), а gtag.js — только на боевом домене."""
    monkeypatch.setattr(A, "GA_ID", "G-TEST123")
    for host in ("localhost:8000", "gtd-488744139718.europe-west1.run.app"):
        h = client.get(path, headers={"host": host}).text
        assert DEFAULT in h and ".cc{position:fixed" in h, host
        assert "googletagmanager" not in h, host


@pytest.mark.parametrize("path, lang", PUBLIC.items())
def test_cookie_settings_link_in_every_footer(client, path, lang):
    h = client.get(path).text
    foot = h[h.index("<footer>"):h.index("</footer>")]
    priv = P.PATHS[(lang, "privacy")]
    assert f'<a href="{priv}#cookies" data-cc-open>{"Настройки cookie" if lang == "ru" else "Cookie settings"}</a>' in foot


def test_cookie_settings_link_in_the_app():
    index = open("static/index.html", encoding="utf-8").read()
    ui = json.loads(index.split('<script type="application/json" id="i18n">')[1].split("</script>")[0])
    assert ui["ru"]["cookie_settings"] == "Настройки cookie" and ui["en"]["cookie_settings"] == "Cookie settings"
    assert ui["ru"]["privacy_url"] == "/privacy" and ui["en"]["privacy_url"] == "/en/privacy"
    assert """<a class="ccl" href="${t('privacy_url')}#cookies" data-cc-open>${t('cookie_settings')}</a>""" in index


def test_banner_texts():
    assert set(P.CONSENT["ru"]) == set(P.CONSENT["en"])
    assert not any(re.search("[а-яё«»]", v, re.I) for v in P.CONSENT["en"].values())
    assert (P.CONSENT["ru"]["yes"], P.CONSENT["ru"]["no"]) == ("Принять", "Отклонить")
    assert (P.CONSENT["en"]["yes"], P.CONSENT["en"]["no"]) == ("Accept", "Decline")
    # тексты в скрипте — JSON: и кавычки, и «</» внутри не ломают <script>
    txt = json.loads(re.search(r"TXT = (\{.*?\});\n", P.CONSENT_HEAD).group(1))
    assert txt["en"]["privacy"] == "/en/privacy" and txt["ru"]["privacy"] == "/privacy"
    assert txt["ru"]["text"] == P.CONSENT["ru"]["text"]


@pytest.mark.parametrize("path, words", [
    ("/privacy", ['<h2 id="cookies">Cookie и согласие</h2>', "Google Consent Mode", "без идентификатора браузера",
                  "Принять", "Отклонить", "12 месяцев", "Настройки cookie", "<code>_ga*</code>",
                  "Свои cookie GA ставит, только если ты\nсогласишься"]),
    ("/en/privacy", ['<h2 id="cookies">Cookies and consent</h2>', "Google Consent Mode", "without a browser identifier",
                     "Accept", "Decline", "12 months", "Cookie settings", "<code>_ga*</code>",
                     "GA sets its cookies only if you\nagree"]),
])
def test_privacy_describes_consent(client, path, words):
    h = client.get(path).text
    for w in words:
        assert w in h, w
    assert "GA ставит свои cookie, а" not in h and "GA sets its own cookies, and" not in h  # старое «ставит всегда»
