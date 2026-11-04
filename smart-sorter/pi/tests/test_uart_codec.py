"""P1 — Test codec UART bằng test_vectors.json dùng chung với C."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from sorter.protocol_gen import (
    EXPECTED_LEN,
    MAX_PAYLOAD,
    MsgType,
    SortPayload,
    SortReason,
    SortTarget,
    StartPayload,
    decode_payload,
    validate_fields,
)
from sorter.uart_codec import FrameDecoder, crc16_ccitt_false, encode_frame

VECTORS = Path(__file__).resolve().parents[2] / "shared" / "protocol" / "test_vectors.json"


def test_crc_check_value():
    assert crc16_ccitt_false(b"123456789") == 0x29B1


def test_crc_incremental():
    data = b"hello world"
    assert crc16_ccitt_false(data) == crc16_ccitt_false(data[6:], crc16_ccitt_false(data[:6]))


def test_encode_layout():
    f = encode_frame(1, MsgType.START, StartPayload(60))
    assert f[:2] == b"\xaa\x55"
    assert f[2] == 0x01 and f[3] == 1 and f[4] == 0x10 and f[5] == 1 and f[6] == 60
    crc = crc16_ccitt_false(f[2:7])
    assert f[7:] == crc.to_bytes(2, "little")


def test_payload_too_long():
    with pytest.raises(ValueError):
        encode_frame(0, MsgType.PING, bytes(MAX_PAYLOAD + 1))


def test_roundtrip_all_types():
    from sorter.protocol_gen import PAYLOAD_CLASSES
    dec = FrameDecoder()
    for mt, cls in PAYLOAD_CLASSES.items():
        p = cls()
        frames = dec.feed(encode_frame(mt.value & 0xFF, mt, p))
        assert len(frames) == 1
        assert frames[0].type == mt
        assert frames[0].decode() == p
        assert len(frames[0].payload) == EXPECTED_LEN[mt]


def test_validate_fields():
    validate_fields(MsgType.START, StartPayload(30))
    validate_fields(MsgType.START, StartPayload(100))
    with pytest.raises(ValueError):
        validate_fields(MsgType.START, StartPayload(29))
    with pytest.raises(ValueError):
        validate_fields(MsgType.START, StartPayload(101))


def test_decode_payload_wrong_len():
    with pytest.raises(ValueError):
        decode_payload(MsgType.SORT, b"\x01")
    with pytest.raises(ValueError):
        decode_payload(0x7F, b"")


def _load_vectors():
    data = json.loads(VECTORS.read_text(encoding="utf-8"))
    return data["vectors"]


@pytest.mark.parametrize("v", _load_vectors(), ids=lambda v: v["name"])
def test_vectors(v):
    dec = FrameDecoder()
    got = []
    for chunk in v["chunks"]:
        got.extend(dec.feed(bytes.fromhex(chunk)))
    exp = v["expected_frames"]
    assert len(got) == len(exp), v.get("note")
    for g, e in zip(got, exp):
        assert g.seq == e["seq"]
        assert g.msg_type == e["type"]
        assert g.payload.hex() == e["payload"]
    assert dec.stats.crc_errors == v["expected_crc_errors"]
    assert dec.stats.len_errors == v["expected_len_errors"]


def test_sort_payload_le():
    p = SortPayload(SortTarget.B, SortReason.DEFECT, 0x0102).pack()
    assert p == bytes([2, 1, 0x02, 0x01])
