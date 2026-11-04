#!/usr/bin/env python3
"""P1 / E-10 / P-07 — Sinh mã giao thức UART từ protocol.yaml.

Sinh ra:
  esp32/components/protocol/include/protocol_gen.h  (C, ESP-IDF)
  pi/sorter/protocol_gen.py                         (Python, package sorter)

Chạy:  python shared/protocol/gen.py   (hoặc `make gen-protocol`)
"""
from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
SPEC = Path(__file__).with_name("protocol.yaml")
OUT_H = ROOT / "esp32" / "components" / "protocol" / "include" / "protocol_gen.h"
OUT_PY = ROOT / "pi" / "sorter" / "protocol_gen.py"

C_TYPES = {"u8": "uint8_t", "u16": "uint16_t", "u32": "uint32_t"}
PY_STRUCT = {"u8": "B", "u16": "H", "u32": "I"}
SIZES = {"u8": 1, "u16": 2, "u32": 4}

HEADER = "AUTO-GENERATED — DO NOT EDIT (sinh bởi shared/protocol/gen.py từ protocol.yaml)"


def load_spec() -> dict:
    with SPEC.open("r", encoding="utf-8") as f:
        spec = yaml.safe_load(f)
    # Kiểm tra tính nhất quán cơ bản
    codes = [m["code"] for m in spec["messages"]]
    if len(codes) != len(set(codes)):
        raise SystemExit("protocol.yaml: trùng mã TYPE")
    names = [m["name"] for m in spec["messages"]]
    if len(names) != len(set(names)):
        raise SystemExit("protocol.yaml: trùng tên message")
    for m in spec["messages"]:
        size = sum(SIZES[f["type"]] for f in m["payload"])
        if size > spec["frame"]["max_payload"]:
            raise SystemExit(f"payload {m['name']} vượt max_payload")
    return spec


def payload_size(m: dict) -> int:
    return sum(SIZES[f["type"]] for f in m["payload"])


# ---------------------------------------------------------------------------
# C header
# ---------------------------------------------------------------------------
def gen_c(spec: dict) -> str:
    fr = spec["frame"]
    L: list[str] = []
    w = L.append
    w(f"/* {HEADER} */")
    w("#ifndef PROTOCOL_GEN_H")
    w("#define PROTOCOL_GEN_H")
    w("#include <stdint.h>")
    w("#include <stddef.h>")
    w("#ifdef __cplusplus")
    w('extern "C" {')
    w("#endif")
    w("")
    w("/* ---- Khung ---- */")
    w(f"#define PROTO_SOF0            0x{fr['sof'][0]:02X}u")
    w(f"#define PROTO_SOF1            0x{fr['sof'][1]:02X}u")
    w(f"#define PROTO_VER             0x{fr['ver']:02X}u")
    w(f"#define PROTO_MAX_PAYLOAD     {fr['max_payload']}u")
    w(f"#define PROTO_HEADER_LEN      5u   /* SOF0 SOF1 VER SEQ TYPE LEN -> trừ SOF = 4, cộng LEN byte... xem protocol.c */")
    w(f"#define PROTO_OVERHEAD        8u   /* SOF(2)+VER+SEQ+TYPE+LEN+CRC(2) */")
    w(f"#define PROTO_MAX_FRAME       (PROTO_OVERHEAD + PROTO_MAX_PAYLOAD)")
    w(f"#define PROTO_CRC_POLY        0x{fr['crc']['poly']:04X}u")
    w(f"#define PROTO_CRC_INIT        0x{fr['crc']['init']:04X}u")
    w(f"#define PROTO_CRC_CHECK_VALUE 0x{fr['crc']['check_123456789']:04X}u /* CRC(\"123456789\") */")
    w(f"#define PROTO_ACK_TIMEOUT_MS  {fr['ack_timeout_ms']}u")
    w(f"#define PROTO_MAX_RETRIES     {fr['max_retries']}u")
    w(f"#define PROTO_PING_INTERVAL_MS {spec['timing']['ping_interval_ms']}u")
    w(f"#define PROTO_LINK_TIMEOUT_MS  {spec['timing']['link_timeout_ms']}u")
    w("")

    def enum(name: str, table: dict, prefix: str) -> None:
        w(f"typedef enum {{")
        for k, v in table.items():
            w(f"    {prefix}{k} = {v},")
        w(f"}} {name};")
        w("")

    w("/* ---- Mã trạng thái (E-01) ---- */")
    enum("proto_state_t", spec["states"], "ST_")
    w("/* ---- Mã lỗi NACK ---- */")
    enum("proto_nack_err_t", spec["nack_errors"], "NACK_")
    w("/* ---- Mã fault ---- */")
    enum("proto_fault_t", spec["fault_codes"], "FAULT_")
    w("/* ---- Nguồn E-stop ---- */")
    enum("proto_estop_src_t", spec["estop_sources"], "ESTOP_SRC_")
    w("/* ---- SORT target / reason ---- */")
    enum("proto_sort_target_t", spec["sort_targets"], "TARGET_")
    enum("proto_sort_reason_t", spec["sort_reasons"], "REASON_")
    w("/* ---- OTA stage / reason (4.5) ---- */")
    enum("proto_ota_stage_t", spec["ota_stages"], "OTA_STAGE_")
    enum("proto_ota_reason_t", spec["ota_reasons"], "OTA_REASON_")

    w("/* ---- Loại khung ---- */")
    w("typedef enum {")
    for m in spec["messages"]:
        w(f"    MSG_{m['name']} = 0x{m['code']:02X},")
    w("} proto_msg_type_t;")
    w("")

    w("/* ---- Payload (packed, little-endian — ESP32 là little-endian) ---- */")
    for m in spec["messages"]:
        if not m["payload"]:
            continue
        w("typedef struct __attribute__((packed)) {")
        for f in m["payload"]:
            w(f"    {C_TYPES[f['type']]} {f['name']};")
        w(f"}} proto_{m['name'].lower()}_t;")
        w(f"_Static_assert(sizeof(proto_{m['name'].lower()}_t) == {payload_size(m)}, \"size {m['name']}\");")
        w("")

    w("/* Kích thước payload mong đợi theo TYPE; -1 = TYPE không tồn tại */")
    w("static inline int proto_expected_len(uint8_t type) {")
    w("    switch (type) {")
    for m in spec["messages"]:
        w(f"    case MSG_{m['name']}: return {payload_size(m)};")
    w("    default: return -1;")
    w("    }")
    w("}")
    w("")
    w("/* Hướng khung: 1 = Pi->ESP32, 2 = ESP32->Pi, 0 = không tồn tại */")
    w("static inline int proto_direction(uint8_t type) {")
    w("    switch (type) {")
    for m in spec["messages"]:
        d = 1 if m["direction"] == "pi2esp" else 2
        w(f"    case MSG_{m['name']}: return {d};")
    w("    default: return 0;")
    w("    }")
    w("}")
    w("")
    w("/* Bitmask trạng thái được phép nhận lệnh (chỉ Pi->ESP32); bit i = trạng thái i */")
    w("#define PROTO_STATE_ANY 0xFFu")
    w("static inline uint8_t proto_allowed_states(uint8_t type) {")
    w("    switch (type) {")
    for m in spec["messages"]:
        if m["direction"] != "pi2esp":
            continue
        st = m.get("allowed_states", [])
        if "ANY" in st:
            w(f"    case MSG_{m['name']}: return PROTO_STATE_ANY;")
        else:
            mask = " | ".join(f"(1u << ST_{s})" for s in st)
            w(f"    case MSG_{m['name']}: return (uint8_t)({mask});")
    w("    default: return 0;")
    w("    }")
    w("}")
    w("")
    w("/* ---- Tham số SET_PARAM (E-11) ---- */")
    w("typedef enum {")
    for p in spec["params"]:
        w(f"    PARAM_{p['name'].upper()} = {p['id']},")
    w("} proto_param_id_t;")
    w(f"#define PROTO_PARAM_COUNT {len(spec['params'])}u")
    w("typedef struct { uint8_t id; const char *name; uint16_t min; uint16_t max; uint16_t def; } proto_param_desc_t;")
    w("static const proto_param_desc_t PROTO_PARAMS[PROTO_PARAM_COUNT] = {")
    for p in spec["params"]:
        w(f"    {{ {p['id']}, \"{p['name']}\", {p['min']}, {p['max']}, {p['default']} }},")
    w("};")
    w("/* Tìm mô tả tham số theo id; NULL nếu không có */")
    w("static inline const proto_param_desc_t *proto_param_find(uint8_t id) {")
    w("    for (size_t i = 0; i < PROTO_PARAM_COUNT; i++) if (PROTO_PARAMS[i].id == id) return &PROTO_PARAMS[i];")
    w("    return NULL;")
    w("}")
    w("")
    w("#ifdef __cplusplus")
    w("}")
    w("#endif")
    w("#endif /* PROTOCOL_GEN_H */")
    return "\n".join(L) + "\n"


# ---------------------------------------------------------------------------
# Python module
# ---------------------------------------------------------------------------
def gen_py(spec: dict) -> str:
    fr = spec["frame"]
    L: list[str] = []
    w = L.append
    w(f'"""{HEADER}')
    w("")
    w("Định nghĩa giao thức UART Pi <-> ESP32 (đặc tả 4.2, 4.5)."
      " Dùng bởi sorter.uart_codec, sorter.uart_link, fake_esp32, tools.")
    w('"""')
    w("from __future__ import annotations")
    w("")
    w("import struct")
    w("from dataclasses import dataclass, fields")
    w("from enum import IntEnum")
    w("from typing import ClassVar")
    w("")
    w(f"PROTOCOL_VERSION = {spec['protocol_version']}")
    w(f"SOF = bytes([0x{fr['sof'][0]:02X}, 0x{fr['sof'][1]:02X}])")
    w(f"VER = 0x{fr['ver']:02X}")
    w(f"MAX_PAYLOAD = {fr['max_payload']}")
    w(f"CRC_POLY = 0x{fr['crc']['poly']:04X}")
    w(f"CRC_INIT = 0x{fr['crc']['init']:04X}")
    w(f"CRC_CHECK_VALUE = 0x{fr['crc']['check_123456789']:04X}  # CRC('123456789')")
    w(f"ACK_TIMEOUT_MS = {fr['ack_timeout_ms']}")
    w(f"MAX_RETRIES = {fr['max_retries']}")
    w(f"PING_INTERVAL_MS = {spec['timing']['ping_interval_ms']}")
    w(f"LINK_TIMEOUT_MS = {spec['timing']['link_timeout_ms']}")
    w("")

    def enum(name: str, table: dict) -> None:
        w(f"class {name}(IntEnum):")
        for k, v in table.items():
            w(f"    {k} = {v}")
        w("")
        w("")

    enum("State", spec["states"])
    enum("NackErr", spec["nack_errors"])
    enum("FaultCode", spec["fault_codes"])
    enum("EstopSource", spec["estop_sources"])
    enum("SortTarget", spec["sort_targets"])
    enum("SortReason", spec["sort_reasons"])
    enum("OtaStage", spec["ota_stages"])
    enum("OtaReason", spec["ota_reasons"])

    w("class MsgType(IntEnum):")
    for m in spec["messages"]:
        w(f"    {m['name']} = 0x{m['code']:02X}")
    w("")
    w("")
    w("PI2ESP: frozenset[MsgType] = frozenset({")
    for m in spec["messages"]:
        if m["direction"] == "pi2esp":
            w(f"    MsgType.{m['name']},")
    w("})")
    w("ESP2PI: frozenset[MsgType] = frozenset({")
    for m in spec["messages"]:
        if m["direction"] == "esp2pi":
            w(f"    MsgType.{m['name']},")
    w("})")
    w("NO_ACK_TYPES: frozenset[MsgType] = frozenset({" + ", ".join(f"MsgType.{n}" for n in fr["no_ack_types"]) + "})")
    w("REPLY_INSTEAD_OF_ACK: dict[MsgType, MsgType] = {" + ", ".join(
        f"MsgType.{k}: MsgType.{v}" for k, v in fr["reply_instead_of_ack"].items()) + "}")
    w("")
    w("# Trạng thái được phép nhận từng lệnh Pi->ESP32; None = mọi trạng thái")
    w("ALLOWED_STATES: dict[MsgType, frozenset[State] | None] = {")
    for m in spec["messages"]:
        if m["direction"] != "pi2esp":
            continue
        st = m.get("allowed_states", [])
        if "ANY" in st:
            w(f"    MsgType.{m['name']}: None,")
        else:
            w(f"    MsgType.{m['name']}: frozenset({{" + ", ".join(f"State.{s}" for s in st) + "}),")
    w("}")
    w("")
    w("")
    w("class Payload:")
    w('    """Lớp cha của mọi payload; con là dataclass với _fmt là chuỗi struct little-endian."""')
    w("    _fmt: ClassVar[str] = '<'")
    w("    MSG: ClassVar[MsgType]")
    w("")
    w("    def pack(self) -> bytes:")
    w("        return struct.pack(self._fmt, *[getattr(self, f.name) for f in fields(self)])  # type: ignore[arg-type]")
    w("")
    w("    @classmethod")
    w("    def unpack(cls, data: bytes):")
    w("        if len(data) != struct.calcsize(cls._fmt):")
    w("            raise ValueError(f'{cls.__name__}: cần {struct.calcsize(cls._fmt)} byte, nhận {len(data)}')")
    w("        return cls(*struct.unpack(cls._fmt, data))")
    w("")
    w("    @classmethod")
    w("    def size(cls) -> int:")
    w("        return struct.calcsize(cls._fmt)")
    w("")
    w("")
    for m in spec["messages"]:
        cls = "".join(p.capitalize() for p in m["name"].lower().split("_")) + "Payload"
        fmt = "<" + "".join(PY_STRUCT[f["type"]] for f in m["payload"])
        w("@dataclass")
        w(f"class {cls}(Payload):")
        w(f"    _fmt: ClassVar[str] = '{fmt}'")
        w(f"    MSG: ClassVar[MsgType] = MsgType.{m['name']}")
        for f in m["payload"]:
            w(f"    {f['name']}: int = 0")
        if not m["payload"]:
            w("    pass")
        w("")
        w("")
    w("PAYLOAD_CLASSES: dict[MsgType, type[Payload]] = {")
    for m in spec["messages"]:
        cls = "".join(p.capitalize() for p in m["name"].lower().split("_")) + "Payload"
        w(f"    MsgType.{m['name']}: {cls},")
    w("}")
    w("")
    w("EXPECTED_LEN: dict[MsgType, int] = {")
    for m in spec["messages"]:
        w(f"    MsgType.{m['name']}: {payload_size(m)},")
    w("}")
    w("")
    w("# Miền giá trị trường payload (dùng để kiểm tra trước khi gửi)")
    w("FIELD_RANGES: dict[MsgType, dict[str, tuple[int, int]]] = {")
    for m in spec["messages"]:
        rng = {f["name"]: (f["min"], f["max"]) for f in m["payload"] if "min" in f}
        if rng:
            w(f"    MsgType.{m['name']}: {rng!r},")
    w("}")
    w("")
    w("")
    w("@dataclass(frozen=True)")
    w("class ParamDesc:")
    w("    id: int")
    w("    name: str")
    w("    unit: str")
    w("    min: int")
    w("    max: int")
    w("    default: int")
    w("")
    w("")
    w("PARAMS: dict[int, ParamDesc] = {")
    for p in spec["params"]:
        w(f"    {p['id']}: ParamDesc({p['id']}, {p['name']!r}, {p['unit']!r}, {p['min']}, {p['max']}, {p['default']}),")
    w("}")
    w("PARAM_BY_NAME: dict[str, ParamDesc] = {p.name: p for p in PARAMS.values()}")
    w("")
    w("")
    w("def decode_payload(msg_type: int, data: bytes) -> Payload:")
    w('    """Giải mã payload theo TYPE; ValueError nếu TYPE lạ hoặc sai độ dài."""')
    w("    try:")
    w("        mt = MsgType(msg_type)")
    w("    except ValueError as e:")
    w("        raise ValueError(f'TYPE không tồn tại: 0x{msg_type:02X}') from e")
    w("    return PAYLOAD_CLASSES[mt].unpack(data)")
    w("")
    w("")
    w("def validate_fields(msg_type: MsgType, payload: Payload) -> None:")
    w('    """ValueError nếu trường ngoài miền (ví dụ START.speed ngoài 30-100)."""')
    w("    for name, (lo, hi) in FIELD_RANGES.get(msg_type, {}).items():")
    w("        v = getattr(payload, name)")
    w("        if not lo <= v <= hi:")
    w("            raise ValueError(f'{msg_type.name}.{name}={v} ngoài miền [{lo}, {hi}]')")
    return "\n".join(L) + "\n"


def main() -> int:
    spec = load_spec()
    OUT_H.parent.mkdir(parents=True, exist_ok=True)
    OUT_PY.parent.mkdir(parents=True, exist_ok=True)
    OUT_H.write_text(gen_c(spec), encoding="utf-8", newline="\n")
    OUT_PY.write_text(gen_py(spec), encoding="utf-8", newline="\n")
    print(f"Đã sinh: {OUT_H.relative_to(ROOT)}")
    print(f"Đã sinh: {OUT_PY.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
