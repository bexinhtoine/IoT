"""Test S-05 / T-04 — RpcHandler: whitelist 4.4, schema, rate limit, trạng thái ESP32, audit, rpc_log.

Không import sorter.uart_link (đang được viết song song): StubUart có cùng API
(state, connected, send_command(msg_type, payload, timeout_ms) -> CommandResult).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import pytest

from sorter.config import RpcCfg, RpcRateLimit
from sorter.interfaces import (
    F_RPC_REJECTED,
    F_RPC_TOTAL,
    SEC_RPC_RATE_LIMITED,
    SEC_RPC_REJECTED,
    LogTelemetrySink,
    MetricCounters,
    SecurityEvent,
    SecurityLevel,
)
from sorter.protocol_gen import MsgType, StartPayload, State
from sorter.rpc_handler import (
    ERR_BAD_PARAMS,
    ERR_BAD_STATE,
    ERR_ESP_TIMEOUT,
    ERR_METHOD_NOT_ALLOWED,
    ERR_RATE_LIMITED,
    WHITELIST,
    RpcCallbacks,
    RpcHandler,
    TokenBucket,
)


# ---------------------------------------------------------------------------
# Stub
# ---------------------------------------------------------------------------
@dataclass
class CommandResult:
    ok: bool
    error: str | None = None
    nack_err: int | None = None
    reply: Any = None


@dataclass
class StubUart:
    """Giả lập UartLink: ghi lại lệnh đã gửi, trả kết quả cấu hình sẵn."""

    state: State | None = State.IDLE
    connected: bool = True
    result: CommandResult = field(default_factory=lambda: CommandResult(True))
    sent: list[tuple[MsgType, Any, int | None]] = field(default_factory=list)

    def send_command(self, msg_type: MsgType, payload: Any = b"", timeout_ms: int | None = None) -> CommandResult:
        self.sent.append((msg_type, payload, timeout_ms))
        return self.result


class FakeClock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, s: float) -> None:
        self.t += s


@dataclass
class Harness:
    uart: StubUart
    sink: LogTelemetrySink
    handler: RpcHandler
    metrics: MetricCounters
    clock: FakeClock
    events: list[SecurityEvent]
    audits: list[dict[str, Any]]
    calls: dict[str, int]


def make(demo_mode: bool = True, state: State | None = State.IDLE, result: CommandResult | None = None,
         count: int = 5, window_s: int = 10) -> Harness:
    uart = StubUart(state=state, result=result or CommandResult(True))
    sink = LogTelemetrySink()
    metrics = MetricCounters()
    clock = FakeClock()
    events: list[SecurityEvent] = []
    audits: list[dict[str, Any]] = []
    calls = {"reset_counters": 0, "reset_used_codes": 0, "get_status": 0}

    def reset_counters() -> None:
        calls["reset_counters"] += 1

    def reset_used_codes() -> int:
        calls["reset_used_codes"] += 1
        return 7

    def get_status() -> dict[str, Any]:
        calls["get_status"] += 1
        return {"cnt_total": 12, "conveyor_state": "IDLE"}

    cb = RpcCallbacks(reset_counters=reset_counters, reset_used_codes=reset_used_codes,
                      get_status=get_status, on_security_event=events.append, audit=audits.append)
    cfg = RpcCfg(rate_limit=RpcRateLimit(count=count, window_s=window_s), esp_timeout_s=2.0)
    h = RpcHandler(uart, cfg, demo_mode, sink, cb, metrics=metrics, clock=clock)
    return Harness(uart, sink, h, metrics, clock, events, audits, calls)


def last_rpc_log(sink: LogTelemetrySink) -> dict[str, Any]:
    rec = sink.sent[-1]
    assert set(rec) == {"rpc_log"}
    line = rec["rpc_log"]
    assert "\n" not in line
    return json.loads(line)


# ---------------------------------------------------------------------------
# Từng phương thức (nhánh thành công)
# ---------------------------------------------------------------------------
def test_whitelist_is_exactly_spec_4_4() -> None:
    assert set(WHITELIST) == {"start", "stop", "emergencyStop", "resetEstop", "resetCounters",
                              "resetUsedCodes", "otaCheck", "getStatus"}


def test_start_ok_sends_uart_start_with_speed() -> None:
    hz = make(state=State.IDLE)
    r = hz.handler.handle("start", {"speed": 60, "requestedBy": "admin@x"}, "1")
    assert r["ok"] is True
    assert r["esp_state"] == "IDLE"
    msg, payload, timeout = hz.uart.sent[-1]
    assert msg == MsgType.START and isinstance(payload, StartPayload) and payload.speed == 60
    assert timeout == 2000


def test_stop_ok() -> None:
    hz = make(state=State.RUNNING)
    assert hz.handler.handle("stop", {}, "2") == {"ok": True, "esp_state": "RUNNING"}
    assert hz.uart.sent[-1][0] == MsgType.STOP


def test_emergency_stop_ok_in_any_state() -> None:
    for st in (State.IDLE, State.RUNNING, State.SORTING, State.FAULT, None):
        hz = make(state=st)
        r = hz.handler.handle("emergencyStop", {}, "3")
        assert r["ok"] is True
        assert hz.uart.sent[-1][0] == MsgType.ESTOP


def test_reset_estop_ok_from_estop_and_fault() -> None:
    for st in (State.ESTOP, State.FAULT):
        hz = make(state=st)
        assert hz.handler.handle("resetEstop", {}, "4")["ok"] is True
        assert hz.uart.sent[-1][0] == MsgType.ESTOP_RESET


def test_reset_counters_calls_callback_no_uart() -> None:
    hz = make()
    assert hz.handler.handle("resetCounters", {}, "5") == {"ok": True}
    assert hz.calls["reset_counters"] == 1
    assert hz.uart.sent == []


def test_reset_used_codes_demo_mode_ok() -> None:
    hz = make(demo_mode=True)
    r = hz.handler.handle("resetUsedCodes", {"confirm": "RESET"}, "6")
    assert r == {"ok": True, "deleted": 7}
    assert hz.calls["reset_used_codes"] == 1


def test_ota_check_ok_when_idle() -> None:
    hz = make(state=State.IDLE)
    assert hz.handler.handle("otaCheck", {}, "7")["ok"] is True
    assert hz.uart.sent[-1][0] == MsgType.OTA_CHECK


def test_get_status_returns_state_link_and_callback_dict() -> None:
    hz = make(state=State.RUNNING)
    r = hz.handler.handle("getStatus", {}, "8")
    assert r["ok"] is True
    assert r["esp_state"] == "RUNNING" and r["esp_link"] is True
    assert r["cnt_total"] == 12 and r["conveyor_state"] == "IDLE"
    assert hz.uart.sent == []


def test_params_none_treated_as_empty() -> None:
    hz = make()
    assert hz.handler.handle("getStatus", None)["ok"] is True


def test_handler_is_callable_like_telemetry_sink_handler() -> None:
    hz = make()
    hz.sink.set_rpc_handler(hz.handler)
    assert hz.sink.call_rpc("getStatus")["ok"] is True


# ---------------------------------------------------------------------------
# BAD_PARAMS
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("params", [
    {},                                  # thiếu speed
    {"speed": "60"},                     # sai kiểu
    {"speed": 60.0},                     # float không nhận
    {"speed": True},                     # bool không phải int
    {"speed": 29},                       # dưới miền
    {"speed": 101},                      # trên miền
    {"speed": 60, "turbo": 1},           # khóa lạ
    {"speed": 60, "requestedBy": 5},     # requestedBy sai kiểu
    {"speed": 60, "requestedBy": "x" * 121},  # requestedBy quá dài
])
def test_start_bad_params(params: dict[str, Any]) -> None:
    hz = make(state=State.IDLE)
    r = hz.handler.handle("start", params, "10")
    assert r["ok"] is False and r["error"] == ERR_BAD_PARAMS
    assert hz.uart.sent == []


def test_stop_with_unknown_key_is_bad_params() -> None:
    hz = make()
    r = hz.handler.handle("stop", {"force": True}, "11")
    assert r == {"ok": False, "error": ERR_BAD_PARAMS, "detail": r["detail"]}
    assert hz.uart.sent == []


@pytest.mark.parametrize("params", [{}, {"confirm": "reset"}, {"confirm": "YES"}, {"confirm": 1}])
def test_reset_used_codes_bad_confirm(params: dict[str, Any]) -> None:
    hz = make(demo_mode=True)
    r = hz.handler.handle("resetUsedCodes", params, "12")
    assert r["error"] == ERR_BAD_PARAMS
    assert hz.calls["reset_used_codes"] == 0


# ---------------------------------------------------------------------------
# BAD_STATE
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("state", [State.RUNNING, State.ESTOP, State.FAULT, State.BOOT, None])
def test_start_bad_state(state: State | None) -> None:
    hz = make(state=state)
    r = hz.handler.handle("start", {"speed": 50}, "20")
    assert r["ok"] is False and r["error"] == ERR_BAD_STATE
    assert hz.uart.sent == []


@pytest.mark.parametrize("state", [State.IDLE, State.RUNNING, None])
def test_reset_estop_bad_state(state: State | None) -> None:
    hz = make(state=state)
    assert hz.handler.handle("resetEstop", {}, "21")["error"] == ERR_BAD_STATE
    assert hz.uart.sent == []


def test_ota_check_bad_state_when_running() -> None:
    hz = make(state=State.RUNNING)
    assert hz.handler.handle("otaCheck", {}, "22")["error"] == ERR_BAD_STATE


def test_reset_used_codes_bad_state_when_not_demo() -> None:
    hz = make(demo_mode=False)
    r = hz.handler.handle("resetUsedCodes", {"confirm": "RESET"}, "23")
    assert r["ok"] is False and r["error"] == ERR_BAD_STATE
    assert hz.calls["reset_used_codes"] == 0


def test_nack_bad_state_from_esp_maps_to_bad_state() -> None:
    hz = make(state=State.RUNNING, result=CommandResult(False, "NACK_BAD_STATE", nack_err=3))
    r = hz.handler.handle("stop", {}, "24")
    assert r["error"] == ERR_BAD_STATE and r["detail"] == "NACK_BAD_STATE"


# ---------------------------------------------------------------------------
# ESP_TIMEOUT
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("uart_err", ["TIMEOUT", "LINK_DOWN", "SOMETHING_ELSE"])
def test_uart_failure_maps_to_esp_timeout(uart_err: str) -> None:
    hz = make(state=State.IDLE, result=CommandResult(False, uart_err))
    r = hz.handler.handle("start", {"speed": 40}, "30")
    assert r["ok"] is False and r["error"] == ERR_ESP_TIMEOUT
    assert r["detail"] == uart_err


def test_uart_exception_is_contained() -> None:
    hz = make()

    def boom(*a: Any, **k: Any) -> CommandResult:
        raise OSError("serial gone")

    hz.uart.send_command = boom  # type: ignore[method-assign]
    r = hz.handler.handle("stop", {}, "31")
    assert r["ok"] is False and r["error"] == ERR_ESP_TIMEOUT


def test_callback_exception_is_contained() -> None:
    hz = make()

    def boom() -> None:
        raise RuntimeError("db locked")

    hz.handler._cb.reset_counters = boom  # type: ignore[assignment]
    r = hz.handler.handle("resetCounters", {}, "32")
    assert r["ok"] is False and r["error"] == "INTERNAL_ERROR"
    assert hz.audits[-1]["ok"] is False


# ---------------------------------------------------------------------------
# METHOD_NOT_ALLOWED + sự kiện bảo mật
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("method", ["reboot", "setParam", "START", "", "start;rm -rf /", "__class__"])
def test_unknown_method_rejected_with_security_event(method: str) -> None:
    hz = make()
    r = hz.handler.handle(method, {"x": 1}, "40")
    assert r == {"ok": False, "error": ERR_METHOD_NOT_ALLOWED}
    assert hz.uart.sent == []
    assert len(hz.events) == 1
    ev = hz.events[0]
    assert ev.event_type == SEC_RPC_REJECTED and ev.level == SecurityLevel.MAJOR
    assert ev.to_telemetry()["security_alert"] is True
    snap = hz.metrics.snapshot()
    assert snap[F_RPC_TOTAL] == 1 and snap[F_RPC_REJECTED] == 1


def test_unknown_method_does_not_consume_rate_limit_tokens() -> None:
    hz = make()
    for _ in range(10):
        hz.handler.handle("bogus", {}, "41")
    assert hz.handler.handle("getStatus", {}, "42")["ok"] is True


# ---------------------------------------------------------------------------
# RATE_LIMITED
# ---------------------------------------------------------------------------
def test_rate_limit_5_per_10s_then_refill() -> None:
    hz = make()
    for i in range(5):
        assert hz.handler.handle("getStatus", {}, str(i))["ok"] is True
    r = hz.handler.handle("getStatus", {}, "6")
    assert r == {"ok": False, "error": ERR_RATE_LIMITED}
    assert len(hz.events) == 1
    assert hz.events[0].event_type == SEC_RPC_RATE_LIMITED
    assert hz.events[0].level == SecurityLevel.WARNING
    assert hz.metrics.snapshot()[F_RPC_REJECTED] == 0  # rate limit không tính là "rejected" (A-01 đếm riêng)
    assert hz.metrics.snapshot()[F_RPC_TOTAL] == 6
    # sau 2 s có 1 token (5/10 s), sau 10 s đầy lại
    hz.clock.advance(2.0)
    assert hz.handler.handle("getStatus", {}, "7")["ok"] is True
    assert hz.handler.handle("getStatus", {}, "8")["error"] == ERR_RATE_LIMITED
    hz.clock.advance(10.0)
    for i in range(5):
        assert hz.handler.handle("getStatus", {}, str(i))["ok"] is True
    assert hz.handler.handle("getStatus", {}, "x")["error"] == ERR_RATE_LIMITED


def test_emergency_stop_bypasses_rate_limit() -> None:
    hz = make(state=State.RUNNING)
    for i in range(5):
        hz.handler.handle("stop", {}, str(i))
    assert hz.handler.handle("stop", {}, "6")["error"] == ERR_RATE_LIMITED
    for _ in range(3):
        assert hz.handler.handle("emergencyStop", {}, "e")["ok"] is True
    assert sum(1 for m, _, _ in hz.uart.sent if m == MsgType.ESTOP) == 3
    # E-stop không tiêu tốn token: lệnh thường vẫn bị giới hạn
    assert hz.handler.handle("stop", {}, "7")["error"] == ERR_RATE_LIMITED


def test_emergency_stop_with_extra_params_still_stops() -> None:
    hz = make()
    assert hz.handler.handle("emergencyStop", {"foo": "bar", "requestedBy": "a"}, "e")["ok"] is True
    assert hz.uart.sent[-1][0] == MsgType.ESTOP


def test_rate_limit_uses_cfg() -> None:
    hz = make(count=2, window_s=10)
    assert hz.handler.handle("getStatus", {})["ok"]
    assert hz.handler.handle("getStatus", {})["ok"]
    assert hz.handler.handle("getStatus", {})["error"] == ERR_RATE_LIMITED


def test_token_bucket_direct() -> None:
    clk = FakeClock()
    b = TokenBucket(5, 10, clk)
    assert all(b.try_consume() for _ in range(5))
    assert not b.try_consume()
    clk.advance(4.0)  # +2 token
    assert b.try_consume() and b.try_consume() and not b.try_consume()
    clk.advance(100.0)
    assert b.tokens == pytest.approx(0.0)  # chưa tính lại cho tới lần consume kế
    assert b.try_consume()
    assert b.tokens == pytest.approx(4.0)  # đầy (5) rồi trừ 1


# ---------------------------------------------------------------------------
# Audit + rpc_log (S-05)
# ---------------------------------------------------------------------------
def test_audit_called_for_every_rpc_including_rejected() -> None:
    hz = make(state=State.IDLE)
    hz.handler.handle("start", {"speed": 70, "requestedBy": "admin@example.com"}, "req-1")
    hz.handler.handle("hack", {}, "req-2")
    hz.handler.handle("start", {"speed": 5}, None)
    assert len(hz.audits) == 3
    a = hz.audits[0]
    assert set(a) == {"ts_ms", "request_id", "method", "params_json", "requested_by", "result_json", "ok"}
    assert a["request_id"] == "req-1" and a["method"] == "start" and a["ok"] is True
    assert a["requested_by"] == "admin@example.com"
    assert json.loads(a["params_json"]) == {"speed": 70}  # requestedBy tách ra cột riêng
    assert json.loads(a["result_json"])["ok"] is True
    assert hz.audits[1]["ok"] is False and hz.audits[1]["method"] == "hack"
    assert hz.audits[2]["request_id"] is None and hz.audits[2]["requested_by"] is None
    assert isinstance(a["ts_ms"], int) and a["ts_ms"] > 1_600_000_000_000


def test_rpc_log_telemetry_sent_as_single_line_json() -> None:
    hz = make(state=State.IDLE)
    hz.handler.handle("start", {"speed": 70, "requestedBy": "admin@example.com"}, "req-9")
    rec = last_rpc_log(hz.sink)
    assert rec["method"] == "start" and rec["request_id"] == "req-9"
    assert rec["params"] == {"speed": 70}
    assert rec["requested_by"] == "admin@example.com"
    assert rec["result"]["ok"] is True
    assert isinstance(rec["ts"], int)
    hz.handler.handle("nope", {}, "req-10")
    rec = last_rpc_log(hz.sink)
    assert rec["result"] == {"ok": False, "error": ERR_METHOD_NOT_ALLOWED}


def test_metrics_total_counts_every_rpc() -> None:
    hz = make()
    hz.handler.handle("getStatus", {})
    hz.handler.handle("emergencyStop", {})
    hz.handler.handle("zzz", {})
    assert hz.metrics.snapshot()[F_RPC_TOTAL] == 3


def test_thread_safety_many_concurrent_calls() -> None:
    import threading

    hz = make(count=1000, window_s=1)
    errors: list[Exception] = []

    def worker() -> None:
        try:
            for _ in range(50):
                hz.handler.handle("getStatus", {})
                hz.handler.handle("emergencyStop", {})
        except Exception as e:  # pragma: no cover
            errors.append(e)

    ts = [threading.Thread(target=worker) for _ in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert not errors
    assert hz.metrics.snapshot()[F_RPC_TOTAL] == 800
    assert len(hz.audits) == 800 and len(hz.sink.sent) == 800
