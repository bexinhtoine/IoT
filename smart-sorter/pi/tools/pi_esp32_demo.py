#!/usr/bin/env python3
"""Camera AI trên Raspberry Pi -> Điều khiển toàn bộ cơ điện qua ESP32 theo chuẩn SPEC 2.3.

Luồng hoạt động:
  1. Camera trên Pi phát hiện thùng hàng (YOLO-World ONNX)
  2. MobileNetV4 ONNX phân loại bao bì: NGUYÊN VẸN (OK) hay MÓP VỠ (DEFECT)
  3. Gửi lệnh qua UART sang ESP32 để ESP32 kích hoạt chân:
     - Hàng OK     -> ESP32 bật LED xanh (GPIO 32), Còi bíp (GPIO 23), Servo A gạt (GPIO 18)
     - Hàng DEFECT -> ESP32 bật LED đỏ (GPIO 33), không gạt servo (trôi vào khay loại)
     - Băng chuyền -> ESP32 điều khiển PWM L298N (ENA: GPIO 25, IN1: GPIO 26, IN2: GPIO 27)
"""
from __future__ import annotations

import argparse
import glob
import logging
import sys
import time
from contextlib import ExitStack
from pathlib import Path
from typing import Any

import cv2

# Đảm bảo UTF-8 console
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PI_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PI_ROOT.parent
if str(PI_ROOT) not in sys.path:
    sys.path.insert(0, str(PI_ROOT))

from sorter.box_detector import Box, BoxDetector
from sorter.camera import Picamera2Camera
from sorter.config import CameraCfg, DetectionCfg, AiCfg, UartCfg
from sorter.classifier_onnx import OnnxClassifier
from sorter.interfaces import LABEL_OK, LABEL_DEFECT, ClassResult
from sorter.pi_demo_actuators import BoxTriggerGate
from sorter.protocol_gen import (
    MsgType,
    SortPayload,
    SortTarget,
    SortReason,
    StartPayload,
    State,
)
from sorter.uart_link import UartLink

log = logging.getLogger("pi_esp32_demo")


class UsbCamera:
    """Camera USB dùng cho webcam thường."""
    def __init__(self, index: int, resolution: tuple[int, int]) -> None:
        self._capture = cv2.VideoCapture(index)
        if not self._capture.isOpened():
            self._capture.release()
            raise RuntimeError(f"Không thể mở USB camera index {index}")
        self._capture.set(cv2.CAP_PROP_FRAME_WIDTH, resolution[0])
        self._capture.set(cv2.CAP_PROP_FRAME_HEIGHT, resolution[1])

    def capture(self) -> Any:
        ok, frame = self._capture.read()
        if not ok or frame is None:
            raise RuntimeError("Camera USB ngừng gửi hình ảnh")
        return cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

    def close(self) -> None:
        self._capture.release()


def _resolve_model(value: str) -> Path:
    path = Path(value).expanduser()
    candidates = (path, REPO_ROOT / path, Path("/opt/sorter/models") / path.name, PI_ROOT.parent / path)
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise FileNotFoundError(f"Không tìm thấy file model: {value}")


def auto_detect_serial_port() -> str:
    """Tự động tìm cổng UART hoặc cổng USB cắm vào ESP32."""
    if Path("/dev/serial0").exists():
        return "/dev/serial0"
    for usb_port in sorted(glob.glob("/dev/ttyUSB*")):
        return usb_port
    for acm_port in sorted(glob.glob("/dev/ttyACM*")):
        return acm_port
    if Path("/dev/ttyAMA0").exists():
        return "/dev/ttyAMA0"
    return "/dev/serial0"


def _draw_preview(frame_rgb: Any, boxes: list[Box], gate: BoxTriggerGate, cls_result: ClassResult | None, last_action: str) -> Any:
    display = cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2BGR)

    if cls_result is not None:
        if cls_result.label == LABEL_OK:
            box_color = (0, 220, 0)
            cls_text = f"NGUYEN VEN ({cls_result.confidence*100:.0f}%)"
        elif cls_result.label == LABEL_DEFECT:
            box_color = (0, 0, 255)
            cls_text = f"MOP VO / RACH ({cls_result.confidence*100:.0f}%)"
        else:
            box_color = (0, 165, 255)
            cls_text = f"CHUA RO ({cls_result.confidence*100:.0f}%)"
    else:
        box_color = (0, 220, 0)
        cls_text = ""

    for box in boxes:
        x1, y1, x2, y2 = box.xyxy
        cv2.rectangle(display, (x1, y1), (x2, y2), box_color, 2)
        label_str = f"Box {box.confidence:.2f}"
        if cls_text:
            label_str += f" | {cls_text}"
        cv2.putText(display, label_str, (x1, max(24, y1 - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.55, box_color, 2)

    status = "ARMED (CHO HOP)" if gate.armed else "DA CHOT! (CHO LAY HOP RA)"
    status_str = f"{status} | boxes={len(boxes)} stable={gate.present_count}/{gate.stable_frames}"
    if cls_text and len(boxes) > 0:
        status_str += f" | Bao bi: {cls_text}"
    cv2.putText(display, status_str, (16, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 200, 255), 2)

    if last_action:
        cv2.putText(display, f"LENH ESP32: {last_action}", (16, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 0), 2)

    return display


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Camera AI trên Pi -> Điều khiển toàn bộ cơ điện qua ESP32 theo chuẩn SPEC"
    )
    parser.add_argument("--detector", default="ai/models/box_detector.onnx")
    parser.add_argument("--classifier", default="ai/models/packaging_cls.onnx")
    parser.add_argument("--model-card", default="ai/models/model_card.json")
    parser.add_argument("--camera", choices=("picamera2", "usb"), default="picamera2")
    parser.add_argument("--camera-index", type=int, default=0)
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--height", type=int, default=480)
    parser.add_argument("--confidence", type=float, default=0.10)
    parser.add_argument("--min-conf", type=float, default=0.60)
    parser.add_argument("--stable-frames", type=int, default=3)
    parser.add_argument("--clear-frames", type=int, default=5)
    parser.add_argument("--cooldown", type=float, default=2.5)
    parser.add_argument("--port", default=None, help="Cổng UART/USB nối ESP32 (mặc định: tự nhận diện)")
    parser.add_argument("--baud", type=int, default=115200, help="Tốc độ Baud (mặc định: 115200)")
    parser.add_argument("--motor-speed", type=int, default=60, help="Tốc độ băng chuyền %% (1..100)")
    parser.add_argument("--preview", action="store_true", help="Hiển thị màn hình xem trước OpenCV")
    parser.add_argument("--max-events", type=int, default=0, help="0: chạy liên tục cho đến khi Ctrl+C")
    parser.add_argument("--log-level", default="INFO")
    return parser


def run(args: argparse.Namespace) -> int:
    port = args.port or auto_detect_serial_port()
    log.info("Cổng nối với ESP32: %s (Baud: %d)", port, args.baud)

    # 1. Khởi tạo UART Link tới ESP32
    uart_cfg = UartCfg(port=port, baud=args.baud, ack_timeout_ms=300, retries=2, ping_interval_ms=500)
    link = UartLink(uart_cfg)
    try:
        link.start()
    except Exception as e:
        log.error("Không thể mở cổng UART/USB %s: %s", port, e)
        log.error("Nếu dùng UART chân Pi, cần bật UART trong /boot/firmware/config.txt. Hoặc cắm ESP32 bằng cáp USB.")
        return 1

    # Kiểm tra kết nối với ESP32
    log.info("Đang kiểm tra kết nối với ESP32 qua lệnh PING...")
    connected = False
    for i in range(1, 6):
        res = link.ping()
        if res.ok:
            connected = True
            log.info("KẾT NỐI ESP32 THÀNH CÔNG! Trạng thái: %s", link.state.name if link.state else "READY")
            break
        time.sleep(0.4)

    if not connected:
        log.warning("Chưa thấy ESP32 phản hồi PING. (Vẫn tiếp tục nhưng có thể ESP32 chưa nạp firmware)")

    # 2. Khởi tạo AI Detector & Classifier
    detector_path = _resolve_model(args.detector)
    detector_cfg = DetectionCfg(
        backend="yolo_world_onnx",
        model_path=str(detector_path),
        confidence=args.confidence,
        iou_threshold=0.5,
        input_size=640,
        padding=0.1,
        min_area_ratio=0.01,
    )

    classifier = None
    try:
        classifier_path = _resolve_model(args.classifier)
        card_path = _resolve_model(args.model_card) if args.model_card else ""
        ai_cfg = AiCfg(
            backend="onnx",
            model_path=str(classifier_path),
            model_card_path=str(card_path) if card_path else "",
            min_confidence=args.min_conf,
        )
        classifier = OnnxClassifier(ai_cfg)
        log.info("Đã nạp model phân loại bao bì: %s", classifier_path)
    except Exception as e:
        log.warning("Không thể nạp classifier (%s); chỉ chạy detector", e)

    # Khởi động băng chuyền qua ESP32
    if connected:
        link.send_command(MsgType.START, StartPayload(args.motor_speed))
        log.info("ESP32: Đã kích hoạt động cơ băng chuyền (Tốc độ %d%%)", args.motor_speed)

    with ExitStack() as resources:
        detector = BoxDetector(detector_cfg, threads=4)
        resources.callback(detector.close)
        if classifier is not None:
            resources.callback(classifier.close)

        resolution = (args.width, args.height)
        if args.camera == "picamera2":
            camera: Any = Picamera2Camera(CameraCfg(resolution=[args.width, args.height], lock_after_s=2.0))
        else:
            camera = UsbCamera(args.camera_index, resolution)
        resources.callback(camera.close)

        if args.preview:
            resources.callback(cv2.destroyAllWindows)

        resources.callback(link.stop)

        gate = BoxTriggerGate(
            stable_frames=args.stable_frames,
            clear_frames=args.clear_frames,
            cooldown_s=args.cooldown,
        )

        events = 0
        last_action = ""
        latched_cls: ClassResult | None = None

        log.info("Hệ thống Camera AI -> ESP32 đã sẵn sàng hoạt động!")

        while True:
            try:
                frame_rgb = camera.capture()
                boxes = detector.detect(frame_rgb)

                cls_res: ClassResult | None = None
                if len(boxes) == 1 and classifier is not None:
                    bx1, by1, bx2, by2 = boxes[0].xyxy
                    crop = frame_rgb[by1:by2, bx1:bx2]
                    if crop.size > 0:
                        cls_res = classifier.predict(crop)

                # Khi phát hiện một hộp ổn định
                if gate.update(len(boxes), now=time.monotonic()):
                    events += 1
                    latched_cls = cls_res
                    label = cls_res.label if cls_res else LABEL_OK
                    conf = cls_res.confidence if cls_res else 1.0

                    log.info("PHÁT HIỆN KIỆN #%d: Bao bì = %s (%.1f%%)", events, label, conf * 100)

                    if label == LABEL_DEFECT:
                        last_action = "SORT REJECT (ESP32 bật LED ĐỎ)"
                        log.info("-> Gửi lệnh REJECT sang ESP32: LED ĐỎ sáng, không gạt...")
                        link.send_command(MsgType.SORT, SortPayload(int(SortTarget.REJECT), int(SortReason.DEFECT), events))
                    else:
                        last_action = "SORT KHO A (ESP32 bật LED XANH + CÒI + SERVO A)"
                        log.info("-> Gửi lệnh SORT A sang ESP32: LED XANH, CÒI kêu, SERVO A gạt...")
                        link.send_command(MsgType.SORT, SortPayload(int(SortTarget.A), int(SortReason.OK), events))

                    if args.max_events and events >= args.max_events:
                        log.info("Đã đạt số kiện tối đa (%d), dừng.", args.max_events)
                        break

                if gate.armed:
                    latched_cls = None

                active_cls = latched_cls if not gate.armed else cls_res

                if args.preview:
                    cv2.imshow(
                        "Pi Camera AI -> ESP32 Actuators (Q/Esc để thoát)",
                        _draw_preview(frame_rgb, boxes, gate, active_cls, last_action),
                    )
                    if cv2.waitKey(1) & 0xFF in {27, ord("q"), ord("Q")}:
                        break

            except KeyboardInterrupt:
                log.info("Dừng theo yêu cầu người dùng (Ctrl+C)")
                break

    # Dừng động cơ khi kết thúc
    try:
        link.send_command(MsgType.STOP)
    except Exception:
        pass
    log.info("Đã dừng hệ thống an toàn.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    try:
        return run(args)
    except (FileNotFoundError, RuntimeError, ValueError) as error:
        parser.error(str(error))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
