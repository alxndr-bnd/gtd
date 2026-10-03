"""SERBITO-363: будильник /tasks/reminders принимает OIDC ID-токен Google от scheduler-invoker.
Токены подписаны одноразовым RSA-ключом и проверяются настоящим verify_oauth2_token; подменена только
загрузка публичных ключей Google."""
import datetime
import json
import time

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from google.auth import crypt, jwt

import app as A

EMAIL = "scheduler-invoker@serbito.iam.gserviceaccount.com"
AUD = "https://gtd.serbito.rs"
KID = "test-kid"


def key_and_cert():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(1).not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=1)).sign(key, hashes.SHA256()))
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption())
    return pem, cert.public_bytes(serialization.Encoding.PEM).decode()


KEY, CERT = key_and_cert()


class Resp:
    def __init__(self, data):
        self.status, self.headers, self.data = 200, {}, data


class FakeGoogle:
    """Вместо google.auth.transport.requests.Request: отдаёт наш сертификат и считает запросы."""
    def __init__(self):
        self.calls = 0

    def __call__(self, url, method="GET", body=None, headers=None, **kw):
        self.calls += 1
        return Resp(json.dumps({KID: CERT}).encode())


@pytest.fixture
def google(monkeypatch):
    fake = FakeGoogle()
    monkeypatch.setattr(A, "CRON_CERTS", A.CachedCerts(fake))
    monkeypatch.setattr(A, "CRON_OIDC_EMAIL", EMAIL)
    monkeypatch.setattr(A, "CRON_OIDC_AUDIENCES", {AUD, "https://gtd-aay5lcpxha-ew.a.run.app"})
    monkeypatch.setattr(A, "CRON_SECRET", "s3cret")
    monkeypatch.setattr(A, "CRON_SECRET_ENABLED", True)
    return fake


def token(key=KEY, **over):
    now = int(time.time())
    claims = {"iss": "https://accounts.google.com", "aud": AUD, "email": EMAIL, "email_verified": True,
              "sub": "1234567890", "iat": now, "exp": now + 3600, **over}
    return jwt.encode(crypt.RSASigner.from_string(key, key_id=KID), claims).decode()


def post(client, t=None, secret=None):
    headers = {}
    if t:
        headers["Authorization"] = f"Bearer {t}"
    if secret:
        headers["X-Cron-Secret"] = secret
    return client.post("/tasks/reminders", headers=headers)


@pytest.mark.parametrize("aud", [AUD, AUD + "/", "https://gtd-aay5lcpxha-ew.a.run.app"])
def test_valid_oidc_token(client, google, aud):
    r = post(client, token(aud=aud))
    assert r.status_code == 200 and r.json() == {"sent": 0}


@pytest.mark.parametrize("over", [
    {"email": "someone@serbito.iam.gserviceaccount.com"},
    {"email_verified": False},
    {"aud": "https://serbito.rs"},
    {"aud": AUD + "/tasks/reminders"},
    {"iat": int(time.time()) - 7200, "exp": int(time.time()) - 3600},
    {"iss": "https://evil.example"},
], ids=["wrong-email", "email-unverified", "wrong-audience", "full-url-audience", "expired", "wrong-issuer"])
def test_bad_oidc_token(client, google, over):
    assert post(client, token(**over)).status_code == 403


def test_foreign_signature(client, google):
    assert post(client, token(key=key_and_cert()[0])).status_code == 403


def test_static_secret_still_works(client, google):
    assert post(client, secret="s3cret").status_code == 200
    assert google.calls == 0  # старый путь ключи Google не качает


def test_static_secret_can_be_switched_off(client, google, monkeypatch):
    monkeypatch.setattr(A, "CRON_SECRET_ENABLED", False)
    assert post(client, secret="s3cret").status_code == 403
    assert post(client, token()).status_code == 200


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer "}, {"Authorization": "Basic abc"},
                                     {"Authorization": "Bearer s3cret"}, {"X-Cron-Secret": "wrong"}])
def test_missing_or_wrong_credentials(client, google, headers):
    assert client.post("/tasks/reminders", headers=headers).status_code == 403


def test_oidc_off_without_config(client, google, monkeypatch):
    monkeypatch.setattr(A, "CRON_OIDC_AUDIENCES", set())
    assert post(client, token()).status_code == 403


def test_google_certs_cached(client, google):
    for _ in range(5):
        assert post(client, token()).status_code == 200
    assert google.calls == 1


def test_signature_stripped_by_cloud_run(client, google):
    head, payload, _ = token().split(".")
    assert post(client, f"{head}.{payload}.SIGNATURE_REMOVED_BY_GOOGLE").status_code == 403
