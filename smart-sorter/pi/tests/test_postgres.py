"""Run against Supabase with SORTER_TEST_DATABASE_URL; all test rows roll back."""
import os
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

from sorter.storage import Storage
from sorter.anomaly.store import AnomalyStore


@pytest.fixture
def pg():
    url = os.environ.get("SORTER_TEST_DATABASE_URL")
    if not url:
        pytest.skip("SORTER_TEST_DATABASE_URL is not configured")
    db = Storage(url)
    yield db
    db.close()


def test_pg_package_atomic_idempotent_rollback(pg):
    before = pg.count_packages()
    counters = pg.all_counters()
    rec = {"record_uid": str(uuid.uuid4()), "pkg_id": "TEST-ROLLBACK", "decision": "SORT_A", "sort_sent": True}
    with pytest.raises(RuntimeError, match="rollback"):
        with pg.transaction():
            values = pg.save_package_and_counters(rec, "cnt_a")
            assert values["cnt_total"] == counters.get("cnt_total", 0) + 1
            assert pg.save_package_and_counters(rec, "cnt_a") == values
            assert pg.count_packages() == before + 1
            assert pg.recent_packages(1)[0]["sort_sent"] == 1
            raise RuntimeError("rollback")
    assert pg.count_packages() == before
    assert pg.all_counters() == counters


def test_pg_all_audit_tables(pg):
    from sorter.interfaces import SecurityEvent, SecurityLevel
    with pytest.raises(RuntimeError, match="rollback"):
        with pg.transaction():
            pg.insert_rpc_audit("test", "test", {"speed": 60}, "test", {"ok": True}, True)
            pg.insert_security_event(SecurityEvent("TEST", SecurityLevel.MAJOR, "rollback"))
            pg.insert_system_event("TEST", "rollback")
            assert pg.recent_rpc_audit(1)[0]["method"] == "test"
            assert pg.recent_security_events(1)[0]["event_type"] == "TEST"
            assert pg.recent_system_events(1)[0]["kind"] == "TEST"
            raise RuntimeError("rollback")


def test_pg_replay_race_separate_connections(pg):
    code = "TEST-" + str(uuid.uuid4())
    def insert(_):
        with Storage(pg.db_path) as other:
            return other.try_insert_used_code(code)
    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            assert sum(pool.map(insert, range(4))) == 1
    finally:
        pg._conn.execute("DELETE FROM used_codes WHERE pkg_id=?", (code,))


def test_pg_anomaly_storage(pg):
    store = AnomalyStore(pg.db_path)
    try:
        with pytest.raises(RuntimeError, match="rollback"):
            with store._conn.raw.transaction():
                ident = store.insert_window(1780000000000, {}, score=0.25, flag=False)
                store.update_score(ident, 0.75, True, "qr")
                rows = store.fetch(1780000000000, 1780000000000)
                assert any(row["id"] == ident and row["flag"] == 1 for row in rows)
                raise RuntimeError("rollback")
    finally:
        store.close()
