"""Giao diện dùng chung giữa các module của sorter-core (P3, P4, P5, P9).

- Classifier / ClassResult      : AI kiểm tra bao bì (P-05, AI-06)
- TelemetrySink / LogTelemetrySink: đích gửi telemetry (P-10, T-03); bản thật là tb_client.ThingsBoardClient
- SecurityEvent / SecurityLevel  : nhật ký sự kiện bảo mật (S-09)
- MetricCounters                : bộ đếm trong bộ nhớ cho phát hiện bất thường (A-01)

Mọi module khác chỉ phụ thuộc vào file này, không phụ thuộc chéo lẫn nhau.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Callable

import numpy as np

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# AI (P-05)
# ---------------------------------------------------------------------------
LABEL_OK = "OK"
LABEL_DEFECT = "DEFECT"
LABEL_UNCERTAIN = "UNCERTAIN"


@dataclass
class ClassResult:
    """Kết quả phân loại một ảnh.

    label      : "OK" | "DEFECT" | "UNCERTAIN" (dưới ngưỡng tin cậy)
    confidence : độ tin cậy của nhãn trả về (0..1)
    probs      : xác suất từng lớp, ví dụ {"OK": 0.1, "DEFECT": 0.9}
    defect_types: xác suất từng loại lỗi (tear/dent/crush/wet) nếu model có đầu 2, rỗng nếu không
    infer_ms   : thời gian suy luận (ms)
    model_version: phiên bản model (gửi lên ThingsBoard dạng attribute)
    """

    label: str
    confidence: float
    probs: dict[str, float] = field(default_factory=dict)
    defect_types: dict[str, float] = field(default_factory=dict)
    infer_ms: float = 0.0
    model_version: str = "unknown"

    @property
    def is_ok(self) -> bool:
        return self.label == LABEL_OK

    @property
    def top_defect_type(self) -> str | None:
        """Loại lỗi có xác suất cao nhất (để hiện nhãn lỗi trên dashboard)."""
        if not self.defect_types:
            return None
        return max(self.defect_types.items(), key=lambda kv: kv[1])[0]


class Classifier(ABC):
    """Giao diện bộ phân loại bao bì. Triển khai: DummyClassifier (P3), OnnxClassifier/TfliteClassifier (P4)."""

    model_version: str = "dummy"
    detects_boxes: bool = False

    def prepare_box(self, image: np.ndarray, roi: tuple[int, int, int, int] | None = None) -> np.ndarray | None:
        """ROI cho simulator/classifier riêng; detector thật override và có thể từ chối vật."""
        return crop_roi(image, roi)

    def classify_box(self, box: np.ndarray) -> ClassResult:
        """Phân loại ảnh hộp đã được xác nhận, sau khi QR hợp lệ."""
        return self.predict(box)

    @abstractmethod
    def predict(self, image: np.ndarray, roi: tuple[int, int, int, int] | None = None) -> ClassResult:
        """image: numpy RGB HxWx3 uint8; roi: (x, y, w, h) hoặc None = cả ảnh."""

    def warmup(self) -> None:  # pragma: no cover - mặc định không làm gì
        return None

    def close(self) -> None:  # pragma: no cover
        return None


def crop_roi(image: np.ndarray, roi: tuple[int, int, int, int] | None) -> np.ndarray:
    """Cắt ROI (x, y, w, h) an toàn trong biên ảnh; roi None -> trả nguyên ảnh."""
    if roi is None:
        return image
    x, y, w, h = (int(v) for v in roi)
    H, W = image.shape[:2]
    x0, y0 = max(0, min(x, W - 1)), max(0, min(y, H - 1))
    x1, y1 = max(x0 + 1, min(x + w, W)), max(y0 + 1, min(y + h, H))
    return image[y0:y1, x0:x1]


# ---------------------------------------------------------------------------
# Telemetry (P-10 / T-03)
# ---------------------------------------------------------------------------
class TelemetrySink(ABC):
    """Đích gửi dữ liệu lên nền tảng IoT. Tên khóa phải đúng đặc tả 4.3 — người gọi chịu trách nhiệm."""

    @abstractmethod
    def send_telemetry(self, data: dict[str, Any], ts_ms: int | None = None) -> None:
        """Gửi một bản ghi telemetry (nhiều khóa cùng lúc)."""

    @abstractmethod
    def send_attributes(self, data: dict[str, Any]) -> None:
        """Gửi client attributes (fw_version, sw_version, model_version, qr_format_version)."""

    def set_rpc_handler(self, handler: Callable[[str, dict[str, Any]], dict[str, Any]]) -> None:
        """Đăng ký hàm xử lý RPC (method, params) -> response dict. Bản log không có RPC."""
        return None

    def is_connected(self) -> bool:
        return True

    def reconnect_count(self) -> int:
        return 0

    def start(self) -> None:
        return None

    def stop(self) -> None:
        return None


class LogTelemetrySink(TelemetrySink):
    """Bản tạm cho P3 / chế độ sim: ghi telemetry ra log JSON, không cần mạng."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.attributes: dict[str, Any] = {}
        self._lock = threading.Lock()
        self._rpc_handler: Callable[[str, dict[str, Any]], dict[str, Any]] | None = None

    def send_telemetry(self, data: dict[str, Any], ts_ms: int | None = None) -> None:
        rec = {"ts": ts_ms or int(time.time() * 1000), "values": _truncate_for_log(data)}
        with self._lock:
            self.sent.append(data)
            if len(self.sent) > 2000:
                del self.sent[:1000]
        log.info("telemetry %s", json.dumps(rec, ensure_ascii=False))

    def send_attributes(self, data: dict[str, Any]) -> None:
        with self._lock:
            self.attributes.update(data)
        log.info("attributes %s", json.dumps(data, ensure_ascii=False))

    def set_rpc_handler(self, handler: Callable[[str, dict[str, Any]], dict[str, Any]]) -> None:
        self._rpc_handler = handler

    def call_rpc(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Dùng trong test/sim để giả lập một RPC từ dashboard."""
        if self._rpc_handler is None:
            return {"ok": False, "error": "NO_HANDLER"}
        return self._rpc_handler(method, params or {})


def _truncate_for_log(data: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for k, v in data.items():
        if isinstance(v, str) and len(v) > 120:
            out[k] = f"<{len(v)} ký tự>"
        else:
            out[k] = v
    return out


# ---------------------------------------------------------------------------
# Sự kiện bảo mật (S-09)
# ---------------------------------------------------------------------------
class SecurityLevel(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    MAJOR = "MAJOR"
    CRITICAL = "CRITICAL"


# Loại sự kiện chuẩn (có thể thêm, nhưng tên dưới đây được các module khác tham chiếu)
SEC_QR_BAD_SIGNATURE = "QR_BAD_SIGNATURE"
SEC_QR_DUPLICATE = "QR_DUPLICATE"
SEC_QR_BAD_FORMAT = "QR_BAD_FORMAT"
SEC_QR_EXPIRED = "QR_EXPIRED"
SEC_QR_FUTURE = "QR_FUTURE_TIMESTAMP"
SEC_QR_BAD_WAREHOUSE = "QR_BAD_WAREHOUSE"
SEC_QR_MULTI = "QR_MULTI_QR"
SEC_RPC_REJECTED = "RPC_REJECTED"
SEC_RPC_RATE_LIMITED = "RPC_RATE_LIMITED"
SEC_UART_CRC = "UART_CRC_ERRORS"
SEC_LINK_LOST = "ESP_LINK_LOST"
SEC_ESTOP = "ESTOP"
SEC_ANOMALY = "ANOMALY"
SEC_RULE = "RULE_TRIGGERED"
SEC_OTA_REJECTED = "OTA_REJECTED"

# Mức mặc định theo loại (dùng cho alarm "Security alert" trên ThingsBoard)
DEFAULT_LEVELS: dict[str, SecurityLevel] = {
    SEC_QR_BAD_SIGNATURE: SecurityLevel.MAJOR,
    SEC_QR_DUPLICATE: SecurityLevel.MAJOR,
    SEC_QR_BAD_FORMAT: SecurityLevel.WARNING,
    SEC_QR_EXPIRED: SecurityLevel.WARNING,
    SEC_QR_FUTURE: SecurityLevel.WARNING,
    SEC_QR_BAD_WAREHOUSE: SecurityLevel.WARNING,
    SEC_QR_MULTI: SecurityLevel.WARNING,
    SEC_RPC_REJECTED: SecurityLevel.MAJOR,
    SEC_RPC_RATE_LIMITED: SecurityLevel.WARNING,
    SEC_UART_CRC: SecurityLevel.WARNING,
    SEC_LINK_LOST: SecurityLevel.MAJOR,
    SEC_ESTOP: SecurityLevel.INFO,
    SEC_ANOMALY: SecurityLevel.CRITICAL,
    SEC_RULE: SecurityLevel.CRITICAL,
    SEC_OTA_REJECTED: SecurityLevel.MAJOR,
}


@dataclass
class SecurityEvent:
    event_type: str
    level: SecurityLevel
    detail: str
    ts_ms: int = field(default_factory=lambda: int(time.time() * 1000))
    extra: dict[str, Any] = field(default_factory=dict)

    def to_telemetry(self) -> dict[str, Any]:
        """Đúng tên khóa đặc tả 4.3 (nhóm Bảo mật)."""
        return {
            "security_alert": True,
            "security_event_type": self.event_type,
            "security_level": self.level.value,
            "security_detail": self.detail[:500],
        }

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["level"] = self.level.value
        return d


# ---------------------------------------------------------------------------
# Bộ đếm cho phát hiện bất thường (A-01)
# ---------------------------------------------------------------------------
# Tên đặc trưng chuẩn — anomaly/features.py đọc đúng các tên này
F_RPC_TOTAL = "rpc_total"
F_RPC_REJECTED = "rpc_rejected"
F_QR_BAD_SIGNATURE = "qr_bad_signature"
F_QR_DUPLICATE = "qr_duplicate"
F_QR_BAD_FORMAT = "qr_bad_format"
F_UART_CRC_ERRORS = "uart_crc_errors"
F_MQTT_RECONNECTS = "mqtt_reconnects"
F_SSH_FAILED = "ssh_failed"
F_ESTOP_COUNT = "estop_count"
F_PACKAGES = "packages"
FEATURE_NAMES: tuple[str, ...] = (
    F_RPC_TOTAL, F_RPC_REJECTED, F_QR_BAD_SIGNATURE, F_QR_DUPLICATE, F_QR_BAD_FORMAT,
    F_UART_CRC_ERRORS, F_MQTT_RECONNECTS, F_SSH_FAILED, F_ESTOP_COUNT, F_PACKAGES,
)


class MetricCounters:
    """Bộ đếm thread-safe, tích lũy trong một cửa sổ rồi được anomaly.features lấy và đặt lại."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._c: dict[str, int] = {k: 0 for k in FEATURE_NAMES}

    def inc(self, name: str, n: int = 1) -> None:
        with self._lock:
            self._c[name] = self._c.get(name, 0) + n

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return {k: self._c.get(k, 0) for k in FEATURE_NAMES}

    def snapshot_and_reset(self) -> dict[str, int]:
        with self._lock:
            snap = {k: self._c.get(k, 0) for k in FEATURE_NAMES}
            for k in self._c:
                self._c[k] = 0
            return snap


# Bộ đếm toàn cục mặc định — các module gọi `metrics.inc(F_...)`; test có thể thay bằng instance riêng.
metrics = MetricCounters()
