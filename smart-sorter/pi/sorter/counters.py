"""P-11 — Bộ đếm sản lượng, lưu bền trong bảng counters của SQLite.

Khóa telemetry đúng đặc tả 4.3: cnt_total, cnt_a, cnt_b, cnt_reject_defect, cnt_reject_security, cnt_reject_failsafe.
"""
from __future__ import annotations

import logging
import threading

from .decision import REJECT_DEFECT, REJECT_FAILSAFE, REJECT_SECURITY, SORT_A, SORT_B
from .storage import Storage

log = logging.getLogger(__name__)

CNT_TOTAL = "cnt_total"
CNT_A = "cnt_a"
CNT_B = "cnt_b"
CNT_REJECT_DEFECT = "cnt_reject_defect"
CNT_REJECT_SECURITY = "cnt_reject_security"
CNT_REJECT_FAILSAFE = "cnt_reject_failsafe"
COUNTER_NAMES: tuple[str, ...] = (CNT_TOTAL, CNT_A, CNT_B, CNT_REJECT_DEFECT, CNT_REJECT_SECURITY, CNT_REJECT_FAILSAFE)

DECISION_TO_COUNTER: dict[str, str] = {
    SORT_A: CNT_A,
    SORT_B: CNT_B,
    REJECT_DEFECT: CNT_REJECT_DEFECT,
    REJECT_SECURITY: CNT_REJECT_SECURITY,
    REJECT_FAILSAFE: CNT_REJECT_FAILSAFE,
}


class Counters:
    """6 bộ đếm; khôi phục từ storage lúc khởi tạo; mỗi record() ghi ngay xuống DB."""

    def __init__(self, storage: Storage) -> None:
        self._storage = storage
        self._lock = threading.Lock()
        self._values: dict[str, int] = {name: storage.get_counter(name, 0) for name in COUNTER_NAMES}
        log.info("Counters khôi phục: %s", self._values)

    def record(self, decision_code: str) -> dict[str, int]:
        """Tăng cnt_total và bộ đếm tương ứng mã quyết định; mã lạ tính vào failsafe."""
        name = DECISION_TO_COUNTER.get(decision_code, CNT_REJECT_FAILSAFE)
        with self._lock:
            self._values[CNT_TOTAL] = self._storage.incr_counter(CNT_TOTAL, 1)
            self._values[name] = self._storage.incr_counter(name, 1)
            return dict(self._values)

    def reset(self) -> dict[str, int]:
        with self._lock:
            for name in COUNTER_NAMES:
                self._storage.set_counter(name, 0)
                self._values[name] = 0
            log.info("Counters đã reset")
            return dict(self._values)

    def record_package(self, rec: dict) -> dict[str, int]:
        name = DECISION_TO_COUNTER.get(rec["decision"], CNT_REJECT_FAILSAFE)
        with self._lock:
            values = self._storage.save_package_and_counters(rec, name)
            self._values = {key: values.get(key, 0) for key in COUNTER_NAMES}
            return dict(self._values)

    def get(self, name: str) -> int:
        with self._lock:
            return self._values.get(name, 0)

    @property
    def values(self) -> dict[str, int]:
        with self._lock:
            return dict(self._values)

    def to_telemetry(self) -> dict[str, int]:
        """Đúng tên khóa 4.3 nhóm Bộ đếm."""
        return self.values
