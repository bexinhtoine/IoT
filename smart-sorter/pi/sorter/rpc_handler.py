"""T-04 / S-05 — Xử lý RPC từ ThingsBoard theo whitelist (đặc tả 4.4).

Luồng xử lý một RPC (method, params, request_id):
  1. emergencyStop: xử lý TRƯỚC mọi thứ, không tính rate limit → UART ESTOP.
  2. Phương thức ngoài whitelist → METHOD_NOT_ALLOWED + security event RPC_REJECTED (MAJOR)
     + metrics rpc_rejected.
  3. Rate limit token bucket (cfg.rate_limit: 5 lệnh / 10 s) → RATE_LIMITED
     + security event RPC_RATE_LIMITED (WARNING).
  4. Kiểm tra schema: kiểu + miền từng tham số, tham số lạ → BAD_PARAMS.
  5. Kiểm tra trạng thái ESP32 (uart.state) → BAD_STATE; gọi UartLink; lỗi
     TIMEOUT/LINK_DOWN → ESP_TIMEOUT, NACK_BAD_STATE → BAD_STATE.
  6. Mọi RPC (kể cả bị từ chối): metrics rpc_total, ghi rpc_audit (callback),
     gửi telemetry rpc_log = JSON một dòng.

Trả về đúng định dạng 4.4: {"ok": true, ...} hoặc
{"ok": false, "error": "METHOD_NOT_ALLOWED" | "BAD_PARAMS" | "BAD_STATE" | "RATE_LIMITED" | "ESP_TIMEOUT"}.

requestedBy trong tham số chỉ để hiển thị (S-05); nguồn tin cậy là Audit Logs của ThingsBoard.
Module này KHÔNG import uart_link để tránh phụ thuộc chéo: chỉ dùng duck-typing
(uart.state, uart.connected, uart.send_command(...) -> có .ok/.error).
"""
from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from sorter import interfaces
from sorter.config import RpcCfg
from sorter.interfaces import (
    F_RPC_REJECTED,
    F_RPC_TOTAL,
    SEC_RPC_RATE_LIMITED,
    SEC_RPC_REJECTED,
    MetricCounters,
    SecurityEvent,
    SecurityLevel,
    TelemetrySink,
)
from sorter.protocol_gen import MsgType, StartPayload, State

log = logging.getLogger(__name__)

# Mã lỗi theo đặc tả 4.4
ERR_METHOD_NOT_ALLOWED = "METHOD_NOT_ALLOWED"
ERR_BAD_PARAMS = "BAD_PARAMS"
ERR_BAD_STATE = "BAD_STATE"
ERR_RATE_LIMITED = "RATE_LIMITED"
ERR_ESP_TIMEOUT = "ESP_TIMEOUT"
ERR_INTERNAL = "INTERNAL_ERROR"  # ngoại lệ không lường trước (fail-safe: không bao giờ ném ra ngoài)

REQUESTED_BY_KEY = "requestedBy"
REQUESTED_BY_MAX_LEN = 120

# Lỗi của UartLink.send_command -> mã lỗi RPC
_UART_ERR_MAP: dict[str, str] = {
    "TIMEOUT": ERR_ESP_TIMEOUT,
    "LINK_DOWN": ERR_ESP_TIMEOUT,
    "NACK_BAD_STATE": ERR_BAD_STATE,
    "NACK_BAD_PARAM": ERR_BAD_PARAMS,
}


@dataclass
class RpcCallbacks:
    """Móc nối sang các phần khác của sorter-core (P-09, P-11, S-09)."""

    reset_counters: Callable[[], None]
    reset_used_codes: Callable[[], int]            # trả số mã đã xóa
    get_status: Callable[[], dict[str, Any]]       # bộ đếm, conveyor_state, phiên bản... để trả cho getStatus
    on_security_event: Callable[[SecurityEvent], None]
    audit: Callable[[dict[str, Any]], None]        # ghi bảng rpc_audit:
    # {ts_ms, request_id, method, params_json, requested_by, result_json, ok}


# ---------------------------------------------------------------------------
# Schema tham số: method -> {tên tham số: hàm kiểm tra (giá trị) -> thông báo lỗi | None}
# ---------------------------------------------------------------------------
Validator = Callable[[Any], str | None]


def _int_range(lo: int, hi: int) -> Validator:
    def check(v: Any) -> str | None:
        # bool là lớp con của int trong Python — loại trừ rõ ràng
        if isinstance(v, bool) or not isinstance(v, int):
            return f"cần số nguyên, nhận {type(v).__name__}"
        if not lo <= v <= hi:
            return f"{v} ngoài miền [{lo}, {hi}]"
        return None

    return check


def _str_equals(expected: str) -> Validator:
    def check(v: Any) -> str | None:
        if not isinstance(v, str):
            return f"cần chuỗi, nhận {type(v).__name__}"
        if v != expected:
            return f"phải bằng '{expected}'"
        return None

    return check


def _str_max(n: int) -> Validator:
    def check(v: Any) -> str | None:
        if not isinstance(v, str):
            return f"cần chuỗi, nhận {type(v).__name__}"
        if len(v) > n:
            return f"dài quá {n} ký tự"
        return None

    return check


@dataclass(frozen=True)
class MethodSpec:
    """Mô tả một phương thức trong whitelist."""

    name: str
    required: dict[str, Validator] = field(default_factory=dict)
    optional: dict[str, Validator] = field(default_factory=dict)
    allowed_states: frozenset[State] | None = None   # None = không kiểm tra trạng thái ESP32
    uart_msg: MsgType | None = None                  # lệnh UART tương ứng (None = xử lý tại Pi)
    rate_limited: bool = True


# Whitelist đúng bảng 4.4 — KHÔNG thêm phương thức ở nơi khác
WHITELIST: dict[str, MethodSpec] = {
    "start": MethodSpec("start", required={"speed": _int_range(30, 100)},
                        allowed_states=frozenset({State.IDLE}), uart_msg=MsgType.START),
    "stop": MethodSpec("stop", uart_msg=MsgType.STOP),
    "emergencyStop": MethodSpec("emergencyStop", uart_msg=MsgType.ESTOP, rate_limited=False),
    "resetEstop": MethodSpec("resetEstop", allowed_states=frozenset({State.ESTOP, State.FAULT}),
                             uart_msg=MsgType.ESTOP_RESET),
    "resetCounters": MethodSpec("resetCounters"),
    "resetUsedCodes": MethodSpec("resetUsedCodes", required={"confirm": _str_equals("RESET")}),
    "otaCheck": MethodSpec("otaCheck", allowed_states=frozenset({State.IDLE}), uart_msg=MsgType.OTA_CHECK),
    "getStatus": MethodSpec("getStatus"),
}


# ---------------------------------------------------------------------------
# Token bucket
# ---------------------------------------------------------------------------
class TokenBucket:
    """Token bucket: dung lượng `capacity`, nạp lại đều `capacity / window_s` token mỗi giây.

    Với cấu hình mặc định 5 lệnh / 10 s: 5 lệnh liên tiếp được nhận, lệnh thứ 6 bị từ chối,
    sau 2 s lại có thêm 1 token. `clock` được tách ra để test không phải chờ thật.
    """

    def __init__(self, capacity: int, window_s: float, clock: Callable[[], float] = time.monotonic) -> None:
        self.capacity = max(1, int(capacity))
        self.rate = self.capacity / max(0.001, float(window_s))
        self._clock = clock
        self._tokens = float(self.capacity)
        self._last = clock()
        self._lock = threading.Lock()

    def try_consume(self, n: float = 1.0) -> bool:
        with self._lock:
            now = self._clock()
            elapsed = max(0.0, now - self._last)
            self._last = now
            self._tokens = min(float(self.capacity), self._tokens + elapsed * self.rate)
            if self._tokens >= n:
                self._tokens -= n
                return True
            return False

    @property
    def tokens(self) -> float:
        with self._lock:
            return self._tokens


# ---------------------------------------------------------------------------
# RpcHandler
# ---------------------------------------------------------------------------
class RpcHandler:
    """Bộ xử lý RPC whitelist (S-05). Thread-safe: mỗi RPC được xử lý tuần tự dưới một khóa."""

    def __init__(
        self,
        uart: Any,
        cfg: RpcCfg,
        demo_mode: bool,
        telemetry: TelemetrySink,
        callbacks: RpcCallbacks,
        metrics: MetricCounters = interfaces.metrics,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._uart = uart
        self._cfg = cfg
        self._demo_mode = bool(demo_mode)
        self._telemetry = telemetry
        self._cb = callbacks
        self._metrics = metrics
        self._bucket = TokenBucket(cfg.rate_limit.count, cfg.rate_limit.window_s, clock)
        self._timeout_ms = int(max(0.1, float(cfg.esp_timeout_s)) * 1000)
        self._lock = threading.RLock()

    # -- API công khai -------------------------------------------------------
    def handle(self, method: str, params: dict[str, Any] | None, request_id: str | None = None) -> dict[str, Any]:
        """Điểm vào duy nhất; chữ ký tương thích TelemetrySink.set_rpc_handler (method, params)."""
        params = params if isinstance(params, dict) else {}
        method = str(method) if method is not None else ""
        requested_by = self._requested_by(params)
        with self._lock:
            try:
                result = self._dispatch(method, params, requested_by)
            except Exception as e:  # fail-safe: không bao giờ ném ngoại lệ về phía MQTT
                log.exception("RPC %s: lỗi nội bộ", method)
                result = {"ok": False, "error": ERR_INTERNAL, "detail": type(e).__name__}
            self._record(method, params, requested_by, request_id, result)
        return result

    def __call__(self, method: str, params: dict[str, Any] | None, request_id: str | None = None) -> dict[str, Any]:
        return self.handle(method, params, request_id)

    # -- Luồng chính ---------------------------------------------------------
    def _dispatch(self, method: str, params: dict[str, Any], requested_by: str | None) -> dict[str, Any]:
        self._metrics.inc(F_RPC_TOTAL)

        # 1. DỪNG KHẨN CẤP: trước mọi thứ, không rate limit, không chặn vì tham số thừa
        if method == "emergencyStop":
            extra = set(params) - {REQUESTED_BY_KEY}
            if extra:
                log.warning("emergencyStop: bỏ qua tham số thừa %s", sorted(extra))
            return self._send_uart(MsgType.ESTOP, b"")

        # 2. Whitelist
        spec = WHITELIST.get(method)
        if spec is None:
            self._metrics.inc(F_RPC_REJECTED)
            self._security(SEC_RPC_REJECTED, SecurityLevel.MAJOR,
                           f"RPC ngoài whitelist: '{method[:60]}'", method=method[:60], requested_by=requested_by)
            return {"ok": False, "error": ERR_METHOD_NOT_ALLOWED}

        # 3. Rate limit
        if spec.rate_limited and not self._bucket.try_consume():
            self._security(SEC_RPC_RATE_LIMITED, SecurityLevel.WARNING,
                           f"Vượt giới hạn {self._cfg.rate_limit.count} lệnh/{self._cfg.rate_limit.window_s} s: {method}",
                           method=method, requested_by=requested_by)
            return {"ok": False, "error": ERR_RATE_LIMITED}

        # 4. Schema
        err = self._validate(spec, params)
        if err is not None:
            log.warning("RPC %s: BAD_PARAMS (%s)", method, err)
            return {"ok": False, "error": ERR_BAD_PARAMS, "detail": err}

        # 5. Thực thi
        return self._execute(spec, params)

    def _execute(self, spec: MethodSpec, params: dict[str, Any]) -> dict[str, Any]:
        if spec.name == "resetCounters":
            self._cb.reset_counters()
            return {"ok": True}
        if spec.name == "resetUsedCodes":
            if not self._demo_mode:
                return {"ok": False, "error": ERR_BAD_STATE, "detail": "chỉ cho phép khi device.demo_mode = true"}
            deleted = int(self._cb.reset_used_codes())
            return {"ok": True, "deleted": deleted}
        if spec.name == "getStatus":
            status = dict(self._cb.get_status() or {})
            status.update({"ok": True, "esp_state": self._esp_state_name(), "esp_link": self._esp_link()})
            return status

        # Lệnh đi xuống ESP32: kiểm tra trạng thái trước khi gửi
        if spec.allowed_states is not None:
            st = getattr(self._uart, "state", None)
            if st is None or st not in spec.allowed_states:
                return {"ok": False, "error": ERR_BAD_STATE,
                        "detail": f"ESP32 đang {self._esp_state_name()}, cần {'/'.join(s.name for s in sorted(spec.allowed_states))}"}
        assert spec.uart_msg is not None
        payload: Any = b""
        if spec.uart_msg == MsgType.START:
            payload = StartPayload(speed=int(params["speed"]))
        return self._send_uart(spec.uart_msg, payload)

    def _send_uart(self, msg: MsgType, payload: Any) -> dict[str, Any]:
        try:
            res = self._uart.send_command(msg, payload, timeout_ms=self._timeout_ms)
        except Exception as e:  # UartLink không được phép ném, nhưng phòng hờ
            log.exception("UART %s: ngoại lệ", msg.name)
            return {"ok": False, "error": ERR_ESP_TIMEOUT, "detail": type(e).__name__}
        if getattr(res, "ok", False):
            return {"ok": True, "esp_state": self._esp_state_name()}
        uart_err = str(getattr(res, "error", None) or "UNKNOWN")
        code = _UART_ERR_MAP.get(uart_err, ERR_ESP_TIMEOUT)
        log.warning("UART %s thất bại: %s -> %s", msg.name, uart_err, code)
        return {"ok": False, "error": code, "detail": uart_err}

    # -- Hỗ trợ ------------------------------------------------------------
    @staticmethod
    def _validate(spec: MethodSpec, params: dict[str, Any]) -> str | None:
        """Trả thông báo lỗi nếu params sai schema, None nếu hợp lệ."""
        allowed = set(spec.required) | set(spec.optional) | {REQUESTED_BY_KEY}
        unknown = sorted(k for k in params if k not in allowed)
        if unknown:
            return f"tham số lạ: {', '.join(str(k)[:30] for k in unknown)}"
        for name, check in spec.required.items():
            if name not in params:
                return f"thiếu tham số '{name}'"
            msg = check(params[name])
            if msg:
                return f"{name}: {msg}"
        for name, check in spec.optional.items():
            if name in params:
                msg = check(params[name])
                if msg:
                    return f"{name}: {msg}"
        if REQUESTED_BY_KEY in params:
            msg = _str_max(REQUESTED_BY_MAX_LEN)(params[REQUESTED_BY_KEY])
            if msg:
                return f"{REQUESTED_BY_KEY}: {msg}"
        return None

    @staticmethod
    def _requested_by(params: dict[str, Any]) -> str | None:
        v = params.get(REQUESTED_BY_KEY)
        if isinstance(v, str) and v:
            return v[:REQUESTED_BY_MAX_LEN]
        return None

    def _esp_state_name(self) -> str:
        st = getattr(self._uart, "state", None)
        if st is None:
            return "UNKNOWN"
        try:
            return State(st).name
        except ValueError:
            return str(st)

    def _esp_link(self) -> bool:
        return bool(getattr(self._uart, "connected", False))

    def _security(self, event_type: str, level: SecurityLevel, detail: str, **extra: Any) -> None:
        ev = SecurityEvent(event_type=event_type, level=level, detail=detail,
                           extra={k: v for k, v in extra.items() if v is not None})
        try:
            self._cb.on_security_event(ev)
        except Exception:
            log.exception("on_security_event lỗi")

    def _record(self, method: str, params: dict[str, Any], requested_by: str | None,
                request_id: str | None, result: dict[str, Any]) -> None:
        """Ghi rpc_audit và gửi rpc_log (S-05). Lỗi ở đây không làm hỏng kết quả trả về."""
        ts_ms = int(time.time() * 1000)
        safe_params = {k: v for k, v in params.items() if k != REQUESTED_BY_KEY}
        params_json = _json_line(safe_params)
        result_json = _json_line(result)
        try:
            self._cb.audit({
                "ts_ms": ts_ms,
                "request_id": request_id,
                "method": method[:60],
                "params_json": params_json,
                "requested_by": requested_by,
                "result_json": result_json,
                "ok": bool(result.get("ok", False)),
            })
        except Exception:
            log.exception("audit lỗi")
        rpc_log = _json_line({
            "ts": ts_ms,
            "request_id": request_id,
            "method": method[:60],
            "params": safe_params,
            "requested_by": requested_by,
            "result": result,
        })
        try:
            self._telemetry.send_telemetry({"rpc_log": rpc_log}, ts_ms=ts_ms)
        except Exception:
            log.exception("gửi rpc_log lỗi")
        log.info("rpc %s", rpc_log)


def _json_line(obj: Any) -> str:
    """JSON một dòng, không ném ngoại lệ với kiểu lạ."""
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), default=str)
