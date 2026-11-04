"""P-07 / P-14 — Test UartLink với FakeEsp32: ACK, NACK BAD_STATE/BAD_PARAM, gửi lại khi mất ACK,
SEQ lặp không thực hiện 2 lần, mất link, PING/PONG, decision timeout failsafe, CRC."""
from __future__ import annotations

import time

import pytest

from sorter.config import UartCfg
from sorter.fake_esp32 import FakeEsp32
from sorter.interfaces import F_UART_CRC_ERRORS, metrics
from sorter.protocol_gen import (
    EvtFaultPayload,
    EvtPkgDetectedPayload,
    FaultCode,
    MsgType,
    NackErr,
    SetParamPayload,
    SortPayload,
    SortReason,
    SortTarget,
    StartPayload,
    State,
)
from sorter.uart_codec import encode_frame
from sorter.uart_link import UartLink


def make_pair(ping_ms: int = 100, retries: int = 3, ack_ms: int = 150, **fake_kw):
    fake_kw.setdefault("status_period_s", 0)
    fake = FakeEsp32(time_scale=0.01, **fake_kw)
    fake.start()
    link = UartLink(UartCfg(port="sim", ack_timeout_ms=ack_ms, retries=retries, ping_interval_ms=ping_ms), transport=fake.pi_transport())
    link.start()
    return link, fake


def wait_for(pred, timeout=2.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.005)
    return pred()


def next_event_of(link: UartLink, mt: MsgType, timeout=2.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        f = link.next_event(max(0.01, end - time.monotonic()))
        if f is not None and f.type == mt:
            return f
    return None


@pytest.fixture
def pair():
    link, fake = make_pair()
    yield link, fake
    link.stop()
    fake.stop()


def test_ping_pong_state_and_connected(pair):
    link, fake = pair
    r = link.ping()
    assert r.ok and link.state == State.IDLE
    assert wait_for(lambda: link.connected)
    st = link.get_status()
    assert st is not None and st.state == State.IDLE and link.last_status is st


def test_sim_port_requires_transport():
    with pytest.raises(ValueError):
        UartLink(UartCfg(port="sim"))


def test_start_ack_and_bad_state(pair):
    link, fake = pair
    r = link.send_command(MsgType.SORT, SortPayload(1, 0, 1))
    assert not r.ok and r.error == "NACK_BAD_STATE" and r.nack_err == NackErr.BAD_STATE
    r = link.send_command(MsgType.START, StartPayload(60))
    assert r.ok and r.attempts == 1
    assert wait_for(lambda: fake.state == State.RUNNING)
    assert link.send_command(MsgType.START, StartPayload(60)).error == "NACK_BAD_STATE"  # đã RUNNING
    assert link.send_command(MsgType.SET_PARAM, SetParamPayload(1, 50)).error == "NACK_BAD_STATE"


def test_set_param_range(pair):
    link, fake = pair
    assert link.send_command(MsgType.SET_PARAM, SetParamPayload(1, 50)).ok
    assert fake.params[1] == 50
    r = link.send_command(MsgType.SET_PARAM, SetParamPayload(1, 20))  # belt_speed < 30
    assert r.error == "NACK_BAD_PARAM"
    assert link.send_command(MsgType.SET_PARAM, SetParamPayload(99, 1)).error == "NACK_BAD_PARAM"
    assert fake.params[1] == 50


def test_validate_fields_before_send(pair):
    link, _ = pair
    with pytest.raises(ValueError):
        link.send_command(MsgType.START, StartPayload(200))
    with pytest.raises(ValueError):
        link.send_command(MsgType.SORT, b"\x01")  # sai độ dài


def test_retry_on_lost_ack_and_no_double_execute(pair):
    link, fake = pair
    assert link.send_command(MsgType.START, StartPayload(60)).ok
    assert fake.trigger_sensor()
    ev = next_event_of(link, MsgType.EVT_PKG_DETECTED)
    assert ev is not None and EvtPkgDetectedPayload.unpack(ev.payload).pkg_seq == 1
    fake.drop_next_acks(2)
    r = link.send_command(MsgType.SORT, SortPayload(int(SortTarget.A), int(SortReason.OK), 1))
    assert r.ok and r.attempts == 3
    assert link.stats.retries == 2
    assert len(fake.sent_sorts) == 1  # SEQ lặp chỉ được ACK lại, không gạt hai lần
    assert fake.stats.dup_cmds >= 2
    done = next_event_of(link, MsgType.EVT_SORT_DONE)
    assert done is not None
    assert wait_for(lambda: fake.state == State.RUNNING)


def test_timeout_when_all_acks_lost():
    link, fake = make_pair(retries=1, ack_ms=60)
    try:
        fake.drop_next_acks(10)
        r = link.send_command(MsgType.STOP)
        assert not r.ok and r.error == "TIMEOUT" and r.attempts == 2
        assert link.stats.timeouts == 1
    finally:
        link.stop()
        fake.stop()


def test_event_dedupe_on_retransmit(pair):
    link, fake = pair
    # ESP32 gửi lại sự kiện cùng SEQ (mất ACK) -> Pi chỉ ACK lại, không đẩy sự kiện lần 2
    frame = encode_frame(200, MsgType.EVT_PKG_DETECTED, EvtPkgDetectedPayload(77))
    esp = fake.loopback.esp_side()
    esp.write(frame)
    esp.write(frame)
    ev = next_event_of(link, MsgType.EVT_PKG_DETECTED)
    assert ev is not None and ev.seq == 200
    assert next_event_of(link, MsgType.EVT_PKG_DETECTED, timeout=0.3) is None
    assert wait_for(lambda: link.stats.dup_events == 1)


def test_crc_error_counted(pair):
    link, fake = pair
    before = metrics.snapshot()[F_UART_CRC_ERRORS]
    fake.inject_bad_crc()
    fake.inject_garbage()
    assert wait_for(lambda: link.crc_errors >= 1)
    assert metrics.snapshot()[F_UART_CRC_ERRORS] >= before + 1
    assert link.ping().ok  # vẫn đồng bộ lại được


def test_unknown_type_and_bad_len_nack(pair):
    link, fake = pair
    # send_command() từ chối TYPE lạ / LEN sai ngay ở Pi; ép gửi khung thô để kiểm tra ESP32 giả NACK
    with pytest.raises(ValueError):
        link.send_command(0x7F, b"")
    link._write(encode_frame(249, 0x7F, b""))
    assert wait_for(lambda: fake.stats.nacks_sent >= 1)
    link._write(encode_frame(250, MsgType.START, b"\x3c\x00"))
    assert wait_for(lambda: fake.stats.nacks_sent >= 2)
    assert link.ping().ok  # liên kết vẫn hoạt động


def test_link_lost_pi_side():
    link, fake = make_pair(ping_ms=50)
    link.link_timeout_ms = 300
    try:
        assert link.ping().ok
        assert wait_for(lambda: link.connected)
        fake.loopback.disconnect()
        assert wait_for(lambda: not link.connected, timeout=1.5)
        r = link.send_command(MsgType.PING, retries=0, timeout_ms=50)
        assert r.error == "TIMEOUT"
        fake.loopback.reconnect()
        assert wait_for(lambda: link.ping().ok, timeout=1.0)
    finally:
        link.stop()
        fake.stop()


def test_fake_link_watchdog_fault():
    # E-09: ESP32 giả vào FAULT(LINK_LOST) khi RUNNING mà không có khung hợp lệ
    link, fake = make_pair(ping_ms=0, link_timeout_s=0.2)
    try:
        assert link.send_command(MsgType.START, StartPayload(60)).ok
        ev = next_event_of(link, MsgType.EVT_FAULT, timeout=1.5)
        assert ev is not None and EvtFaultPayload.unpack(ev.payload).code == FaultCode.LINK_LOST
        assert fake.state == State.FAULT
        assert link.send_command(MsgType.START, StartPayload(60)).error == "NACK_BAD_STATE"
        assert link.send_command(MsgType.ESTOP_RESET).ok
        assert wait_for(lambda: fake.state == State.IDLE)
    finally:
        link.stop()
        fake.stop()


def test_decision_timeout_failsafe(pair):
    # E-05: không gửi SORT -> ESP32 giả tự loại + EVT_FAULT(DECISION_TIMEOUT) rồi về RUNNING
    link, fake = pair
    assert link.send_command(MsgType.START, StartPayload(60)).ok
    assert fake.trigger_sensor()
    ev = next_event_of(link, MsgType.EVT_FAULT, timeout=1.5)
    assert ev is not None and EvtFaultPayload.unpack(ev.payload).code == FaultCode.DECISION_TIMEOUT
    assert fake.stats.failsafe_rejects == 1
    assert wait_for(lambda: fake.state == State.RUNNING)
    assert fake.sent_sorts == []
    # SORT muộn bị NACK BAD_STATE (không còn DETECTED)
    assert link.send_command(MsgType.SORT, SortPayload(1, 0, 1)).error == "NACK_BAD_STATE"


def test_estop_physical_and_remote(pair):
    link, fake = pair
    assert link.send_command(MsgType.START, StartPayload(60)).ok
    fake.press_estop()
    ev = next_event_of(link, MsgType.EVT_ESTOP)
    assert ev is not None and ev.payload[0] == 1 and link.state == State.ESTOP
    assert link.send_command(MsgType.ESTOP_RESET).error == "NACK_BAD_STATE"  # nút chưa nhả
    fake.release_estop()
    assert link.send_command(MsgType.ESTOP_RESET).ok
    assert next_event_of(link, MsgType.EVT_ESTOP_CLEARED) is not None
    assert wait_for(lambda: fake.state == State.IDLE)
    assert link.send_command(MsgType.ESTOP).ok  # từ xa, mọi trạng thái
    ev = next_event_of(link, MsgType.EVT_ESTOP)
    assert ev is not None and ev.payload[0] == 2


def test_sensor_only_when_running(pair):
    link, fake = pair
    assert not fake.trigger_sensor()
    assert link.send_command(MsgType.START, StartPayload(60)).ok
    assert fake.trigger_sensor()
    assert not fake.trigger_sensor()  # đã DETECTED


def test_seq_wraps(pair):
    link, _ = pair
    link._seq = 254
    seqs = [link.ping().seq for _ in range(4)]
    assert seqs == [254, 255, 0, 1]
