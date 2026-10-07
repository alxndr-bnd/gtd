"""Пул соединений с базой: запрос после обрыва всех соединений (рестарт Cloud SQL, сбой сети) — SERBITO-561."""
import time

import app as A
from conftest import kill_pool_connections


def test_request_after_all_pooled_connections_die_is_fast(client, monkeypatch):
    """Все пять соединений пула мертвы. Раньше пул проверял их по одному с паузой 1+2+4+8 с — запрос ждал ~15 с.
    Теперь первое мёртвое соединение запускает проверку всего пула: ответ быстрее 3 с."""
    monkeypatch.setattr(A, "CRON_SECRET", "s3cret")
    assert client.post("/tasks/reminders", headers={"X-Cron-Secret": "s3cret"}).json() == {"sent": 0}
    lost = A._pool.get_stats().get("connections_lost", 0)
    assert kill_pool_connections() == A._pool.max_size
    t = time.monotonic()
    r = client.post("/tasks/reminders", headers={"X-Cron-Secret": "s3cret"})
    elapsed = time.monotonic() - t
    print(f"\nrequest after {A._pool.max_size} dead pooled connections: {elapsed:.3f} s")
    assert r.status_code == 200 and r.json() == {"sent": 0}
    assert A._pool.get_stats().get("connections_lost", 0) > lost  # мёртвые соединения были, пул их заменил
    assert elapsed < 3
