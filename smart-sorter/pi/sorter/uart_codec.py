"""P1 / E-10 / P-07 — Mã hóa và giải mã khung UART Pi <-> ESP32 (đặc tả 4.2).

Khung: SOF(AA 55) VER SEQ TYPE LEN PAYLOAD[LEN] CRC16(LE)
CRC-16/CCITT-FALSE (poly 0x1021, init 0xFFFF, không đảo bit, không XOR cuối)
tính trên VER..PAYLOAD. CRC("123456789") = 0x29B1.

Bộ giải mã nhận dữ liệu theo từng mảnh bất kỳ, tự dò lại SOF, bỏ khung sai CRC
hoặc LEN > MAX_PAYLOAD và đếm lỗi.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto

from .protocol_gen import (
    CRC_INIT,
    CRC_POLY,
    MAX_PAYLOAD,
    SOF,
    VER,
    MsgType,
    Payload,
    decode_payload,
)

__all__ = ["crc16_ccitt_false", "encode_frame", "Frame", "FrameDecoder", "DecoderStats"]


def _make_table() -> list[int]:
    table = []
    for byte in range(256):
        crc = byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ CRC_POLY) if (crc & 0x8000) else (crc << 1)
            crc &= 0xFFFF
        table.append(crc)
    return table


_TABLE = _make_table()


def crc16_ccitt_false(data: bytes | bytearray | memoryview, crc: int = CRC_INIT) -> int:
    """CRC-16/CCITT-FALSE. Có thể gọi nối tiếp bằng cách truyền crc của đoạn trước."""
    for b in data:
        crc = ((crc << 8) & 0xFFFF) ^ _TABLE[((crc >> 8) ^ b) & 0xFF]
    return crc


def encode_frame(seq: int, msg_type: int, payload: bytes | Payload = b"") -> bytes:
    """Đóng khung hoàn chỉnh. payload có thể là bytes hoặc dataclass Payload."""
    if isinstance(payload, Payload):
        payload = payload.pack()
    if not 0 <= seq <= 255:
        raise ValueError("seq phải trong 0..255")
    if not 0 <= int(msg_type) <= 255:
        raise ValueError("msg_type phải trong 0..255")
    if len(payload) > MAX_PAYLOAD:
        raise ValueError(f"payload {len(payload)} byte > {MAX_PAYLOAD}")
    body = bytes([VER, seq, int(msg_type), len(payload)]) + bytes(payload)
    crc = crc16_ccitt_false(body)
    return SOF + body + crc.to_bytes(2, "little")


@dataclass(frozen=True)
class Frame:
    seq: int
    msg_type: int
    payload: bytes
    ver: int = VER

    @property
    def type(self) -> MsgType | None:
        try:
            return MsgType(self.msg_type)
        except ValueError:
            return None

    @property
    def type_name(self) -> str:
        t = self.type
        return t.name if t else f"UNKNOWN_0x{self.msg_type:02X}"

    def decode(self) -> Payload:
        """Giải mã payload theo TYPE (ValueError nếu TYPE lạ / sai độ dài)."""
        return decode_payload(self.msg_type, self.payload)

    def __str__(self) -> str:  # pragma: no cover - tiện debug
        return f"Frame(seq={self.seq}, type={self.type_name}, payload={self.payload.hex()})"


@dataclass
class DecoderStats:
    frames_ok: int = 0
    crc_errors: int = 0
    len_errors: int = 0
    ver_errors: int = 0
    resyncs: int = 0  # số byte rác bị bỏ khi dò lại SOF


class _S(Enum):
    SOF0 = auto()
    SOF1 = auto()
    VER = auto()
    SEQ = auto()
    TYPE = auto()
    LEN = auto()
    PAYLOAD = auto()
    CRC0 = auto()
    CRC1 = auto()


class FrameDecoder:
    """Bộ giải mã từng byte, trạng thái giữ giữa các lần feed()."""

    def __init__(self) -> None:
        self.stats = DecoderStats()
        self._reset()

    def _reset(self) -> None:
        self._state = _S.SOF0
        self._body = bytearray()
        self._seq = 0
        self._type = 0
        self._len = 0
        self._payload = bytearray()
        self._crc0 = 0

    def feed(self, data: bytes | bytearray) -> list[Frame]:
        frames: list[Frame] = []
        for b in data:
            f = self._feed_byte(b)
            if f is not None:
                frames.append(f)
        return frames

    def _feed_byte(self, b: int) -> Frame | None:
        st = self._state
        if st is _S.SOF0:
            if b == SOF[0]:
                self._state = _S.SOF1
            else:
                self.stats.resyncs += 1
            return None
        if st is _S.SOF1:
            if b == SOF[1]:
                self._body = bytearray()
                self._state = _S.VER
            elif b == SOF[0]:
                self.stats.resyncs += 1  # AA AA 55 ... -> giữ lại AA
            else:
                self.stats.resyncs += 2
                self._state = _S.SOF0
            return None
        if st is _S.VER:
            if b != VER:
                self.stats.ver_errors += 1
                self._state = _S.SOF0
                return None
            self._body.append(b)
            self._state = _S.SEQ
            return None
        if st is _S.SEQ:
            self._seq = b
            self._body.append(b)
            self._state = _S.TYPE
            return None
        if st is _S.TYPE:
            self._type = b
            self._body.append(b)
            self._state = _S.LEN
            return None
        if st is _S.LEN:
            if b > MAX_PAYLOAD:
                self.stats.len_errors += 1
                self._state = _S.SOF0
                return None
            self._len = b
            self._body.append(b)
            self._payload = bytearray()
            self._state = _S.PAYLOAD if b > 0 else _S.CRC0
            return None
        if st is _S.PAYLOAD:
            self._payload.append(b)
            self._body.append(b)
            if len(self._payload) == self._len:
                self._state = _S.CRC0
            return None
        if st is _S.CRC0:
            self._crc0 = b
            self._state = _S.CRC1
            return None
        # CRC1
        crc_rx = self._crc0 | (b << 8)
        self._state = _S.SOF0
        if crc_rx != crc16_ccitt_false(self._body):
            self.stats.crc_errors += 1
            return None
        self.stats.frames_ok += 1
        return Frame(seq=self._seq, msg_type=self._type, payload=bytes(self._payload))
