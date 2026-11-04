"""P-09 — Lưu trữ SQLite (WAL, thread-safe) cho sorter-core.

Bảng: packages, used_codes, rpc_audit, security_events, system_events, counters, schema_version.
Migration đơn giản theo schema_version (MIGRATIONS[n] = câu lệnh nâng từ n-1 lên n).
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterator
from contextlib import contextmanager

from .interfaces import SecurityEvent

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1

MIGRATIONS: dict[int, list[str]] = {
    1: [
        """CREATE TABLE IF NOT EXISTS packages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts_ms INTEGER NOT NULL,
            pkg_seq INTEGER,
            pkg_id TEXT,
            warehouse TEXT,
            qr_status TEXT,
            ai_label TEXT,
            ai_conf REAL,
            ai_types_json TEXT,
            decision TEXT NOT NULL,
            reason TEXT,
            target TEXT,
            t_capture_ms REAL,
            t_qr_ms REAL,
            t_verify_ms REAL,
            t_ai_ms REAL,
            latency_ms REAL,
            defect_image_path TEXT
        )""",
        "CREATE INDEX IF NOT EXISTS idx_packages_ts ON packages(ts_ms)",
        "CREATE INDEX IF NOT EXISTS idx_packages_decision ON packages(decision)",
        """CREATE TABLE IF NOT EXISTS used_codes (
            pkg_id TEXT PRIMARY KEY,
            ts_ms INTEGER NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS rpc_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts_ms INTEGER NOT NULL,
            request_id TEXT,
            method TEXT NOT NULL,
            params_json TEXT,
            requested_by TEXT,
            result_json TEXT,
            ok INTEGER NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS security_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts_ms INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            level TEXT NOT NULL,
            detail TEXT,
            extra_json TEXT
        )""",
        "CREATE INDEX IF NOT EXISTS idx_security_ts ON security_events(ts_ms)",
        """CREATE TABLE IF NOT EXISTS system_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts_ms INTEGER NOT NULL,
            kind TEXT NOT NULL,
            detail TEXT
        )""",
        """CREATE TABLE IF NOT EXISTS counters (
            name TEXT PRIMARY KEY,
            value INTEGER NOT NULL DEFAULT 0
        )""",
    ],
}

PACKAGE_COLUMNS: tuple[str, ...] = (
    "ts_ms", "pkg_seq", "pkg_id", "warehouse", "qr_status", "ai_label", "ai_conf", "ai_types_json",
    "decision", "reason", "target", "t_capture_ms", "t_qr_ms", "t_verify_ms", "t_ai_ms", "latency_ms",
    "defect_image_path",
)


def now_ms() -> int:
    return int(time.time() * 1000)


class Storage:
    """SQLite WAL; mọi thao tác qua một lock nên an toàn giữa các thread."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = str(db_path)
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=5.0, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        try:
            self._conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.DatabaseError:  # :memory: không hỗ trợ WAL
            pass
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._migrate()
        self._closed = False

    # ------------------------------------------------------------------ transaction
    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """BEGIN IMMEDIATE ... COMMIT/ROLLBACK trong lock."""
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    def _migrate(self) -> None:
        with self._lock:
            self._conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
            row = self._conn.execute("SELECT version FROM schema_version ORDER BY version DESC LIMIT 1").fetchone()
            current = int(row[0]) if row else 0
            for v in range(current + 1, SCHEMA_VERSION + 1):
                with self.transaction() as c:
                    for sql in MIGRATIONS.get(v, []):
                        c.execute(sql)
                    c.execute("DELETE FROM schema_version")
                    c.execute("INSERT INTO schema_version(version) VALUES (?)", (v,))
                log.info("Storage: nâng schema lên phiên bản %d", v)

    @property
    def schema_version(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
            return int(row[0]) if row else 0

    # ------------------------------------------------------------------ packages
    def insert_package(self, rec: dict[str, Any]) -> int:
        """Ghi một kiện; chỉ lấy các cột đã biết, thiếu thì NULL. Trả id."""
        data = {k: rec.get(k) for k in PACKAGE_COLUMNS}
        if data["ts_ms"] is None:
            data["ts_ms"] = now_ms()
        if data["decision"] is None:
            data["decision"] = "REJECT_FAILSAFE"
        if isinstance(data["ai_types_json"], dict):
            data["ai_types_json"] = json.dumps(data["ai_types_json"], ensure_ascii=False)
        cols = ", ".join(PACKAGE_COLUMNS)
        qs = ", ".join("?" for _ in PACKAGE_COLUMNS)
        with self._lock:
            cur = self._conn.execute(f"INSERT INTO packages ({cols}) VALUES ({qs})", [data[k] for k in PACKAGE_COLUMNS])
            return int(cur.lastrowid or 0)

    def recent_packages(self, n: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM packages ORDER BY id DESC LIMIT ?", (int(n),)).fetchall()
        return [dict(r) for r in rows]

    def count_packages_by_decision(self) -> dict[str, int]:
        with self._lock:
            rows = self._conn.execute("SELECT decision, COUNT(*) AS n FROM packages GROUP BY decision").fetchall()
        return {str(r["decision"]): int(r["n"]) for r in rows}

    def count_packages(self) -> int:
        with self._lock:
            return int(self._conn.execute("SELECT COUNT(*) FROM packages").fetchone()[0])

    # ------------------------------------------------------------------ used_codes (S-03 chống replay)
    def try_insert_used_code(self, pkg_id: str, ts_ms: int | None = None) -> bool:
        """Atomic: True nếu pkg_id chưa từng dùng (và vừa được ghi), False nếu đã tồn tại."""
        with self.transaction() as c:
            exists = c.execute("SELECT 1 FROM used_codes WHERE pkg_id = ?", (pkg_id,)).fetchone()
            if exists:
                return False
            c.execute("INSERT OR IGNORE INTO used_codes(pkg_id, ts_ms) VALUES (?, ?)", (pkg_id, ts_ms or now_ms()))
            return True

    def is_code_used(self, pkg_id: str) -> bool:
        with self._lock:
            return self._conn.execute("SELECT 1 FROM used_codes WHERE pkg_id = ?", (pkg_id,)).fetchone() is not None

    def clear_used_codes(self) -> int:
        with self.transaction() as c:
            n = int(c.execute("SELECT COUNT(*) FROM used_codes").fetchone()[0])
            c.execute("DELETE FROM used_codes")
            return n

    def count_used_codes(self) -> int:
        with self._lock:
            return int(self._conn.execute("SELECT COUNT(*) FROM used_codes").fetchone()[0])

    # ------------------------------------------------------------------ rpc_audit (S-05)
    def insert_rpc_audit(
        self,
        request_id: str | int | None,
        method: str,
        params: dict[str, Any] | None,
        requested_by: str | None,
        result: dict[str, Any] | None,
        ok: bool,
        ts_ms: int | None = None,
    ) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO rpc_audit(ts_ms, request_id, method, params_json, requested_by, result_json, ok) VALUES (?,?,?,?,?,?,?)",
                (
                    ts_ms or now_ms(),
                    None if request_id is None else str(request_id),
                    method,
                    json.dumps(params or {}, ensure_ascii=False),
                    requested_by,
                    json.dumps(result or {}, ensure_ascii=False),
                    1 if ok else 0,
                ),
            )
            return int(cur.lastrowid or 0)

    def recent_rpc_audit(self, n: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM rpc_audit ORDER BY id DESC LIMIT ?", (int(n),)).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------ security_events (S-09)
    def insert_security_event(self, ev: SecurityEvent) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO security_events(ts_ms, event_type, level, detail, extra_json) VALUES (?,?,?,?,?)",
                (ev.ts_ms, ev.event_type, ev.level.value, ev.detail, json.dumps(ev.extra, ensure_ascii=False, default=str)),
            )
            return int(cur.lastrowid or 0)

    def recent_security_events(self, n: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM security_events ORDER BY id DESC LIMIT ?", (int(n),)).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------ system_events
    def insert_system_event(self, kind: str, detail: str = "", ts_ms: int | None = None) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO system_events(ts_ms, kind, detail) VALUES (?,?,?)", (ts_ms or now_ms(), kind, detail[:2000])
            )
            return int(cur.lastrowid or 0)

    def recent_system_events(self, n: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM system_events ORDER BY id DESC LIMIT ?", (int(n),)).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------ counters (P-11)
    def get_counter(self, name: str, default: int = 0) -> int:
        with self._lock:
            row = self._conn.execute("SELECT value FROM counters WHERE name = ?", (name,)).fetchone()
            return int(row[0]) if row else default

    def set_counter(self, name: str, value: int) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO counters(name, value) VALUES (?, ?) ON CONFLICT(name) DO UPDATE SET value = excluded.value",
                (name, int(value)),
            )

    def incr_counter(self, name: str, n: int = 1) -> int:
        with self.transaction() as c:
            c.execute(
                "INSERT INTO counters(name, value) VALUES (?, ?) ON CONFLICT(name) DO UPDATE SET value = value + excluded.value",
                (name, int(n)),
            )
            return int(c.execute("SELECT value FROM counters WHERE name = ?", (name,)).fetchone()[0])

    def all_counters(self) -> dict[str, int]:
        with self._lock:
            rows = self._conn.execute("SELECT name, value FROM counters").fetchall()
        return {str(r["name"]): int(r["value"]) for r in rows}

    # ------------------------------------------------------------------
    def close(self) -> None:
        with self._lock:
            if not self._closed:
                try:
                    self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                except sqlite3.DatabaseError:
                    pass
                self._conn.close()
                self._closed = True

    def __enter__(self) -> "Storage":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()
