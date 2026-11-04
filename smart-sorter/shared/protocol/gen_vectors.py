#!/usr/bin/env python3
"""P1 — Sinh shared/protocol/test_vectors.json từ codec Python (đã kiểm tra chéo CRC 0x29B1).

Mỗi vector: name, chunks (danh sách hex được feed lần lượt), expected_frames
(seq, type, payload hex), expected_crc_errors, expected_len_errors.
Test Python và test C cùng đọc file này.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "pi"))

from sorter.protocol_gen import (  # noqa: E402
    MsgType,
    PingPayload,
    SetParamPayload,
    SortPayload,
    SortReason,
    SortTarget,
    StartPayload,
    StatusPayload,
    State,
    EvtPkgDetectedPayload,
    AckPayload,
)
from sorter.uart_codec import crc16_ccitt_false, encode_frame  # noqa: E402


def vec(name, chunks, frames, crc_err=0, len_err=0, note=""):
    return {
        "name": name,
        "note": note,
        "chunks": [c.hex() for c in chunks],
        "expected_frames": [{"seq": s, "type": int(t), "payload": p.hex()} for s, t, p in frames],
        "expected_crc_errors": crc_err,
        "expected_len_errors": len_err,
    }


def main() -> None:
    assert crc16_ccitt_false(b"123456789") == 0x29B1
    V = []
    ping = encode_frame(0, MsgType.PING)
    V.append(vec("ping_no_payload", [ping], [(0, MsgType.PING, b"")], note="khung không payload"))

    start = encode_frame(1, MsgType.START, StartPayload(speed=60))
    V.append(vec("start_speed60", [start], [(1, MsgType.START, bytes([60]))]))

    sort = encode_frame(42, MsgType.SORT, SortPayload(SortTarget.A, SortReason.OK, 0x0102))
    V.append(vec("sort_a_ok_seq42", [sort], [(42, MsgType.SORT, bytes([1, 0, 0x02, 0x01]))], note="pkg_seq little-endian"))

    setp = encode_frame(255, MsgType.SET_PARAM, SetParamPayload(6, 2500))
    V.append(vec("set_param_seq255_wrap", [setp], [(255, MsgType.SET_PARAM, bytes([6, 0xC4, 0x09]))]))

    status = encode_frame(9, MsgType.STATUS, StatusPayload(State.RUNNING, 0x00010002, 3600, 12, 1, 3))
    V.append(vec("status_frame", [status], [(9, MsgType.STATUS, StatusPayload(State.RUNNING, 0x00010002, 3600, 12, 1, 3).pack())]))

    evt = encode_frame(3, MsgType.EVT_PKG_DETECTED, EvtPkgDetectedPayload(7))
    ack = encode_frame(4, MsgType.ACK, AckPayload(3))
    V.append(vec("two_frames_back_to_back", [evt + ack], [(3, MsgType.EVT_PKG_DETECTED, bytes([7, 0])), (4, MsgType.ACK, bytes([3]))],
                 note="2 khung dính liền trong 1 lần feed"))

    V.append(vec("garbage_before_sof", [b"\x00\x11\xaa\x22\x55" + ping], [(0, MsgType.PING, b"")],
                 note="rác trước SOF, gồm cả AA không theo sau bởi 55"))

    bad = bytearray(sort)
    bad[-1] ^= 0xFF
    V.append(vec("bad_crc_dropped", [bytes(bad), ping], [(0, MsgType.PING, b"")], crc_err=1, note="khung sai CRC bị bỏ, khung sau vẫn nhận"))

    bad_len = bytes([0xAA, 0x55, 0x01, 5, 0x20, 65]) + bytes(65) + b"\x00\x00"
    V.append(vec("len_too_large", [bad_len, ping], [(0, MsgType.PING, b"")], len_err=1, note="LEN=65 > 64 bị bỏ, tự dò lại SOF"))

    half = len(sort) // 2
    V.append(vec("split_across_feeds", [sort[:half], sort[half:]], [(42, MsgType.SORT, bytes([1, 0, 0x02, 0x01]))],
                 note="khung bị cắt đôi qua 2 lần feed"))

    V.append(vec("byte_by_byte", [bytes([b]) for b in start], [(1, MsgType.START, bytes([60]))], note="feed từng byte một"))

    payload_max = bytes(range(64))
    big = encode_frame(10, 0x7F, payload_max)
    V.append(vec("max_payload_64_unknown_type", [big], [(10, 0x7F, payload_max)], note="LEN=64 hợp lệ; TYPE lạ vẫn giải mã ở tầng khung"))

    bad_ver = bytearray(ping)
    bad_ver[2] = 0x02
    V.append(vec("bad_version_dropped", [bytes(bad_ver), ping], [(0, MsgType.PING, b"")], note="VER != 1 bị bỏ (đếm ver_errors)"))

    sof_in_payload = encode_frame(5, MsgType.EVT_PKG_DETECTED, bytes([0xAA, 0x55]))
    V.append(vec("sof_bytes_inside_payload", [sof_in_payload], [(5, MsgType.EVT_PKG_DETECTED, bytes([0xAA, 0x55]))],
                 note="AA 55 trong payload không làm lệch bộ giải mã"))

    out = {
        "_comment": "AUTO-GENERATED bởi gen_vectors.py — dùng chung cho pytest (Python) và host_test.c (C)",
        "crc_check": {"input_ascii": "123456789", "expected": "0x29B1"},
        "vectors": V,
    }
    path = Path(__file__).with_name("test_vectors.json")
    path.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Đã ghi {len(V)} vector -> {path}")


if __name__ == "__main__":
    main()
