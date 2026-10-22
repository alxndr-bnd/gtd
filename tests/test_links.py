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
