"""Chạy pipeline AI thật trên một ảnh; không gửi UART/MQTT hoặc điều khiển băng chuyền."""
from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import asdict
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sorter.config import load_config
from sorter.factories import make_classifier


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--output", help="Lưu ảnh có box và kết quả")
    parser.add_argument("--full-frame", action="store_true", help="Không dùng camera.roi; ảnh đã crop hộp")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    cfg = load_config(args.config, validate_files=False)
    if cfg.ai.backend != "onnx":
        parser.error("Công cụ này yêu cầu ai.backend: onnx")
    bgr = cv2.imread(args.image)
    if bgr is None:
        parser.error(f"Không đọc được ảnh: {args.image}")
    roi = None if args.full_frame else tuple(cfg.camera.roi)
    if roi:
        x, y, w, h = roi
        if x < 0 or y < 0 or w <= 0 or h <= 0 or x + w > bgr.shape[1] or y + h > bgr.shape[0]:
            parser.error("camera.roi vượt ảnh; chỉnh ROI hoặc dùng --full-frame")
    classifier = make_classifier(cfg)
    try:
        result = classifier.predict(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB), roi)
        boxes = getattr(classifier, "last_boxes", [])
        print(json.dumps({"result": asdict(result), "boxes_in_roi": [asdict(b) for b in boxes]}, indent=2))
        if args.output:
            ox, oy = (roi[0], roi[1]) if roi else (0, 0)
            for box in boxes:
                x1, y1, x2, y2 = box.xyxy
                cv2.rectangle(bgr, (x1 + ox, y1 + oy), (x2 + ox, y2 + oy), (0, 255, 255), 2)
            cv2.putText(bgr, f"{result.label} {result.confidence:.3f}", (15, 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 200, 0) if result.is_ok else (0, 0, 255), 2)
            output = Path(args.output)
            output.parent.mkdir(parents=True, exist_ok=True)
            if not cv2.imwrite(str(output), bgr):
                raise RuntimeError(f"Không ghi được {output}")
    finally:
        classifier.close()


if __name__ == "__main__":
    main()
