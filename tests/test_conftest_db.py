"""Уборка тестовых баз (SERBITO-553): старт сессии удаляет только старые базы без соединений."""
import uuid

import psycopg

from conftest import ADMIN_URL, DB, _admin, _drop_orphans


def _exists(name):
    return bool(_admin("select 1 from pg_database where datname=%s", (name,)))


def test_drop_orphans_spares_young_and_connected_dbs():
    # Свой префикс, а не gtd_test_: соседние воркеры и чужие сессии на этом сервере тест не трогает
    tag = f"gtdorphan_{uuid.uuid4().hex[:8]}"
    idle, busy = f"{tag}_idle", f"{tag}_busy"
    like = tag + r"\_%"
    for name in (idle, busy):
        _admin(f'create database "{name}"')
    try:
        _drop_orphans(like=like)  # обе моложе часа
        assert _exists(idle) and _exists(busy)
        with psycopg.connect(f"{ADMIN_URL.rsplit('/', 1)[0]}/{busy}"):
            _drop_orphans(like=like, age_s=0)  # «старые» обе, но к busy есть соединение
            assert not _exists(idle) and _exists(busy)
    finally:
        for name in (idle, busy):
            _admin(f'drop database if exists "{name}" with (force)')
    assert _exists(DB)  # своя база сессии на месте
