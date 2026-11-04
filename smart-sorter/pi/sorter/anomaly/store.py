"""P9 / A-01 — Lưu cửa sổ đặc trưng vào PostgreSQL hoặc SQLite.

DB dùng chung với storage.py của module P3 (sorter.db). Lớp này chỉ tạo và đụng
đúng bảng anomaly_windows (CREATE TABLE IF NOT EXISTS), không chạm bảng khác.

Mở kết nối riêng: WAL để nhiều tiến trình/thread cùng đọc-ghi, timeout 5 s cho khóa
bận, check_same_thread=False (detector dùng thread riêng) kèm Lock của chính lớp.
"""
from __future__ import annotations

import logging
import sqlite3
import threading
from pathlib import Path
from typing import Any

from .. import interfaces
from ..database import open_database

log = logging.getLogger(__name__)

# Tên cột đặc trưng khớp đúng FEATURE_NAMES (đặc tả 4.3 / A-01)
_FEATURE_COLS: tuple[str, ...] = interfaces.FEATURE_NAMES


def _col_defs() -> str:
    """Sinh phần định nghĩa 10 cột đặc trưng INTEGER theo đúng thứ tự FEATURE_NAMES."""
    return ", ".join(f"{name} INTEGER NOT NULL DEFAULT 0" for name in _FEATURE_COLS)


class AnomalyStore:
    """Kho cửa sổ đặc trưng cho phát hiện bất thường."""

    def __init__(self, db_path: str) -> None:
        self.db_path = str(db_path)
        self._lock = threading.Lock()
        self._conn = open_database(self.db_path)
        with self._lock:
            self._create_table()

    def _create_table(self) -> None:
        sql = (
            "CREATE TABLE IF NOT EXISTS anomaly_windows ("
            "id INTEGER PRIMARY KEY, "
            "ts_ms INTEGER NOT NULL, "
            f"{_col_defs()}, "
            "score REAL, "
            "flag INTEGER, "
            "top_features TEXT, "
            "label TEXT)"
        )
        self._conn.execute(sql)
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_anomaly_windows_ts ON anomaly_windows(ts_ms)"
        )

    def insert_window(
        self,
        ts_ms: int,
        features: dict[str, int],
        score: float | None = None,
        flag: bool | None = None,
        top_features: str | None = None,
        label: str | None = None,
    ) -> int:
        """Chèn một cửa sổ, trả về id. features theo tên trong FEATURE_NAMES."""
        cols = ["ts_ms", *_FEATURE_COLS, "score", "flag", "top_features", "label"]
        vals: list[Any] = [int(ts_ms)]
        vals += [int(features.get(name, 0)) for name in _FEATURE_COLS]
        vals += [
            None if score is None else float(score),
            None if flag is None else (1 if flag else 0),
            top_features,
            label,
        ]
        placeholders = ", ".join("?" for _ in cols)
        sql = f"INSERT INTO anomaly_windows ({', '.join(cols)}) VALUES ({placeholders})"
        with self._lock:
            cur = self._conn.execute(sql, vals)
            return int(cur.lastrowid)

    def update_score(
        self,
        window_id: int,
        score: float,
        flag: bool,
        top_features: str,
    ) -> None:
        """Cập nhật điểm/flag/top_features cho cửa sổ đã chèn."""
        with self._lock:
            self._conn.execute(
                "UPDATE anomaly_windows SET score=?, flag=?, top_features=? WHERE id=?",
                (float(score), 1 if flag else 0, top_features, int(window_id)),
            )

    def fetch(self, since_ms: int, until_ms: int) -> list[dict[str, Any]]:
        """Lấy các cửa sổ có ts_ms trong [since_ms, until_ms], tăng dần theo thời gian."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM anomaly_windows WHERE ts_ms >= ? AND ts_ms <= ? ORDER BY ts_ms ASC",
                (int(since_ms), int(until_ms)),
            ).fetchall()
        return [dict(r) for r in rows]

    def count(self) -> int:
        """Tổng số cửa sổ đang lưu."""
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) AS n FROM anomaly_windows").fetchone()
        return int(row["n"])

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> "AnomalyStore":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()
