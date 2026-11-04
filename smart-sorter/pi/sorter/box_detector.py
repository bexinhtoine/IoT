"""AI-06: tìm đúng một hộp bằng YOLO-World ONNX, sau đó phân loại vùng hộp.

Pi chỉ dùng OpenCV/ONNX Runtime. Model phải được xuất bởi ai/export_detector.py
với đúng một prompt cardboard box và output YOLO thô [1,5,N].
"""
from __future__ import annotations

import ast
import logging
import math
import time
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from .config import DetectionCfg
from .interfaces import Classifier, ClassResult, LABEL_UNCERTAIN, crop_roi

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Box:
    xyxy: tuple[int, int, int, int]
    confidence: float


def letterbox(image: np.ndarray, size: int) -> tuple[np.ndarray, float, tuple[int, int]]:
    """Ultralytics letterbox: giữ tỷ lệ, pad 114; input đã là RGB."""
    h, w = image.shape[:2]
    ratio = min(size / h, size / w)
    nw, nh = round(w * ratio), round(h * ratio)
    left, top = round((size - nw) / 2 - 0.1), round((size - nh) / 2 - 0.1)
    resized = cv2.resize(image, (nw, nh), interpolation=cv2.INTER_LINEAR)
    padded = np.full((size, size, 3), 114, dtype=np.uint8)
    padded[top:top + nh, left:left + nw] = resized
    tensor = np.ascontiguousarray(padded.transpose(2, 0, 1)[None], dtype=np.float32) / 255.0
    return tensor, ratio, (left, top)


def decode_boxes(raw: np.ndarray, shape: tuple[int, int], ratio: float,
                 pad: tuple[int, int], cfg: DetectionCfg) -> list[Box]:
    if raw.ndim != 3 or raw.shape[0] != 1 or raw.shape[1] != 5:
        raise ValueError(f"Detector cần output [1,5,N] một lớp, nhận {raw.shape}")
    if not np.isfinite(raw).all():
        raise ValueError("Detector trả NaN/Inf")
    rows = raw[0].T
    rows = rows[rows[:, 4] >= cfg.confidence]
    boxes, scores = [], []
    h, w = shape
    for cx, cy, bw, bh, score in rows:
        if not 0 <= score <= 1 or bw <= 0 or bh <= 0:
            raise ValueError("Detector trả box/score không hợp lệ")
        x1 = max(0.0, min(w, (float(cx - bw / 2) - pad[0]) / ratio))
        y1 = max(0.0, min(h, (float(cy - bh / 2) - pad[1]) / ratio))
        x2 = max(0.0, min(w, (float(cx + bw / 2) - pad[0]) / ratio))
        y2 = max(0.0, min(h, (float(cy + bh / 2) - pad[1]) / ratio))
        if x2 <= x1 or y2 <= y1 or (x2 - x1) * (y2 - y1) < cfg.min_area_ratio * h * w:
            continue
        boxes.append([x1, y1, x2 - x1, y2 - y1])
        scores.append(float(score))
    if not boxes:
        return []
    keep = cv2.dnn.NMSBoxes(boxes, scores, cfg.confidence, cfg.iou_threshold)
    found = []
    for i in np.asarray(keep).reshape(-1):
        x, y, bw, bh = boxes[int(i)]
        px, py = bw * cfg.padding, bh * cfg.padding
        xyxy = (max(0, math.floor(x - px)), max(0, math.floor(y - py)),
                min(w, math.ceil(x + bw + px)), min(h, math.ceil(y + bh + py)))
        found.append(Box(xyxy, scores[int(i)]))
    return found


class BoxDetector:
    def __init__(self, cfg: DetectionCfg, threads: int = 4) -> None:
        import onnxruntime as ort
        self.cfg = cfg
        if not Path(cfg.model_path).is_file():
            raise ValueError(f"Không có detector: {cfg.model_path}; chạy ai/export_detector.py trước")
        options = ort.SessionOptions()
        options.intra_op_num_threads = max(1, threads)
        self.session = ort.InferenceSession(cfg.model_path, options, providers=["CPUExecutionProvider"])
        inputs, outputs = self.session.get_inputs(), self.session.get_outputs()
        if len(inputs) != 1 or inputs[0].shape != [1, 3, cfg.input_size, cfg.input_size]:
            raise ValueError("Detector ONNX phải có input tĩnh [1,3,input_size,input_size]")
        if len(outputs) != 1 or len(outputs[0].shape) != 3 or outputs[0].shape[1] != 5:
            raise ValueError("Sai detector: cần YOLO-World một lớp [1,5,N], nms=False")
        names = ast.literal_eval(self.session.get_modelmeta().custom_metadata_map.get("names", "{}"))
        if names not in ({0: "cardboard box"}, {"0": "cardboard box"}):
            raise ValueError(f"Detector sai lớp: {names}; xuất lại bằng ai/export_detector.py")
        self.input_name = inputs[0].name

    def detect(self, image: np.ndarray) -> list[Box]:
        tensor, ratio, pad = letterbox(image, self.cfg.input_size)
        raw = self.session.run(None, {self.input_name: tensor})[0]
        return decode_boxes(raw, image.shape[:2], ratio, pad, self.cfg)

    def close(self) -> None:
        self.session = None


class DetectedBoxClassifier(Classifier):
    """Không có hộp/nhiều hộp → UNCERTAIN; lỗi suy luận → orchestrator fail-safe."""
    def __init__(self, classifier: Classifier, detector: BoxDetector) -> None:
        self.classifier, self.detector = classifier, detector
        self.model_version = classifier.model_version
        self.spec = getattr(classifier, "spec", None)
        self.last_boxes: list[Box] = []

    def predict(self, image: np.ndarray, roi: tuple[int, int, int, int] | None = None) -> ClassResult:
        start = time.perf_counter()
        region = crop_roi(image, roi)
        self.last_boxes = self.detector.detect(region)
        if len(self.last_boxes) != 1:
            log.warning("BOX_COUNT=%d: cần đúng một hộp trong camera.roi", len(self.last_boxes))
            return ClassResult(LABEL_UNCERTAIN, 0.0, infer_ms=(time.perf_counter() - start) * 1000,
                               model_version=self.model_version)
        box = self.last_boxes[0]
        x1, y1, x2, y2 = box.xyxy
        log.info("BOX xyxy=%s confidence=%.3f (tọa độ trong ROI)", box.xyxy, box.confidence)
        result = self.classifier.predict(region[y1:y2, x1:x2])
        result.infer_ms = round((time.perf_counter() - start) * 1000, 2)
        return result

    def warmup(self) -> None:
        self.classifier.warmup()
        self.detector.detect(np.full((640, 640, 3), 114, np.uint8))

    def close(self) -> None:
        try:
            self.classifier.close()
        finally:
            self.detector.close()
