"""Код на почту нельзя подобрать (SERBITO-346): лимит попыток на код и на адрес за сутки, атомарные
счётчики против параллельных догадок, лимиты писем, адрес клиента для лимитов — не из подделываемого XFF."""
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

import app as A
from conftest import email_start, email_verify, last_code

EN = {"Accept-Language": "en"}
CF_EDGE = "104.16.0.5"


def wrong(code):
    return "000000" if code != "000000" else "111111"


def new_code(email, mail):
    """Свежий код на адрес в обход минутного кулдауна и лимитов писем (но не бюджета неверных кодов)."""
    A.run("update email_codes set sent=0")
    A.run("delete from auth_limits where key not like 'fail:%%'")
    A.send_code(email, "ip:test")
    return last_code(mail)


def fails(email):
    return A.limit_count("fail:" + email, A.DAY)


# ── Бюджет неверных кодов ──

def test_resend_does_not_reset_email_budget(client, mail):
    email = "victim@example.com"
    for i in range(A.EMAIL_FAILS // A.CODE_TRIES):
        code = new_code(email, mail)
        for j in range(A.CODE_TRIES):
            r = email_verify(client, email, wrong(code))
            last = i == A.EMAIL_FAILS // A.CODE_TRIES - 1 and j == A.CODE_TRIES - 1
            assert r.status_code == (429 if last else 400)
        A.run("delete from auth_limits where key like 'guess:%%'")
    assert fails(email) == A.EMAIL_FAILS
    assert "закрыт на сутки" in r.json()["detail"]
    # новый код не отправляется, а уже выданный — не принимается даже верный
    A.run("update email_codes set sent=0")
    r = email_start(client, email)
    assert r.status_code == 429 and "закрыт на сутки" in r.json()["detail"]
    assert A.row("select count(*) n from email_codes")["n"] == 0
    A.run("insert into email_codes(email,code_hash,expires,attempts,sent) values(%s,%s,%s,0,0)",
          (email, A.code_hash(email, "123456"), 2 ** 40))
    r = email_verify(client, email, "123456")
    assert r.status_code == 429 and "закрыт на сутки" in r.json()["detail"]
    assert client.get("/api/me").status_code == 401


def test_budget_resets_after_a_day(client, mail):
    email = "a@example.com"
    A.run("insert into auth_limits(key,n,since) values(%s,%s,%s)",
          ("fail:" + email, A.EMAIL_FAILS, A.time.time() - A.DAY - 1))
    assert email_start(client, email).status_code == 200
    assert email_verify(client, email, last_code(mail)).status_code == 200


def test_lockout_message_ru_en_and_bot(client, mail, tg):
    email = "a@example.com"
    A.run("insert into auth_limits(key,n,since) values(%s,%s,%s)", ("fail:" + email, A.EMAIL_FAILS, A.time.time()))
    ru, en = (email_start(client, email, headers=h).json()["detail"] for h in ({}, EN))
    assert ru == A.TEXTS["ru"]["code_locked"] and en == A.TEXTS["en"]["code_locked"]
    assert en.startswith("Too many wrong codes") and "24 hours" in en
    assert email_verify(client, email, "123456", headers=EN).json()["detail"] == A.TEXTS["en"]["code_locked"]
    from conftest import bot_message
    bot_message(f"/email {email}")
    assert tg[-1][1]["text"] == A.TEXTS["ru"]["code_locked"]


def test_correct_code_is_not_a_failure(client, mail):
    email = "a@example.com"
    email_start(client, email)
    code = last_code(mail)
    email_verify(client, email, wrong(code))
    assert email_verify(client, email, code).status_code == 200
    assert fails(email) == 1


# ── Параллельные догадки ──

def parallel(n, fn):
    with ThreadPoolExecutor(max_workers=n) as ex:
        return list(ex.map(fn, range(n)))


def guess(email, code):
    def go(i):
        try:
            A.check_code(email, code, f"ip:10.0.0.{i}")
            return "ok"
        except A.AuthError as e:
            return e.key
    return go


def counting_compare(monkeypatch):
    """Сколько раз код действительно сравнили с присланным."""
    calls = []
    real = A.code_hash
    monkeypatch.setattr(A, "code_hash", lambda e, c: calls.append(c) or real(e, c))
    return calls


def test_parallel_guesses_cannot_exceed_tries_per_code(mail, monkeypatch):
    email = "a@example.com"
    code = new_code(email, mail)
    compared = counting_compare(monkeypatch)
    res = parallel(25, guess(email, wrong(code)))
    assert res.count("code_wrong") == A.CODE_TRIES == len(compared)
    assert set(res) == {"code_wrong", "code_expired"}
    assert A.row("select attempts from email_codes")["attempts"] == A.CODE_TRIES
    assert fails(email) == A.CODE_TRIES


def test_parallel_guesses_cannot_exceed_email_budget(mail, monkeypatch):
    email = "a@example.com"
    code = new_code(email, mail)
    A.run("insert into auth_limits(key,n,since) values(%s,%s,%s)",
          ("fail:" + email, A.EMAIL_FAILS - 2, A.time.time()))
    compared = counting_compare(monkeypatch)
    res = parallel(20, guess(email, wrong(code)))
    assert len(compared) == 2  # оставалось 2 неверных попытки — сравнений было ровно 2
    assert res.count("code_wrong") + res.count("code_locked") >= 2 and "ok" not in res
    assert A.row("select count(*) n from email_codes")["n"] == 0  # на последней код сгорел


def test_parallel_correct_code_logs_in_once(mail):
    email = "a@example.com"
    code = new_code(email, mail)
    res = parallel(10, guess(email, code))
    assert res.count("ok") == 1


# ── Лимиты от одного отправителя и на адрес ──

def test_guess_limit_per_ip(client, mail):
    limit, _ = A.SENDER_GUESSES
    for i in range(limit):
        assert email_verify(client, f"u{i}@example.com", "123456").status_code == 400
    r = email_verify(client, "other@example.com", "123456")
    assert r.status_code == 429 and r.json()["detail"] == A.TEXTS["ru"]["too_many"]


def test_email_send_cap_per_day(mail):
    email = "a@example.com"
    for i in range(A.EMAIL_SENDS):
        A.run("update email_codes set sent=0")
        A.send_code(email, f"ip:10.0.0.{i}")
    A.run("update email_codes set sent=0")
    with pytest.raises(A.AuthError) as e:
        A.send_code(email, "ip:10.0.1.1")
    assert e.value.key == "too_many_day" and len(mail) == A.EMAIL_SENDS


def test_ip_send_cap_per_day(client, mail):
    limit = A.SENDER_SENDS[1][0]
    A.run("insert into auth_limits(key,n,since) values('send-day:ip:testclient',%s,%s)", (limit, A.time.time()))
    r = email_start(client, "a@example.com", headers=EN)
    assert r.status_code == 429 and r.json()["detail"] == A.TEXTS["en"]["too_many_day"]


# ── Адрес клиента ──

def req(peer, xff=None, cf=None):
    headers = [(b"x-forwarded-for", x.encode()) for x in ([xff] if isinstance(xff, str) else xff or [])]
    if cf:
        headers.append((b"cf-connecting-ip", cf.encode()))
    return Request({"type": "http", "method": "GET", "path": "/", "headers": headers, "client": (peer, 1234)})


CLOUD_RUN = "169.254.169.126"


@pytest.mark.parametrize("xff", ["203.0.113.7", "6.6.6.6, 203.0.113.7", "1.1.1.1,2.2.2.2, 203.0.113.7",
                                 "garbage, 203.0.113.7", ["6.6.6.6", "203.0.113.7"]])
def test_cloud_run_rightmost_hop_is_client(xff):
    assert A.client_ip(req(CLOUD_RUN, xff)) == "203.0.113.7"


def test_load_balancer_hops_are_skipped(monkeypatch):
    assert A.client_ip(req(CLOUD_RUN, "6.6.6.6, 203.0.113.7, 35.191.10.20")) == "203.0.113.7"
    # адрес внешнего балансировщика — публичный: его хоп пропускается, только если он в TRUSTED_PROXIES
    assert A.client_ip(req(CLOUD_RUN, "6.6.6.6, 203.0.113.7, 34.120.1.2")) == "34.120.1.2"
    monkeypatch.setattr(A, "TRUSTED_PROXIES", A._nets(["34.120.1.2"]))
    assert A.client_ip(req(CLOUD_RUN, "6.6.6.6, 203.0.113.7, 34.120.1.2")) == "203.0.113.7"


def test_xff_ignored_from_untrusted_peer():
    assert A.client_ip(req("8.8.4.4", "6.6.6.6")) == "8.8.4.4"  # uvicorn без прокси, открытый наружу
    assert A.client_ip(req("testclient", "6.6.6.6")) == "testclient"
    assert A.client_ip(req("172.18.0.3")) == "172.18.0.3"  # docker без XFF
    assert A.client_ip(req("172.18.0.3", "192.168.1.20")) == "192.168.1.20"  # клиент в локальной сети


def test_cf_connecting_ip_only_via_cloudflare():
    assert A.client_ip(req(CLOUD_RUN, f"6.6.6.6, {CF_EDGE}", cf="203.0.113.7")) == "203.0.113.7"
    assert A.client_ip(req(CLOUD_RUN, "198.51.100.1", cf="203.0.113.7")) == "198.51.100.1"  # не через Cloudflare
    assert A.client_ip(req(CLOUD_RUN, CF_EDGE, cf="junk")) == CF_EDGE


def test_ipv6_key_is_slash_64():
    assert A.ip_key("2001:db8:1:2:aaaa::1") == A.ip_key("2001:db8:1:2:bbbb::9") == "2001:db8:1:2::/64"
    assert A.ip_key("203.0.113.7") == "203.0.113.7"


def test_spoofed_leftmost_xff_does_not_change_rate_limit_key(mail):
    c = TestClient(A.app, client=(CLOUD_RUN, 1234))
    limit, _ = A.SENDER_SENDS[0]
    for i in range(limit):
        r = email_start(c, f"u{i}@example.com", headers={"X-Forwarded-For": f"10.9.9.{i}, 203.0.113.7"})
        assert r.status_code == 200
    r = email_start(c, "x@example.com", headers={"X-Forwarded-For": "1.2.3.4, 203.0.113.7"})
    assert r.status_code == 429
    # другой настоящий клиент — свой бюджет
    assert email_start(c, "y@example.com", headers={"X-Forwarded-For": "1.2.3.4, 203.0.113.8"}).status_code == 200
