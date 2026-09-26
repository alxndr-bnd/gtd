"""Аккаунты: вход по коду на почту, Google, Telegram и привязка способов входа друг к другу."""
import pytest

import app as A
from conftest import bot_callback, bot_message, last_code


def email_start(c, email):
    return c.post("/api/auth/email/start", json={"email": email})


def email_verify(c, email, code, link=False):
    return c.post("/api/auth/email/verify", json={"email": email, "code": code, "link": link})


# ── Почта ──

def test_email_signup_normalizes_address(client, mail, login):
    login(client, "  Alice@Example.COM ")
    assert client.get("/api/me").json() == {"name": "alice", "email": "alice@example.com", "tg": False, "google": False, "undo_seconds": 30, "lang": None, "admin": False}
    assert mail[-1][0] == "alice@example.com" and "Код входа" in mail[-1][1]


def test_email_relogin_same_account(new_client, login):
    assert login(new_client(), "alice@example.com") == login(new_client(), "alice@example.com")
    assert A.row("select count(*) n from users")["n"] == 1


@pytest.mark.parametrize("email", ["not-an-email", "a@b", "x" * 250 + "@example.com", ""])
def test_email_rejects_bad_address(client, mail, email):
    assert email_start(client, email).status_code == 400


def test_email_cooldown(client, mail):
    assert email_start(client, "a@example.com").status_code == 200
    assert email_start(client, "a@example.com").status_code == 429


def test_email_ip_rate_limit(client, mail):
    for i in range(5):
        assert email_start(client, f"u{i}@example.com").status_code == 200
    assert email_start(client, "u5@example.com").status_code == 429


def test_email_five_attempts_then_code_burned(client, mail):
    email_start(client, "a@example.com")
    code = last_code(mail)
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(5):
        assert email_verify(client, "a@example.com", wrong).json()["detail"] == "Неверный код"
    r = email_verify(client, "a@example.com", code)
    assert r.status_code == 400 and "устарел" in r.json()["detail"]


def test_email_code_expires(client, mail):
    email_start(client, "a@example.com")
    A.run("update email_codes set expires=0")
    assert email_verify(client, "a@example.com", last_code(mail)).status_code == 400


def test_email_code_single_use(client, mail):
    email_start(client, "a@example.com")
    code = last_code(mail)
    assert email_verify(client, "a@example.com", code).status_code == 200
    assert email_verify(client, "a@example.com", code).status_code == 400


def test_email_disabled_without_smtp_in_prod(client, monkeypatch):
    monkeypatch.setattr(A, "DEV", False)
    assert email_start(client, "a@example.com").json()["detail"] == "Вход по почте не настроен"


def test_email_dev_logs_code_instead_of_sending(client, caplog):
    assert email_start(client, "a@example.com").status_code == 200
    assert "код входа для a@example.com" in caplog.text


def test_smtp_failure_reported_and_code_dropped(client, monkeypatch):
    monkeypatch.setattr(A, "SMTP_PASSWORD", "x")

    def boom(*a):
        raise OSError("connection refused")
    monkeypatch.setattr(A, "send_email", boom)
    assert email_start(client, "a@example.com").status_code == 502
    assert A.row("select count(*) n from email_codes")["n"] == 0


# ── Google ──

@pytest.fixture
def google(monkeypatch):
    """Токен "good-<sub>" проходит проверку, любой другой — нет."""
    def verify(cred):
        if not cred.startswith("good-"):
            return None
        sub = cred.removeprefix("good-")
        return {"sub": sub, "email": f"{sub}@example.com", "name": sub.title()}
    monkeypatch.setattr(A, "google_verify", verify)


def test_google_signup_and_bad_token(client, google):
    assert client.post("/api/auth/google", json={"credential": "bad"}).status_code == 400
    assert client.post("/api/auth/google", json={"credential": "good-alice"}).status_code == 200
    assert client.get("/api/me").json() == {"name": "Alice", "email": "alice@example.com", "tg": False, "google": True, "undo_seconds": 30, "lang": None, "admin": False}


def test_google_joins_email_account(new_client, login, google):
    uid = login(new_client(), "alice@example.com")
    c = new_client()
    c.post("/api/auth/google", json={"credential": "good-alice"})
    assert A.row("select id, google_sub from users") == {"id": uid, "google_sub": "alice"}


def test_google_not_configured(client, monkeypatch):
    monkeypatch.setattr(A, "GOOGLE_CLIENT_ID", "")
    assert client.post("/api/auth/google", json={"credential": "x"}).status_code == 400


def test_google_verify_checks_claims(monkeypatch):
    class R:
        status_code = 200
        def __init__(self, d): self.d = d
        def json(self): return self.d
    ok = {"aud": "test-client", "iss": "https://accounts.google.com", "email_verified": "true",
          "email": "a@example.com", "sub": "1"}
    for bad in ({"aud": "other"}, {"iss": "evil.com"}, {"email_verified": "false"}, {"email": ""}):
        monkeypatch.setattr(A.httpx, "get", lambda *a, _d={**ok, **bad}, **k: R(_d))
        assert A.google_verify("t") is None, bad
    monkeypatch.setattr(A.httpx, "get", lambda *a, **k: R(ok))
    assert A.google_verify("t")["sub"] == "1"


def test_google_link_sets_email_if_missing(client, tg, google):
    d = client.post("/api/auth/tg/start", json={}).json()
    bot_callback(f"tgok:{d['nonce']}")
    client.get("/api/auth/tg/poll", params={"nonce": d["nonce"]})
    assert client.post("/api/auth/google", json={"credential": "good-alice", "link": True}).json() == {"ok": True}
    me = client.get("/api/me").json()
    assert me["tg"] and me["google"] and me["email"] == "alice@example.com"


# ── Telegram ──

def test_tg_login_requires_confirmation(client, tg):
    d = client.post("/api/auth/tg/start", json={}).json()
    assert d["url"] == f"https://t.me/gtd_test_bot?start={d['nonce']}"
    assert client.get("/api/auth/tg/poll", params={"nonce": d["nonce"]}).json()["status"] == "pending"

    bot_message(f"/start {d['nonce']}")
    kb = tg[-1][1]["reply_markup"]["inline_keyboard"][0][0]
    assert kb["callback_data"] == f"tgok:{d['nonce']}"
    assert A.row("select count(*) n from users")["n"] == 0  # до «Подтвердить» аккаунта нет

    bot_callback(f"tgok:{d['nonce']}")
    r = client.get("/api/auth/tg/poll", params={"nonce": d["nonce"]})
    assert r.json()["status"] == "ok" and "sid" in r.cookies
    assert client.get("/api/me").json()["tg"] is True
    assert client.get("/api/auth/tg/poll", params={"nonce": d["nonce"]}).json()["status"] == "expired"


def test_tg_login_disabled_without_bot(client):
    assert client.post("/api/auth/tg/start", json={}).status_code == 400


def test_tg_expired_link(client, tg):
    d = client.post("/api/auth/tg/start", json={}).json()
    A.run("update tg_logins set expires=0")
    bot_message(f"/start {d['nonce']}")
    assert "устарела" in tg[-1][1]["text"]
    assert client.get("/api/auth/tg/poll", params={"nonce": d["nonce"]}).json()["status"] == "expired"


def test_link_tg_absorbs_empty_account(new_client, login, tg):
    c = new_client()
    uid = login(c)
    tom = A.tg_user(777, "Tom")["id"]  # пустой аккаунт Тома, у него открыта веб-сессия
    A.run("insert into sessions(token,user_id,created) values('tom-sid',%s,0)", (tom,))
    tom_web = new_client()
    tom_web.cookies.set("sid", "tom-sid")

    d = c.post("/api/auth/tg/start", json={"link": True}).json()
    bot_message(f"/start {d['nonce']}")
    assert "привязать" in tg[-1][1]["text"]
    bot_callback(f"tgok:{d['nonce']}")
    assert c.get("/api/auth/tg/poll", params={"nonce": d["nonce"]}).json()["status"] == "ok"
    assert A.row("select tg_id from users where id=%s", (uid,))["tg_id"] == 777
    assert A.row("select id from users where id=%s", (tom,)) is None
    assert tom_web.get("/api/me").status_code == 401  # сессии поглощённого аккаунта погашены


def test_link_email_of_busy_account_offers_merge(new_client, login, mail):
    c, carol = new_client(), new_client()
    alice = login(c, "alice@example.com")
    c.post("/api/capture", json={"text": "моё"})
    login(carol, "carol@example.com")
    carol.post("/api/capture", json={"text": "важное #Работа"})

    email_start(c, "carol@example.com")
    r = email_verify(c, "carol@example.com", last_code(mail), link=True)
    assert r.status_code == 409
    d = r.json()
    assert (d["items"], d["projects"]) == (1, 1) and "Объединить" in d["detail"] and d["merge"]
    assert A.row("select email from users where id=%s", (alice,))["email"] == "alice@example.com"  # пока ничего не тронуто

    assert c.post("/api/auth/merge", json={"token": d["merge"]}).json() == {"ok": True}
    assert A.row("select count(*) n from users")["n"] == 1
    assert A.row("select email from users where id=%s", (alice,))["email"] == "carol@example.com"
    assert sorted(i["title"] for i in c.get("/api/items?status=all").json()) == ["важное", "моё"]
    assert carol.get("/api/me").json()["email"] == "carol@example.com"  # сессия Кэрол теперь в общем аккаунте
    assert c.post("/api/auth/merge", json={"token": d["merge"]}).status_code == 400  # одноразово


def test_merge_confirm_only_by_initiator(new_client, login, mail):
    c, carol = new_client(), new_client()
    login(c, "alice@example.com")
    c.post("/api/capture", json={"text": "моё"})
    login(carol, "carol@example.com")
    carol.post("/api/capture", json={"text": "её"})
    email_start(c, "carol@example.com")
    token = email_verify(c, "carol@example.com", last_code(mail), link=True).json()["merge"]
    assert carol.post("/api/auth/merge", json={"token": token}).status_code == 400
    A.run("update merge_offers set expires=0")
    assert c.post("/api/auth/merge", json={"token": token}).status_code == 400
    assert A.row("select count(*) n from users")["n"] == 2


def test_empty_account_moves_into_existing_one(new_client, login, tg, mail):
    carol = new_client()
    carol_id = login(carol, "carol@example.com")
    carol.post("/api/capture", json={"text": "важное"})
    fresh = new_client()  # пустой аккаунт, вошедший через Telegram — терять ему нечего
    d = fresh.post("/api/auth/tg/start", json={}).json()
    bot_callback(f"tgok:{d['nonce']}", tg_id=555)
    fresh.get("/api/auth/tg/poll", params={"nonce": d["nonce"]})

    email_start(fresh, "carol@example.com")
    assert email_verify(fresh, "carol@example.com", last_code(mail), link=True).json() == {"ok": True}
    me = fresh.get("/api/me").json()  # без вопросов переехали в аккаунт с данными, Telegram — к нему
    assert me["email"] == "carol@example.com" and me["tg"] is True
    assert A.row("select count(*) n from users")["n"] == 1 and A.row("select id from users")["id"] == carol_id


def test_empty_account_with_own_email_is_asked(new_client, login, google, mail):
    carol = new_client()
    login(carol, "carol@example.com")
    carol.post("/api/capture", json={"text": "важное"})
    fresh = new_client()
    fresh.post("/api/auth/google", json={"credential": "good-newbie"})  # у него своя почта newbie@…
    email_start(fresh, "carol@example.com")
    r = email_verify(fresh, "carol@example.com", last_code(mail), link=True)
    assert r.status_code == 409 and r.json()["items"] == 1  # молча терять newbie@ нельзя — спрашиваем


def test_link_tg_of_busy_account_offers_merge_in_browser(new_client, login, tg):
    c = new_client()
    alice = login(c)
    c.post("/api/capture", json={"text": "моё"})
    bot_message("задача из бота", tg_id=888)  # у Telegram 888 свой аккаунт с задачей

    d = c.post("/api/auth/tg/start", json={"link": True}).json()
    bot_callback(f"tgok:{d['nonce']}", tg_id=888)
    assert "подтверди объединение" in tg[-1][1]["text"]
    r = c.get("/api/auth/tg/poll", params={"nonce": d["nonce"]}).json()
    assert r["status"] == "merge" and r["items"] == 1
    assert c.post("/api/auth/merge", json={"token": r["merge"]}).json() == {"ok": True}
    assert A.row("select tg_id from users where id=%s", (alice,))["tg_id"] == 888
    assert len(c.get("/api/items?status=all").json()) == 2


def test_merge_glues_projects_by_name(new_client, login, mail):
    c, carol = new_client(), new_client()
    login(c, "alice@example.com")
    c.post("/api/capture", json={"text": "a #Ремонт"})
    login(carol, "carol@example.com")
    carol.post("/api/capture", json={"text": "b #ремонт"})
    carol.post("/api/capture", json={"text": "c #Отпуск"})
    email_start(c, "carol@example.com")
    c.post("/api/auth/merge", json={"token": email_verify(c, "carol@example.com", last_code(mail), link=True).json()["merge"]})
    projects = {p["title"]: p["open_count"] for p in c.get("/api/projects").json()}
    assert projects == {"Ремонт": 2, "Отпуск": 1}


def test_link_email_replaces_own_email(client, login, mail):
    uid = login(client, "old@example.com")
    email_start(client, "new@example.com")
    assert email_verify(client, "new@example.com", last_code(mail), link=True).json() == {"ok": True}
    assert A.row("select email from users where id=%s", (uid,))["email"] == "new@example.com"
