"""AUTO-GENERATED — DO NOT EDIT (sinh bởi shared/protocol/gen.py từ protocol.yaml)

Định nghĩa giao thức UART Pi <-> ESP32 (đặc tả 4.2, 4.5). Dùng bởi sorter.uart_codec, sorter.uart_link, fake_esp32, tools.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, fields
from enum import IntEnum
from typing import ClassVar

PROTOCOL_VERSION = 1
SOF = bytes([0xAA, 0x55])
VER = 0x01
MAX_PAYLOAD = 64
CRC_POLY = 0x1021
CRC_INIT = 0xFFFF
CRC_CHECK_VALUE = 0x29B1  # CRC('123456789')
ACK_TIMEOUT_MS = 200
MAX_RETRIES = 3
PING_INTERVAL_MS = 500
LINK_TIMEOUT_MS = 2000

class State(IntEnum):
    BOOT = 0
    IDLE = 1
    RUNNING = 2
    DETECTED = 3
    SORTING = 4
    ESTOP = 5
    FAULT = 6
    OTA = 7


class NackErr(IntEnum):
    CRC = 1
    UNKNOWN_TYPE = 2
    BAD_STATE = 3
    BAD_PARAM = 4
    BAD_LEN = 5


class FaultCode(IntEnum):
    DECISION_TIMEOUT = 1
    LINK_LOST = 2
    SENSOR = 3
    WDT_RESET = 4


class EstopSource(IntEnum):
    PHYSICAL = 1
    REMOTE = 2


class SortTarget(IntEnum):
    A = 1
    B = 2
    REJECT = 3


class SortReason(IntEnum):
    OK = 0
    DEFECT = 1
    SECURITY = 2
    FAILSAFE = 3


class OtaStage(IntEnum):
    CHECKING = 1
    DOWNLOADING = 2
    VERIFIED = 3
    REJECTED = 4
    REBOOTING = 5
    SUCCESS = 6
    ROLLED_BACK = 7
    UP_TO_DATE = 8


class OtaReason(IntEnum):
    NONE = 0
    SIGNATURE_INVALID = 1
    HASH_MISMATCH = 2
    ROLLBACK_REJECTED = 3
    MODEL_MISMATCH = 4
    TLS_ERROR = 5
    DOWNLOAD_ERROR = 6
    SELFTEST_FAILED = 7
    BAD_STATE = 8


class MsgType(IntEnum):
    PING = 0x01
    START = 0x10
    STOP = 0x11
    ESTOP = 0x12
    ESTOP_RESET = 0x13
    SORT = 0x20
    SET_PARAM = 0x40
    OTA_CHECK = 0x50
    GET_STATUS = 0x60
    ACK = 0x80
    NACK = 0x81
    PONG = 0x82
    EVT_PKG_DETECTED = 0x90
    EVT_SORT_DONE = 0x91
    EVT_ESTOP = 0x92
    EVT_ESTOP_CLEARED = 0x93
    EVT_FAULT = 0x94
    EVT_OTA_STATUS = 0xA0
    STATUS = 0xA1


PI2ESP: frozenset[MsgType] = frozenset({
    MsgType.PING,
    MsgType.START,
    MsgType.STOP,
    MsgType.ESTOP,
    MsgType.ESTOP_RESET,
    MsgType.SORT,
    MsgType.SET_PARAM,
    MsgType.OTA_CHECK,
    MsgType.GET_STATUS,
})
ESP2PI: frozenset[MsgType] = frozenset({
    MsgType.ACK,
    MsgType.NACK,
    MsgType.PONG,
    MsgType.EVT_PKG_DETECTED,
    MsgType.EVT_SORT_DONE,
    MsgType.EVT_ESTOP,
    MsgType.EVT_ESTOP_CLEARED,
    MsgType.EVT_FAULT,
    MsgType.EVT_OTA_STATUS,
    MsgType.STATUS,
})
NO_ACK_TYPES: frozenset[MsgType] = frozenset({MsgType.ACK, MsgType.NACK, MsgType.PONG, MsgType.STATUS})
REPLY_INSTEAD_OF_ACK: dict[MsgType, MsgType] = {MsgType.PING: MsgType.PONG, MsgType.GET_STATUS: MsgType.STATUS}

# Trạng thái được phép nhận từng lệnh Pi->ESP32; None = mọi trạng thái
ALLOWED_STATES: dict[MsgType, frozenset[State] | None] = {
    MsgType.PING: None,
    MsgType.START: frozenset({State.IDLE}),
    MsgType.STOP: frozenset({State.IDLE, State.RUNNING}),
    MsgType.ESTOP: None,
    MsgType.ESTOP_RESET: frozenset({State.ESTOP, State.FAULT}),
    MsgType.SORT: frozenset({State.DETECTED}),
    MsgType.SET_PARAM: frozenset({State.IDLE}),
    MsgType.OTA_CHECK: frozenset({State.IDLE}),
    MsgType.GET_STATUS: None,
}


class Payload:
    """Lớp cha của mọi payload; con là dataclass với _fmt là chuỗi struct little-endian."""
    _fmt: ClassVar[str] = '<'
    MSG: ClassVar[MsgType]

    def pack(self) -> bytes:
        return struct.pack(self._fmt, *[getattr(self, f.name) for f in fields(self)])  # type: ignore[arg-type]

    @classmethod
    def unpack(cls, data: bytes):
        if len(data) != struct.calcsize(cls._fmt):
            raise ValueError(f'{cls.__name__}: cần {struct.calcsize(cls._fmt)} byte, nhận {len(data)}')
        return cls(*struct.unpack(cls._fmt, data))

    @classmethod
    def size(cls) -> int:
        return struct.calcsize(cls._fmt)


@dataclass
class PingPayload(Payload):
    _fmt: ClassVar[str] = '<'
    MSG: ClassVar[MsgType] = MsgType.PING
    pass


@dataclass
class StartPayload(Payload):
    _fmt: ClassVar[str] = '<B'
    MSG: ClassVar[MsgType] = MsgType.START
    speed: int = 0


@dataclass
class StopPayload(Payload):
    _fmt: ClassVar[str] = '<'
    MSG: ClassVar[MsgType] = MsgType.STOP
    pass


@dataclass
class EstopPayload(Payload):
    _fmt: ClassVar[str] = '<'
    MSG: ClassVar[MsgType] = MsgType.ESTOP
    pass


@dataclass
class EstopResetPayload(Payload):
    _fmt: ClassVar[str] = '<'
    MSG: ClassVar[MsgType] = MsgType.ESTOP_RESET
    pass


@dataclass
class SortPayload(Payload):
    _fmt: ClassVar[str] = '<BBH'
    MSG: ClassVar[MsgType] = MsgType.SORT
    target: int = 0
    reason: int = 0
    pkg_seq: int = 0


@dataclass
class SetParamPayload(Payload):
    _fmt: ClassVar[str] = '<BH'
    MSG: ClassVar[MsgType] = MsgType.SET_PARAM
    param_id: int = 0
    value: int = 0


@dataclass
class OtaCheckPayload(Payload):
    _fmt: ClassVar[str] = '<'
    MSG: ClassVar[MsgType] = MsgType.OTA_CHECK
    pass


@dataclass
class GetStatusPayload(Payload):
    _fmt: ClassVar[str] = '<'
    MSG: ClassVar[MsgType] = MsgType.GET_STATUS
    pass


@dataclass
class AckPayload(Payload):
    _fmt: ClassVar[str] = '<B'
    MSG: ClassVar[MsgType] = MsgType.ACK
    acked_seq: int = 0


@dataclass
class NackPayload(Payload):
    _fmt: ClassVar[str] = '<BB'
    MSG: ClassVar[MsgType] = MsgType.NACK
    acked_seq: int = 0
    err: int = 0


@dataclass
class PongPayload(Payload):
    _fmt: ClassVar[str] = '<B'
    MSG: ClassVar[MsgType] = MsgType.PONG
    state: int = 0


@dataclass
class EvtPkgDetectedPayload(Payload):
    _fmt: ClassVar[str] = '<H'
    MSG: ClassVar[MsgType] = MsgType.EVT_PKG_DETECTED
    pkg_seq: int = 0


@dataclass
class EvtSortDonePayload(Payload):
    _fmt: ClassVar[str] = '<HB'
    MSG: ClassVar[MsgType] = MsgType.EVT_SORT_DONE
    pkg_seq: int = 0
    target: int = 0


@dataclass
class EvtEstopPayload(Payload):
    _fmt: ClassVar[str] = '<B'
    MSG: ClassVar[MsgType] = MsgType.EVT_ESTOP
    source: int = 0


@dataclass
class EvtEstopClearedPayload(Payload):
    _fmt: ClassVar[str] = '<'
    MSG: ClassVar[MsgType] = MsgType.EVT_ESTOP_CLEARED
    pass


@dataclass
class EvtFaultPayload(Payload):
    _fmt: ClassVar[str] = '<B'
    MSG: ClassVar[MsgType] = MsgType.EVT_FAULT
    code: int = 0


@dataclass
class EvtOtaStatusPayload(Payload):
    _fmt: ClassVar[str] = '<BBBI'
    MSG: ClassVar[MsgType] = MsgType.EVT_OTA_STATUS
    stage: int = 0
    reason: int = 0
    progress: int = 0
    version_code: int = 0


@dataclass
class StatusPayload(Payload):
    _fmt: ClassVar[str] = '<BIIHHB'
    MSG: ClassVar[MsgType] = MsgType.STATUS
    state: int = 0
    fw_version_code: int = 0
    uptime_s: int = 0
    pkg_count: int = 0
    crc_err: int = 0
    reset_reason: int = 0


PAYLOAD_CLASSES: dict[MsgType, type[Payload]] = {
    MsgType.PING: PingPayload,
    MsgType.START: StartPayload,
    MsgType.STOP: StopPayload,
    MsgType.ESTOP: EstopPayload,
    MsgType.ESTOP_RESET: EstopResetPayload,
    MsgType.SORT: SortPayload,
    MsgType.SET_PARAM: SetParamPayload,
    MsgType.OTA_CHECK: OtaCheckPayload,
    MsgType.GET_STATUS: GetStatusPayload,
    MsgType.ACK: AckPayload,
    MsgType.NACK: NackPayload,
    MsgType.PONG: PongPayload,
    MsgType.EVT_PKG_DETECTED: EvtPkgDetectedPayload,
    MsgType.EVT_SORT_DONE: EvtSortDonePayload,
    MsgType.EVT_ESTOP: EvtEstopPayload,
    MsgType.EVT_ESTOP_CLEARED: EvtEstopClearedPayload,
    MsgType.EVT_FAULT: EvtFaultPayload,
    MsgType.EVT_OTA_STATUS: EvtOtaStatusPayload,
    MsgType.STATUS: StatusPayload,
}

EXPECTED_LEN: dict[MsgType, int] = {
    MsgType.PING: 0,
    MsgType.START: 1,
    MsgType.STOP: 0,
    MsgType.ESTOP: 0,
    MsgType.ESTOP_RESET: 0,
    MsgType.SORT: 4,
    MsgType.SET_PARAM: 3,
    MsgType.OTA_CHECK: 0,
    MsgType.GET_STATUS: 0,
    MsgType.ACK: 1,
    MsgType.NACK: 2,
    MsgType.PONG: 1,
    MsgType.EVT_PKG_DETECTED: 2,
    MsgType.EVT_SORT_DONE: 3,
    MsgType.EVT_ESTOP: 1,
    MsgType.EVT_ESTOP_CLEARED: 0,
    MsgType.EVT_FAULT: 1,
    MsgType.EVT_OTA_STATUS: 7,
    MsgType.STATUS: 14,
}

# Miền giá trị trường payload (dùng để kiểm tra trước khi gửi)
FIELD_RANGES: dict[MsgType, dict[str, tuple[int, int]]] = {
    MsgType.START: {'speed': (30, 100)},
}


@dataclass(frozen=True)
class ParamDesc:
    id: int
    name: str
    unit: str
    min: int
    max: int
    default: int


PARAMS: dict[int, ParamDesc] = {
    1: ParamDesc(1, 'belt_speed', '%', 30, 100, 60),
    2: ParamDesc(2, 'servo_a_home', 'deg', 0, 180, 0),
    3: ParamDesc(3, 'servo_a_divert', 'deg', 0, 180, 45),
    4: ParamDesc(4, 'servo_b_home', 'deg', 0, 180, 0),
    5: ParamDesc(5, 'servo_b_divert', 'deg', 0, 180, 45),
    6: ParamDesc(6, 't_clear_a', 'ms', 500, 8000, 2500),
    7: ParamDesc(7, 't_clear_b', 'ms', 500, 8000, 3500),
    8: ParamDesc(8, 't_clear_reject', 'ms', 500, 10000, 5000),
    9: ParamDesc(9, 't_rearm', 'ms', 0, 3000, 500),
    10: ParamDesc(10, 't_decision_timeout', 'ms', 1000, 10000, 3000),
    11: ParamDesc(11, 'hcsr04_threshold', 'mm', 20, 400, 80),
}
PARAM_BY_NAME: dict[str, ParamDesc] = {p.name: p for p in PARAMS.values()}


def decode_payload(msg_type: int, data: bytes) -> Payload:
    """Giải mã payload theo TYPE; ValueError nếu TYPE lạ hoặc sai độ dài."""
    try:
        mt = MsgType(msg_type)
    except ValueError as e:
        raise ValueError(f'TYPE không tồn tại: 0x{msg_type:02X}') from e
    return PAYLOAD_CLASSES[mt].unpack(data)


def validate_fields(msg_type: MsgType, payload: Payload) -> None:
    """ValueError nếu trường ngoài miền (ví dụ START.speed ngoài 30-100)."""
    for name, (lo, hi) in FIELD_RANGES.get(msg_type, {}).items():
        v = getattr(payload, name)
        if not lo <= v <= hi:
            raise ValueError(f'{msg_type.name}.{name}={v} ngoài miền [{lo}, {hi}]')
