#!/usr/bin/env python3
"""P-16 — Theo dõi UART: in mọi khung giải mã được kèm thời gian, tên TYPE và payload đã decode.

Dùng: python tools/uart_monitor.py --port /dev/serial0 [--baud 115200] [--raw]
      python tools/uart_monitor.py --fake        (FakeEsp32 trong tiến trình, tự PING để thấy PONG/STATUS)
Chỉ nghe (không gửi) trừ khi --ping N (gửi PING mỗi N ms để ESP32 không vào FAULT khi đang RUNNING).
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sorter.protocol_gen import MsgType  # noqa: E402
from sorter.uart_codec import Frame, FrameDecoder, encode_frame  # noqa: E402


def fmt(f: Frame, direction: str) -> str:
    ts = time.strftime("%H:%M:%S") + f".{int(time.time() * 1000) % 1000:03d}"
    try:
        dec = f.decode()
        body = ", ".join(f"{k}={v}" for k, v in dec.__dict__.items()) or "-"
    except ValueError as e:
        body = f"({e}) raw={f.payload.hex()}"
    return f"{ts} {direction} seq={f.seq:3d} {f.type_name:<18} {body}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default=None)
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--fake", action="store_true", help="dùng FakeEsp32 thay cổng thật")
    ap.add_argument("--ping", type=int, default=0, help="gửi PING mỗi N ms (0 = chỉ nghe)")
    ap.add_argument("--raw", action="store_true", help="in cả byte thô")
    args = ap.parse_args(argv)

    if args.fake:
        from sorter.fake_esp32 import FakeEsp32

        fake = FakeEsp32(time_scale=0.1)
        fake.start()
        tr = fake.pi_transport()
        if not args.ping:
            args.ping = 500
    elif args.port:
        from sorter.uart_link import SerialTransport

        tr = SerialTransport(args.port, args.baud)
    else:
        ap.error("cần --port hoặc --fake")
        return 2

    dec = FrameDecoder()
    seq = 0
    last_ping = 0.0
    last_stats = (0, 0, 0)
    try:
        sys.stdout.reconfigure(line_buffering=True)  # type: ignore[attr-defined]
    except Exception:
        pass
    print("Đang nghe... Ctrl+C để dừng")
    try:
        while True:
            data = tr.read(256, 0.05)
            if data:
                if args.raw:
                    print(f"{'RAW':>12} {data.hex(' ')}")
                for f in dec.feed(data):
                    print(fmt(f, "RX <-"))
            st = (dec.stats.crc_errors, dec.stats.len_errors, dec.stats.resyncs)
            if st != last_stats:
                print(f"{'STATS':>12} crc_err={st[0]} len_err={st[1]} resync_bytes={st[2]}")
                last_stats = st
            if args.ping and time.monotonic() - last_ping >= args.ping / 1000.0:
                last_ping = time.monotonic()
                tr.write(encode_frame(seq, MsgType.PING))
                print(fmt(Frame(seq, int(MsgType.PING), b""), "TX ->"))
                seq = (seq + 1) & 0xFF
    except (KeyboardInterrupt, BrokenPipeError, OSError):  # OSError: đầu ống bị đóng (| head)
        pass
    finally:
        tr.close()
        if args.fake:
            fake.stop()
    print(f"frames_ok={dec.stats.frames_ok} crc_errors={dec.stats.crc_errors}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
