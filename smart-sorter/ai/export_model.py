"""AI-04: export checkpoint 3-best thành ONNX FP32, kiểm tra parity trước khi phát hành."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import onnx
import onnxruntime as ort
import torch
from torch import nn

from packaging_net import load_model

ROOT = Path(__file__).resolve().parent


def export(checkpoint: Path, out: Path) -> dict:
    torch.set_num_threads(4)
    torch.manual_seed(42)
    model = nn.Sequential(load_model(checkpoint), nn.Softmax(dim=1)).eval()
    out.mkdir(parents=True, exist_ok=True)
    target = out / "packaging_cls.onnx"
    x = torch.rand(1, 3, 256, 256)
    torch.onnx.export(model, x, str(target), input_names=["image"], output_names=["probabilities"],
                      opset_version=17, do_constant_folding=True, dynamo=False)
    onnx.checker.check_model(str(target))
    options = ort.SessionOptions()
    options.intra_op_num_threads = 4
    session = ort.InferenceSession(str(target), options, providers=["CPUExecutionProvider"])
    max_error = 0.0
    # Màu đen/trắng, gradient và ảnh ngẫu nhiên; không phải phép đo accuracy.
    samples = [torch.zeros_like(x), torch.ones_like(x),
               torch.linspace(0, 1, x.numel()).reshape_as(x), x, torch.rand_like(x)]
    with torch.inference_mode():
        for sample in samples:
            expected = model(sample).numpy()
            actual = session.run(None, {"image": sample.numpy()})[0]
            np.testing.assert_allclose(actual, expected, rtol=1e-4, atol=1e-5)
            if actual.argmax() != expected.argmax():
                raise RuntimeError("ONNX/PyTorch khác nhãn")
            max_error = max(max_error, float(np.max(np.abs(actual - expected))))
    sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    card = {
        "version": "3-best-mobilenetv4-fp32",
        "architecture": "mobilenetv4_conv_medium.e500_r256_in1k",
        "source_notebook": "3-best.ipynb", "checkpoint": checkpoint.name,
        "checkpoint_sha256": sha(checkpoint), "onnx_sha256": sha(target),
        "input": {"name": "image", "shape": [1, 3, 256, 256], "size": 256,
                  "layout": "NCHW", "color": "RGB", "range": [0, 1],
                  "resize": "INTER_AREA", "normalization_embedded": True},
        "classes": ["normal", "damage"], "outputs": {"probabilities": ["normal", "damage"]},
        "output_activation": "probabilities", "damage_types": [],
        "defect_threshold": 0.5, "min_confidence": 0.6,
        "threshold_note": "0.5 = argmax notebook; p_normal < 0.6 bị loại UNCERTAIN. Chưa hiệu chỉnh tại trạm.",
        "notebook_reported_test_metrics": {"accuracy": 0.9725, "precision_damage": 0.9827,
            "recall_damage": 0.9703, "f1_damage": 0.9765, "average_precision_damage": 0.9915},
        "metrics_note": "Chép từ output notebook, chưa tái đánh giá vì không có tập test gốc.",
        "parity": {"samples": len(samples), "max_abs_error": max_error, "rtol": 1e-4, "atol": 1e-5},
        "export_versions": {"torch": torch.__version__, "onnx": onnx.__version__, "onnxruntime": ort.__version__},
    }
    (out / "labels.json").write_text(json.dumps({"classes": ["normal", "damage"]}, indent=2), encoding="utf-8")
    (out / "model_card.json").write_text(json.dumps(card, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return card


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, default=ROOT / "best_model.pth")
    parser.add_argument("--out", type=Path, default=ROOT / "models")
    args = parser.parse_args()
    print(json.dumps(export(args.checkpoint, args.out)["parity"], indent=2))
