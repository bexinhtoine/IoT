#!/usr/bin/env python3
"""Camera -> one stable cardboard box -> LED + buzzer + servo demo on Raspberry Pi."""

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
from sorter.config import CameraCfg, DetectionCfg
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
    candidates = (path, REPO_ROOT / path, Path("/opt/sorter/models") / path.name)
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise FileNotFoundError(f"box detector not found: {value}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Pi camera box detector demo with direct LED, active buzzer and servo outputs"
    )
    parser.add_argument("--detector", default="ai/models/box_detector.onnx")
    parser.add_argument("--camera", choices=("picamera2", "usb"), default="picamera2")
    parser.add_argument("--camera-index", type=int, default=0)
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=960)
    parser.add_argument("--confidence", type=float, default=0.35)
    parser.add_argument("--stable-frames", type=int, default=3)
    parser.add_argument("--clear-frames", type=int, default=5)
    parser.add_argument("--cooldown", type=float, default=2.0)
    parser.add_argument("--led-pin", type=int, default=17, help="BCM number")
    parser.add_argument("--buzzer-pin", type=int, default=27, help="BCM number")
    parser.add_argument("--servo-pin", type=int, default=18, help="BCM number")
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
        help="run LED, buzzer and servo once without camera or model",
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
    pins = (args.led_pin, args.buzzer_pin, args.servo_pin)
    if any(pin < 0 or pin > 27 for pin in pins) or len(set(pins)) != 3:
        raise ValueError("GPIO pins must be distinct BCM numbers in 0..27")


def _draw_preview(frame_rgb: Any, boxes: list[Box], gate: BoxTriggerGate) -> Any:
    display = cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2BGR)
    for box in boxes:
        x1, y1, x2, y2 = box.xyxy
        cv2.rectangle(display, (x1, y1), (x2, y2), (0, 220, 0), 2)
        cv2.putText(
            display,
            f"cardboard box {box.confidence:.2f}",
            (x1, max(24, y1 - 8)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.65,
            (0, 220, 0),
            2,
        )
    status = "ARMED" if gate.armed else "WAIT UNTIL BOX LEAVES"
    cv2.putText(
        display,
        f"{status} | boxes={len(boxes)} stable={gate.present_count}/{gate.stable_frames}",
        (16, 32),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.65,
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
            log.info("actuator self-test completed")
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
    with ExitStack() as resources:
        detector = BoxDetector(detector_cfg, threads=4)
        resources.callback(detector.close)
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
            "ready detector=%s camera=%s GPIO led=%d buzzer=%d servo=%d dry_run=%s",
            detector_path,
            args.camera,
            args.led_pin,
            args.buzzer_pin,
            args.servo_pin,
            args.dry_run,
        )
        while True:
            try:
                frame_rgb = camera.capture()
                boxes = detector.detect(frame_rgb)
                if gate.update(len(boxes), now=time.monotonic()):
                    events += 1
                    log.info(
                        "BOX_TRIGGER event=%d confidence=%.3f",
                        events,
                        boxes[0].confidence,
                    )
                    sequence.activate()
                    if args.max_events and events >= args.max_events:
                        return 0
                if args.preview:
                    cv2.imshow(
                        "Pi smart sorter GPIO demo - Q/Esc quit",
                        _draw_preview(frame_rgb, boxes, gate),
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
