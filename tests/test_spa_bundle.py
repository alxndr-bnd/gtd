"""SERBITO-444: словарь и код SPA — не в каждом HTML, а в /app.<хеш>.js. Гость получает лендинг без ~120 КБ кода,
который ему нужен только для входа; повторный визит берёт код из кэша браузера."""
import json
import re
import shutil

import app as A

BUNDLE = re.compile(r'<script defer src="(/app\.([0-9a-f]{12})\.js)"></script>')


def test_landing_html_is_light_and_has_one_h1(client):
    for path in ("/", "/en/"):
        h = client.get(path).text
        assert len(h.encode()) < 45_000, len(h.encode())  # было ~160 КБ
        assert "function loginScreen" not in h and 'id="i18n"' not in h
        assert h.count("<h1") == 1  # лишние <h1> были в шаблонах кода приложения
        assert BUNDLE.search(h)


def test_bundle_is_cached_forever_and_holds_code_and_dictionary(client):
    src, digest = BUNDLE.search(client.get("/").text).groups()
    r = client.get(src)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/javascript")
    assert r.headers["cache-control"] == "public, max-age=31536000, immutable"
    head, code = r.text.split(";\n", 1)
    i18n = json.loads(head.removeprefix("window.GTD_I18N = "))
    assert set(i18n) == {"ru", "en"} and "function loginScreen" in code
    assert "window.GTD_I18N ||" in code  # код берёт словарь из бандла, а не со страницы


def test_signed_in_pages_use_the_same_bundle(client, login):
    guest = BUNDLE.search(client.get("/").text).group(1)
    login(client)
    for path in ("/", "/next", "/en/projects"):
        assert BUNDLE.search(client.get(path).text).group(1) == guest


def test_old_hash_gets_current_code_without_cache(client):
    """Во время выкладки страница прошлой ревизии может попросить прошлый бандл: рабочий код лучше 404."""
    current = client.get(BUNDLE.search(client.get("/").text).group(1)).text
    r = client.get("/app.000000000000.js")
    assert r.status_code == 200 and r.text == current and r.headers["cache-control"] == "no-store"


def test_hash_follows_index_html(client, tmp_path, monkeypatch):
    shutil.copy(f"{A.STATIC}/index.html", tmp_path / "index.html")
    monkeypatch.setattr(A, "STATIC", str(tmp_path))
    monkeypatch.setattr(A, "_shell", {})
    before = A.spa_shell()[2]
    page = (tmp_path / "index.html").read_text(encoding="utf-8")
    (tmp_path / "index.html").write_text(page.replace("const LANG_NAMES", "const X = 1;\nconst LANG_NAMES"),
                                         encoding="utf-8")
    after = A.spa_shell()[2]
    assert before != after and "const X = 1;" in A.spa_shell()[1]
