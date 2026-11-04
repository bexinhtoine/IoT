#!/usr/bin/env python3
"""Camera -> one stable cardboard box -> LED + buzzer + servo + motor demo on Raspberry Pi.

Tích hợp:
  1. Box Detector (YOLO-World ONNX): Phát hiện hộp hàng
  2. Packaging Classifier (MobileNetV4 ONNX): Phân loại hộp NGUYÊN VẸN (Normal) hay MÓP VỠ / RÁCH (Damage)
  3. GPIO Actuators: Điều khiển Đèn LED, Còi Buzzer, Động cơ Servo và Động cơ Băng chuyền (L298N)
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from contextlib import ExitStack
from pathlib import Path
from typing import Any

import cv2

PI_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PI_ROOT.parent
if str(PI_ROOT) not in sys.path:
    sys.path.insert(0, str(PI_ROOT))

from sorter.box_detector import Box, BoxDetector
from sorter.camera import Picamera2Camera
from sorter.config import CameraCfg, DetectionCfg, AiCfg
from sorter.classifier_onnx import OnnxClassifier
from sorter.interfaces import LABEL_OK, LABEL_DEFECT, ClassResult
from sorter.pi_demo_actuators import (
    BoxTriggerGate,
    DemoActuatorSequence,
    DemoActuatorTiming,
    GpioZeroActuatorDriver,
    LoggingActuatorDriver,
)

log = logging.getLogger("pi_gpio_demo")


class UsbCamera:
    """Small RGB camera adapter used only for laptop dry runs."""

    def __init__(self, index: int, resolution: tuple[int, int]) -> None:
        self._capture = cv2.VideoCapture(index)
        if not self._capture.isOpened():
            self._capture.release()
            raise RuntimeError(f"cannot open USB camera index {index}")
        self._capture.set(cv2.CAP_PROP_FRAME_WIDTH, resolution[0])
        self._capture.set(cv2.CAP_PROP_FRAME_HEIGHT, resolution[1])

    def capture(self) -> Any:
        ok, frame = self._capture.read()
        if not ok or frame is None:
            raise RuntimeError("USB camera stopped returning frames")
        return cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

    def close(self) -> None:
        self._capture.release()


def _resolve_model(value: str) -> Path:
    path = Path(value).expanduser()
    candidates = (path, REPO_ROOT / path, Path("/opt/sorter/models") / path.name, PI_ROOT.parent / path)
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise FileNotFoundError(f"model not found: {value}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Pi camera box detector & packaging classifier demo with direct LED, active buzzer, servo and motor outputs"
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
    parser.add_argument("--cooldown", type=float, default=2.0)
    parser.add_argument("--led-pin", type=int, default=17, help="BCM number (default: GPIO17)")
    parser.add_argument("--buzzer-pin", type=int, default=27, help="BCM number (default: GPIO27)")
    parser.add_argument("--servo-pin", type=int, default=18, help="BCM number (default: GPIO18)")
    parser.add_argument("--motor-in1", type=int, default=None, help="BCM pin for motor IN1 driver L298N (optional)")
    parser.add_argument("--motor-in2", type=int, default=None, help="BCM pin for motor IN2 driver L298N (optional)")
    parser.add_argument("--motor-en", type=int, default=None, help="BCM pin for motor ENA driver L298N (optional)")
    parser.add_argument("--home-angle", type=float, default=0.0)
    parser.add_argument("--active-angle", type=float, default=45.0)
    parser.add_argument("--buzzer-seconds", type=float, default=0.12)
    parser.add_argument("--servo-hold", type=float, default=0.8)
    parser.add_argument(
        "--pigpio", action="store_true", help="use pigpio daemon for steadier servo PWM"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="log actions without touching GPIO"
    )
    parser.add_argument(
        "--preview", action="store_true", help="show OpenCV preview; needs a desktop"
    )
    parser.add_argument(
        "--max-events", type=int, default=0, help="0 means run until Ctrl+C"
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="run LED, buzzer, servo and motor once without camera or model",
    )
    parser.add_argument("--log-level", default="INFO")
    return parser


def _validate_args(args: argparse.Namespace) -> None:
    if args.width <= 0 or args.height <= 0:
        raise ValueError("camera width and height must be positive")
    if not 0 < args.confidence <= 1:
        raise ValueError("confidence must be in (0, 1]")
    if args.max_events < 0:
        raise ValueError("max-events must be non-negative")
    pins = [args.led_pin, args.buzzer_pin, args.servo_pin]
    for m_pin in (args.motor_in1, args.motor_in2, args.motor_en):
        if m_pin is not None:
            pins.append(m_pin)
    if any(pin < 0 or pin > 27 for pin in pins) or len(set(pins)) != len(pins):
        raise ValueError("GPIO pins must be distinct BCM numbers in 0..27")


def _draw_preview(frame_rgb: Any, boxes: list[Box], gate: BoxTriggerGate, cls_result: ClassResult | None) -> Any:
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
        cv2.putText(
            display,
            label_str,
            (x1, max(24, y1 - 8)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            box_color,
            2,
        )

    status = "ARMED (CHO HOP)" if gate.armed else "DA CHOT! (CHO LAY HOP RA)"
    status_str = f"{status} | boxes={len(boxes)} stable={gate.present_count}/{gate.stable_frames}"
    if cls_text and len(boxes) > 0:
        status_str += f" | Bao bi: {cls_text}"
    cv2.putText(
        display,
        status_str,
        (16, 32),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.55,
        (0, 200, 255),
        2,
    )
    return display


def _make_sequence(args: argparse.Namespace) -> DemoActuatorSequence:
    if args.dry_run:
        driver = LoggingActuatorDriver(lambda event: log.info("DRY_RUN %s", event))
    else:
        driver = GpioZeroActuatorDriver(
            led_pin=args.led_pin,
            buzzer_pin=args.buzzer_pin,
            servo_pin=args.servo_pin,
            motor_in1=args.motor_in1,
            motor_in2=args.motor_in2,
            motor_en=args.motor_en,
            home_angle=args.home_angle,
            use_pigpio=args.pigpio,
        )
    return DemoActuatorSequence(
        driver,
        home_angle=args.home_angle,
        active_angle=args.active_angle,
        timing=DemoActuatorTiming(
            buzzer_s=args.buzzer_seconds,
            servo_hold_s=args.servo_hold,
        ),
    )


def run(args: argparse.Namespace) -> int:
    _validate_args(args)
    if args.self_test:
        sequence = _make_sequence(args)
        try:
            sequence.activate()
            log.info("actuator self-test completed (LED, buzzer, servo, motor)")
            return 0
        finally:
            sequence.close()

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
        log.info("loaded packaging classifier: %s", classifier_path)
    except Exception as e:
        log.warning("could not load packaging classifier (%s); running detector only", e)

    with ExitStack() as resources:
        detector = BoxDetector(detector_cfg, threads=4)
        resources.callback(detector.close)
        if classifier is not None:
            resources.callback(classifier.close)

        resolution = (args.width, args.height)
        if args.camera == "picamera2":
            camera: Any = Picamera2Camera(
                CameraCfg(resolution=[args.width, args.height], lock_after_s=2.0)
            )
        else:
            camera = UsbCamera(args.camera_index, resolution)
        resources.callback(camera.close)
        sequence = _make_sequence(args)
        resources.callback(sequence.close)
        if args.preview:
            resources.callback(cv2.destroyAllWindows)
        gate = BoxTriggerGate(
            stable_frames=args.stable_frames,
            clear_frames=args.clear_frames,
            cooldown_s=args.cooldown,
        )
        events = 0
        log.info(
            "ready detector=%s classifier=%s camera=%s GPIO led=%d buzzer=%d servo=%d motor=%s dry_run=%s",
            detector_path,
            args.classifier if classifier else "None",
            args.camera,
            args.led_pin,
            args.buzzer_pin,
            args.servo_pin,
            f"in1={args.motor_in1},in2={args.motor_in2}" if args.motor_in1 is not None else "None",
            args.dry_run,
        )
        latched_cls: ClassResult | None = None
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

                if gate.update(len(boxes), now=time.monotonic()):
                    events += 1
                    latched_cls = cls_res
                    label_desc = cls_res.label if cls_res else "UNKNOWN"
                    conf_desc = cls_res.confidence if cls_res else 0.0
                    log.info(
                        "BOX_TRIGGER event=%d box_conf=%.3f packaging=%s (%.1f%%)",
                        events,
                        boxes[0].confidence,
                        label_desc,
                        conf_desc * 100,
                    )
                    sequence.activate()
                    if args.max_events and events >= args.max_events:
                        return 0

                if gate.armed:
                    latched_cls = None

                active_cls = latched_cls if not gate.armed else cls_res

                if args.preview:
                    cv2.imshow(
                        "Pi smart sorter GPIO demo - Q/Esc quit",
                        _draw_preview(frame_rgb, boxes, gate, active_cls),
                    )
                    if cv2.waitKey(1) & 0xFF in {27, ord("q"), ord("Q")}:
                        return 0
            except KeyboardInterrupt:
                log.info("stopped by user")
                return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    try:
        return run(args)
    except (FileNotFoundError, RuntimeError, ValueError) as error:
        parser.error(str(error))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
