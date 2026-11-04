"""P-05 (bản giả) — DummyClassifier cho P3 / chế độ sim.

Bản thật OnnxClassifier/TfliteClassifier nằm ở classifier_onnx.py (P4). Giao diện Classifier/ClassResult
ở interfaces.py. DummyClassifier trả OK mặc định; ở sim đọc nhãn từ tên file ảnh (label_hint).
"""
from __future__ import annotations

import logging
import time
from typing import Callable

import numpy as np

from .config import AiCfg
from .interfaces import LABEL_DEFECT, LABEL_OK, LABEL_UNCERTAIN, ClassResult, Classifier, crop_roi

log = logging.getLogger(__name__)

MODEL_VERSION = "dummy-1.0"

# Từ khóa trong tên file -> loại lỗi (tiếng Anh và không dấu tiếng Việt)
DEFECT_KEYWORDS: dict[str, str] = {
    "tear": "tear", "rach": "tear",
    "dent": "dent", "mop": "dent",
    "wet": "wet", "uot": "wet",
    "crush": "crush", "vo": "crush",
    "defect": "defect", "loi": "defect",
}


class DummyClassifier(Classifier):
    """Bộ phân loại giả: nhãn suy từ tên file (sim) hoặc luôn OK."""

    model_version = MODEL_VERSION

    def __init__(self, cfg: AiCfg, label_hint: Callable[[], str | None] | None = None) -> None:
        self._cfg = cfg
        self._label_hint = label_hint

    @staticmethod
    def label_from_name(name: str | None) -> tuple[str, float, dict[str, float]]:
        """(label, confidence, defect_types) theo tên file."""
        if not name:
            return LABEL_OK, 0.97, {}
        n = name.lower()
        if "uncertain" in n:
            return LABEL_UNCERTAIN, 0.5, {}
        types: dict[str, float] = {}
        for kw, dtype in DEFECT_KEYWORDS.items():
            if kw in n:
                types[dtype] = 0.95
        if types:
            # Nếu có loại cụ thể thì bỏ nhãn chung "defect"
            if len(types) > 1 and "defect" in types:
                types.pop("defect")
            return LABEL_DEFECT, 0.95, types
        return LABEL_OK, 0.97, {}

    def predict(self, image: np.ndarray, roi: tuple[int, int, int, int] | None = None) -> ClassResult:
        t0 = time.perf_counter()
        crop = crop_roi(image, roi)  # chỉ để mô phỏng chi phí cắt ROI; không dùng nội dung ảnh
        _ = crop.shape
        name = None
        if self._label_hint is not None:
            try:
                name = self._label_hint()
            except Exception as e:  # pragma: no cover
                log.warning("label_hint lỗi: %s", e)
        label, conf, types = self.label_from_name(name)
        probs = {LABEL_OK: conf if label == LABEL_OK else 1.0 - conf, LABEL_DEFECT: conf if label == LABEL_DEFECT else 1.0 - conf}
        if label == LABEL_UNCERTAIN:
            probs = {LABEL_OK: 0.5, LABEL_DEFECT: 0.5}
        return ClassResult(
            label=label,
            confidence=conf,
            probs=probs,
            defect_types=types,
            infer_ms=(time.perf_counter() - t0) * 1000.0,
            model_version=MODEL_VERSION,
        )
