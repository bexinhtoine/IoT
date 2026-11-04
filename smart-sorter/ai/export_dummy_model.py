"""AI-04 (bản giả) — Tạo model ONNX nhỏ "thật" không cần PyTorch, cùng model_card.json + labels.json.

Dùng để: (1) unit test OnnxClassifier, (2) chạy sorter --sim với ai.backend: onnx trước khi có model train thật,
(3) benchmark đường đi tiền xử lý + ONNX Runtime trên Pi.

Model (vài KB, luật heuristic có nghĩa vật lý):
    image [N,3,S,S] RGB 0..1
      → AveragePool(k=S/4)      : độ sáng trung bình từng ô S/4 x S/4
      → Neg → GlobalMaxPool → Neg : ô TỐI NHẤT (vết rách/thủng lộ lớp giấy tối, bóng móp)
      → Flatten [N,3]
      → Gemm → Softmax  "probs" [N,2] = [OK, DEFECT]   : p_defect tăng khi ô tối nhất tối hơn ngưỡng
      → Gemm → Sigmoid  "type_probs" [N,4] = tear, dent, crush, wet
Định dạng đầu ra giống hệt ai/train_packaging_damage.ipynb (ô cuối) nên OnnxClassifier dùng chung một đường code.

Chạy:  python ai/export_dummy_model.py [--out ai/samples] [--size 224]
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np

log = logging.getLogger("export_dummy_model")

DAMAGE_TYPES = ["tear", "dent", "crush", "wet"]
DAMAGE_TYPES_VI = {"tear": "rách", "dent": "móp", "crush": "bẹp/vỡ", "wet": "ướt/bẩn"}


@dataclass
class DummySpec:
    size: int = 224
    patch_div: int = 4          # kích thước ô = size // patch_div
    dark_threshold: float = 0.40  # ô tối nhất có độ sáng < ngưỡng này → nghiêng về DEFECT
    sharpness: float = 12.0     # độ dốc sigmoid: p_defect = sigmoid(2*a*(t - z))
    version: str = "0.0.0-dummy"


def build_dummy_onnx(spec: DummySpec):
    """Dựng ModelProto bằng onnx.helper (không cần torch)."""
    import onnx
    from onnx import TensorProto, helper, numpy_helper

    S, a, t = spec.size, spec.sharpness, spec.dark_threshold
    k = max(1, S // spec.patch_div)
    # Gemm 1: logits = z @ W1^T + b1, z = [r, g, b] của ô tối nhất; độ sáng z̄ = (r+g+b)/3
    #   logit_ok     = +a*(z̄ - t)  ;  logit_defect = -a*(z̄ - t)   → p_defect = sigmoid(2a(t - z̄))
    w1 = np.array([[a / 3, a / 3, a / 3], [-a / 3, -a / 3, -a / 3]], np.float32)
    b1 = np.array([-a * t, a * t], np.float32)
    # Gemm 2: loại lỗi (chỉ minh họa): tear ~ rất tối; dent ~ hơi tối; crush ~ tối vừa; wet ~ kênh xanh dương trội
    w2 = np.array([
        [-2 * a / 3, -2 * a / 3, -2 * a / 3],   # tear
        [-a / 3, -a / 3, -a / 3],               # dent
        [-a / 3, -a / 3, -a / 3],               # crush
        [-6.0, -6.0, 12.0],                     # wet: b > (r+g)/2
    ], np.float32)
    b2 = np.array([2 * a * t, a * t, a * t - 1.5, -1.0], np.float32)

    nodes = [
        helper.make_node("AveragePool", ["image"], ["patch_mean"], kernel_shape=[k, k], strides=[k, k]),
        helper.make_node("Neg", ["patch_mean"], ["neg_patch"]),
        helper.make_node("GlobalMaxPool", ["neg_patch"], ["neg_min"]),
        helper.make_node("Neg", ["neg_min"], ["darkest"]),
        helper.make_node("Flatten", ["darkest"], ["feat"], axis=1),
        helper.make_node("Gemm", ["feat", "W1", "b1"], ["logits"], transB=1),
        helper.make_node("Softmax", ["logits"], ["probs"], axis=1),
        helper.make_node("Gemm", ["feat", "W2", "b2"], ["type_logits"], transB=1),
        helper.make_node("Sigmoid", ["type_logits"], ["type_probs"]),
    ]
    graph = helper.make_graph(
        nodes, "dummy_packaging_cls",
        inputs=[helper.make_tensor_value_info("image", TensorProto.FLOAT, ["batch", 3, S, S])],
        outputs=[helper.make_tensor_value_info("probs", TensorProto.FLOAT, ["batch", 2]),
                 helper.make_tensor_value_info("type_probs", TensorProto.FLOAT, ["batch", len(DAMAGE_TYPES)])],
        initializer=[numpy_helper.from_array(w1, "W1"), numpy_helper.from_array(b1, "b1"),
                     numpy_helper.from_array(w2, "W2"), numpy_helper.from_array(b2, "b2")],
    )
    model = helper.make_model(graph, producer_name="smart-sorter-dummy", opset_imports=[helper.make_opsetid("", 13)])
    model.ir_version = 8
    onnx.checker.check_model(model)
    return model


def make_model_card(spec: DummySpec, defect_threshold: float = 0.6) -> dict:
    """model_card.json cùng khóa với notebook nhóm (ô "Ghi model_card.json")."""
    S = spec.size
    return {
        "model_name": "dummy_packaging_cls",
        "version": spec.version,
        "created": _dt.datetime.now().isoformat(timespec="seconds"),
        "architecture": "heuristic: darkest-patch brightness (không học từ dữ liệu)",
        "teacher": None, "distilled": False, "precision": "fp32", "file": "dummy_packaging_cls.onnx",
        "input": {"name": "image", "shape": [1, 3, S, S], "size": S, "dtype": "float32", "layout": "NCHW",
                  "color": "RGB", "range": [0.0, 1.0],
                  "resize": f"cắt ROI rồi resize thẳng về {S}x{S} bằng cv2.INTER_AREA",
                  "normalization": "đã nhúng trong mô hình — không trừ mean/chia std thêm"},
        "outputs": {"probs": ["OK", "DEFECT"], "type_probs": DAMAGE_TYPES},
        "decision_rule": "DEFECT nếu probs[0][1] >= defect_threshold, ngược lại OK",
        "defect_threshold": defect_threshold, "temperature": 1.0, "target_recall": 0.95,
        "threshold_chosen_on": "không (model giả)",
        "damage_types": DAMAGE_TYPES, "damage_types_vi": DAMAGE_TYPES_VI, "type_threshold": 0.5,
        "metrics": {"note": "model giả để test/benchmark — không có số liệu đánh giá"},
        "dummy": {"patch": S // spec.patch_div, "dark_threshold": spec.dark_threshold, "sharpness": spec.sharpness},
    }


def export_dummy(out_dir: str | Path, size: int = 224, defect_threshold: float = 0.6,
                 name: str = "dummy_packaging_cls.onnx", version: str = "0.0.0-dummy") -> dict[str, Path]:
    """Ghi <out_dir>/{name, model_card.json, labels.json}; trả về đường dẫn từng file."""
    import onnx

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    spec = DummySpec(size=size, version=version)
    model = build_dummy_onnx(spec)
    model_path = out / name
    onnx.save(model, str(model_path))
    card = make_model_card(spec, defect_threshold)
    card["file"] = name
    card_path = out / "model_card.json"
    card_path.write_text(json.dumps(card, ensure_ascii=False, indent=2), encoding="utf-8")
    labels_path = out / "labels.json"
    labels_path.write_text(json.dumps({"classes": ["OK", "DEFECT"], "types": DAMAGE_TYPES}, indent=2), encoding="utf-8")
    log.info("Đã ghi %s (%d byte), %s, %s", model_path, model_path.stat().st_size, card_path, labels_path)
    return {"model": model_path, "model_card": card_path, "labels": labels_path}


def export_generic_dummy(out_dir: str | Path, classes: tuple[str, ...] = ("defect", "ok"), size: int = 224,
                         name: str = "dummy_generic_cls.onnx", with_softmax: bool = False) -> dict[str, Path]:
    """Model giả kiểu YOLOv8-cls/generic: 1 output "output0" [N,C] (logits, hoặc softmax nếu with_softmax)
    + labels.json dạng list. Dùng để test nhánh generic của OnnxClassifier. Lớp 'ok' sáng, lớp khác tối."""
    import onnx
    from onnx import TensorProto, helper, numpy_helper

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    spec = DummySpec(size=size)
    S, a, t = spec.size, spec.sharpness, spec.dark_threshold
    k = max(1, S // spec.patch_div)
    C = len(classes)
    w = np.zeros((C, 3), np.float32)
    b = np.zeros((C,), np.float32)
    for i, c in enumerate(classes):
        if c.lower() in ("ok", "good", "intact"):
            w[i] = a / 3
            b[i] = -a * t
        else:
            w[i] = -a / 3 / max(1, C - 1)
            b[i] = a * t / max(1, C - 1)
    nodes = [
        helper.make_node("AveragePool", ["images"], ["pm"], kernel_shape=[k, k], strides=[k, k]),
        helper.make_node("Neg", ["pm"], ["npm"]),
        helper.make_node("GlobalMaxPool", ["npm"], ["nmin"]),
        helper.make_node("Neg", ["nmin"], ["dark"]),
        helper.make_node("Flatten", ["dark"], ["feat"], axis=1),
        helper.make_node("Gemm", ["feat", "W", "b"], ["logits" if with_softmax else "output0"], transB=1),
    ]
    if with_softmax:
        nodes.append(helper.make_node("Softmax", ["logits"], ["output0"], axis=1))
    graph = helper.make_graph(
        nodes, "dummy_generic_cls",
        inputs=[helper.make_tensor_value_info("images", TensorProto.FLOAT, [1, 3, S, S])],
        outputs=[helper.make_tensor_value_info("output0", TensorProto.FLOAT, [1, C])],
        initializer=[numpy_helper.from_array(w, "W"), numpy_helper.from_array(b, "b")],
    )
    model = helper.make_model(graph, producer_name="smart-sorter-dummy", opset_imports=[helper.make_opsetid("", 13)])
    model.ir_version = 8
    onnx.checker.check_model(model)
    model_path = out / name
    onnx.save(model, str(model_path))
    labels_path = out / "labels.json"
    labels_path.write_text(json.dumps(list(classes), indent=2), encoding="utf-8")
    return {"model": model_path, "labels": labels_path}


def main() -> None:
    ap = argparse.ArgumentParser(description="Tạo model ONNX giả + model_card.json + labels.json")
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent / "samples"))
    ap.add_argument("--size", type=int, default=224)
    ap.add_argument("--defect-threshold", type=float, default=0.6)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    paths = export_dummy(args.out, size=args.size, defect_threshold=args.defect_threshold)
    # Tự kiểm tra bằng onnxruntime nếu có
    try:
        import onnxruntime as ort
    except ImportError:
        log.warning("Không có onnxruntime để tự kiểm tra")
        return
    sess = ort.InferenceSession(str(paths["model"]), providers=["CPUExecutionProvider"])
    bright = np.full((1, 3, args.size, args.size), 0.8, np.float32)
    dark = bright.copy()
    dark[:, :, :80, :80] = 0.05
    for nm, x in (("sáng", bright), ("có vết tối", dark)):
        probs, types = sess.run(None, {"image": x})
        log.info("ảnh %-10s → p_ok=%.3f p_defect=%.3f types=%s", nm, probs[0, 0], probs[0, 1],
                 dict(zip(DAMAGE_TYPES, np.round(types[0], 3).tolist())))


if __name__ == "__main__":
    main()
