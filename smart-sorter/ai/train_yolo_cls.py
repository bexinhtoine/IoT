"""AI-03 / AI-04 — Huấn luyện YOLOv8n-cls và xuất ONNX + labels.json + model_card.json (kiểu generic).

Tương đương ai/train_yolo_cls.ipynb (Colab). Yêu cầu dữ liệu đã chia bằi prepare_dataset.py:
    data/train/<lớp>/  data/val/<lớp>/  data/test/<lớp>/
Cài: pip install ultralytics onnx onnxruntime   (KHÔNG cần trên Pi; script này chạy ở laptop/Colab)

Ví dụ:
    python ai/train_yolo_cls.py --data ai/data --epochs 50 --out ai/runs/yolo_cls
Sau đó:
    python ai/evaluate.py --model ai/runs/yolo_cls/export/packaging_cls.onnx --data ai/data   # chọn ngưỡng (AI-07)
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import logging
import shutil
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

log = logging.getLogger("train_yolo_cls")


@dataclass
class TrainCfg:
    data: str = "ai/data"
    out: str = "ai/runs/yolo_cls"
    model: str = "yolov8n-cls.pt"
    imgsz: int = 224
    epochs: int = 50
    patience: int = 10
    batch: int = 32
    seed: int = 42
    device: str = ""           # "" = tự chọn (cuda nếu có), "cpu", "0"
    version: str = "1.0.0"
    opset: int = 12
    # Augmentation theo AI-03: xoay ±15°, lật ngang, đổi độ sáng, scale nhẹ
    degrees: float = 15.0
    fliplr: float = 0.5
    flipud: float = 0.0
    hsv_v: float = 0.4
    hsv_s: float = 0.4
    hsv_h: float = 0.015
    scale: float = 0.1
    translate: float = 0.05


def train(cfg: TrainCfg):
    """Train YOLOv8n-cls; trả về (YOLO model đã nạp best.pt, thư mục run)."""
    from ultralytics import YOLO  # import lười — chỉ cần khi train

    data = Path(cfg.data)
    if not (data / "train").is_dir() or not (data / "val").is_dir():
        raise SystemExit(f"{data} phải có train/ và val/ (chạy prepare_dataset.py trước)")
    model = YOLO(cfg.model)
    results = model.train(
        data=str(data), imgsz=cfg.imgsz, epochs=cfg.epochs, patience=cfg.patience, batch=cfg.batch,
        seed=cfg.seed, device=cfg.device or None, project=str(Path(cfg.out).parent), name=Path(cfg.out).name,
        exist_ok=True, pretrained=True, optimizer="auto",
        degrees=cfg.degrees, fliplr=cfg.fliplr, flipud=cfg.flipud, hsv_v=cfg.hsv_v, hsv_s=cfg.hsv_s, hsv_h=cfg.hsv_h,
        scale=cfg.scale, translate=cfg.translate, erasing=0.2, mosaic=0.0, mixup=0.0,
        plots=True, verbose=True,
    )
    run_dir = Path(results.save_dir) if hasattr(results, "save_dir") else Path(cfg.out)
    best = run_dir / "weights" / "best.pt"
    if not best.exists():
        raise SystemExit(f"Không thấy {best}")
    log.info("best.pt: %s", best)
    return YOLO(str(best)), run_dir


def export_onnx(model, run_dir: Path, cfg: TrainCfg) -> dict[str, Path]:
    """Export ONNX imgsz 224 opset 12 → export/packaging_cls.onnx + labels.json + model_card.json."""
    export_dir = run_dir / "export"
    export_dir.mkdir(parents=True, exist_ok=True)
    onnx_path = Path(model.export(format="onnx", imgsz=cfg.imgsz, opset=cfg.opset, simplify=True, dynamic=False))
    dst = export_dir / "packaging_cls.onnx"
    shutil.copy2(onnx_path, dst)
    names = model.names  # dict {idx: name} đúng thứ tự output của model
    classes = [names[i] for i in sorted(names)]
    labels_path = export_dir / "labels.json"
    labels_path.write_text(json.dumps({"classes": classes}, ensure_ascii=False, indent=2), encoding="utf-8")

    # Số liệu val của ultralytics (top1) nếu có
    metrics: dict[str, float] = {}
    try:
        val = model.val(data=cfg.data, imgsz=cfg.imgsz, split="val", plots=False, verbose=False)
        metrics = {"val_top1_acc": float(val.top1), "val_top5_acc": float(val.top5)}
    except Exception as e:  # noqa: BLE001
        log.warning("Không lấy được metrics val: %s", e)

    card = {
        "model_name": "packaging_cls",
        "version": cfg.version,
        "created": _dt.datetime.now().isoformat(timespec="seconds"),
        "architecture": f"{Path(cfg.model).stem} (ultralytics classify)",
        "file": "packaging_cls.onnx",
        "imgsz": cfg.imgsz,
        "normalization": "0..1 RGB",
        "input": {"name": "images", "shape": [1, 3, cfg.imgsz, cfg.imgsz], "size": cfg.imgsz, "dtype": "float32",
                  "layout": "NCHW", "color": "RGB", "range": [0.0, 1.0],
                  "resize": f"cắt ROI rồi resize thẳng về {cfg.imgsz}x{cfg.imgsz} bằng cv2.INTER_AREA",
                  "normalization": "chia 255, không trừ mean/std (giống ultralytics classify)"},
        "classes": classes,
        "outputs": {"output0": classes},
        "decision_rule": "p_defect = tổng xác suất các lớp khác 'ok'; DEFECT nếu p_defect >= defect_threshold; "
                         "OK nếu p_ok >= min_confidence; còn lại UNCERTAIN",
        # Ngưỡng tạm; evaluate.py quét trên val và ghi đè (AI-07)
        "defect_threshold": 0.5, "min_confidence": 0.6, "threshold_chosen_on": "chưa chọn — chạy ai/evaluate.py",
        "metrics": metrics,
        "train_config": asdict(cfg),
    }
    card_path = export_dir / "model_card.json"
    card_path.write_text(json.dumps(card, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("Đã xuất %s, %s, %s", dst, labels_path, card_path)
    return {"onnx": dst, "labels": labels_path, "model_card": card_path}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Train YOLOv8n-cls + export ONNX (AI-03/AI-04)")
    d = TrainCfg()
    ap.add_argument("--data", default=d.data)
    ap.add_argument("--out", default=d.out)
    ap.add_argument("--model", default=d.model, help="yolov8n-cls.pt | yolov8s-cls.pt | yolo11n-cls.pt")
    ap.add_argument("--imgsz", type=int, default=d.imgsz)
    ap.add_argument("--epochs", type=int, default=d.epochs)
    ap.add_argument("--patience", type=int, default=d.patience)
    ap.add_argument("--batch", type=int, default=d.batch)
    ap.add_argument("--device", default=d.device)
    ap.add_argument("--version", default=d.version, help="ghi vào model_card.version (tăng mỗi lần train lại)")
    ap.add_argument("--export-only", action="store_true", help="chỉ export từ <out>/weights/best.pt")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    cfg = TrainCfg(data=args.data, out=args.out, model=args.model, imgsz=args.imgsz, epochs=args.epochs,
                   patience=args.patience, batch=args.batch, device=args.device, version=args.version)
    if args.export_only:
        from ultralytics import YOLO
        run_dir = Path(cfg.out)
        model = YOLO(str(run_dir / "weights" / "best.pt"))
    else:
        model, run_dir = train(cfg)
    export_onnx(model, run_dir, cfg)
    print("Tiếp theo: python ai/evaluate.py --model", run_dir / "export" / "packaging_cls.onnx", "--data", cfg.data)
    return 0


if __name__ == "__main__":
    sys.exit(main())
