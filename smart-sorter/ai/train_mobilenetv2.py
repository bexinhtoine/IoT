"""AI-03 (phương án 2) / AI-04 — MobileNetV2 transfer learning bằng Keras, xuất TFLite float16 + int8.

Giai đoạn 1: đóng băng backbone ImageNet, train head 10 epoch.
Giai đoạn 2: mở ~30 lớp cuối, fine-tune với learning rate nhỏ (1e-5), early stopping.
Xuất: packaging_cls_fp16.tflite, packaging_cls_int8.tflite (representative dataset từ train), labels.json,
model_card.json với input.range [-1, 1] (MobileNetV2 preprocess_input) để TfliteClassifier chuẩn hóa đúng.

Cài: pip install tensorflow (laptop/Colab). KHÔNG cần trên Pi (Pi chỉ cần tflite-runtime).
Ví dụ:
    python ai/train_mobilenetv2.py --data ai/data --out ai/runs/mnv2
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import logging
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

log = logging.getLogger("train_mobilenetv2")


@dataclass
class TrainCfg:
    data: str = "ai/data"
    out: str = "ai/runs/mnv2"
    imgsz: int = 224
    batch: int = 32
    head_epochs: int = 10
    finetune_epochs: int = 20
    finetune_layers: int = 30
    head_lr: float = 1e-3
    finetune_lr: float = 1e-5
    seed: int = 42
    version: str = "1.0.0"
    rep_samples: int = 200       # số ảnh đại diện cho lượng tử int8


def load_datasets(cfg: TrainCfg):
    import tensorflow as tf

    data = Path(cfg.data)
    kw = dict(image_size=(cfg.imgsz, cfg.imgsz), batch_size=cfg.batch, label_mode="categorical", seed=cfg.seed)
    train_ds = tf.keras.utils.image_dataset_from_directory(data / "train", shuffle=True, **kw)
    val_ds = tf.keras.utils.image_dataset_from_directory(data / "val", shuffle=False, **kw)
    classes = list(train_ds.class_names)
    log.info("Lớp (thứ tự output): %s", classes)
    # Augmentation theo AI-03: xoay ±15°, lật ngang, độ sáng/tương phản, zoom nhẹ
    aug = tf.keras.Sequential([
        tf.keras.layers.RandomFlip("horizontal"),
        tf.keras.layers.RandomRotation(15 / 360.0),
        tf.keras.layers.RandomZoom(0.1),
        tf.keras.layers.RandomBrightness(0.2),
        tf.keras.layers.RandomContrast(0.2),
    ], name="augment")
    AUTOTUNE = tf.data.AUTOTUNE
    pre = tf.keras.applications.mobilenet_v2.preprocess_input   # 0..255 → -1..1 (giống lúc suy luận trên Pi)
    train_ds = train_ds.map(lambda x, y: (pre(aug(x, training=True)), y), num_parallel_calls=AUTOTUNE).prefetch(AUTOTUNE)
    val_ds = val_ds.map(lambda x, y: (pre(x), y), num_parallel_calls=AUTOTUNE).prefetch(AUTOTUNE)
    return train_ds, val_ds, classes


def build_model(cfg: TrainCfg, n_classes: int):
    """MobileNetV2 ImageNet + GAP + Dense softmax. Đầu vào là ảnh ĐÃ chuẩn hóa -1..1 (preprocess_input);
    bước chuẩn hóa KHÔNG nhúng trong model để lượng tử int8 gọn → model_card.input.range = [-1, 1]
    và TfliteClassifier tự đưa uint8 0..255 về -1..1 (hoặc về int8 theo scale/zero_point)."""
    import tensorflow as tf

    base = tf.keras.applications.MobileNetV2(input_shape=(cfg.imgsz, cfg.imgsz, 3), include_top=False, weights="imagenet")
    base.trainable = False
    inp = tf.keras.Input(shape=(cfg.imgsz, cfg.imgsz, 3), name="image")
    x = base(inp, training=False)
    x = tf.keras.layers.GlobalAveragePooling2D()(x)
    x = tf.keras.layers.Dropout(0.2)(x)
    out = tf.keras.layers.Dense(n_classes, activation="softmax", name="probs")(x)
    return tf.keras.Model(inp, out, name="packaging_mnv2"), base


def train(cfg: TrainCfg):
    import tensorflow as tf

    tf.keras.utils.set_random_seed(cfg.seed)
    out = Path(cfg.out)
    out.mkdir(parents=True, exist_ok=True)
    train_ds, val_ds, classes = load_datasets(cfg)
    model, base = build_model(cfg, len(classes))
    ckpt = out / "best.keras"
    cbs = [tf.keras.callbacks.ModelCheckpoint(str(ckpt), monitor="val_accuracy", save_best_only=True),
           tf.keras.callbacks.EarlyStopping(monitor="val_loss", patience=5, restore_best_weights=True),
           tf.keras.callbacks.CSVLogger(str(out / "history.csv"), append=True)]

    # Giai đoạn 1: chỉ head
    model.compile(optimizer=tf.keras.optimizers.Adam(cfg.head_lr), loss="categorical_crossentropy", metrics=["accuracy"])
    log.info("Giai đoạn 1: train head %d epoch", cfg.head_epochs)
    model.fit(train_ds, validation_data=val_ds, epochs=cfg.head_epochs, callbacks=cbs)

    # Giai đoạn 2: mở N lớp cuối của backbone, lr nhỏ (BatchNorm giữ nguyên để ổn định)
    base.trainable = True
    for layer in base.layers[:-cfg.finetune_layers]:
        layer.trainable = False
    for layer in base.layers[-cfg.finetune_layers:]:
        if isinstance(layer, tf.keras.layers.BatchNormalization):
            layer.trainable = False
    model.compile(optimizer=tf.keras.optimizers.Adam(cfg.finetune_lr), loss="categorical_crossentropy", metrics=["accuracy"])
    log.info("Giai đoạn 2: fine-tune %d lớp cuối, %d epoch, lr=%g", cfg.finetune_layers, cfg.finetune_epochs, cfg.finetune_lr)
    hist = model.fit(train_ds, validation_data=val_ds, epochs=cfg.finetune_epochs, callbacks=cbs)
    val_acc = float(max(hist.history.get("val_accuracy", [0.0])))
    model.save(str(out / "final.keras"))
    return model, classes, val_acc, train_ds


def export_tflite(model, classes: list[str], val_acc: float, cfg: TrainCfg, train_ds) -> None:
    import tensorflow as tf

    out = Path(cfg.out)
    # float16
    conv = tf.lite.TFLiteConverter.from_keras_model(model)
    conv.optimizations = [tf.lite.Optimize.DEFAULT]
    conv.target_spec.supported_types = [tf.float16]
    (out / "packaging_cls_fp16.tflite").write_bytes(conv.convert())

    # int8 toàn phần với representative dataset (ảnh train đã qua preprocess -1..1; input/output int8)
    def rep_gen():
        n = 0
        for x, _ in train_ds.unbatch().batch(1):
            yield [tf.cast(x, tf.float32)]
            n += 1
            if n >= cfg.rep_samples:
                break

    conv = tf.lite.TFLiteConverter.from_keras_model(model)
    conv.optimizations = [tf.lite.Optimize.DEFAULT]
    conv.representative_dataset = rep_gen
    conv.target_spec.supported_ops = [tf.lite.OpsSet.TFLITE_BUILTINS_INT8]
    conv.inference_input_type = tf.int8
    conv.inference_output_type = tf.int8
    try:
        (out / "packaging_cls_int8.tflite").write_bytes(conv.convert())
    except Exception as e:  # noqa: BLE001
        log.warning("Lượng tử int8 thất bại (%s) — chỉ có bản float16", e)

    (out / "labels.json").write_text(json.dumps({"classes": classes}, ensure_ascii=False, indent=2), encoding="utf-8")
    card = {
        "model_name": "packaging_cls", "version": cfg.version,
        "created": _dt.datetime.now().isoformat(timespec="seconds"),
        "architecture": "MobileNetV2 (ImageNet) + GAP + Dense softmax; Keras → TFLite",
        "file": "packaging_cls_fp16.tflite",
        "imgsz": cfg.imgsz,
        "normalization": "-1..1 RGB (MobileNetV2 preprocess_input: x/127.5 - 1) — KHÔNG nhúng trong model; "
                         "TfliteClassifier đọc input.range và tự chuẩn hóa; bản int8 áp scale/zero_point của tensor input",
        "input": {"name": "image", "shape": [1, cfg.imgsz, cfg.imgsz, 3], "size": cfg.imgsz, "dtype": "float32|int8",
                  "layout": "NHWC", "color": "RGB", "range": [-1.0, 1.0],
                  "resize": f"cắt ROI rồi resize thẳng về {cfg.imgsz}x{cfg.imgsz} bằng cv2.INTER_AREA"},
        "classes": classes,
        "outputs": {"probs": classes},
        "decision_rule": "p_defect = tổng xác suất các lớp khác 'ok'; DEFECT nếu p_defect >= defect_threshold",
        "defect_threshold": 0.5, "min_confidence": 0.6, "threshold_chosen_on": "chưa chọn — chạy ai/evaluate.py",
        "metrics": {"val_accuracy": val_acc},
        "train_config": asdict(cfg),
    }
    (out / "model_card.json").write_text(json.dumps(card, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("Đã xuất TFLite + labels.json + model_card.json vào %s", out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="MobileNetV2 Keras → TFLite (AI-03 phương án 2)")
    d = TrainCfg()
    ap.add_argument("--data", default=d.data)
    ap.add_argument("--out", default=d.out)
    ap.add_argument("--imgsz", type=int, default=d.imgsz)
    ap.add_argument("--batch", type=int, default=d.batch)
    ap.add_argument("--head-epochs", type=int, default=d.head_epochs)
    ap.add_argument("--finetune-epochs", type=int, default=d.finetune_epochs)
    ap.add_argument("--finetune-layers", type=int, default=d.finetune_layers)
    ap.add_argument("--version", default=d.version)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    cfg = TrainCfg(data=args.data, out=args.out, imgsz=args.imgsz, batch=args.batch, head_epochs=args.head_epochs,
                   finetune_epochs=args.finetune_epochs, finetune_layers=args.finetune_layers, version=args.version)
    model, classes, val_acc, train_ds = train(cfg)
    export_tflite(model, classes, val_acc, cfg, train_ds)
    return 0


if __name__ == "__main__":
    sys.exit(main())
