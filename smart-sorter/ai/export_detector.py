"""Chạy MỘT LẦN trên laptop: YOLO-World với prompt cardboard box → ONNX cho Pi."""
import argparse
import hashlib
import json
import shutil
from functools import partial
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    root = Path(__file__).resolve().parent
    parser.add_argument("--model", default=str(root / ".cache" / "yolov8s-worldv2.pt"))
    parser.add_argument("--out", type=Path, default=Path(__file__).resolve().parent / "models")
    parser.add_argument("--size", type=int, default=640)
    parser.add_argument("--cache", type=Path, default=root / ".cache" / "clip")
    args = parser.parse_args()
    from ultralytics import YOLOWorld
    import onnx
    import onnxruntime as ort
    import numpy as np
    import torch
    import clip

    torch.set_num_threads(4)
    # Ultralytics không cung cấp download_root qua set_classes; cấu hình loader
    # cho lần export này để cache nằm trong dự án, không đổi thư mục HOME.
    clip.load = partial(clip.load, download_root=str(args.cache))

    args.out.mkdir(parents=True, exist_ok=True)
    model = YOLOWorld(args.model)
    model.set_classes(["cardboard box"])
    # Trọng số/CLIP chỉ tải ở bước chuẩn bị này; Pi không tải hoặc export model.
    source = Path(model.export(format="onnx", imgsz=args.size, opset=17,
                             dynamic=False, simplify=False, nms=False, device="cpu"))
    target = args.out / "box_detector.onnx"
    if source.resolve() != target.resolve():
        shutil.copy2(source, target)
    onnx.checker.check_model(str(target))
    options = ort.SessionOptions()
    options.intra_op_num_threads = 4
    session = ort.InferenceSession(str(target), options, providers=["CPUExecutionProvider"])
    raw = session.run(None, {session.get_inputs()[0].name: np.zeros((1, 3, args.size, args.size), np.float32)})[0]
    if raw.ndim != 3 or raw.shape[1] != 5 or not np.isfinite(raw).all():
        raise RuntimeError(f"Detector export sai output: {raw.shape}")
    card = {"source": Path(args.model).name, "source_url": "https://github.com/ultralytics/assets/releases/download/v8.3.0/yolov8s-worldv2.pt", "classes": ["cardboard box"], "input_size": args.size,
            "output": list(raw.shape), "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            "license": session.get_modelmeta().custom_metadata_map.get("license", ""),
            "note": "Zero-shot detector; chưa đánh giá recall trên băng chuyền thật."}
    (args.out / "detector_card.json").write_text(json.dumps(card, indent=2) + "\n", encoding="utf-8")
    print(f"Đã xuất {target}; copy sang Pi một lần.")


if __name__ == "__main__":
    main()
