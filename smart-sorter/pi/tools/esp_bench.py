#!/usr/bin/env python3
"""P-16 — Kịch bản kiểm tra firmware ESP32 (P2) qua UART: in PASS/FAIL từng ca.

Dùng: python tools/esp_bench.py --port /dev/serial0 [--skip-sensor] [--no-link-test]
      python tools/esp_bench.py --fake   (tự kiểm tra tool với FakeEsp32)

Các ca:
  1 PING -> PONG            2 GET_STATUS -> STATUS         3 SORT khi IDLE -> NACK BAD_STATE
  4 SET_PARAM ngoài miền -> NACK BAD_PARAM   5 SET_PARAM hợp lệ -> ACK   6 khung sai CRC -> ESP32 bỏ, vẫn PING được
  7 TYPE lạ -> NACK UNKNOWN_TYPE   8 START -> ACK, RUNNING   9 SEQ lặp -> chỉ ACK lại, không thực hiện 2 lần
 10 chờ EVT_PKG_DETECTED (đưa tay qua cảm biến) -> SORT A -> EVT_SORT_DONE    11 lặp với B và REJECT
 12 ngừng PING 3 s khi RUNNING -> EVT_FAULT(LINK_LOST) -> ESTOP_RESET -> IDLE   13 ESTOP từ xa -> EVT_ESTOP -> ESTOP_RESET
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sorter.config import UartCfg  # noqa: E402
from sorter.protocol_gen import (  # noqa: E402
    EvtFaultPayload,
    EvtPkgDetectedPayload,
    FaultCode,
    MsgType,
    SetParamPayload,
    SortPayload,
    SortReason,
    SortTarget,
    StartPayload,
    State,
    StatusPayload,
)
from sorter.uart_codec import encode_frame  # noqa: E402
from sorter.uart_link import UartLink  # noqa: E402


class Bench:
    def __init__(self, link: UartLink, fake=None, interactive: bool = True):
        self.link = link
        self.fake = fake
        self.interactive = interactive
        self.results: list[tuple[str, bool, str]] = []

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        self.results.append((name, ok, detail))
        print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}")
        return ok

    def wait_event(self, mt: MsgType, timeout: float):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            f = self.link.next_event(max(0.01, end - time.monotonic()))
            if f is not None and f.type == mt:
                return f
        return None

    def wait_state(self, st: State, timeout: float = 2.0) -> bool:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if self.link.state == st:
                return True
            time.sleep(0.05)
        self.link.get_status()
        return self.link.state == st

    def ensure_idle(self) -> None:
        self.link.get_status()
        if self.link.state in (State.ESTOP, State.FAULT):
            self.link.send_command(MsgType.ESTOP_RESET)
        elif self.link.state in (State.RUNNING,):
            self.link.send_command(MsgType.STOP)
        self.wait_state(State.IDLE, 2.0)

    def run(self, skip_sensor: bool, link_test: bool) -> None:
        link = self.link
        r = link.ping()
        self.check("1 PING->PONG", r.ok and link.state is not None, f"state={link.state.name if link.state else None}")
        st = link.get_status()
        self.check("2 GET_STATUS->STATUS", isinstance(st, StatusPayload), f"{st}")
        self.ensure_idle()
        r = link.send_command(MsgType.SORT, SortPayload(1, 0, 1))
        self.check("3 SORT khi IDLE -> NACK BAD_STATE", r.error == "NACK_BAD_STATE", str(r.error))
        r = link.send_command(MsgType.SET_PARAM, SetParamPayload(1, 20))
        self.check("4 SET_PARAM belt_speed=20 -> NACK BAD_PARAM", r.error == "NACK_BAD_PARAM", str(r.error))
        r = link.send_command(MsgType.SET_PARAM, SetParamPayload(1, 60))
        self.check("5 SET_PARAM belt_speed=60 -> ACK", r.ok, str(r.error))
        crc_before = link.last_status.crc_err if link.last_status else -1
        bad = bytearray(encode_frame(123, MsgType.PING))
        bad[-1] ^= 0xFF
        link._write(bytes(bad))
        time.sleep(0.1)
        st2 = link.get_status()
        ok = st2 is not None and (crc_before < 0 or st2.crc_err >= crc_before + 1)
        self.check("6 khung sai CRC -> ESP32 bỏ và đếm crc_err", ok, f"crc_err {crc_before}->{st2.crc_err if st2 else None}")
        link._write(encode_frame(124, 0x7F, b""))
        time.sleep(0.2)
        self.check("7 TYPE lạ -> ESP32 không treo, PING vẫn OK", link.ping().ok, "(NACK UNKNOWN_TYPE xem uart_monitor)")
        r = link.send_command(MsgType.START, StartPayload(60))
        self.check("8 START -> ACK, RUNNING", r.ok and self.wait_state(State.RUNNING), str(r.error))
        # 9 SEQ lặp: gửi lại chính khung STOP cùng SEQ -> lần 2 chỉ ACK, không đổi trạng thái thêm
        seq = link._next_seq()
        frame = encode_frame(seq, MsgType.STOP)
        link._write(frame)
        time.sleep(0.2)
        idle = self.wait_state(State.IDLE, 1.0)
        link._write(frame)
        time.sleep(0.2)
        r = link.send_command(MsgType.START, StartPayload(60))
        self.check("9 SEQ lặp (STOP x2) -> chỉ ACK lại, START sau đó vẫn OK", idle and r.ok, str(r.error))
        self.wait_state(State.RUNNING)

        if not skip_sensor:
            for target, reason in ((SortTarget.A, SortReason.OK), (SortTarget.B, SortReason.OK), (SortTarget.REJECT, SortReason.SECURITY)):
                if self.fake is not None:
                    time.sleep(0.2)
                    self.fake.trigger_sensor()
                else:
                    print(f"  -> Đưa hộp/tay qua cảm biến để gạt {target.name} (chờ 20 s)...")
                ev = self.wait_event(MsgType.EVT_PKG_DETECTED, 20.0)
                if ev is None:
                    self.check(f"10 EVT_PKG_DETECTED (cho {target.name})", False, "không nhận được")
                    continue
                pkg_seq = EvtPkgDetectedPayload.unpack(ev.payload).pkg_seq
                t0 = time.perf_counter()
                r = link.send_command(MsgType.SORT, SortPayload(int(target), int(reason), pkg_seq))
                dt = (time.perf_counter() - t0) * 1000
                done = self.wait_event(MsgType.EVT_SORT_DONE, 15.0)
                self.check(f"10 SORT {target.name} pkg_seq={pkg_seq} -> ACK ({dt:.0f} ms) + EVT_SORT_DONE", r.ok and done is not None, str(r.error))
        else:
            print("  (bỏ qua ca cảm biến: --skip-sensor)")

        if link_test:
            self.wait_state(State.RUNNING, 2.0)
            print("  -> Ngừng PING 3 s, mong EVT_FAULT(LINK_LOST)...")
            link.ping_enabled = False
            ev = self.wait_event(MsgType.EVT_FAULT, 3.5)
            link.ping_enabled = True
            if ev is None:
                link.get_status()
            code = EvtFaultPayload.unpack(ev.payload).code if ev is not None else None
            fault = (ev is not None and code == FaultCode.LINK_LOST) or link.state == State.FAULT
            self.check("12 mất PING 3 s -> FAULT(LINK_LOST)", fault, f"evt={ev} state={link.state}")
            r = link.send_command(MsgType.ESTOP_RESET)
            self.check("12b ESTOP_RESET -> IDLE", r.ok and self.wait_state(State.IDLE), str(r.error))
        r = link.send_command(MsgType.ESTOP)
        ev = self.wait_event(MsgType.EVT_ESTOP, 2.0)
        self.check("13 ESTOP từ xa -> ACK + EVT_ESTOP(REMOTE)", r.ok and ev is not None and ev.payload[0] == 2, str(r.error))
        r = link.send_command(MsgType.ESTOP_RESET)
        self.check("13b ESTOP_RESET -> IDLE", r.ok and self.wait_state(State.IDLE), str(r.error))

    def summary(self) -> int:
        n_ok = sum(1 for _, ok, _ in self.results if ok)
        print(f"\n{n_ok}/{len(self.results)} PASS; link stats={self.link.stats.as_dict()}")
        return 0 if n_ok == len(self.results) else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default=None)
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--fake", action="store_true")
    ap.add_argument("--skip-sensor", action="store_true")
    ap.add_argument("--no-link-test", action="store_true")
    args = ap.parse_args(argv)
    fake = None
    if args.fake:
        from sorter.fake_esp32 import FakeEsp32

        fake = FakeEsp32(time_scale=0.1, link_timeout_s=2.0)
        fake.start()
        link = UartLink(UartCfg(port="sim", ping_interval_ms=500), transport=fake.pi_transport())
    elif args.port:
        link = UartLink(UartCfg(port=args.port, baud=args.baud, ping_interval_ms=500))
    else:
        ap.error("cần --port hoặc --fake")
        return 2
    link.start()
    bench = Bench(link, fake)
    try:
        bench.run(skip_sensor=args.skip_sensor, link_test=not args.no_link_test)
    finally:
        rc = bench.summary()
        link.stop()
        if fake is not None:
            fake.stop()
    return rc


if __name__ == "__main__":
    sys.exit(main())
