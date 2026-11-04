#!/usr/bin/env python3
"""Công cụ kiểm thử Camera máy tính cho Smart Logistics Sorter.

Hỗ trợ 2 chế độ:
  1. Chế độ Chốt kiện (Package Trigger Gate): Giả lập cảm biến siêu âm bằng thị giác.
     Khi đưa hộp vào -> Chốt phân loại & QR đúng 1 lần -> Khóa kết quả -> Chờ lấy hộp ra mới đón hộp mới.
  2. Chế độ Quét liên tục (Continuous Mode): Phân loại từng khung hình thời gian thực.

Cách dùng:
  python test_camera.py                         # Mở camera với chế độ Chốt từng kiện (Khuyên dùng)
  python test_camera.py --continuous            # Mở camera ở chế độ quét liên tục
  python test_camera.py --cam 1                 # Chọn camera phụ
"""
from __future__ import annotations

import argparse
import datetime
import json
import logging
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

# Đảm bảo UTF-8 trên Windows console
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import cv2
import numpy as np

# Thiết lập đường dẫn import
PI_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PI_ROOT.parent
sys.path.insert(0, str(PI_ROOT))

from sorter.config import AiCfg, DetectionCfg
from sorter.box_detector import BoxDetector, Box
from sorter.classifier_onnx import OnnxClassifier
from sorter.interfaces import LABEL_DEFECT, LABEL_OK, LABEL_UNCERTAIN, ClassResult
from sorter.pi_demo_actuators import BoxTriggerGate

try:
    from pyzbar import pyzbar as _pyzbar
    from pyzbar.pyzbar import ZBarSymbol as _ZBarSymbol
    HAVE_PYZBAR = True
except Exception:
    _pyzbar = None
    _ZBarSymbol = None
    HAVE_PYZBAR = False

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("test_camera_ai")


@dataclass
class QrVisualItem:
    text: str
    polygon: list[tuple[int, int]]
    rect: tuple[int, int, int, int]
    pkg_id: str | None = None
    warehouse: str | None = None


@dataclass
class LatchedPackage:
    pkg_seq: int
    pkg_id: str | None
    warehouse: str | None
    qr_text: str | None
    label: str
    confidence: float
    probs: dict[str, float]
    timestamp: str
    box_xyxy: tuple[int, int, int, int]


def resolve_model_path(relative_or_abs: str | Path) -> Path:
    p = Path(relative_or_abs)
    if p.is_file():
        return p
    cand1 = REPO_ROOT / p
    if cand1.is_file():
        return cand1
    cand2 = PI_ROOT / p
    if cand2.is_file():
        return cand2
    return p


def parse_qr_payload(text: str) -> tuple[str | None, str | None]:
    """Phân tích nội dung mã QR: PKGxxxxxx|Kho|Timestamp|Sig."""
    parts = text.strip().split("|")
    pkg_id = None
    warehouse = None
    if len(parts) >= 2:
        if re.match(r"^PKG\d{3,}$", parts[0], re.IGNORECASE):
            pkg_id = parts[0].upper()
            if parts[1].upper() in ("A", "B"):
                warehouse = f"Kho {parts[1].upper()}"
    return pkg_id, warehouse


def scan_qr_with_geometry(frame_bgr: np.ndarray) -> list[QrVisualItem]:
    """Tìm mã QR và lấy tọa độ hình học (polygon/rect)."""
    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    results: list[QrVisualItem] = []

    # 1. Thử dùng pyzbar
    if HAVE_PYZBAR:
        try:
            objs = _pyzbar.decode(gray, symbols=[_ZBarSymbol.QRCODE])
            for o in objs:
                try:
                    text = o.data.decode("utf-8")
                except UnicodeDecodeError:
                    text = o.data.decode("latin-1", errors="replace")
                poly = [(p.x, p.y) for p in o.polygon] if hasattr(o, "polygon") else []
                rect = (o.rect.left, o.rect.top, o.rect.width, o.rect.height)
                pkg_id, warehouse = parse_qr_payload(text)
                results.append(QrVisualItem(text=text, polygon=poly, rect=rect, pkg_id=pkg_id, warehouse=warehouse))
        except Exception as e:
            log.debug("pyzbar qr error: %s", e)

    # 2. Dự phòng: cv2.QRCodeDetector
    if not results:
        detector = cv2.QRCodeDetector()
        try:
            ok, infos, points, _ = detector.detectAndDecodeMulti(gray)
            if ok and infos is not None:
                for idx, text in enumerate(infos):
                    if text:
                        pts = points[idx] if points is not None and idx < len(points) else None
                        poly = []
                        rect = (0, 0, 0, 0)
                        if pts is not None:
                            pts_int = pts.astype(int)
                            poly = [(int(p[0]), int(p[1])) for p in pts_int]
                            min_x = int(np.min(pts_int[:, 0]))
                            min_y = int(np.min(pts_int[:, 1]))
                            max_x = int(np.max(pts_int[:, 0]))
                            max_y = int(np.max(pts_int[:, 1]))
                            rect = (min_x, min_y, max_x - min_x, max_y - min_y)
                        pkg_id, warehouse = parse_qr_payload(text)
                        results.append(QrVisualItem(text=text, polygon=poly, rect=rect, pkg_id=pkg_id, warehouse=warehouse))
        except Exception as e:
            log.debug("cv2 qr error: %s", e)

    return results


def draw_header_badge(img: np.ndarray, text: str, bg_color: tuple[int, int, int], x: int, y: int) -> int:
    font = cv2.FONT_HERSHEY_DUPLEX
    scale = 0.52
    thick = 1
    (tw, th), baseline = cv2.getTextSize(text, font, scale, thick)
    pad = 6
    cv2.rectangle(img, (x, y - th - pad), (x + tw + pad * 2, y + pad), bg_color, -1)
    cv2.putText(img, text, (x + pad, y), font, scale, (255, 255, 255), thick, cv2.LINE_AA)
    return x + tw + pad * 2 + 10


def draw_styled_box(img: np.ndarray, x1: int, y1: int, x2: int, y2: int, color: tuple[int, int, int], label: str):
    cv2.rectangle(img, (x1, y1), (x2, y2), color, 2)
    corner_len = min(20, max(5, (x2 - x1) // 5), max(5, (y2 - y1) // 5))
    thick = 3
    cv2.line(img, (x1, y1), (x1 + corner_len, y1), color, thick)
    cv2.line(img, (x1, y1), (x1, y1 + corner_len), color, thick)
    cv2.line(img, (x2, y1), (x2 - corner_len, y1), color, thick)
    cv2.line(img, (x2, y1), (x2, y1 + corner_len), color, thick)
    cv2.line(img, (x1, y2), (x1 + corner_len, y2), color, thick)
    cv2.line(img, (x1, y2), (x1, y1 - corner_len), color, thick)
    cv2.line(img, (x2, y2), (x2 - corner_len, y2), color, thick)
    cv2.line(img, (x2, y2), (x2, y2 - corner_len), color, thick)

    font = cv2.FONT_HERSHEY_DUPLEX
    scale = 0.5
    (tw, th), _ = cv2.getTextSize(label, font, scale, 1)
    cv2.rectangle(img, (x1, max(0, y1 - th - 8)), (x1 + tw + 10, y1), color, -1)
    cv2.putText(img, label, (x1 + 5, max(th + 2, y1 - 4)), font, scale, (255, 255, 255), 1, cv2.LINE_AA)


def draw_qr_highlight(img: np.ndarray, item: QrVisualItem):
    color = (255, 140, 0)
    if item.polygon and len(item.polygon) >= 3:
        pts = np.array(item.polygon, dtype=np.int32).reshape((-1, 1, 2))
        cv2.polylines(img, [pts], isClosed=True, color=color, thickness=2, lineType=cv2.LINE_AA)
        x, y = item.polygon[0]
    else:
        bx, by, bw, bh = item.rect
        cv2.rectangle(img, (bx, by), (bx + bw, by + bh), color, 2, cv2.LINE_AA)
        x, y = bx, by

    if item.pkg_id and item.warehouse:
        tag = f"QR [{item.pkg_id} -> {item.warehouse}]"
    else:
        tag = f"QR: {item.text[:20]}"
    font = cv2.FONT_HERSHEY_DUPLEX
    scale = 0.48
    (tw, th), _ = cv2.getTextSize(tag, font, scale, 1)
    tag_y = max(th + 4, y - 6)
    cv2.rectangle(img, (x, tag_y - th - 4), (x + tw + 8, tag_y + 4), color, -1)
    cv2.putText(img, tag, (x + 4, tag_y), font, scale, (15, 15, 15), 1, cv2.LINE_AA)


def main():
    parser = argparse.ArgumentParser(description="Mở camera máy tính test nhận diện hộp, phân loại bao bì & đọc mã QR")
    parser.add_argument("--cam", type=int, default=0, help="ID của webcam (mặc định: 0)")
    parser.add_argument("--source", type=str, default=None, help="Đường dẫn file video hoặc ảnh thay cho webcam")
    parser.add_argument("--detector", type=str, default="smart-sorter/ai/models/box_detector.onnx",
                        help="Đường dẫn model phát hiện hộp ONNX")
    parser.add_argument("--classifier", type=str, default="smart-sorter/ai/models/packaging_cls.onnx",
                        help="Đường dẫn model phân loại bao bì ONNX")
    parser.add_argument("--model-card", type=str, default="smart-sorter/ai/models/model_card.json",
                        help="Đường dẫn model_card.json")
    parser.add_argument("--box-conf", type=float, default=0.30, help="Ngưỡng tin cậy phát hiện hộp (mặc định: 0.30)")
    parser.add_argument("--min-conf", type=float, default=0.60, help="Ngưỡng tin cậy tối thiểu phân loại (mặc định: 0.60)")
    parser.add_argument("--width", type=int, default=1280, help="Độ rộng khung hình camera")
    parser.add_argument("--height", type=int, default=720, help="Độ cao khung hình camera")
    parser.add_argument("--continuous", action="store_true",
                        help="Chạy quét liên tục mọi khung hình (không chốt kiện)")
    parser.add_argument("--always-classify", action="store_true",
                        help="Luôn phân loại vùng giữa màn hình ngay cả khi chưa phát hiện hộp 3D")
    args = parser.parse_args()

    # 1. Tìm và nạp models
    det_path = resolve_model_path(args.detector)
    cls_path = resolve_model_path(args.classifier)
    card_path = resolve_model_path(args.model_card)

    if not det_path.is_file():
        sys.exit(f"Lỗi: Không tìm thấy file model detector tại {det_path}")
    if not cls_path.is_file():
        sys.exit(f"Lỗi: Không tìm thấy file model classifier tại {cls_path}")

    print("=" * 75)
    print(" KHỞI ĐỘNG HỆ THỐNG TEST CAMERA AI (HỘP HÀNG - BAO BÌ - MÃ QR)")
    print("=" * 75)
    print(f"[*] Detector Model  : {det_path}")
    print(f"[*] Classifier Model: {cls_path}")
    if card_path.is_file():
        print(f"[*] Model Card      : {card_path}")

    det_cfg = DetectionCfg(model_path=str(det_path), confidence=args.box_conf)
    detector = BoxDetector(det_cfg)
    print("[+] BoxDetector đã sẵn sàng (Cardboard Box YOLO-World)")

    ai_cfg = AiCfg(backend="onnx", model_path=str(cls_path),
                   model_card_path=str(card_path) if card_path.is_file() else "",
                   min_confidence=args.min_conf)
    classifier = OnnxClassifier(ai_cfg)
    print(f"[+] OnnxClassifier đã sẵn sàng (Classes: {classifier.spec.classes})")
    print("[+] QR Scanner đã sẵn sàng (pyzbar + OpenCV)")

    # 2. Khởi tạo Cổng chốt kiện (BoxTriggerGate) thay thế cảm biến siêu âm
    trigger_mode = not args.continuous
    gate = BoxTriggerGate(stable_frames=3, clear_frames=5, cooldown_s=1.5)
    package_counter = 0
    latched_package: LatchedPackage | None = None
    trigger_flash_until = 0.0

    # 3. Mở Camera hoặc Video
    is_image = False
    if args.source:
        source_p = Path(args.source)
        if not source_p.exists():
            sys.exit(f"Lỗi: Nguồn '{args.source}' không tồn tại")
        if source_p.suffix.lower() in [".jpg", ".jpeg", ".png", ".bmp"]:
            is_image = True
            cap = None
        else:
            cap = cv2.VideoCapture(str(source_p))
    else:
        cap = cv2.VideoCapture(args.cam, cv2.CAP_DSHOW)
        if not cap.isOpened():
            cap = cv2.VideoCapture(args.cam)
        if not cap.isOpened():
            sys.exit(f"Lỗi: Không mở được Camera ID={args.cam}. Hãy kiểm tra quyền camera hoặc thử --cam 1")
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, args.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, args.height)

    output_dir = REPO_ROOT / "runs" / "camera_tests"
    output_dir.mkdir(parents=True, exist_ok=True)

    print("\n--- PHÍM TẮT ĐIỀU KHIỂN ---")
    print("  [M]            : Chuyển đổi giữa chế độ 'Chốt từng kiện' và 'Quét liên tục'")
    print("  [S]            : Chụp và lưu ảnh kết quả vào thư mục runs/camera_tests/")
    print("  [C]            : Bật/Tắt chế độ phân loại vùng giữa khi chưa thấy hộp 3D")
    print("  [SPACE]        : Tạm dừng / Tiếp tục video")
    print("  [Q] hoặc [ESC] : Thoát chương trình")
    print("-" * 75)

    fps_list = []
    always_classify = args.always_classify
    paused = False
    last_frame = None
    save_msg_until = 0.0

    window_name = "Smart Sorter - Live Camera AI Test"
    cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(window_name, 1024, 620)

    try:
        while True:
            if not paused:
                if is_image:
                    frame = cv2.imread(str(args.source))
                else:
                    ret, frame = cap.read()
                    if not ret:
                        if args.source:
                            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                            continue
                        else:
                            print("Không nhận được tín hiệu từ camera!")
                            break
                last_frame = frame
            else:
                frame = last_frame.copy() if last_frame is not None else None
                if frame is None:
                    continue

            h, w = frame.shape[:2]
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            now_ts = time.time()

            t0 = time.perf_counter()

            # A. PHÁT HIỆN HỘP (Box Detection)
            t_det_start = time.perf_counter()
            boxes: list[Box] = detector.detect(rgb)
            det_ms = (time.perf_counter() - t_det_start) * 1000.0

            # B. CƠ CHẾ CHỐT KIỆN (Thay thế cảm biến siêu âm)
            just_triggered = False
            if trigger_mode:
                just_triggered = gate.update(len(boxes), now=now_ts)
                if just_triggered:
                    trigger_flash_until = now_ts + 1.2
                    package_counter += 1

            # C. PHÂN LOẠI & QUÉT QR
            target_region = None
            cls_result: ClassResult | None = None
            cls_ms = 0.0
            qr_items: list[QrVisualItem] = []
            qr_ms = 0.0

            if trigger_mode:
                # Trong chế độ chốt kiện:
                if just_triggered and len(boxes) == 1:
                    # KHI VỪA TRIGGER: Thực hiện chụp & phân loại duy nhất 1 lần
                    b = boxes[0]
                    x1, y1, x2, y2 = b.xyxy
                    target_region = rgb[y1:y2, x1:x2]

                    t_cls_start = time.perf_counter()
                    cls_result = classifier.predict(target_region)
                    cls_ms = (time.perf_counter() - t_cls_start) * 1000.0

                    t_qr_start = time.perf_counter()
                    qr_items = scan_qr_with_geometry(frame)
                    qr_ms = (time.perf_counter() - t_qr_start) * 1000.0

                    first_qr = qr_items[0] if qr_items else None
                    latched_package = LatchedPackage(
                        pkg_seq=package_counter,
                        pkg_id=first_qr.pkg_id if first_qr else None,
                        warehouse=first_qr.warehouse if first_qr else None,
                        qr_text=first_qr.text if first_qr else None,
                        label=cls_result.label,
                        confidence=cls_result.confidence,
                        probs=cls_result.probs,
                        timestamp=datetime.datetime.now().strftime("%H:%M:%S"),
                        box_xyxy=b.xyxy
                    )
                    # Tiếng bíp thông báo chốt kiện (trên Windows)
                    if sys.platform == "win32":
                        try:
                            import winsound
                            winsound.Beep(1200, 100)
                        except Exception:
                            pass
                elif not gate.armed:
                    # Hộp vẫn còn trong khung hình -> giữ nguyên kết quả latched_package
                    pass
                else:
                    # Đã lấy hộp ra -> reset để sẵn sàng đón hộp mới
                    latched_package = None
            else:
                # Chế độ quét liên tục mọi frame
                if len(boxes) == 1:
                    b = boxes[0]
                    x1, y1, x2, y2 = b.xyxy
                    target_region = rgb[y1:y2, x1:x2]
                elif len(boxes) == 0 and always_classify:
                    rw, rh = int(w * 0.5), int(h * 0.5)
                    rx1, ry1 = (w - rw) // 2, (h - rh) // 2
                    target_region = rgb[ry1:ry1 + rh, rx1:rx1 + rw]

                if target_region is not None and target_region.size > 0:
                    t_cls_start = time.perf_counter()
                    cls_result = classifier.predict(target_region)
                    cls_ms = (time.perf_counter() - t_cls_start) * 1000.0

                t_qr_start = time.perf_counter()
                qr_items = scan_qr_with_geometry(frame)
                qr_ms = (time.perf_counter() - t_qr_start) * 1000.0

            # Tính FPS
            total_cycle = (time.perf_counter() - t0)
            fps = 1.0 / max(total_cycle, 0.001)
            fps_list.append(fps)
            if len(fps_list) > 20:
                fps_list.pop(0)
            avg_fps = sum(fps_list) / len(fps_list)

            # D. VẼ GIAO DIỆN (UI OVERLAY)
            display = frame.copy()

            # 1. Vẽ mã QR
            for q_item in qr_items:
                draw_qr_highlight(display, q_item)

            # 2. Vẽ bounding boxes của hộp
            if len(boxes) > 0:
                for idx, b in enumerate(boxes):
                    bx1, by1, bx2, by2 = b.xyxy
                    if len(boxes) == 1:
                        # Nếu trong trigger mode đã chốt thì viền xanh đậm
                        color = (0, 220, 0) if (trigger_mode and not gate.armed) else (0, 200, 100)
                        lbl = f"Box: {b.confidence*100:.0f}%"
                        draw_styled_box(display, bx1, by1, bx2, by2, color, lbl)
                    else:
                        color = (0, 165, 255)
                        lbl = f"Box #{idx+1} ({b.confidence*100:.0f}%)"
                        draw_styled_box(display, bx1, by1, bx2, by2, color, lbl)

            # 3. Header Dashboard (Top bar)
            header_h = 75
            overlay = display.copy()
            cv2.rectangle(overlay, (0, 0), (w, header_h), (25, 25, 25), -1)
            cv2.addWeighted(overlay, 0.75, display, 0.25, 0, display)

            cur_x = 15
            cur_y = 30
            # Badge Chế độ
            mode_tag = "CHOT KIEN (VIRTUAL SENSOR)" if trigger_mode else "QUET LIEN TUC"
            mode_col = (140, 50, 0) if trigger_mode else (80, 80, 80)
            cur_x = draw_header_badge(display, mode_tag, mode_col, cur_x, cur_y)

            # Badge Trạng thái Cổng / Trigger
            if trigger_mode:
                if gate.armed:
                    cur_x = draw_header_badge(display, "SAN SANG (CHO HOP VAO)", (30, 140, 30), cur_x, cur_y)
                else:
                    cur_x = draw_header_badge(display, "DA CHOT! (VUI LONG LAY HOP RA)", (0, 120, 220), cur_x, cur_y)
            else:
                if len(boxes) == 1:
                    cur_x = draw_header_badge(display, "1 HOP DETECTED", (30, 150, 30), cur_x, cur_y)
                elif len(boxes) > 1:
                    cur_x = draw_header_badge(display, f"{len(boxes)} HOP (LOAI)", (0, 100, 220), cur_x, cur_y)
                else:
                    cur_x = draw_header_badge(display, "DANG TIM HOP...", (60, 60, 60), cur_x, cur_y)

            # Badge Phân loại Bao bì
            disp_label = latched_package.label if (trigger_mode and latched_package) else (cls_result.label if cls_result else None)
            disp_conf = latched_package.confidence if (trigger_mode and latched_package) else (cls_result.confidence if cls_result else 0.0)

            if disp_label:
                if disp_label == LABEL_OK:
                    cur_x = draw_header_badge(display, f"BAO BI: NGUYEN VEN ({disp_conf*100:.0f}%)", (30, 160, 30), cur_x, cur_y)
                elif disp_label == LABEL_DEFECT:
                    cur_x = draw_header_badge(display, f"BAO BI: BI HU/LOI ({disp_conf*100:.0f}%)", (30, 30, 200), cur_x, cur_y)
                else:
                    cur_x = draw_header_badge(display, f"BAO BI: CHUA RO ({disp_conf*100:.0f}%)", (0, 140, 220), cur_x, cur_y)
            else:
                cur_x = draw_header_badge(display, "BAO BI: CHO HOP", (60, 60, 60), cur_x, cur_y)

            # Dòng phụ trong Header (FPS & Latency)
            info_str = f"FPS: {avg_fps:4.1f} | Box: {det_ms:3.0f}ms | Cls: {cls_ms:3.0f}ms | Cam: {w}x{h} | Kiện đã phân loại: {package_counter}"
            cv2.putText(display, info_str, (15, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (200, 200, 200), 1, cv2.LINE_AA)

            # 4. Footer Bar: Kết quả kiện hàng
            footer_h = 85
            overlay_f = display.copy()
            cv2.rectangle(overlay_f, (0, h - footer_h), (w, h), (20, 20, 20), -1)
            cv2.addWeighted(overlay_f, 0.85, display, 0.15, 0, display)

            if trigger_mode and latched_package:
                lp = latched_package
                # Kiện đã chốt
                qr_desc = f"[{lp.pkg_id} -> {lp.warehouse}]" if (lp.pkg_id and lp.warehouse) else (f"[{lp.qr_text[:30]}]" if lp.qr_text else "Khong co QR")
                p_norm = lp.probs.get("normal", lp.probs.get(LABEL_OK, 0.0)) * 100
                p_dam = lp.probs.get("damage", lp.probs.get(LABEL_DEFECT, 0.0)) * 100
                res_desc = f"Lanh lan ({p_norm:.1f}%)" if lp.label == LABEL_OK else f"Loi/Hong ({p_dam:.1f}%)"

                line1 = f"KIEN #{lp.pkg_seq} ({lp.timestamp})  |  QR: {qr_desc}  |  Bao bi: {res_desc}"
                cv2.putText(display, line1, (15, h - footer_h + 30), cv2.FONT_HERSHEY_DUPLEX, 0.55, (0, 255, 120), 1, cv2.LINE_AA)

                line2 = f"-> Da chot ket qua! Hay lay hop ra khoi camera de he thong san sang don hop tiep theo."
                cv2.putText(display, line2, (15, h - footer_h + 62), cv2.FONT_HERSHEY_SIMPLEX, 0.48, (0, 200, 255), 1, cv2.LINE_AA)
            elif trigger_mode and not latched_package:
                cv2.putText(display, "CHOT KIEN: Dat hop carton on dinh vao camera, he thong se chot phan loai 1 lan duy nhat.",
                            (15, h - footer_h + 30), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (180, 180, 180), 1, cv2.LINE_AA)
                cv2.putText(display, "Bam [M] de doi sang che do Quet lien tuc  |  [S] Chup luu  |  [Q] Thoat",
                            (15, h - footer_h + 62), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (140, 140, 140), 1, cv2.LINE_AA)
            else:
                # Continuous mode
                if cls_result:
                    p_norm = cls_result.probs.get("normal", cls_result.probs.get(LABEL_OK, 0.0)) * 100
                    p_dam = cls_result.probs.get("damage", cls_result.probs.get(LABEL_DEFECT, 0.0)) * 100
                    cv2.putText(display, f"QUET LIEN TUC -> Normal: {p_norm:.1f}%  |  Damage: {p_dam:.1f}%",
                                (15, h - footer_h + 30), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (220, 220, 220), 1, cv2.LINE_AA)
                else:
                    cv2.putText(display, "QUET LIEN TUC: Cho hop vao camera", (15, h - footer_h + 30),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (150, 150, 150), 1, cv2.LINE_AA)
                cv2.putText(display, "Bam [M] de bat che do Chot tung kien (Tranh nhay ket qua) | [Q] Thoat",
                            (15, h - footer_h + 62), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (140, 140, 140), 1, cv2.LINE_AA)

            # Flash thông báo khi vừa chốt
            if now_ts < trigger_flash_until:
                cv2.rectangle(display, (w // 2 - 220, h // 2 - 35), (w // 2 + 220, h // 2 + 35), (0, 180, 0), -1)
                cv2.putText(display, "DA CHOT KIEN HANG MOI!", (w // 2 - 190, h // 2 + 10),
                            cv2.FONT_HERSHEY_DUPLEX, 0.8, (255, 255, 255), 2)

            # Thông báo đã lưu ảnh
            if time.time() < save_msg_until:
                cv2.rectangle(display, (w // 2 - 180, h // 2 - 25), (w // 2 + 180, h // 2 + 25), (30, 140, 30), -1)
                cv2.putText(display, "DA LUU ANH VA KET QUA!", (w // 2 - 150, h // 2 + 8),
                            cv2.FONT_HERSHEY_DUPLEX, 0.65, (255, 255, 255), 2)

            cv2.imshow(window_name, display)

            # Xử lý phím bấm
            key = cv2.waitKey(1 if not is_image else 30) & 0xFF
            if key in (ord("q"), ord("Q"), 27):
                print("[*] Đang thoát...")
                break
            elif key in (ord("m"), ord("M")):
                trigger_mode = not trigger_mode
                latched_package = None
                print(f"[*] Đã chuyển sang: {'Chế độ Chốt từng kiện (Trigger)' if trigger_mode else 'Chế độ Quét liên tục (Continuous)'}")
            elif key in (ord("s"), ord("S")):
                ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                img_file = output_dir / f"test_{ts}.jpg"
                json_file = output_dir / f"test_{ts}.json"
                cv2.imwrite(str(img_file), frame)
                result_data = {
                    "timestamp": ts,
                    "mode": "trigger" if trigger_mode else "continuous",
                    "latched_package": {
                        "pkg_seq": latched_package.pkg_seq,
                        "pkg_id": latched_package.pkg_id,
                        "warehouse": latched_package.warehouse,
                        "label": latched_package.label,
                        "confidence": latched_package.confidence,
                        "probs": latched_package.probs,
                    } if latched_package else None,
                    "boxes": [{"xyxy": b.xyxy, "confidence": b.confidence} for b in boxes],
                    "fps": round(avg_fps, 2)
                }
                json_file.write_text(json.dumps(result_data, indent=2, ensure_ascii=False), encoding="utf-8")
                print(f"[+] Đã lưu snapshot tại: {img_file}")
                save_msg_until = time.time() + 1.5
            elif key in (ord("c"), ord("C")):
                always_classify = not always_classify
                print(f"[*] Chế độ Always-Classify tâm: {'BẬT' if always_classify else 'TẮT'}")
            elif key == ord(" "):
                paused = not paused
                print(f"[*] Trạng thái: {'TẠM DỪNG' if paused else 'ĐANG CHẠY'}")

    finally:
        if cap is not None:
            cap.release()
        cv2.destroyAllWindows()
        classifier.close()
        detector.close()
        print("[+] Đã giải phóng Camera và AI Models hoàn tất.")


if __name__ == "__main__":
    main()
