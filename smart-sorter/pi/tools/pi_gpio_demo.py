#!/usr/bin/env python3
"""Bản Demo Hoàn Chỉnh (Chưa cần Web) - Trực tiếp trên Raspberry Pi GPIO.

Đúng theo yêu cầu của bạn:
  1. Động cơ DC (Băng chuyền) bình thường CHẠY.
  2. Khi camera NHẬN DIỆN ĐƯỢC HỘP -> ĐỘNG CƠ DC DỪNG LẠI (đứng yên).
  3. Phân loại bằng AI (MobileNetV4):
     - HÀNG NGUYÊN VẸN (OK):
         + Đèn XANH nhấp nháy
         + Còi KHÔNG kêu
         + Servo xoay 90 ĐỘ để gạt -> Gạt xong quay về 0 ĐỘ
     - HÀNG LỖI (MÓP VỠ / RÁCH):
         + Đèn ĐỎ nhấp nháy
         + CÒI KÊU BÍP BÍP cảnh báo
         + Servo xoay 180 ĐỘ -> Gạt xong quay về 0 ĐỘ
  4. Sau khi servo quay về 0 độ -> ĐỘNG CƠ DC CHẠY TIẾP để đón kiện tiếp theo.
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

# Cấu hình UTF-8 cho console
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PI_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PI_ROOT.parent
if str(PI_ROOT) not in sys.path:
    sys.path.insert(0, str(PI_ROOT))

from sorter.box_detector import Box, BoxDetector
from sorter.camera import Picamera2Camera
from sorter.config import CameraCfg, DetectionCfg, AiCfg
from sorter.classifier_onnx import OnnxClassifier
from sorter.interfaces import LABEL_OK, LABEL_DEFECT, ClassResult
from sorter.pi_demo_actuators import BoxTriggerGate

try:
    from gpiozero import AngularServo, LED, Buzzer, Motor
    HAS_GPIO = True
except ImportError:
    HAS_GPIO = False

log = logging.getLogger("pi_gpio_demo")


class UsbCamera:
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
            raise RuntimeError("Camera ngừng gửi khung hình")
        return cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

    def close(self) -> None:
        self._capture.release()


def _resolve_model(value: str) -> Path:
    path = Path(value).expanduser()
    candidates = (path, REPO_ROOT / path, Path("/opt/sorter/models") / path.name, PI_ROOT.parent / path)
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise FileNotFoundError(f"Không tìm thấy model: {value}")


class HardwareController:
    """Điều khiển trực tiếp Đèn Xanh, Đèn Đỏ, Còi, 1 Servo và Động cơ DC."""

    def __init__(
        self,
        led_green_pin: int = 17,
        led_red_pin: int = 22,
        buzzer_pin: int = 27,
        servo_pin: int = 18,
        motor_in1: int = 23,
        motor_in2: int = 24,
        dry_run: bool = False,
    ):
        self.dry_run = dry_run or (not HAS_GPIO)
        if not HAS_GPIO and not dry_run:
            log.warning("Chưa có thư viện gpiozero. Đang chạy chế độ giả lập (Dry-Run).")

        self.led_green = None
        self.led_red = None
        self.buzzer = None
        self.servo = None
        self.motor = None

        if not self.dry_run:
            try:
                self.led_green = LED(led_green_pin)
                self.led_red = LED(led_red_pin)
                self.buzzer = Buzzer(buzzer_pin)
                # Servo SG90: 0 đến 180 độ
                self.servo = AngularServo(
                    servo_pin,
                    min_angle=0,
                    max_angle=180,
                    min_pulse_width=0.0005,
                    max_pulse_width=0.0025,
                )
                self.servo.angle = 0  # Góc ban đầu
                time.sleep(0.3)
                self.detach_servo()
                if motor_in1 is not None and motor_in2 is not None:
                    self.motor = Motor(forward=motor_in1, backward=motor_in2)
            except Exception as e:
                log.error("Lỗi khởi tạo phần cứng GPIO: %s", e)
                self.dry_run = True

    def detach_servo(self):
        """Ngắt xung PWM tạm thời để servo không bị rung kêu rè rè."""
        if self.servo:
            try:
                self.servo.detach()
            except Exception:
                pass

    def start_motor(self):
        """Băng chuyền quay."""
        if self.motor:
            self.motor.forward()
        log.info("[BĂNG CHUYỀN] -> Đang quay (chờ kiện hàng)...")

    def stop_motor(self):
        """Băng chuyền dừng lại khi đang nhận diện hàng."""
        if self.motor:
            self.motor.stop()
        log.info("[BĂNG CHUYỀN] -> ĐÃ DỪNG LẠI để phân loại hàng!")

    def action_valid_package(self, hold_s: float = 1.2):
        """HÀNG NGUYÊN VẸN: LED Xanh nháy, còi im, Servo xoay 90 độ rồi về 0."""
        log.info("==> [KÍCH HOẠT HÀNG OK]: Đèn Xanh nháy | Còi im | Servo xoay 90 độ!")
        if not self.dry_run and self.servo:
            self.servo.angle = 90

        # Nháy đèn xanh 3 lần
        for _ in range(3):
            if self.led_green:
                self.led_green.on()
            time.sleep(0.18)
            if self.led_green:
                self.led_green.off()
            time.sleep(0.18)

        time.sleep(max(0.1, hold_s - 1.0))

        # Quay servo về 0 độ
        log.info("==> Servo trả về vị trí ban đầu (0 độ)")
        if not self.dry_run and self.servo:
            self.servo.angle = 0
            time.sleep(0.4)
            self.detach_servo()

    def action_defect_package(self, hold_s: float = 1.2):
        """HÀNG LỖI: LED Đỏ nháy, CÒI KÊU BÍP BÍP, Servo xoay 180 độ rồi về 0."""
        log.info("==> [KÍCH HOẠT HÀNG LỖI]: Đèn Đỏ nháy | CÒI BÍP BÍP | Servo xoay 180 độ!")
        if not self.dry_run and self.servo:
            self.servo.angle = 180

        # Nháy đèn đỏ và bíp còi 4 nhịp
        for _ in range(4):
            if self.led_red:
                self.led_red.on()
            if self.buzzer:
                self.buzzer.on()
            time.sleep(0.15)
            if self.led_red:
                self.led_red.off()
            if self.buzzer:
                self.buzzer.off()
            time.sleep(0.15)

        time.sleep(max(0.1, hold_s - 1.2))

        # Quay servo về 0 độ
        log.info("==> Servo trả về vị trí ban đầu (0 độ)")
        if not self.dry_run and self.servo:
            self.servo.angle = 0
            time.sleep(0.4)
            self.detach_servo()

    def close(self):
        self.stop_motor()
        if self.servo:
            try:
                self.servo.angle = 0
                time.sleep(0.2)
                self.servo.close()
            except Exception:
                pass
        if self.led_green:
            self.led_green.close()
        if self.led_red:
            self.led_red.close()
        if self.buzzer:
            self.buzzer.close()
        if self.motor:
            self.motor.close()


def _draw_preview(frame_rgb: Any, boxes: list[Box], gate: BoxTriggerGate, cls_result: ClassResult | None, status_action: str) -> Any:
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

    status = "BANG CHUYEN CHAY (CHO HOP)" if gate.armed else "DANG XU LY (BANG CHUYEN DUNG)"
    status_str = f"{status} | stable={gate.present_count}/{gate.stable_frames}"
    cv2.putText(display, status_str, (16, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 200, 255), 2)

    if status_action:
        cv2.putText(display, status_action, (16, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)

    return display


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Demo Phân Loại Hàng Hóa Thực Tế Trên Raspberry Pi")
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

    # Chân cắm trên Raspberry Pi
    parser.add_argument("--led-green", type=int, default=17, help="BCM pin LED Xanh (mặc định: GPIO 17 - Chân 11)")
    parser.add_argument("--led-red", type=int, default=22, help="BCM pin LED Đỏ (mặc định: GPIO 22 - Chân 15)")
    parser.add_argument("--buzzer-pin", type=int, default=27, help="BCM pin Còi Buzzer (mặc định: GPIO 27 - Chân 13)")
    parser.add_argument("--servo-pin", type=int, default=18, help="BCM pin Servo (mặc định: GPIO 18 - Chân 12)")
    parser.add_argument("--motor-in1", type=int, default=23, help="BCM pin Motor L298N IN1 (mặc định: GPIO 23 - Chân 16)")
    parser.add_argument("--motor-in2", type=int, default=24, help="BCM pin Motor L298N IN2 (mặc định: GPIO 24 - Chân 18)")

    parser.add_argument("--servo-hold", type=float, default=1.2, help="Thời gian giữ servo gạt (giây)")
    parser.add_argument("--dry-run", action="store_true", help="Chạy giả lập log, không xuất điện ra GPIO")
    parser.add_argument("--preview", action="store_true", help="Hiển thị màn hình camera xem trước")
    parser.add_argument("--max-events", type=int, default=0)
    parser.add_argument("--log-level", default="INFO")
    return parser


def run(args: argparse.Namespace) -> int:
    print("=" * 65)
    print(" KHỞI ĐỘNG HỆ THỐNG PHÂN LOẠI HỘP THỰC TẾ (DEMO)")
    print("=" * 65)
    print(f"  * LED XANH (Hàng OK)    : GPIO {args.led_green} (Chân 11)")
    print(f"  * LED ĐỎ (Hàng Lỗi)     : GPIO {args.led_red} (Chân 15)")
    print(f"  * CÒI BUZZER (Báo lỗi)  : GPIO {args.buzzer_pin} (Chân 13)")
    print(f"  * SERVO (90° OK / 180°): GPIO {args.servo_pin} (Chân 12)")
    print(f"  * BĂNG CHUYỀN L298N     : GPIO {args.motor_in1} & {args.motor_in2} (Chân 16 & 18)")
    print("-" * 65)

    hw = HardwareController(
        led_green_pin=args.led_green,
        led_red_pin=args.led_red,
        buzzer_pin=args.buzzer_pin,
        servo_pin=args.servo_pin,
        motor_in1=args.motor_in1,
        motor_in2=args.motor_in2,
        dry_run=args.dry_run,
    )

    detector_path = _resolve_model(args.detector)
    detector = BoxDetector(
        DetectionCfg(
            backend="yolo_world_onnx",
            model_path=str(detector_path),
            confidence=args.confidence,
            iou_threshold=0.5,
            input_size=640,
            padding=0.1,
            min_area_ratio=0.01,
        ),
        threads=4,
    )

    classifier_path = _resolve_model(args.classifier)
    classifier = OnnxClassifier(
        AiCfg(
            backend="onnx",
            model_path=str(classifier_path),
            min_confidence=args.min_conf,
        )
    )

    resolution = (args.width, args.height)
    if args.camera == "picamera2":
        camera: Any = Picamera2Camera(CameraCfg(resolution=[args.width, args.height], lock_after_s=2.0))
    else:
        camera = UsbCamera(args.camera_index, resolution)

    gate = BoxTriggerGate(
        stable_frames=args.stable_frames,
        clear_frames=args.clear_frames,
        cooldown_s=args.cooldown,
    )

    # 1. Bắt đầu: Động cơ băng chuyền quay để đưa hàng vào
    hw.start_motor()

    events = 0
    status_action = ""
    latched_cls: ClassResult | None = None

    try:
        while True:
            frame_rgb = camera.capture()
            boxes = detector.detect(frame_rgb)

            cls_res: ClassResult | None = None
            if len(boxes) == 1:
                bx1, by1, bx2, by2 = boxes[0].xyxy
                crop = frame_rgb[by1:by2, bx1:bx2]
                if crop.size > 0:
                    cls_res = classifier.predict(crop)

            # 2. Khi phát hiện thấy kiện hàng ổn định trước camera
            if gate.update(len(boxes), now=time.monotonic()):
                events += 1
                latched_cls = cls_res

                # A. DỪNG NGAY BĂNG CHUYỀN
                hw.stop_motor()

                # Cập nhật hiển thị màn hình
                is_ok = (cls_res is not None and cls_res.label == LABEL_OK)
                if is_ok:
                    status_action = f"KIEN #{events}: HANG NGUYEN -> LED XANH + SERVO 90 DO"
                    print(f"\n[+] KIỆN #{events}: HÀNG NGUYÊN VẸN ({cls_res.confidence*100:.1f}%)")
                    # B. KÍCH HOẠT HÀNG OK: LED xanh nháy, servo 90 độ, còi không kêu
                    hw.action_valid_package(hold_s=args.servo_hold)
                else:
                    conf = cls_res.confidence if cls_res else 0.0
                    status_action = f"KIEN #{events}: HANG LOI -> LED DO + COI + SERVO 180 DO"
                    print(f"\n[!] KIỆN #{events}: HÀNG LỖI MÓP/RÁCH ({conf*100:.1f}%)")
                    # C. KÍCH HOẠT HÀNG LỖI: LED đỏ nháy, CÒI BÍP BÍP, servo 180 độ
                    hw.action_defect_package(hold_s=args.servo_hold)

                # D. SAU KHI SERVO QUAY XONG VÀ VỀ 0 -> BẬT LẠI BĂNG CHUYỀN CHẠY TIẾP
                print("[*] Đã gạt xong -> Tiếp tục bật băng chuyền chạy đón kiện tiếp theo...")
                hw.start_motor()

                if args.max_events and events >= args.max_events:
                    print(f"Đã hoàn thành {args.max_events} kiện. Kết thúc.")
                    break

            if gate.armed:
                latched_cls = None

            active_cls = latched_cls if not gate.armed else cls_res

            if args.preview:
                cv2.imshow(
                    "Smart Sorter Demo - Nhan Q de thoat",
                    _draw_preview(frame_rgb, boxes, gate, active_cls, status_action),
                )
                if cv2.waitKey(1) & 0xFF in {27, ord("q"), ord("Q")}:
                    break

    except KeyboardInterrupt:
        print("\n[*] Dừng chương trình theo lệnh người dùng...")
    finally:
        hw.close()
        camera.close()
        detector.close()
        classifier.close()
        if args.preview:
            cv2.destroyAllWindows()

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
