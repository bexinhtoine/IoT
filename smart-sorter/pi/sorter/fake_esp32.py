"""P-14 — FakeEsp32: giả lập máy trạng thái E-01 của firmware ESP32 để chạy/test trên laptop.

BOOT -> IDLE -> RUNNING -> DETECTED -> SORTING -> RUNNING; thêm ESTOP, FAULT, OTA.
- Giao tiếp qua LoopbackTransport (cặp transport trong bộ nhớ, thread-safe); pi_side() đưa cho UartLink.
- Xử lý mọi lệnh 4.2: whitelist trạng thái (NACK BAD_STATE), miền tham số (NACK BAD_PARAM),
  TYPE lạ (NACK UNKNOWN_TYPE), LEN sai (NACK BAD_LEN), sai CRC thì đếm, chống lặp SEQ (SEQ trùng chỉ ACK lại).
- E-05: ở DETECTED quá t_decision_timeout không có SORT -> tự REJECT + EVT_FAULT(DECISION_TIMEOUT) -> RUNNING.
- E-09: RUNNING/DETECTED mà `link_timeout_s` không có khung hợp lệ -> FAULT + EVT_FAULT(LINK_LOST).
- Mọi EVT_* gửi lại tối đa 3 lần nếu không nhận ACK.
- `time_scale` rút ngắn t_clear_*, t_rearm, t_decision_timeout (mặc định 0.01 để test nhanh).
- Hàm giả: trigger_sensor(), press_estop()/release_estop(), inject_bad_crc(), drop_next_acks(n).
- Tùy chọn open_pty() trên Linux để tools dùng với cổng thật (không bắt buộc).
"""
from __future__ import annotations

import logging
import os
import threading
import time
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from typing import Any

from .protocol_gen import (
    ACK_TIMEOUT_MS,
    ALLOWED_STATES,
    EXPECTED_LEN,
    LINK_TIMEOUT_MS,
    MAX_RETRIES,
    PARAMS,
    PI2ESP,
    AckPayload,
    EstopSource,
    EvtEstopPayload,
    EvtFaultPayload,
    EvtOtaStatusPayload,
    EvtPkgDetectedPayload,
    EvtSortDonePayload,
    FaultCode,
    MsgType,
    NackErr,
    NackPayload,
    OtaReason,
    OtaStage,
    PongPayload,
    SetParamPayload,
    SortPayload,
    SortReason,
    SortTarget,
    StartPayload,
    State,
    StatusPayload,
)
from .uart_codec import SOF, Frame, FrameDecoder, encode_frame
from .uart_link import Transport

log = logging.getLogger(__name__)

RESET_REASON_POWERON = 1


# ---------------------------------------------------------------------------
# Transport trong bộ nhớ
# ---------------------------------------------------------------------------
class LoopbackEndpoint(Transport):
    """Một đầu của cặp loopback. write() đẩy sang bộ đệm của đầu kia."""

    def __init__(self, name: str) -> None:
        self.name = name
        self._buf = bytearray()
        self._cond = threading.Condition()
        self.peer: LoopbackEndpoint | None = None
        self.closed = False
        self.connected = True  # False = "đứt dây": write bị bỏ

    def _deliver(self, data: bytes) -> None:
        with self._cond:
            self._buf += data
            self._cond.notify_all()

    def write(self, data: bytes) -> None:
        if self.closed:
            raise OSError(f"loopback {self.name} đã đóng")
        peer = self.peer
        if peer is None or peer.closed or not self.connected or not peer.connected:
            return  # mất kết nối: byte rơi
        peer._deliver(bytes(data))

    def read(self, max_bytes: int, timeout_s: float) -> bytes:
        with self._cond:
            if not self._buf:
                self._cond.wait(timeout_s)
            if not self._buf:
                return b""
            n = min(max_bytes, len(self._buf))
            out = bytes(self._buf[:n])
            del self._buf[:n]
            return out

    def close(self) -> None:
        with self._cond:
            self.closed = True
            self._cond.notify_all()


class LoopbackTransport:
    """Cặp endpoint: pi_side() cho UartLink, esp_side() cho FakeEsp32."""

    def __init__(self) -> None:
        self._pi = LoopbackEndpoint("pi")
        self._esp = LoopbackEndpoint("esp")
        self._pi.peer = self._esp
        self._esp.peer = self._pi

    def pi_side(self) -> LoopbackEndpoint:
        return self._pi

    def esp_side(self) -> LoopbackEndpoint:
        return self._esp

    def disconnect(self) -> None:
        """Mô phỏng đứt dây hai chiều."""
        self._pi.connected = False
        self._esp.connected = False

    def reconnect(self) -> None:
        self._pi.connected = True
        self._esp.connected = True


class PtyTransport(Transport):  # pragma: no cover - chỉ Linux
    """Đầu master của một pty (os.openpty) để tool nối với cổng giả /dev/pts/N."""

    def __init__(self, master_fd: int) -> None:
        self._fd = master_fd

    def read(self, max_bytes: int, timeout_s: float) -> bytes:
        import select

        r, _, _ = select.select([self._fd], [], [], timeout_s)
        if not r:
            return b""
        return os.read(self._fd, max_bytes)

    def write(self, data: bytes) -> None:
        os.write(self._fd, data)

    def close(self) -> None:
        try:
            os.close(self._fd)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# FakeEsp32
# ---------------------------------------------------------------------------
@dataclass
class _PendingEvt:
    seq: int
    frame: bytes
    attempts: int = 1
    deadline: float = 0.0
    name: str = ""


@dataclass
class FakeStats:
    rx_frames: int = 0
    tx_frames: int = 0
    crc_errors: int = 0
    nacks_sent: int = 0
    dup_cmds: int = 0
    evt_retx: int = 0
    evt_dropped: int = 0
    failsafe_rejects: int = 0
    link_lost: int = 0
    events_sent: list[str] = field(default_factory=list)


class FakeEsp32:
    """Giả lập ESP32 (E-01, E-05, E-09, E-10, E-11, E-12)."""

    def __init__(
        self,
        params: dict[int, int] | None = None,
        time_scale: float = 0.01,
        transport: LoopbackTransport | None = None,
        link_timeout_s: float = LINK_TIMEOUT_MS / 1000.0,
        status_period_s: float = 1.0,
        fw_version_code: int = 10000,
        evt_ack_timeout_s: float = ACK_TIMEOUT_MS / 1000.0,
    ) -> None:
        self.params: dict[int, int] = {pid: p.default for pid, p in PARAMS.items()}
        if params:
            for k, v in params.items():
                self.params[int(k)] = int(v)
        self.time_scale = float(time_scale)
        self.link_timeout_s = float(link_timeout_s)
        self.status_period_s = float(status_period_s)
        self.fw_version_code = int(fw_version_code)
        self.evt_ack_timeout_s = float(evt_ack_timeout_s)
        self.loopback = transport or LoopbackTransport()
        self._tr: Transport = self.loopback.esp_side()
        self._lock = threading.RLock()
        self._state = State.BOOT
        self._decoder = FrameDecoder()
        self._esp_seq = 0
        self._recent_cmds: "OrderedDict[tuple[int, int], bytes]" = OrderedDict()
        self._pending_evts: "OrderedDict[int, _PendingEvt]" = OrderedDict()
        self._drop_acks = 0
        self._estop_pressed = False
        self._pkg_seq = 0
        self._detected_at = 0.0
        self._sort_done_at = 0.0
        self._sort_done_payload: EvtSortDonePayload | None = None
        self._last_rx = time.monotonic()
        self._last_status = 0.0
        self._boot_mono = time.monotonic()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.sent_sorts: list[SortPayload] = []
        self.stats = FakeStats()
        self.state_history: deque[State] = deque(maxlen=64)
        self.pkg_count = 0
        self.belt_speed = 0

    # ------------------------------------------------------------------ vòng đời
    def pi_transport(self) -> Transport:
        """Transport đưa cho UartLink(cfg, transport=...)."""
        return self.loopback.pi_side()

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        with self._lock:
            self._set_state(State.IDLE)  # BOOT -> IDLE ngay (không mô phỏng thời gian khởi động)
            self._last_rx = time.monotonic()
        self._thread = threading.Thread(target=self._run, name="fake-esp32", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        t = self._thread
        if t is not None and t.is_alive():
            t.join(timeout=2.0)
        self._thread = None
        try:
            self._tr.close()
        except Exception:
            pass

    def open_pty(self) -> str | None:  # pragma: no cover - chỉ Linux
        """Mở pty; trả tên cổng slave (/dev/pts/N) để tool mở bằng pyserial. None nếu không hỗ trợ."""
        if not hasattr(os, "openpty"):
            return None
        try:
            master, slave = os.openpty()
            self._tr = PtyTransport(master)
            return os.ttyname(slave)
        except Exception as e:
            log.warning("Không mở được pty: %s", e)
            return None

    # ------------------------------------------------------------------ thuộc tính
    @property
    def state(self) -> State:
        with self._lock:
            return self._state

    @property
    def crc_errors(self) -> int:
        return self._decoder.stats.crc_errors

    @property
    def estop_pressed(self) -> bool:
        return self._estop_pressed

    def _set_state(self, s: State) -> None:
        if s != self._state:
            log.debug("FakeEsp32: %s -> %s", self._state.name, s.name)
            self._state = s
            self.state_history.append(s)

    def _scaled(self, param_id: int) -> float:
        return self.params[param_id] / 1000.0 * self.time_scale

    # ------------------------------------------------------------------ cảm biến / nút giả
    def trigger_sensor(self) -> bool:
        """Cảm biến thấy hàng: chỉ khi RUNNING -> DETECTED + EVT_PKG_DETECTED(pkg_seq)."""
        with self._lock:
            if self._state != State.RUNNING:
                return False
            self._pkg_seq = (self._pkg_seq + 1) & 0xFFFF
            self._set_state(State.DETECTED)
            self._detected_at = time.monotonic()
            self._send_event(MsgType.EVT_PKG_DETECTED, EvtPkgDetectedPayload(self._pkg_seq))
            return True

    @property
    def current_pkg_seq(self) -> int:
        return self._pkg_seq

    def press_estop(self) -> None:
        with self._lock:
            self._estop_pressed = True
            self._enter_estop(EstopSource.PHYSICAL)

    def release_estop(self) -> None:
        with self._lock:
            self._estop_pressed = False

    def inject_bad_crc(self) -> None:
        """Gửi một khung có CRC sai tới Pi (Pi phải bỏ và đếm)."""
        good = encode_frame(self._esp_seq, MsgType.PONG, PongPayload(int(self._state)))
        bad = bytearray(good)
        bad[-1] ^= 0xFF
        self._write(bytes(bad))

    def inject_garbage(self, data: bytes = b"\x00\x12\xaa\x34") -> None:
        self._write(data)

    def drop_next_acks(self, n: int) -> None:
        """Không gửi n ACK/NACK tiếp theo (lệnh vẫn được thực hiện) để test gửi lại + chống lặp SEQ."""
        with self._lock:
            self._drop_acks = int(n)

    # ------------------------------------------------------------------ gửi
    def _write(self, data: bytes) -> None:
        try:
            self._tr.write(data)
            self.stats.tx_frames += 1
        except Exception as e:
            log.debug("FakeEsp32 write lỗi: %s", e)

    def _next_seq(self) -> int:
        s = self._esp_seq
        self._esp_seq = (self._esp_seq + 1) & 0xFF
        return s

    def _send_event(self, mt: MsgType, payload: Any) -> None:
        seq = self._next_seq()
        frame = encode_frame(seq, mt, payload)
        self._pending_evts[seq] = _PendingEvt(seq, frame, 1, time.monotonic() + self.evt_ack_timeout_s, mt.name)
        self.stats.events_sent.append(mt.name)
        self._write(frame)

    def _send_reply(self, mt: MsgType, payload: Any, key: tuple[int, int] | None) -> None:
        frame = encode_frame(self._next_seq(), mt, payload)
        if key is not None:
            self._recent_cmds[key] = frame
            while len(self._recent_cmds) > 16:
                self._recent_cmds.popitem(last=False)
        if mt in (MsgType.ACK, MsgType.NACK) and self._drop_acks > 0:
            self._drop_acks -= 1
            log.debug("FakeEsp32: cố ý bỏ %s (còn %d)", mt.name, self._drop_acks)
            return
        if mt == MsgType.NACK:
            self.stats.nacks_sent += 1
        self._write(frame)

    def _status_payload(self) -> StatusPayload:
        return StatusPayload(
            state=int(self._state),
            fw_version_code=self.fw_version_code,
            uptime_s=int(time.monotonic() - self._boot_mono),
            pkg_count=self.pkg_count & 0xFFFF,
            crc_err=self._decoder.stats.crc_errors & 0xFFFF,
            reset_reason=RESET_REASON_POWERON,
        )

    def _enter_estop(self, source: EstopSource) -> None:
        self._set_state(State.ESTOP)
        self._sort_done_payload = None
        self._send_event(MsgType.EVT_ESTOP, EvtEstopPayload(int(source)))

    def _enter_fault(self, code: FaultCode) -> None:
        self._set_state(State.FAULT)
        self._sort_done_payload = None
        self._send_event(MsgType.EVT_FAULT, EvtFaultPayload(int(code)))

    # ------------------------------------------------------------------ vòng chạy
    def _run(self) -> None:
        last_crc = 0
        while not self._stop.is_set():
            try:
                data = self._tr.read(256, 0.005)
            except Exception:
                data = b""
            if data:
                frames = self._decoder.feed(data)
                if self._decoder.stats.crc_errors != last_crc:
                    last_crc = self._decoder.stats.crc_errors
                    self.stats.crc_errors = last_crc
                with self._lock:
                    for f in frames:
                        try:
                            self._handle(f)
                        except Exception as e:  # pragma: no cover
                            log.exception("FakeEsp32 lỗi xử lý khung: %s", e)
            with self._lock:
                self._tick(time.monotonic())

    def _tick(self, now: float) -> None:
        # Gửi lại sự kiện chưa được ACK (tối đa MAX_RETRIES lần)
        for seq, ev in list(self._pending_evts.items()):
            if now >= ev.deadline:
                if ev.attempts >= 1 + MAX_RETRIES:
                    log.warning("FakeEsp32: bỏ sự kiện %s seq=%d sau %d lần", ev.name, seq, ev.attempts)
                    self.stats.evt_dropped += 1
                    del self._pending_evts[seq]
                    continue
                ev.attempts += 1
                ev.deadline = now + self.evt_ack_timeout_s
                self.stats.evt_retx += 1
                self._write(ev.frame)
        # E-05: quá hạn quyết định
        if self._state == State.DETECTED and now - self._detected_at >= self._scaled(10):
            log.warning("FakeEsp32: DECISION_TIMEOUT pkg_seq=%d -> tự REJECT (FAILSAFE)", self._pkg_seq)
            self.stats.failsafe_rejects += 1
            self._send_event(MsgType.EVT_FAULT, EvtFaultPayload(int(FaultCode.DECISION_TIMEOUT)))
            self._set_state(State.SORTING)
            self._sort_done_at = now + self._scaled(8)
            self._sort_done_payload = EvtSortDonePayload(self._pkg_seq, int(SortTarget.REJECT))
        # Xong gạt hàng
        if self._state == State.SORTING and now >= self._sort_done_at:
            if self._sort_done_payload is not None:
                self._send_event(MsgType.EVT_SORT_DONE, self._sort_done_payload)
                self._sort_done_payload = None
            self._set_state(State.RUNNING)
        # E-09: watchdog liên kết
        if self._state in (State.RUNNING, State.DETECTED) and now - self._last_rx >= self.link_timeout_s:
            log.warning("FakeEsp32: LINK_LOST (không có khung hợp lệ %.1fs)", self.link_timeout_s)
            self.stats.link_lost += 1
            self._enter_fault(FaultCode.LINK_LOST)
        # E-12: STATUS định kỳ
        if self.status_period_s > 0 and now - self._last_status >= self.status_period_s:
            self._last_status = now
            self._write(encode_frame(self._next_seq(), MsgType.STATUS, self._status_payload()))

    # ------------------------------------------------------------------ xử lý khung từ Pi
    def _handle(self, f: Frame) -> None:
        self._last_rx = time.monotonic()
        self.stats.rx_frames += 1
        t = f.type
        if t in (MsgType.ACK, MsgType.NACK):
            if len(f.payload) >= 1:
                self._pending_evts.pop(f.payload[0], None)
            return
        if t is None or t not in PI2ESP:
            self._send_reply(MsgType.NACK, NackPayload(f.seq, int(NackErr.UNKNOWN_TYPE)), None)
            return
        if len(f.payload) != EXPECTED_LEN[t]:
            self._send_reply(MsgType.NACK, NackPayload(f.seq, int(NackErr.BAD_LEN)), None)
            return
        key = (f.seq, int(t))
        if key in self._recent_cmds:
            # Chống lặp: SEQ trùng chỉ gửi lại trả lời cũ, không thực hiện lần hai
            self.stats.dup_cmds += 1
            frame = self._recent_cmds[key]
            if frame[4] in (MsgType.ACK, MsgType.NACK) and self._drop_acks > 0:
                self._drop_acks -= 1
                return
            self._write(frame)
            return
        allowed = ALLOWED_STATES.get(t)
        if allowed is not None and self._state not in allowed:
            self._send_reply(MsgType.NACK, NackPayload(f.seq, int(NackErr.BAD_STATE)), key)
            return
        handler = getattr(self, f"_cmd_{t.name.lower()}")
        handler(f, key)

    # --- từng lệnh -----------------------------------------------------
    def _cmd_ping(self, f: Frame, key: tuple[int, int]) -> None:
        self._send_reply(MsgType.PONG, PongPayload(int(self._state)), key)

    def _cmd_get_status(self, f: Frame, key: tuple[int, int]) -> None:
        self._send_reply(MsgType.STATUS, self._status_payload(), key)

    def _cmd_start(self, f: Frame, key: tuple[int, int]) -> None:
        p = StartPayload.unpack(f.payload)
        if not 30 <= p.speed <= 100:
            self._send_reply(MsgType.NACK, NackPayload(f.seq, int(NackErr.BAD_PARAM)), key)
            return
        self.belt_speed = p.speed
        self._send_reply(MsgType.ACK, AckPayload(f.seq), key)
        self._set_state(State.RUNNING)

    def _cmd_stop(self, f: Frame, key: tuple[int, int]) -> None:
        self._send_reply(MsgType.ACK, AckPayload(f.seq), key)
        self._set_state(State.IDLE)

    def _cmd_estop(self, f: Frame, key: tuple[int, int]) -> None:
        self._send_reply(MsgType.ACK, AckPayload(f.seq), key)
        self._enter_estop(EstopSource.REMOTE)

    def _cmd_estop_reset(self, f: Frame, key: tuple[int, int]) -> None:
        if self._estop_pressed:  # nút chưa nhả
            self._send_reply(MsgType.NACK, NackPayload(f.seq, int(NackErr.BAD_STATE)), key)
            return
        was_estop = self._state == State.ESTOP
        self._send_reply(MsgType.ACK, AckPayload(f.seq), key)
        self._set_state(State.IDLE)
        self._last_rx = time.monotonic()
        if was_estop:
            self._send_event(MsgType.EVT_ESTOP_CLEARED, b"")

    def _cmd_sort(self, f: Frame, key: tuple[int, int]) -> None:
        p = SortPayload.unpack(f.payload)
        if p.target not in (1, 2, 3) or p.reason not in (0, 1, 2, 3):
            self._send_reply(MsgType.NACK, NackPayload(f.seq, int(NackErr.BAD_PARAM)), key)
            return
        self._send_reply(MsgType.ACK, AckPayload(f.seq), key)
        self.sent_sorts.append(p)
        self.pkg_count += 1
        t_clear = {1: 6, 2: 7, 3: 8}[p.target]
        self._set_state(State.SORTING)
        self._sort_done_at = time.monotonic() + self._scaled(t_clear)
        self._sort_done_payload = EvtSortDonePayload(p.pkg_seq, p.target)
        log.info("FakeEsp32: SORT target=%s reason=%s pkg_seq=%d", SortTarget(p.target).name, SortReason(p.reason).name, p.pkg_seq)

    def _cmd_set_param(self, f: Frame, key: tuple[int, int]) -> None:
        p = SetParamPayload.unpack(f.payload)
        desc = PARAMS.get(p.param_id)
        if desc is None or not desc.min <= p.value <= desc.max:
            self._send_reply(MsgType.NACK, NackPayload(f.seq, int(NackErr.BAD_PARAM)), key)
            return
        self.params[p.param_id] = p.value
        self._send_reply(MsgType.ACK, AckPayload(f.seq), key)

    def _cmd_ota_check(self, f: Frame, key: tuple[int, int]) -> None:
        self._send_reply(MsgType.ACK, AckPayload(f.seq), key)
        self._send_event(MsgType.EVT_OTA_STATUS, EvtOtaStatusPayload(int(OtaStage.CHECKING), int(OtaReason.NONE), 0, self.fw_version_code))
        self._send_event(MsgType.EVT_OTA_STATUS, EvtOtaStatusPayload(int(OtaStage.UP_TO_DATE), int(OtaReason.NONE), 100, self.fw_version_code))


__all__ = ["FakeEsp32", "LoopbackTransport", "LoopbackEndpoint", "PtyTransport", "FakeStats", "SOF"]
