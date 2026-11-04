"""P-09 / P-11 — Test Storage (used_codes atomic, counters bền, migration) và Counters."""
from __future__ import annotations

import threading

import pytest

from sorter.counters import CNT_A, CNT_REJECT_SECURITY, CNT_TOTAL, COUNTER_NAMES, Counters
from sorter.decision import REJECT_SECURITY, SORT_A
from sorter.interfaces import SecurityEvent, SecurityLevel
from sorter.storage import SCHEMA_VERSION, Storage


@pytest.fixture
def db(tmp_path):
    s = Storage(tmp_path / "sub" / "sorter.db")  # thư mục cha được tạo
    yield s
    s.close()


def test_schema_and_tables(db):
    assert db.schema_version == SCHEMA_VERSION
    names = {r["name"] for r in db._conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"packages", "used_codes", "rpc_audit", "security_events", "system_events", "counters", "schema_version"} <= names


def test_package_and_counters_atomic_idempotent(db):
    rec = {"record_uid": "one", "decision": SORT_A}
    assert db.save_package_and_counters(rec, CNT_A)[CNT_TOTAL] == 1
    assert db.save_package_and_counters(rec, CNT_A)[CNT_TOTAL] == 1
    assert db.count_packages() == 1
    with pytest.raises(RuntimeError):
        with db.transaction():
            db.insert_package({"record_uid": "rolled-back", "decision": SORT_A})
            raise RuntimeError("interrupted")
    assert db.count_packages() == 1


def test_local_journal_survives_failure_and_replays(db, tmp_path, monkeypatch):
    from sorter.database import open_database
    db._outbox = open_database(tmp_path / "pending.db")
    db._outbox.execute("CREATE TABLE pending_packages(record_uid TEXT PRIMARY KEY, payload TEXT NOT NULL, counter_name TEXT NOT NULL)")
    original = db._save_package_transaction
    def offline(*args):
        raise ConnectionError("network interrupted")
    monkeypatch.setattr(db, "_save_package_transaction", offline)
    with pytest.raises(ConnectionError):
        db.save_package_and_counters({"record_uid": "pending", "decision": SORT_A}, CNT_A)
    assert db._outbox.execute("SELECT COUNT(*) FROM pending_packages").fetchone()[0] == 1
    monkeypatch.setattr(db, "_save_package_transaction", original)
    assert db.flush_pending_packages() == 1
    assert db.get_counter(CNT_TOTAL) == 1
    assert db.count_packages() == 1
    assert db.flush_pending_packages() == 0


def test_reopen_keeps_data(tmp_path):
    p = tmp_path / "x.db"
    s = Storage(p)
    s.set_counter("cnt_a", 7)
    assert s.try_insert_used_code("PKG000001", 1)
    s.close()
    s2 = Storage(p)
    assert s2.get_counter("cnt_a") == 7
    assert s2.is_code_used("PKG000001")
    assert s2.schema_version == SCHEMA_VERSION
    s2.close()


def test_used_codes_atomic_threads(db):
    wins = []
    lock = threading.Lock()

    def worker():
        if db.try_insert_used_code("PKG000042", 1):
            with lock:
                wins.append(1)

    ts = [threading.Thread(target=worker) for _ in range(16)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert len(wins) == 1
    assert db.count_used_codes() == 1
    assert db.clear_used_codes() == 1
    assert db.try_insert_used_code("PKG000042", 2)


def test_packages_insert_and_query(db):
    pid = db.insert_package({"pkg_seq": 1, "pkg_id": "PKG000001", "decision": SORT_A, "ai_types_json": {"tear": 0.9}, "latency_ms": 123.4})
    assert pid >= 1
    db.insert_package({"pkg_seq": 2, "decision": REJECT_SECURITY, "qr_status": "BAD_SIGNATURE"})
    recent = db.recent_packages(10)
    assert len(recent) == 2 and recent[0]["pkg_seq"] == 2
    assert db.count_packages_by_decision() == {SORT_A: 1, REJECT_SECURITY: 1}
    assert db.recent_packages(1)[0]["qr_status"] == "BAD_SIGNATURE"


def test_other_tables(db):
    db.insert_rpc_audit("r1", "start", {"speed": 60}, "admin", {"ok": True}, True)
    db.insert_security_event(SecurityEvent("QR_BAD_SIGNATURE", SecurityLevel.MAJOR, "x", extra={"pkg": "PKG000001"}))
    db.insert_system_event("STARTUP", "sim")
    assert db.recent_rpc_audit(1)[0]["method"] == "start"
    assert db.recent_security_events(1)[0]["level"] == "MAJOR"
    assert db.recent_system_events(1)[0]["kind"] == "STARTUP"


def test_counters_persist_and_reset(tmp_path):
    p = tmp_path / "c.db"
    s = Storage(p)
    c = Counters(s)
    assert c.to_telemetry() == {n: 0 for n in COUNTER_NAMES}
    c.record(SORT_A)
    c.record(SORT_A)
    c.record(REJECT_SECURITY)
    c.record("WEIRD")  # mã lạ -> failsafe
    v = c.to_telemetry()
    assert v[CNT_TOTAL] == 4 and v[CNT_A] == 2 and v[CNT_REJECT_SECURITY] == 1 and v["cnt_reject_failsafe"] == 1
    s.close()
    s2 = Storage(p)
    c2 = Counters(s2)
    assert c2.to_telemetry() == v
    c2.reset()
    assert all(x == 0 for x in c2.to_telemetry().values())
    s2.close()
    s3 = Storage(p)
    assert Counters(s3).get(CNT_TOTAL) == 0
    s3.close()
