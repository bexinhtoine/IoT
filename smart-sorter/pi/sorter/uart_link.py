"""P-07 — Liên kết UART Pi <-> ESP32 chạy thread riêng.

- Thread đọc: FrameDecoder; mỗi khung hợp lệ cập nhật last_rx; sự kiện EVT_* của ESP32 được tự ACK và
  đẩy vào hàng đợi `events`; chống lặp theo SEQ (SEQ trùng chỉ ACK lại); PONG/STATUS cập nhật `state`;
  ACK/NACK ghép với lệnh đang chờ theo acked_seq.
- Thread PING: gửi PING mỗi ping_interval_ms (không chiếm khóa lệnh nếu đang có lệnh chờ).
- send_command(): gửi lệnh, chờ ACK (PONG cho PING, STATUS cho GET_STATUS) tối đa ack_timeout_ms,
  gửi lại cùng SEQ tối đa cfg.retries lần. Chỉ một lệnh chờ tại một thời điểm.
- `connected`: có khung hợp lệ trong LINK_TIMEOUT_MS (2000 ms) gần nhất.
- Đếm lỗi CRC vào metrics (F_UART_CRC_ERRORS) cho module phát hiện bất thường.
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from .config import UartCfg
from .interfaces import F_UART_CRC_ERRORS, metrics
from .protocol_gen import (
    ESP2PI,
    EXPECTED_LEN,
    LINK_TIMEOUT_MS,
    NO_ACK_TYPES,
    REPLY_INSTEAD_OF_ACK,
    AckPayload,
    MsgType,
    NackErr,
    NackPayload,
    Payload,
    PongPayload,
    State,
    StatusPayload,
    validate_fields,
)
from .uart_codec import Frame, FrameDecoder, encode_frame

log = logging.getLogger(__name__)

ERR_TIMEOUT = "TIMEOUT"
ERR_LINK_DOWN = "LINK_DOWN"
NACK_ERRORS: dict[int, str] = {
    NackErr.CRC: "NACK_CRC",
    NackErr.UNKNOWN_TYPE: "NACK_UNKNOWN_TYPE",
    NackErr.BAD_STATE: "NACK_BAD_STATE",
    NackErr.BAD_PARAM: "NACK_BAD_PARAM",
    NackErr.BAD_LEN: "NACK_BAD_LEN",
}

# Lệnh được ACK -> trạng thái ESP32 chuyển sang (E-01); cập nhật `state` ngay, không chờ PONG/STATUS kế tiếp
_CMD_STATE_HINT: dict[MsgType, State] = {
    MsgType.START: State.RUNNING,
    MsgType.STOP: State.IDLE,
    MsgType.ESTOP: State.ESTOP,
    MsgType.ESTOP_RESET: State.IDLE,
    MsgType.SORT: State.SORTING,
}
# Sự kiện ESP32 gợi ý trạng thái mới (cập nhật `state` ngay, không chờ PONG)
_EVT_STATE_HINT: dict[MsgType, State] = {
    MsgType.EVT_PKG_DETECTED: State.DETECTED,
    MsgType.EVT_SORT_DONE: State.RUNNING,
    MsgType.EVT_ESTOP: State.ESTOP,
    MsgType.EVT_ESTOP_CLEARED: State.IDLE,
    MsgType.EVT_FAULT: State.FAULT,
}


class Transport(ABC):
    """Kênh byte thô. read() trả b"" khi hết thời gian chờ; ném OSError khi kênh hỏng."""

    @abstractmethod
    def read(self, max_bytes: int, timeout_s: float) -> bytes: ...

    @abstractmethod
    def write(self, data: bytes) -> None: ...

    def close(self) -> None:  # pragma: no cover
        return None


class SerialTransport(Transport):
    """Cổng nối tiếp thật qua pyserial (import lười)."""

    def __init__(self, port: str, baud: int = 115200) -> None:
        try:
            import serial  # type: ignore[import-not-found]
        except ImportError as e:  # pragma: no cover
            raise RuntimeError("Thiếu pyserial (pip install pyserial)") from e
        self._ser = serial.Serial(port, baudrate=baud, bytesize=8, parity="N", stopbits=1, timeout=0.05, write_timeout=1.0)
        self._lock = threading.Lock()
        log.info("Mở cổng nối tiếp %s @ %d", port, baud)

    def read(self, max_bytes: int, timeout_s: float) -> bytes:
        self._ser.timeout = timeout_s
        data = self._ser.read(1)
        if data and self._ser.in_waiting:
            data += self._ser.read(min(self._ser.in_waiting, max_bytes - 1))
        return bytes(data)

    def write(self, data: bytes) -> None:
        with self._lock:
            self._ser.write(data)
            self._ser.flush()

    def close(self) -> None:
        try:
            self._ser.close()
        except Exception:  # pragma: no cover
            pass


@dataclass
class CommandResult:
    ok: bool
    error: str | None = None       # None | TIMEOUT | NACK_* | LINK_DOWN
    nack_err: NackErr | None = None
    reply: Frame | None = None     # khung ACK/NACK/PONG/STATUS nhận được
    seq: int = -1
    attempts: int = 0

    @property
    def reply_payload(self) -> Payload | None:
        if self.reply is None:
            return None
        try:
            return self.reply.decode()
        except ValueError:
            return None


@dataclass
class LinkStats:
    tx_frames: int = 0
    rx_frames: int = 0
    crc_errors: int = 0
    retries: int = 0
    timeouts: int = 0
    nacks: int = 0
    events: int = 0
    dup_events: int = 0
    pings_sent: int = 0

    def as_dict(self) -> dict[str, int]:
        return dict(self.__dict__)


@dataclass
class _Pending:
    seq: int
    msg_type: MsgType
    event: threading.Event = field(default_factory=threading.Event)
    reply: Frame | None = None


class UartLink:
    """Liên kết UART (P-07). Khởi tạo xong phải gọi start()."""

    def __init__(self, cfg: UartCfg, transport: Transport | None = None, link_timeout_ms: int = LINK_TIMEOUT_MS) -> None:
        self.cfg = cfg
        if transport is None:
            if cfg.port == "sim":
                raise ValueError("uart.port='sim' cần truyền transport của FakeEsp32 (dùng factories.make_uart)")
            transport = SerialTransport(cfg.port, cfg.baud)
        self._tr = transport
        self.link_timeout_ms = int(link_timeout_ms)
        self.events: "queue.Queue[Frame]" = queue.Queue()
        self.stats = LinkStats()
        self.state: State | None = None
        self.last_status: StatusPayload | None = None
        self.last_rx_mono: float = 0.0
        self._decoder = FrameDecoder()
        self._seq = 0
        self._seq_lock = threading.Lock()
        self._cmd_lock = threading.Lock()
        self._pending: _Pending | None = None
        self._pending_lock = threading.Lock()
        self._recent_evt_seqs: deque[int] = deque(maxlen=16)
        self._stop = threading.Event()
        self._rx_thread: threading.Thread | None = None
        self._ping_thread: threading.Thread | None = None
        self._running = False
        self._write_lock = threading.Lock()
        self.ping_enabled = True  # False = tạm ngừng PING (tools/esp_bench kiểm tra E-09)

    # ------------------------------------------------------------------ vòng đời
    def start(self) -> None:
        if self._running:
            return
        self._stop.clear()
        self._running = True
        self._rx_thread = threading.Thread(target=self._rx_loop, name="uart-rx", daemon=True)
        self._rx_thread.start()
        if self.cfg.ping_interval_ms > 0:
            self._ping_thread = threading.Thread(target=self._ping_loop, name="uart-ping", daemon=True)
            self._ping_thread.start()
        log.info("UartLink start (port=%s)", self.cfg.port)

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        self._stop.set()
        for t in (self._rx_thread, self._ping_thread):
            if t is not None and t.is_alive() and t is not threading.current_thread():
                t.join(timeout=2.0)
        try:
            self._tr.close()
        except Exception:
            pass
        log.info("UartLink stop; stats=%s", self.stats.as_dict())

    @property
    def running(self) -> bool:
        return self._running

    @property
    def connected(self) -> bool:
        if not self._running or self.last_rx_mono <= 0:
            return False
        return (time.monotonic() - self.last_rx_mono) * 1000.0 < self.link_timeout_ms

    @property
    def crc_errors(self) -> int:
        return self._decoder.stats.crc_errors

    @property
    def transport(self) -> Transport:
        return self._tr

    # ------------------------------------------------------------------ gửi
    def _next_seq(self) -> int:
        with self._seq_lock:
            s = self._seq
            self._seq = (self._seq + 1) & 0xFF
            return s

    def _write(self, data: bytes) -> None:
        with self._write_lock:
            self._tr.write(data)
        self.stats.tx_frames += 1

    def send_raw(self, msg_type: MsgType, payload: bytes | Payload = b"") -> int:
        """Gửi một khung không chờ trả lời (dùng cho ACK/NACK và PING định kỳ). Trả SEQ."""
        seq = self._next_seq()
        self._write(encode_frame(seq, msg_type, payload))
        return seq

    def send_command(
        self,
        msg_type: MsgType | int,
        payload: bytes | Payload = b"",
        timeout_ms: int | None = None,
        retries: int | None = None,
    ) -> CommandResult:
        """Gửi lệnh Pi->ESP32 và chờ ACK (PONG/STATUS với PING/GET_STATUS); gửi lại cùng SEQ khi quá hạn.

        error: None | 'TIMEOUT' | 'NACK_CRC' | 'NACK_UNKNOWN_TYPE' | 'NACK_BAD_STATE' | 'NACK_BAD_PARAM'
               | 'NACK_BAD_LEN' | 'LINK_DOWN'
        """
        mt = MsgType(msg_type)
        if isinstance(payload, Payload):
            validate_fields(mt, payload)  # ValueError nếu ngoài miền — lỗi lập trình, không gửi
            data = payload.pack()
        else:
            data = bytes(payload)
        if mt in EXPECTED_LEN and len(data) != EXPECTED_LEN[mt]:
            raise ValueError(f"{mt.name}: payload cần {EXPECTED_LEN[mt]} byte, nhận {len(data)}")
        if not self._running:
            return CommandResult(False, ERR_LINK_DOWN)
        timeout_s = (self.cfg.ack_timeout_ms if timeout_ms is None else int(timeout_ms)) / 1000.0
        n_retry = self.cfg.retries if retries is None else int(retries)

        if mt in NO_ACK_TYPES:
            try:
                seq = self.send_raw(mt, data)
            except Exception as e:
                log.error("Ghi UART lỗi: %s", e)
                return CommandResult(False, ERR_LINK_DOWN)
            return CommandResult(True, seq=seq, attempts=1)

        with self._cmd_lock:
            seq = self._next_seq()
            pend = _Pending(seq, mt)
            with self._pending_lock:
                self._pending = pend
            frame = encode_frame(seq, mt, data)
            try:
                attempts = 0
                for attempt in range(1 + n_retry):
                    attempts = attempt + 1
                    if attempt > 0:
                        self.stats.retries += 1
                        log.warning("Gửi lại %s seq=%d (lần %d)", mt.name, seq, attempt + 1)
                    try:
                        self._write(frame)
                    except Exception as e:
                        log.error("Ghi UART lỗi: %s", e)
                        return CommandResult(False, ERR_LINK_DOWN, seq=seq, attempts=attempts)
                    if pend.event.wait(timeout_s) and pend.reply is not None:
                        res = self._result_from_reply(pend.reply, seq, attempts)
                        if res.ok and mt in _CMD_STATE_HINT:
                            self.state = _CMD_STATE_HINT[mt]
                        return res
                self.stats.timeouts += 1
                log.error("%s seq=%d: không có ACK sau %d lần", mt.name, seq, attempts)
                return CommandResult(False, ERR_TIMEOUT, seq=seq, attempts=attempts)
            finally:
                with self._pending_lock:
                    self._pending = None

    def _result_from_reply(self, reply: Frame, seq: int, attempts: int) -> CommandResult:
        if reply.type == MsgType.NACK:
            self.stats.nacks += 1
            try:
                nack = NackPayload.unpack(reply.payload)
                err = NackErr(nack.err)
                name = NACK_ERRORS[err]
            except (ValueError, KeyError):
                err = None
                name = "NACK_UNKNOWN_TYPE"
            log.warning("NACK %s cho seq=%d", name, seq)
            return CommandResult(False, name, err, reply, seq, attempts)
        return CommandResult(True, None, None, reply, seq, attempts)

    def ping(self, timeout_ms: int | None = None) -> CommandResult:
        return self.send_command(MsgType.PING, b"", timeout_ms, 0)

    def get_status(self, timeout_ms: int | None = None) -> StatusPayload | None:
        res = self.send_command(MsgType.GET_STATUS, b"", timeout_ms)
        if res.ok and res.reply is not None and res.reply.type == MsgType.STATUS:
            try:
                st = StatusPayload.unpack(res.reply.payload)
            except ValueError:
                return None
            self.last_status = st
            return st
        return None

    # ------------------------------------------------------------------ thread PING
    def _ping_loop(self) -> None:
        interval = self.cfg.ping_interval_ms / 1000.0
        while not self._stop.wait(interval):
            if not self.ping_enabled:
                continue
            # Không chen vào khi đang có lệnh chờ (tránh SEQ xen kẽ làm ESP32 mất dấu chống lặp)
            if not self._cmd_lock.acquire(blocking=False):
                continue
            try:
                self.send_raw(MsgType.PING)
                self.stats.pings_sent += 1
            except Exception as e:
                log.debug("PING lỗi: %s", e)
            finally:
                self._cmd_lock.release()

    # ------------------------------------------------------------------ thread đọc
    def _rx_loop(self) -> None:
        last_crc = self._decoder.stats.crc_errors
        while not self._stop.is_set():
            try:
                data = self._tr.read(256, 0.05)
            except Exception as e:
                log.error("Đọc UART lỗi: %s", e)
                self._stop.wait(0.2)
                continue
            if not data:
                continue
            frames = self._decoder.feed(data)
            crc_now = self._decoder.stats.crc_errors
            if crc_now != last_crc:
                delta = crc_now - last_crc
                last_crc = crc_now
                self.stats.crc_errors = crc_now
                metrics.inc(F_UART_CRC_ERRORS, delta)
                log.warning("UART: %d khung sai CRC (tổng %d)", delta, crc_now)
            for f in frames:
                try:
                    self._handle_frame(f)
                except Exception as e:  # không để thread đọc chết
                    log.exception("Lỗi xử lý khung %s: %s", f, e)

    def _resolve_pending(self, f: Frame, *, acked_seq: int | None) -> bool:
        with self._pending_lock:
            p = self._pending
            if p is None or p.reply is not None:
                return False
            if acked_seq is not None:
                if acked_seq != p.seq:
                    return False
            else:  # PONG/STATUS: ghép theo loại lệnh đang chờ
                if REPLY_INSTEAD_OF_ACK.get(p.msg_type) != f.type:
                    return False
            p.reply = f
            p.event.set()
            return True

    def _handle_frame(self, f: Frame) -> None:
        self.last_rx_mono = time.monotonic()
        self.stats.rx_frames += 1
        t = f.type
        if t is None:
            log.warning("Khung TYPE lạ 0x%02X seq=%d", f.msg_type, f.seq)
            self.send_raw(MsgType.NACK, NackPayload(f.seq, NackErr.UNKNOWN_TYPE))
            return
        if t not in ESP2PI:
            log.debug("Bỏ qua khung %s (không phải chiều ESP->Pi)", t.name)
            return
        if len(f.payload) != EXPECTED_LEN[t]:
            log.warning("Khung %s sai độ dài %d", t.name, len(f.payload))
            if t not in NO_ACK_TYPES:
                self.send_raw(MsgType.NACK, NackPayload(f.seq, NackErr.BAD_LEN))
            return

        if t in (MsgType.ACK, MsgType.NACK):
            acked = f.payload[0]
            if not self._resolve_pending(f, acked_seq=acked):
                log.debug("%s cho seq=%d không có lệnh chờ (ACK muộn?)", t.name, acked)
            return
        if t == MsgType.PONG:
            self._set_state(PongPayload.unpack(f.payload).state)
            self._resolve_pending(f, acked_seq=None)
            return
        if t == MsgType.STATUS:
            st = StatusPayload.unpack(f.payload)
            self.last_status = st
            self._set_state(st.state)
            self._resolve_pending(f, acked_seq=None)
            self.events.put(f)  # orchestrator dùng để cập nhật fw_version
            return

        # EVT_*: ACK ngay, chống lặp theo SEQ
        self.send_raw(MsgType.ACK, AckPayload(f.seq))
        if f.seq in self._recent_evt_seqs:
            self.stats.dup_events += 1
            log.debug("Sự kiện %s seq=%d lặp — chỉ ACK lại", t.name, f.seq)
            return
        self._recent_evt_seqs.append(f.seq)
        self.stats.events += 1
        hint = _EVT_STATE_HINT.get(t)
        if hint is not None:
            self.state = hint
        self.events.put(f)

    def _set_state(self, value: int) -> None:
        try:
            self.state = State(value)
        except ValueError:
            log.warning("Trạng thái lạ từ ESP32: %d", value)

    # ------------------------------------------------------------------ tiện ích
    def next_event(self, timeout_s: float | None = 1.0) -> Frame | None:
        try:
            return self.events.get(timeout=timeout_s)
        except queue.Empty:
            return None

    def info(self) -> dict[str, Any]:
        return {
            "connected": self.connected,
            "state": self.state.name if self.state is not None else None,
            "crc_errors": self.crc_errors,
            **self.stats.as_dict(),
        }
