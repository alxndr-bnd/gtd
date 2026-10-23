"""Ссылки на Google-документы в заголовке (gtd #85): название из Google Picker подписывает ссылку на сервере."""
import app as A

SHEET = "https://docs.google.com/spreadsheets/d/1sOIVa-tx1TTXkBW9eYAKrTYoe6SfBM05qGNsPN5Nk1Y/edit?gid=1#gid=1&range=I29"


def test_config_picker(client, monkeypatch):
    assert client.get("/api/config").json()["picker"] is None  # ключей нет — кнопки «Показать название» нет
    monkeypatch.setattr(A, "GOOGLE_API_KEY", "key")
    assert client.get("/api/config").json()["picker"] is None  # без номера проекта Picker не работает
    monkeypatch.setattr(A, "GOOGLE_APP_ID", "123")
    assert client.get("/api/config").json()["picker"] == {"key": "key", "app": "123"}
    monkeypatch.setattr(A, "GOOGLE_CLIENT_ID", "")
    assert client.get("/api/config").json()["picker"] is None  # токен drive.file выдаёт тот же OAuth-клиент


def test_link_in_title_survives_capture(client, login):
    """#gid= и @ внутри ссылки — не проект и не контекст: токены разбираются только после пробела."""
    login(client)
    it = client.post("/api/capture", json={"text": f"С Лешей доработать OKR {SHEET}"}).json()
    assert it["title"] == f"С Лешей доработать OKR {SHEET}" and it["project_id"] is None and not it["contexts"]


def test_link_name(client, login):
    login(client)
    iid = client.post("/api/capture", json={"text": f"Обсудить {SHEET} и {SHEET}"}).json()["id"]
    # «#2026» и «завтра» в названии документа — не проект и не напоминание: заголовок не разбирается
    it = client.post(f"/api/items/{iid}/link-name", json={"url": SHEET, "name": " OKR\n#2026 [план] завтра "}).json()
    assert it["title"] == f"Обсудить [OKR #2026 (план) завтра]({SHEET}) и {SHEET}"
    assert it["project_id"] is None and it["remind_at"] is None
    # Вторая такая же ссылка — следующий вызов; подписанную второй раз не трогаем
    it = client.post(f"/api/items/{iid}/link-name", json={"url": SHEET, "name": "OKR"}).json()
    assert it["title"] == f"Обсудить [OKR #2026 (план) завтра]({SHEET}) и [OKR]({SHEET})"
    assert client.post(f"/api/items/{iid}/link-name", json={"url": SHEET, "name": "OKR"}).status_code == 404


def test_link_name_rejects(client, login):
    login(client)
    iid = client.post("/api/capture", json={"text": f"задача {SHEET} https://example.com/x"}).json()["id"]
    bad = [{"url": "https://example.com/x", "name": "x"}, {"url": SHEET, "name": "   "}, {"url": SHEET},
           {"url": SHEET + " x", "name": "x"}, {"url": "javascript:alert(1)", "name": "x"}]
    for body in bad:
        assert client.post(f"/api/items/{iid}/link-name", json=body).status_code == 400, body
    other = "https://docs.google.com/document/d/1AbCdEfGhIjKlMnOpQrStUvWxYz/edit"
    assert client.post(f"/api/items/{iid}/link-name", json={"url": other, "name": "x"}).status_code == 404


def test_link_name_other_user(client, login):
    login(client)
    iid = client.post("/api/capture", json={"text": f"моя {SHEET}"}).json()["id"]
    client.cookies.clear()
    login(client, "bob@example.com")
    assert client.post(f"/api/items/{iid}/link-name", json={"url": SHEET, "name": "x"}).status_code == 404
    assert A.row("select title from items where id=%s", (iid,))["title"] == f"моя {SHEET}"


# ── Название публичного документа без входа (gtd #85): og:title страницы ──
import contextlib  # noqa: E402

import httpx  # noqa: E402

PUBLIC = "https://docs.google.com/spreadsheets/d/1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms/edit"
OG = b'<html><head><meta property="og:title" content="Example &amp; [Spreadsheet]"><title>x</title>'


def fake_google(monkeypatch, status=200, body=OG, error=None):
    """httpx.stream вместо Google: ответ со статусом и телом; запросы — в calls."""
    calls = []

    class Resp:
        status_code = status

        def iter_bytes(self):
            yield body

    @contextlib.contextmanager
    def stream(method, url, **kw):
        calls.append((url, kw))
        if error:
            raise error
        yield Resp()
    monkeypatch.setattr(A.httpx, "stream", stream)
    monkeypatch.setattr(A, "fetch_doc_title", REAL_FETCH)
    A._doc_titles.clear()
    return calls


REAL_FETCH = A.fetch_doc_title


def test_fetch_doc_title(monkeypatch):
    calls = fake_google(monkeypatch)
    assert A.fetch_doc_title(PUBLIC) == "Example & (Spreadsheet)"
    assert A.fetch_doc_title(PUBLIC) == "Example & (Spreadsheet)" and len(calls) == 1  # из памяти
    assert calls[0][1]["follow_redirects"] is False and calls[0][1]["timeout"] == 2
    # Только Google и только https: другой адрес сервер не запрашивает (SSRF)
    for url in ("https://example.com/x", "http://docs.google.com/d/x", "https://docs.google.com.evil.com/x",
                "https://169.254.169.254/latest"):
        assert A.fetch_doc_title(url) is None
    assert len(calls) == 1


def test_fetch_doc_title_private(monkeypatch):
    for status in (401, 302, 404):
        fake_google(monkeypatch, status=status)
        assert A.fetch_doc_title(PUBLIC) is None, status
    fake_google(monkeypatch, body=b"<html>no og</html>")
    assert A.fetch_doc_title(PUBLIC) is None
    calls = fake_google(monkeypatch, error=httpx.ConnectTimeout("slow"))
    assert A.fetch_doc_title(PUBLIC) is None and A.fetch_doc_title(PUBLIC) is None
    assert len(calls) == 2  # сбой сети не запоминается


def test_capture_names_public_links(client, login, monkeypatch):
    login(client)
    names = {PUBLIC: "OKR #Q4 завтра"}
    monkeypatch.setattr(A, "fetch_doc_title", names.get)
    it = client.post("/api/capture", json={"text": f"Смотри {PUBLIC}. И {SHEET} @дом"}).json()
    # Название с «#Q4» и «завтра» — не проект и не напоминание; приватная ссылка осталась голой
    assert it["title"] == f"Смотри [OKR #Q4 завтра]({PUBLIC}). И {SHEET}"
    assert it["contexts"] == ["дом"] and it["project_id"] is None and it["remind_at"] is None
    # Правка заголовка в карточке: готовая ссылка не разбирается, новая голая — получает название
    names[SHEET] = "План"
    it = client.patch(f"/api/items/{it['id']}", json={"title": it["title"] + " ещё"}).json()
    assert it["title"] == f"Смотри [OKR #Q4 завтра]({PUBLIC}). И [План]({SHEET}) ещё"
    assert it["project_id"] is None and it["remind_at"] is None


def test_link_title_endpoint(client, login, monkeypatch):
    assert client.post("/api/link-title", json={"url": PUBLIC}).status_code == 401
    login(client)
    monkeypatch.setattr(A, "fetch_doc_title", {PUBLIC: "Example"}.get)
    assert client.post("/api/link-title", json={"url": PUBLIC}).json() == {"name": "Example"}
    assert client.post("/api/link-title", json={"url": SHEET}).json() == {"name": None}
    assert client.post("/api/link-title", json={"url": "https://example.com"}).status_code == 400
