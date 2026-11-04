"""P-05 / AI-06 / AI-07 — Bộ phân loại bao bì chạy trên Raspberry Pi bằng ONNX Runtime hoặc TFLite.

Hai lớp cùng giao diện `Classifier` (interfaces.py):
- OnnxClassifier  : onnxruntime CPUExecutionProvider (mặc định cho Pi 4).
- TfliteClassifier: tflite_runtime (dự phòng: tensorflow.lite), hỗ trợ model int8 lượng tử.

Hỗ trợ hai kiểu model:
(a) kiểu notebook nhóm (ai/train_packaging_damage.ipynb): input "image" NCHW RGB 0..1, chuẩn hóa đã nhúng,
    outputs "probs" [N,2] = [OK, DEFECT] và "type_probs" [N,K] sigmoid theo damage_types.
(b) kiểu YOLOv8-cls / generic: một output [N,C] (softmax nếu tổng ≠ 1), tên lớp lấy từ labels.json;
    lớp ok/good/intact → OK, mọi lớp khác (defect/tear/dent/crush/wet…) → DEFECT.

Mọi tham số tiền xử lý + ngưỡng được đọc từ model_card.json (AI-04) để suy luận giống hệt lúc train.
Quy tắc quyết định ưu tiên an toàn (AI-07):
    p_defect >= defect_threshold  → DEFECT  (confidence = p_defect)
    p_ok     >= min_confidence    → OK      (confidence = p_ok)
    còn lại                       → UNCERTAIN (confidence = max) → orchestrator coi như lỗi (P-05).
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from .config import AiCfg
from .interfaces import LABEL_DEFECT, LABEL_OK, LABEL_UNCERTAIN, Classifier, ClassResult, crop_roi

log = logging.getLogger(__name__)

# Tên lớp được coi là "hàng tốt" khi ánh xạ model generic (không phân biệt hoa/thường)
OK_CLASS_NAMES: frozenset[str] = frozenset({"ok", "good", "intact", "normal", "clean", "lanh", "tot"})
# Tên lớp lỗi "chung chung" (không phải loại lỗi cụ thể) → không đưa vào defect_types
GENERIC_DEFECT_NAMES: frozenset[str] = frozenset({"defect", "defective", "damaged", "damage", "bad", "loi", "hong"})

KIND_BINARY_TYPES = "binary_types"   # kiểu (a): probs[OK,DEFECT] + type_probs
KIND_GENERIC = "generic"             # kiểu (b): một output [N,C]


class ModelLoadError(RuntimeError):
    """Model/labels/model_card không khớp hoặc không đọc được — báo rõ khi khởi động."""


@dataclass
class ModelSpec:
    """Thông tin cần để tiền xử lý và diễn giải đầu ra, gộp từ model_card.json + labels.json + AiCfg."""

    input_size: int = 224
    input_name: str = ""                 # rỗng = lấy từ model
    layout: str = "NCHW"                 # NCHW | NHWC
    color: str = "RGB"                   # RGB | BGR
    value_range: tuple[float, float] = (0.0, 1.0)   # ảnh uint8 0..255 được đưa về [lo, hi]
    mean: list[float] | None = None      # nếu model_card ghi mean/std thì áp dụng sau khi đưa về value_range
    std: list[float] | None = None
    kind: str = KIND_GENERIC
    classes: list[str] = field(default_factory=lambda: ["OK", "DEFECT"])
    damage_types: list[str] = field(default_factory=list)
    probs_output: str = ""               # tên output xác suất (kiểu a: "probs"; kiểu b: output duy nhất)
    types_output: str = ""               # tên output loại lỗi (kiểu a: "type_probs")
    defect_threshold: float = 0.6
    min_confidence: float = 0.6
    type_threshold: float = 0.5
    version: str = "unknown"
    card: dict[str, Any] = field(default_factory=dict)

    @property
    def ok_indices(self) -> list[int]:
        return [i for i, c in enumerate(self.classes) if c.strip().lower() in OK_CLASS_NAMES]

    @property
    def defect_indices(self) -> list[int]:
        return [i for i, c in enumerate(self.classes) if c.strip().lower() not in OK_CLASS_NAMES]


# ---------------------------------------------------------------------------
# Đọc model_card.json / labels.json
# ---------------------------------------------------------------------------
def _read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def find_model_card(cfg: AiCfg) -> Path | None:
    """cfg.model_card_path nếu đặt; không thì <thư mục model>/model_card.json nếu tồn tại."""
    if cfg.model_card_path:
        p = Path(cfg.model_card_path)
        if not p.exists():
            raise ModelLoadError(f"ai.model_card_path không tồn tại: {p}")
        return p
    p = Path(cfg.model_path).parent / "model_card.json"
    return p if p.exists() else None


def load_labels(path: str | Path) -> list[str]:
    """labels.json: {"classes": [...]} hoặc list hoặc {"0": "ok", "1": "defect"} (kiểu ultralytics names)."""
    data = _read_json(Path(path))
    if isinstance(data, dict) and "classes" in data:
        classes = data["classes"]
    elif isinstance(data, dict) and "names" in data:
        classes = data["names"]
    elif isinstance(data, dict):
        # dict {index: name} → sắp theo index
        try:
            classes = [data[k] for k in sorted(data, key=lambda s: int(s))]
        except (ValueError, TypeError) as e:
            raise ModelLoadError(f"labels.json không hiểu định dạng: {path}") from e
    else:
        classes = data
    if isinstance(classes, dict):
        classes = [classes[k] for k in sorted(classes, key=lambda s: int(s))]
    if not isinstance(classes, list) or not classes:
        raise ModelLoadError(f"labels.json rỗng hoặc sai kiểu: {path}")
    return [str(c) for c in classes]


def build_spec(cfg: AiCfg) -> ModelSpec:
    """Gộp model_card.json (ưu tiên) + labels.json + AiCfg thành ModelSpec."""
    spec = ModelSpec(input_size=int(cfg.input_size), min_confidence=float(cfg.min_confidence),
                     defect_threshold=float(cfg.min_confidence))
    card_path = find_model_card(cfg)
    if card_path is not None:
        card = _read_json(card_path)
        if not isinstance(card, dict):
            raise ModelLoadError(f"model_card.json phải là một object JSON: {card_path}")
        spec.card = card
        _apply_card(spec, card)
        log.info("model_card %s: version=%s size=%d kind=%s defect_threshold=%.3f",
                 card_path, spec.version, spec.input_size, spec.kind, spec.defect_threshold)
    # labels.json: chỉ cần cho kiểu generic (hoặc khi model_card không ghi lớp)
    lp = Path(cfg.labels_path) if cfg.labels_path else None
    if lp is not None and lp.exists():
        classes = load_labels(lp)
        if spec.kind == KIND_GENERIC or not spec.card.get("outputs"):
            spec.classes = classes
    elif spec.kind == KIND_GENERIC and not spec.card:
        log.warning("Không có labels.json (%s) — giả định 2 lớp %s", lp, spec.classes)
    if spec.version == "unknown":
        spec.version = Path(cfg.model_path).stem
    return spec


def _apply_card(spec: ModelSpec, card: dict[str, Any]) -> None:
    """Đọc các khóa của model_card.json (định dạng notebook nhóm và định dạng generic của train_yolo_cls)."""
    inp = card.get("input") or {}
    if isinstance(inp, dict):
        size = inp.get("size") or inp.get("imgsz")
        if size is None and isinstance(inp.get("shape"), list) and len(inp["shape"]) == 4:
            size = inp["shape"][-1] if str(inp.get("layout", "NCHW")).upper() == "NCHW" else inp["shape"][1]
        if size is not None:
            spec.input_size = int(size)
        spec.input_name = str(inp.get("name") or "")
        spec.layout = str(inp.get("layout") or spec.layout).upper()
        spec.color = str(inp.get("color") or spec.color).upper()
        rng = inp.get("range")
        if isinstance(rng, (list, tuple)) and len(rng) == 2:
            spec.value_range = (float(rng[0]), float(rng[1]))
        if inp.get("mean") is not None and inp.get("std") is not None:
            spec.mean = [float(v) for v in inp["mean"]]
            spec.std = [float(v) for v in inp["std"]]
    # Khóa phẳng (kiểu generic): imgsz, normalization "0..1 RGB"
    if "imgsz" in card and "input" not in card:
        spec.input_size = int(card["imgsz"])
    norm = str(card.get("normalization") or "")
    if "-1..1" in norm or "[-1,1]" in norm.replace(" ", ""):
        spec.value_range = (-1.0, 1.0)
    if "BGR" in norm.upper():
        spec.color = "BGR"

    outputs = card.get("outputs")
    if isinstance(outputs, dict) and "probs" in outputs:
        spec.kind = KIND_BINARY_TYPES
        spec.probs_output = "probs"
        spec.classes = [str(c) for c in outputs["probs"]]
        types_key = next((k for k in outputs if k != "probs"), "")
        spec.types_output = types_key
        spec.damage_types = [str(t) for t in (card.get("damage_types") or outputs.get(types_key) or [])]
    else:
        spec.kind = KIND_GENERIC
        classes = card.get("classes") or (outputs if isinstance(outputs, list) else None)
        if classes:
            spec.classes = [str(c) for c in classes]
        if isinstance(outputs, dict) and len(outputs) == 1:
            spec.probs_output = next(iter(outputs))

    if card.get("defect_threshold") is not None:
        spec.defect_threshold = float(card["defect_threshold"])
    if card.get("min_confidence") is not None:
        # model_card có thể ghi min_confidence đã chọn trên val; vẫn giữ giá trị lớn hơn giữa card và config để an toàn
        spec.min_confidence = max(spec.min_confidence, float(card["min_confidence"]))
    if card.get("type_threshold") is not None:
        spec.type_threshold = float(card["type_threshold"])
    if card.get("version"):
        spec.version = str(card["version"])


# ---------------------------------------------------------------------------
# Tiền xử lý và quyết định (dùng chung cho ONNX / TFLite)
# ---------------------------------------------------------------------------
def preprocess(image: np.ndarray, roi: tuple[int, int, int, int] | None, spec: ModelSpec) -> np.ndarray:
    """crop_roi → resize (S,S) INTER_AREA → float32 theo value_range (→ mean/std) → NCHW hoặc NHWC, batch 1."""
    if image is None or image.ndim != 3 or image.shape[2] != 3:
        raise ValueError(f"Ảnh phải là HxWx3 RGB uint8, nhận shape {None if image is None else image.shape}")
    img = crop_roi(image, roi)
    S = spec.input_size
    if img.shape[0] != S or img.shape[1] != S:
        img = cv2.resize(img, (S, S), interpolation=cv2.INTER_AREA)
    if spec.color == "BGR":
        img = img[:, :, ::-1]
    lo, hi = spec.value_range
    x = img.astype(np.float32) * ((hi - lo) / 255.0) + lo
    if spec.mean is not None and spec.std is not None:
        x = (x - np.asarray(spec.mean, np.float32)) / np.asarray(spec.std, np.float32)
    if spec.layout == "NHWC":
        return np.ascontiguousarray(x[None, ...])
    return np.ascontiguousarray(x.transpose(2, 0, 1)[None, ...])


def softmax(z: np.ndarray) -> np.ndarray:
    z = z.astype(np.float64) - np.max(z)
    e = np.exp(z)
    return e / e.sum()


def decide(p_ok: float, p_defect: float, defect_threshold: float, min_confidence: float) -> tuple[str, float]:
    """AI-07: ưu tiên loại nhầm hàng tốt hơn là để lọt hàng hỏng."""
    if p_defect >= defect_threshold:
        return LABEL_DEFECT, float(p_defect)
    if p_ok >= min_confidence:
        return LABEL_OK, float(p_ok)
    return LABEL_UNCERTAIN, float(max(p_ok, p_defect))


def interpret(outputs: dict[str, np.ndarray], spec: ModelSpec, infer_ms: float) -> ClassResult:
    """Diễn giải đầu ra model (dict tên→mảng) thành ClassResult theo kiểu model."""
    if spec.kind == KIND_BINARY_TYPES:
        probs = np.asarray(outputs[spec.probs_output], np.float64).reshape(-1)
        if probs.size != 2:
            raise ModelLoadError(f"Output '{spec.probs_output}' phải có 2 phần tử [OK, DEFECT], nhận {probs.shape}")
        if not 0.98 <= probs.sum() <= 1.02:
            probs = softmax(probs)
        p_ok, p_defect = float(probs[0]), float(probs[1])
        defect_types: dict[str, float] = {}
        if spec.types_output and spec.types_output in outputs:
            tp = np.asarray(outputs[spec.types_output], np.float64).reshape(-1)
            names = spec.damage_types or [f"type_{i}" for i in range(tp.size)]
            defect_types = {n: round(float(v), 4) for n, v in zip(names, tp)}
        label, conf = decide(p_ok, p_defect, spec.defect_threshold, spec.min_confidence)
        return ClassResult(label=label, confidence=round(conf, 4),
                           probs={LABEL_OK: round(p_ok, 4), LABEL_DEFECT: round(p_defect, 4)},
                           defect_types=defect_types, infer_ms=round(infer_ms, 2), model_version=spec.version)

    # Kiểu generic: một output [N,C]
    raw = np.asarray(outputs[spec.probs_output], np.float64).reshape(-1)
    if raw.size != len(spec.classes):
        raise ModelLoadError(f"Output có {raw.size} lớp nhưng labels.json có {len(spec.classes)}: {spec.classes}")
    probs = raw if (0.98 <= raw.sum() <= 1.02 and raw.min() >= 0.0) else softmax(raw)
    ok_idx, def_idx = spec.ok_indices, spec.defect_indices
    if not ok_idx:
        raise ModelLoadError(f"labels.json không có lớp hàng tốt (ok/good/intact): {spec.classes}")
    p_ok = float(probs[ok_idx].sum())
    p_defect = float(probs[def_idx].sum()) if def_idx else 0.0
    defect_types = {}
    if len(def_idx) > 1 or (def_idx and spec.classes[def_idx[0]].lower() not in GENERIC_DEFECT_NAMES):
        defect_types = {spec.classes[i]: round(float(probs[i]), 4) for i in def_idx}
    label, conf = decide(p_ok, p_defect, spec.defect_threshold, spec.min_confidence)
    per_class = {c: round(float(p), 4) for c, p in zip(spec.classes, probs)}
    per_class.update({LABEL_OK: round(p_ok, 4), LABEL_DEFECT: round(p_defect, 4)})
    return ClassResult(label=label, confidence=round(conf, 4), probs=per_class, defect_types=defect_types,
                       infer_ms=round(infer_ms, 2), model_version=spec.version)


def _check_input_shape(shape: list[Any], spec: ModelSpec, has_card: bool, model_path: str) -> None:
    """Đối chiếu shape input của model với ModelSpec; sai lệch rõ ràng → ModelLoadError."""
    if len(shape) != 4:
        raise ModelLoadError(f"{model_path}: input phải 4 chiều (N,C,H,W)/(N,H,W,C), nhận {shape}")
    dims = [d if isinstance(d, int) and d > 0 else None for d in shape]
    if dims[1] == 3 and dims[3] != 3:
        layout = "NCHW"
    elif dims[3] == 3 and dims[1] != 3:
        layout = "NHWC"
    else:
        layout = spec.layout
    if layout != spec.layout:
        if has_card and spec.card.get("input", {}).get("layout"):
            raise ModelLoadError(f"{model_path}: model layout {layout} nhưng model_card ghi {spec.layout}")
        log.warning("Layout input suy ra từ model là %s (spec %s) — dùng %s", layout, spec.layout, layout)
        spec.layout = layout
    h, w = (dims[2], dims[3]) if spec.layout == "NCHW" else (dims[1], dims[2])
    c = dims[1] if spec.layout == "NCHW" else dims[3]
    if c not in (None, 3):
        raise ModelLoadError(f"{model_path}: model cần {c} kênh, chỉ hỗ trợ ảnh 3 kênh")
    if h is not None and w is not None:
        if h != w:
            raise ModelLoadError(f"{model_path}: input không vuông {h}x{w}, không hỗ trợ")
        if h != spec.input_size:
            if has_card:
                raise ModelLoadError(f"{model_path}: model nhận {h}x{h} nhưng model_card/config ghi "
                                     f"{spec.input_size}x{spec.input_size} — sửa model_card.json hoặc ai.input_size")
            log.warning("ai.input_size=%d khác model (%d) — dùng %d theo model", spec.input_size, h, h)
            spec.input_size = h


# ---------------------------------------------------------------------------
# ONNX Runtime
# ---------------------------------------------------------------------------
class OnnxClassifier(Classifier):
    """P-05 / AI-06: suy luận ONNX Runtime trên CPU, tiền xử lý theo model_card.json."""

    def __init__(self, cfg: AiCfg) -> None:
        try:
            import onnxruntime as ort  # import lười: Pi cài onnxruntime, laptop test có thể không
        except ImportError as e:  # pragma: no cover
            raise ModelLoadError("Thiếu onnxruntime (pip install onnxruntime)") from e
        self.cfg = cfg
        model_path = Path(cfg.model_path)
        if not model_path.exists():
            raise ModelLoadError(f"ai.model_path không tồn tại: {model_path}")
        self.spec = build_spec(cfg)
        so = ort.SessionOptions()
        so.intra_op_num_threads = max(1, int(cfg.threads))
        so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        so.log_severity_level = 3
        try:
            self.session = ort.InferenceSession(str(model_path), so, providers=["CPUExecutionProvider"])
        except Exception as e:
            raise ModelLoadError(f"Không nạp được model ONNX {model_path}: {e}") from e
        inputs = self.session.get_inputs()
        if len(inputs) != 1:
            raise ModelLoadError(f"{model_path}: model có {len(inputs)} input, cần đúng 1")
        inp = inputs[0]
        if self.spec.input_name and self.spec.input_name != inp.name:
            raise ModelLoadError(f"{model_path}: input tên '{inp.name}' nhưng model_card ghi '{self.spec.input_name}'")
        self.input_name = inp.name
        _check_input_shape(list(inp.shape), self.spec, bool(self.spec.card), str(model_path))
        if "float16" in inp.type:
            self._input_dtype: type = np.float16
        elif "float" not in inp.type:
            raise ModelLoadError(f"{model_path}: input kiểu {inp.type}, chỉ hỗ trợ float (int8 dùng QDQ bên trong)")
        else:
            self._input_dtype = np.float32
        self.output_names = [o.name for o in self.session.get_outputs()]
        self._resolve_outputs(str(model_path))
        self.model_version = self.spec.version
        log.info("OnnxClassifier sẵn sàng: %s v%s input=%s %dx%d kind=%s classes=%s thr=%.3f/%.2f threads=%d",
                 model_path.name, self.model_version, self.input_name, self.spec.input_size, self.spec.input_size,
                 self.spec.kind, self.spec.classes, self.spec.defect_threshold, self.spec.min_confidence, cfg.threads)

    def _resolve_outputs(self, model_path: str) -> None:
        names = self.output_names
        spec = self.spec
        if spec.kind == KIND_BINARY_TYPES:
            if spec.probs_output not in names:
                raise ModelLoadError(f"{model_path}: không có output '{spec.probs_output}' (có: {names})")
            if spec.types_output and spec.types_output not in names:
                log.warning("Model không có output '%s' — bỏ qua loại lỗi", spec.types_output)
                spec.types_output = ""
            return
        # generic: 1 output (nếu nhiều output mà có 'probs' + 'type_probs' thì tự chuyển sang kiểu a)
        if "probs" in names and "type_probs" in names and len(spec.classes) == 2:
            spec.kind, spec.probs_output, spec.types_output = KIND_BINARY_TYPES, "probs", "type_probs"
            if not spec.damage_types:
                k = next((o.shape[-1] for o in self.session.get_outputs() if o.name == "type_probs"), 0)
                spec.damage_types = [f"type_{i}" for i in range(int(k) if isinstance(k, int) else 0)]
            spec.classes = [LABEL_OK, LABEL_DEFECT]
            log.info("Phát hiện output probs/type_probs → dùng kiểu notebook nhóm")
            return
        if spec.probs_output and spec.probs_output in names:
            return
        if len(names) != 1:
            raise ModelLoadError(f"{model_path}: model generic phải có 1 output, có {names}")
        spec.probs_output = names[0]

    def predict(self, image: np.ndarray, roi: tuple[int, int, int, int] | None = None) -> ClassResult:
        x = preprocess(image, roi, self.spec).astype(self._input_dtype, copy=False)
        t0 = time.perf_counter()
        outs = self.session.run(self.output_names, {self.input_name: x})
        infer_ms = (time.perf_counter() - t0) * 1000.0
        return interpret(dict(zip(self.output_names, outs)), self.spec, infer_ms)

    def warmup(self) -> None:
        """Chạy 3 lần với ảnh giả để ORT cấp phát bộ nhớ/khởi tạo thread trước kiện đầu tiên."""
        S = self.spec.input_size
        dummy = np.full((S, S, 3), 128, np.uint8)
        for _ in range(3):
            self.predict(dummy)
        log.info("OnnxClassifier warmup xong")

    def close(self) -> None:
        self.session = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# TFLite
# ---------------------------------------------------------------------------
class TfliteClassifier(Classifier):
    """P-05 / AI-06: suy luận TFLite (tflite_runtime hoặc tensorflow.lite), hỗ trợ input/output int8 lượng tử."""

    def __init__(self, cfg: AiCfg) -> None:
        Interpreter = _import_tflite_interpreter()
        self.cfg = cfg
        model_path = Path(cfg.model_path)
        if not model_path.exists():
            raise ModelLoadError(f"ai.model_path không tồn tại: {model_path}")
        self.spec = build_spec(cfg)
        if self.spec.layout == "NCHW" and not self.spec.card.get("input", {}).get("layout"):
            self.spec.layout = "NHWC"  # TFLite/Keras mặc định NHWC
        try:
            self.interp = Interpreter(model_path=str(model_path), num_threads=max(1, int(cfg.threads)))
            self.interp.allocate_tensors()
        except Exception as e:
            raise ModelLoadError(f"Không nạp được model TFLite {model_path}: {e}") from e
        inputs = self.interp.get_input_details()
        if len(inputs) != 1:
            raise ModelLoadError(f"{model_path}: model có {len(inputs)} input, cần đúng 1")
        self._in = inputs[0]
        _check_input_shape([int(d) for d in self._in["shape"]], self.spec, bool(self.spec.card), str(model_path))
        self._outs = self.interp.get_output_details()
        self.output_names = [str(o["name"]) for o in self._outs]
        self._resolve_outputs(str(model_path))
        self.model_version = self.spec.version
        log.info("TfliteClassifier sẵn sàng: %s v%s %dx%d dtype=%s kind=%s classes=%s thr=%.3f/%.2f",
                 model_path.name, self.model_version, self.spec.input_size, self.spec.input_size,
                 np.dtype(self._in["dtype"]).name, self.spec.kind, self.spec.classes,
                 self.spec.defect_threshold, self.spec.min_confidence)

    def _resolve_outputs(self, model_path: str) -> None:
        spec = self.spec
        names = self.output_names
        if spec.kind == KIND_BINARY_TYPES:
            # Tên tensor TFLite thường dài (StatefulPartitionedCall:0...) → khớp theo kích thước: [N,2] = probs, còn lại = types
            if spec.probs_output not in names:
                two = [o for o in self._outs if int(o["shape"][-1]) == 2]
                if not two:
                    raise ModelLoadError(f"{model_path}: không tìm thấy output 2 lớp [OK, DEFECT] (có {names})")
                spec.probs_output = str(two[0]["name"])
                rest = [o for o in self._outs if o is not two[0]]
                spec.types_output = str(rest[0]["name"]) if rest else ""
            return
        if len(self._outs) != 1:
            raise ModelLoadError(f"{model_path}: model generic phải có 1 output, có {names}")
        spec.probs_output = names[0]

    def predict(self, image: np.ndarray, roi: tuple[int, int, int, int] | None = None) -> ClassResult:
        x = preprocess(image, roi, self.spec)
        dtype = np.dtype(self._in["dtype"])
        if dtype in (np.int8, np.uint8):
            scale, zero = _quant_params(self._in)
            x = np.clip(np.round(x / scale + zero), np.iinfo(dtype).min, np.iinfo(dtype).max).astype(dtype)
        else:
            x = x.astype(dtype, copy=False)
        t0 = time.perf_counter()
        self.interp.set_tensor(self._in["index"], x)
        self.interp.invoke()
        outputs: dict[str, np.ndarray] = {}
        for o in self._outs:
            y = self.interp.get_tensor(o["index"])
            if np.dtype(o["dtype"]) in (np.int8, np.uint8):
                scale, zero = _quant_params(o)
                y = (y.astype(np.float32) - zero) * scale
            outputs[str(o["name"])] = y
        infer_ms = (time.perf_counter() - t0) * 1000.0
        return interpret(outputs, self.spec, infer_ms)

    def warmup(self) -> None:
        S = self.spec.input_size
        dummy = np.full((S, S, 3), 128, np.uint8)
        for _ in range(3):
            self.predict(dummy)
        log.info("TfliteClassifier warmup xong")

    def close(self) -> None:
        self.interp = None  # type: ignore[assignment]


def _quant_params(detail: dict[str, Any]) -> tuple[float, int]:
    scale, zero = detail.get("quantization", (0.0, 0))
    if not scale:
        qp = detail.get("quantization_parameters") or {}
        scales, zeros = qp.get("scales", []), qp.get("zero_points", [])
        scale = float(scales[0]) if len(scales) else 1.0
        zero = int(zeros[0]) if len(zeros) else 0
    return float(scale) or 1.0, int(zero)


def _import_tflite_interpreter():
    """tflite_runtime (gọn, cho Pi) → tensorflow.lite (laptop). Import lười để không bắt buộc cài TF."""
    try:
        from tflite_runtime.interpreter import Interpreter  # type: ignore
        return Interpreter
    except ImportError:
        pass
    try:
        from tensorflow.lite import Interpreter  # type: ignore
        return Interpreter
    except ImportError as e:
        raise ModelLoadError("Thiếu tflite_runtime (pip install tflite-runtime) hoặc tensorflow") from e


def make_classifier(cfg: AiCfg) -> Classifier:
    """Chọn backend theo ai.backend (onnx | tflite). 'dummy' do orchestrator P3 xử lý."""
    if cfg.backend == "onnx":
        return OnnxClassifier(cfg)
    if cfg.backend == "tflite":
        return TfliteClassifier(cfg)
    raise ModelLoadError(f"ai.backend '{cfg.backend}' không do classifier_onnx xử lý (onnx | tflite)")
