#!/usr/bin/env python3
"""AI-01 / P-16 — Thu dataset tại trạm chụp (đúng đèn, góc camera khi demo).

Dùng trên Pi:   python tools/capture_dataset.py --config /etc/sorter/config.yaml --out dataset --box-id box01
Trên laptop:    python tools/capture_dataset.py --sim --config pi/config.sim.yaml --out /tmp/ds --box-id test
Tự chụp theo cảm biến: thêm --auto (chụp khi UartLink nhận EVT_PKG_DETECTED, gán nhãn bằng --label hoặc phím)

Phím: 1=ok 2=tear 3=dent 4=wet 5=crush  (c = chụp lại, q = thoát). Có màn hình thì dùng cv2.imshow,
không thì dùng input(). Lưu dataset/<lớp>/<box_id>_<YYYYmmdd_HHMMSS>.jpg + metadata.jsonl.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sorter.config import load_config  # noqa: E402
from sorter.factories import make_camera, make_uart  # noqa: E402
from sorter.interfaces import crop_roi  # noqa: E402
from sorter.protocol_gen import MsgType, StartPayload  # noqa: E402

LABELS = {"1": "ok", "2": "tear", "3": "dent", "4": "wet", "5": "crush"}


def have_display() -> bool:
    try:
        cv2.namedWindow("probe", cv2.WINDOW_NORMAL)
        cv2.destroyWindow("probe")
        return True
    except cv2.error:
        return False


def ask_label(frame_bgr, gui: bool) -> str | None:
    """Trả nhãn, 'c' để chụp lại, None để thoát."""
    prompt = "1=ok 2=tear 3=dent 4=wet 5=crush  c=chup lai  q=thoat"
    if gui:
        view = cv2.resize(frame_bgr, (960, 720))
        cv2.putText(view, prompt, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
        cv2.imshow("capture_dataset", view)
        while True:
            k = cv2.waitKey(0) & 0xFF
            ch = chr(k) if k < 128 else ""
            if ch in LABELS:
                return LABELS[ch]
            if ch == "c":
                return "c"
            if ch == "q" or k == 27:
                return None
    while True:
        ch = input(f"{prompt}: ").strip().lower()
        if ch in LABELS:
            return LABELS[ch]
        if ch in ("c", "q"):
            return None if ch == "q" else "c"


def save(out: Path, label: str, box_id: str, frame_rgb, roi, meta_extra: dict) -> Path:
    d = out / label
    d.mkdir(parents=True, exist_ok=True)
    name = f"{box_id}_{time.strftime('%Y%m%d_%H%M%S')}_{int(time.time() * 1000) % 1000:03d}.jpg"
    path = d / name
    cv2.imwrite(str(path), cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2BGR), [int(cv2.IMWRITE_JPEG_QUALITY), 95])
    rec = {"file": str(path.relative_to(out)), "label": label, "box_id": box_id, "ts": int(time.time()), "roi": list(roi), "shape": list(frame_rgb.shape[:2]), **meta_extra}
    with (out / "metadata.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--box-id", required=True, help="mã hộp (để chia train/test theo hộp)")
    ap.add_argument("--sim", action="store_true", help="FolderCamera + FakeEsp32")
    ap.add_argument("--auto", action="store_true", help="chụp khi nhận EVT_PKG_DETECTED")
    ap.add_argument("--label", default=None, help="nhãn cố định (bỏ hỏi phím), ví dụ ok")
    ap.add_argument("--crop", action="store_true", help="lưu ROI thay vì ảnh đầy đủ")
    ap.add_argument("--max", type=int, default=0, help="số ảnh tối đa (0 = không giới hạn)")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    if args.sim:
        cfg.camera.source = "folder"
        cfg.uart.port = "sim"
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    cam = make_camera(cfg)
    gui = have_display() and args.label is None
    roi = tuple(cfg.camera.roi)
    uart = fake = None
    if args.auto:
        uart, fake = make_uart(cfg)
        uart.start()
        uart.send_command(MsgType.START, StartPayload(cfg.belt_speed))
    n = 0
    print(f"Lưu vào {out}; Ctrl+C để dừng")
    try:
        while not args.max or n < args.max:
            if args.auto:
                assert uart is not None
                if fake is not None:
                    time.sleep(0.3)
                    fake.trigger_sensor()
                f = uart.next_event(1.0)
                if f is None or f.type != MsgType.EVT_PKG_DETECTED:
                    continue
                time.sleep(cfg.camera.settle_ms / 1000.0)
            else:
                cam.on_package_start()
            frame = cam.capture()
            img = crop_roi(frame, roi) if args.crop else frame
            label = args.label or ask_label(cv2.cvtColor(img, cv2.COLOR_RGB2BGR), gui)
            if label is None:
                break
            if label == "c":
                continue
            path = save(out, label, args.box_id, img, roi, {"source": cam.next_name() or cfg.camera.source})
            n += 1
            print(f"[{n}] {label}: {path}")
            if args.auto:
                from sorter.protocol_gen import SortPayload, SortReason, SortTarget

                from sorter.protocol_gen import EvtPkgDetectedPayload

                seq = EvtPkgDetectedPayload.unpack(f.payload).pkg_seq
                uart.send_command(MsgType.SORT, SortPayload(int(SortTarget.REJECT), int(SortReason.OK), seq))
            if not args.auto and args.sim and n >= len(getattr(cam, "files", [])):
                break
    except KeyboardInterrupt:
        pass
    finally:
        if gui:
            cv2.destroyAllWindows()
        cam.close()
        if uart is not None:
            uart.stop()
        if fake is not None:
            fake.stop()
    print(f"Đã lưu {n} ảnh; metadata: {out / 'metadata.jsonl'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
