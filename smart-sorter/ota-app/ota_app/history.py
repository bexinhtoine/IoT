"""O-06 — Lịch sử phát hành và báo cáo nạp từ thiết bị (SQLite).

Bảng:
- releases(id, version_code, version_name, fw_sha256, fw_size, key_id, signed_by, signed_at,
           dir, is_current, note)
- device_reports(id, ts, device, version_code, result, reason, remote_ip)   -- từ POST /ota/report
- request_log(id, ts, remote_ip, method, route, status)                     -- O-05 log mọi request

Kết nối dùng ``check_same_thread=False`` + lock vì server HTTPS (thread riêng) cũng ghi vào đây.
"""
from __future__ import annotations

import csv
import logging
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

log = logging.getLogger(__name__)

REPORT_RESULTS: tuple[str, ...] = ("SUCCESS", "REJECTED", "ROLLED_BACK")
TABLES: tuple[str, ...] = ("releases", "device_reports", "request_log")


@dataclass
class ReleaseRecord:
    id: int
    version_code: int
    version_name: str
    fw_sha256: str
    fw_size: int
    key_id: str
    signed_by: str
    signed_at: float
    dir: str
    is_current: bool
    note: str


@dataclass
class DeviceReport:
    id: int
    ts: float
    device: str
    version_code: int
    result: str
    reason: str
    remote_ip: str


class HistoryStore:
    SCHEMA = """
    CREATE TABLE IF NOT EXISTS releases (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        version_code INTEGER NOT NULL,
        version_name TEXT NOT NULL,
        fw_sha256    TEXT NOT NULL,
        fw_size      INTEGER NOT NULL,
        key_id       TEXT NOT NULL,
        signed_by    TEXT NOT NULL,
        signed_at    REAL NOT NULL,
        dir          TEXT NOT NULL,
        is_current   INTEGER NOT NULL DEFAULT 0,
        note         TEXT NOT NULL DEFAULT ''
    );
    CREATE TABLE IF NOT EXISTS device_reports (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        ts           REAL NOT NULL,
        device       TEXT NOT NULL,
        version_code INTEGER NOT NULL,
        result       TEXT NOT NULL,
        reason       TEXT NOT NULL DEFAULT '',
        remote_ip    TEXT NOT NULL DEFAULT ''
    );
    CREATE TABLE IF NOT EXISTS request_log (
        id        INTEGER PRIMARY KEY AUTOINCREMENT,
        ts        REAL NOT NULL,
        remote_ip TEXT NOT NULL,
        method    TEXT NOT NULL,
        route     TEXT NOT NULL,
        status    INTEGER NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_releases_vc ON releases(version_code);
    CREATE INDEX IF NOT EXISTS idx_reports_ts ON device_reports(ts);
    """

    def __init__(self, db_path: Path | str, clock: Callable[[], float] = time.time) -> None:
        self.db_path = Path(db_path)
        self.clock = clock
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._conn:
            self._conn.executescript(self.SCHEMA)

    def close(self) -> None:
        self._conn.close()

    # ---------------- releases ----------------
    def max_version_code(self) -> int:
        """version_code lớn nhất đã phát hành (0 nếu chưa có). Dùng để chặn version không tăng (O-03)."""
        with self._lock:
            row = self._conn.execute("SELECT MAX(version_code) AS m FROM releases").fetchone()
        return int(row["m"]) if row and row["m"] is not None else 0

    def add_release(
        self,
        version_code: int,
        version_name: str,
        fw_sha256: str,
        fw_size: int,
        key_id: str,
        signed_by: str,
        release_dir: Path | str,
        note: str = "",
        make_current: bool = True,
        signed_at: Optional[float] = None,
    ) -> int:
        ts = signed_at if signed_at is not None else self.clock()
        with self._lock, self._conn:
            if make_current:
                self._conn.execute("UPDATE releases SET is_current=0")
            cur = self._conn.execute(
                "INSERT INTO releases(version_code, version_name, fw_sha256, fw_size, key_id, signed_by,"
                " signed_at, dir, is_current, note) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (version_code, version_name, fw_sha256, fw_size, key_id, signed_by, ts,
                 str(release_dir), 1 if make_current else 0, note),
            )
            rid = int(cur.lastrowid or 0)
        log.info("Ghi lịch sử phát hành #%d version_code=%d bởi %s", rid, version_code, signed_by)
        return rid

    def set_current(self, release_id: int) -> None:
        with self._lock, self._conn:
            self._conn.execute("UPDATE releases SET is_current=0")
            self._conn.execute("UPDATE releases SET is_current=1 WHERE id=?", (release_id,))

    def current_release(self) -> Optional[ReleaseRecord]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM releases WHERE is_current=1 ORDER BY id DESC LIMIT 1").fetchone()
        return self._to_release(row) if row else None

    def list_releases(self, text_filter: str = "", limit: int = 500) -> list[ReleaseRecord]:
        q = "SELECT * FROM releases"
        params: list[Any] = []
        if text_filter:
            q += " WHERE version_name LIKE ? OR fw_sha256 LIKE ? OR signed_by LIKE ? OR CAST(version_code AS TEXT) LIKE ?"
            like = f"%{text_filter}%"
            params = [like, like, like, like]
        q += " ORDER BY version_code DESC, id DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(q, params).fetchall()
        return [self._to_release(r) for r in rows]

    @staticmethod
    def _to_release(row: sqlite3.Row) -> ReleaseRecord:
        return ReleaseRecord(
            id=int(row["id"]), version_code=int(row["version_code"]), version_name=row["version_name"],
            fw_sha256=row["fw_sha256"], fw_size=int(row["fw_size"]), key_id=row["key_id"],
            signed_by=row["signed_by"], signed_at=float(row["signed_at"]), dir=row["dir"],
            is_current=bool(row["is_current"]), note=row["note"],
        )

    # ---------------- device_reports ----------------
    def add_device_report(
        self, device: str, version_code: int, result: str, reason: str, remote_ip: str = "", ts: Optional[float] = None
    ) -> int:
        if result not in REPORT_RESULTS:
            raise ValueError(f"result phải thuộc {REPORT_RESULTS}")
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT INTO device_reports(ts, device, version_code, result, reason, remote_ip) VALUES (?,?,?,?,?,?)",
                (ts if ts is not None else self.clock(), device, version_code, result, reason, remote_ip),
            )
            rid = int(cur.lastrowid or 0)
        log.info("Báo cáo thiết bị #%d: %s version_code=%d %s %s", rid, device, version_code, result, reason)
        return rid

    def list_device_reports(self, text_filter: str = "", limit: int = 1000) -> list[DeviceReport]:
        q = "SELECT * FROM device_reports"
        params: list[Any] = []
        if text_filter:
            q += " WHERE device LIKE ? OR result LIKE ? OR reason LIKE ? OR CAST(version_code AS TEXT) LIKE ?"
            like = f"%{text_filter}%"
            params = [like, like, like, like]
        q += " ORDER BY ts DESC, id DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(q, params).fetchall()
        return [
            DeviceReport(int(r["id"]), float(r["ts"]), r["device"], int(r["version_code"]),
                         r["result"], r["reason"], r["remote_ip"])
            for r in rows
        ]

    # ---------------- request_log ----------------
    def add_request_log(self, remote_ip: str, method: str, route: str, status: int, ts: Optional[float] = None) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO request_log(ts, remote_ip, method, route, status) VALUES (?,?,?,?,?)",
                (ts if ts is not None else self.clock(), remote_ip, method, route, int(status)),
            )

    def list_request_log(self, limit: int = 500) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM request_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    # ---------------- CSV ----------------
    def export_csv(self, path: Path | str, table: str) -> int:
        """Xuất một bảng ra CSV (UTF-8, có header). Trả về số dòng ghi."""
        if table not in TABLES:
            raise ValueError(f"table phải thuộc {TABLES}")
        with self._lock:
            cur = self._conn.execute(f"SELECT * FROM {table} ORDER BY id")  # tên bảng đã được whitelist
            cols = [c[0] for c in cur.description]
            rows: Iterable[sqlite3.Row] = cur.fetchall()
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        n = 0
        with open(p, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(cols)
            for r in rows:
                w.writerow([r[c] for c in cols])
                n += 1
        log.info("Xuất %d dòng bảng %s ra %s", n, table, p)
        return n
