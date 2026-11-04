"""AI-05 / AI-07 — Đánh giá model ONNX trên tập test và chọn ngưỡng an toàn trên tập val.

Chạy model qua đúng OnnxClassifier của Pi (pi/sorter/classifier_onnx.py) để tiền xử lý giống lúc triển khai.
Đầu ra (trong --out, mặc định cạnh model):
    metrics.json            accuracy, precision/recall/F1 từng lớp, confusion matrix, ngưỡng đã chọn
    confusion_matrix.png    (matplotlib, bỏ qua nếu thiếu)
    misclassified_grid.png  ≤ 24 ảnh dự đoán sai (Pillow)
    model_card.json         cập nhật defect_threshold / metrics / threshold_chosen_on (nếu --update-card)

Chọn ngưỡng (AI-07): quét defect_threshold trên val, lấy ngưỡng LỚN NHẤT mà recall lớp DEFECT ≥ 0.95
(tương đương min_confidence NHỎ NHẤT = 1 − ngưỡng đạt recall ≥ 95 %: loại càng ít hàng tốt càng tốt
nhưng không bao giờ để lọt hàng hỏng quá 5 %). Nếu không ngưỡng nào đạt → lấy ngưỡng nhỏ nhất và cảnh báo.

Ví dụ:
    python ai/evaluate.py --model ai/runs/yolo_cls/export/packaging_cls.onnx --data ai/data --update-card
    python ai/evaluate.py --model ai/samples/dummy_packaging_cls.onnx --data ai/data   # thử với model giả
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pi"))

from sorter.classifier_onnx import OnnxClassifier  # noqa: E402
from sorter.config import AiCfg  # noqa: E402
from sorter.interfaces import LABEL_DEFECT, LABEL_OK  # noqa: E402

log = logging.getLogger("evaluate")
IMG_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
OK_NAMES = {"ok", "good", "intact", "normal", "clean", "lanh", "tot"}


@dataclass
class Sample:
    path: Path
    cls: str             # tên thư mục lớp
    y: int               # 0 = OK, 1 = DEFECT
    p_defect: float = 0.0
    pred: int = -1
    label: str = ""      # OK / DEFECT / UNCERTAIN theo classifier


@dataclass
class ClassMetrics:
    precision: float
    recall: float
    f1: float
    support: int


@dataclass
class EvalReport:
    split: str
    n: int
    threshold: float
    min_confidence: float
    accuracy: float
    per_class: dict[str, ClassMetrics]
    confusion: list[list[int]]    # [[TN, FP], [FN, TP]] với lớp dương = DEFECT
    uncertain_rate: float
    auc: float | None = None
    extra: dict[str, float] = field(default_factory=dict)


def list_split(data: Path, split: str) -> list[Sample]:
    d = data / split
    if not d.is_dir():
        return []
    out: list[Sample] = []
    for cls_dir in sorted(p for p in d.iterdir() if p.is_dir()):
        y = 0 if cls_dir.name.lower() in OK_NAMES else 1
        for p in sorted(cls_dir.iterdir()):
            if p.suffix.lower() in IMG_EXTS:
                out.append(Sample(path=p, cls=cls_dir.name, y=y))
    return out


def read_rgb(path: Path) -> np.ndarray:
    import cv2
    bgr = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if bgr is None:
        raise ValueError(f"Không đọc được {path}")
    return cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)


def run_model(clf: OnnxClassifier, samples: list[Sample]) -> list[float]:
    times: list[float] = []
    for i, s in enumerate(samples):
        r = clf.predict(read_rgb(s.path))
        s.p_defect = r.probs[LABEL_DEFECT]
        s.label = r.label
        times.append(r.infer_ms)
        if (i + 1) % 100 == 0:
            log.info("  %d/%d", i + 1, len(samples))
    return times


def metrics_at(samples: list[Sample], thr: float, min_conf: float, split: str) -> EvalReport:
    """Dự đoán theo đúng quy tắc P-05/AI-07: p_defect ≥ thr → DEFECT; p_ok < min_conf → UNCERTAIN (bị loại → tính là DEFECT)."""
    y = np.array([s.y for s in samples], int)
    p = np.array([s.p_defect for s in samples], float)
    is_defect = p >= thr
    is_uncertain = (~is_defect) & ((1.0 - p) < min_conf)
    pred = (is_defect | is_uncertain).astype(int)
    for s, pr in zip(samples, pred):
        s.pred = int(pr)
    tp = int(((pred == 1) & (y == 1)).sum()); tn = int(((pred == 0) & (y == 0)).sum())
    fp = int(((pred == 1) & (y == 0)).sum()); fn = int(((pred == 0) & (y == 1)).sum())

    def prf(tp_: int, fp_: int, fn_: int) -> ClassMetrics:
        pr_ = tp_ / (tp_ + fp_) if tp_ + fp_ else 0.0
        rc_ = tp_ / (tp_ + fn_) if tp_ + fn_ else 0.0
        f1_ = 2 * pr_ * rc_ / (pr_ + rc_) if pr_ + rc_ else 0.0
        return ClassMetrics(round(pr_, 4), round(rc_, 4), round(f1_, 4), tp_ + fn_)

    auc = None
    if len(set(y.tolist())) == 2:
        try:
            from sklearn.metrics import roc_auc_score
            auc = round(float(roc_auc_score(y, p)), 4)
        except ImportError:
            pass
    return EvalReport(split=split, n=len(samples), threshold=round(thr, 4), min_confidence=min_conf,
                      accuracy=round((tp + tn) / max(1, len(y)), 4),
                      per_class={LABEL_OK: prf(tn, fn, fp), LABEL_DEFECT: prf(tp, fp, fn)},
                      confusion=[[tn, fp], [fn, tp]], uncertain_rate=round(float(is_uncertain.mean()), 4), auc=auc)


def choose_threshold(val: list[Sample], target_recall: float, min_conf: float) -> tuple[float, list[dict[str, float]]]:
    """Quét ngưỡng trên val → ngưỡng lớn nhất đạt recall DEFECT ≥ target (= min_confidence nhỏ nhất).

    Lưu ý: trên Pi, ảnh có p_ok < ai.min_confidence cũng bị loại (UNCERTAIN) nên ngưỡng hiệu dụng là
    min(defect_threshold, 1 - min_confidence); hàm trả về ngưỡng nghiêm ngặt để ghi vào model_card."""
    cands = sorted(set(np.round(np.linspace(0.05, 0.95, 91), 4).tolist()) | {round(s.p_defect, 4) for s in val})
    sweep: list[dict[str, float]] = []
    best = None
    for thr in cands:
        if not 0.0 < thr < 1.0:
            continue
        # Quét với min_conf=0: recall "nghiêm ngặt" chỉ tính ảnh có p_defect >= thr (không nhờ UNCERTAIN gánh),
        # nên ngưỡng chọn ra mới có ý nghĩa độc lập với ai.min_confidence.
        rep = metrics_at(val, thr, 0.0, "val")
        rec = rep.per_class[LABEL_DEFECT].recall
        sweep.append({"threshold": thr, "recall_defect": rec, "precision_defect": rep.per_class[LABEL_DEFECT].precision,
                      "accuracy": rep.accuracy, "fp_rate_ok": round(1 - rep.per_class[LABEL_OK].recall, 4)})
        if rec >= target_recall:
            best = thr          # cands tăng dần → giá trị cuối cùng đạt là lớn nhất
    if best is None:
        best = min(c for c in cands if 0.0 < c < 1.0)
        log.warning("Không ngưỡng nào đạt recall DEFECT ≥ %.2f trên val — dùng %.3f (model cần train thêm)", target_recall, best)
    return float(best), sweep


def save_confusion_png(rep: EvalReport, path: Path) -> bool:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        log.warning("Thiếu matplotlib — bỏ qua %s", path.name)
        return False
    cm = np.array(rep.confusion)
    fig, ax = plt.subplots(figsize=(4.2, 3.8))
    ax.imshow(cm, cmap="Blues")
    for (i, j), v in np.ndenumerate(cm):
        ax.text(j, i, str(v), ha="center", va="center", fontsize=14, color="white" if v > cm.max() / 2 else "black")
    ax.set_xticks([0, 1], ["OK", "DEFECT"]); ax.set_yticks([0, 1], ["OK", "DEFECT"])
    ax.set_xlabel("Dự đoán"); ax.set_ylabel("Thật")
    ax.set_title(f"{rep.split}: acc {rep.accuracy:.3f}, recall lỗi {rep.per_class[LABEL_DEFECT].recall:.3f}\n"
                 f"ngưỡng {rep.threshold:.3f}", fontsize=9)
    fig.tight_layout(); fig.savefig(path, dpi=150); plt.close(fig)
    return True


def save_misclassified_grid(samples: list[Sample], path: Path, max_n: int = 24, tile: int = 160) -> int:
    from PIL import Image, ImageDraw
    wrong = [s for s in samples if s.pred != s.y][:max_n]
    if not wrong:
        return 0
    ncol = 6
    nrow = (len(wrong) + ncol - 1) // ncol
    grid = Image.new("RGB", (ncol * tile, nrow * (tile + 18)), "white")
    draw = ImageDraw.Draw(grid)
    for i, s in enumerate(wrong):
        im = Image.open(s.path).convert("RGB")
        im.thumbnail((tile, tile))
        x, y = (i % ncol) * tile, (i // ncol) * (tile + 18)
        grid.paste(im, (x + (tile - im.width) // 2, y))
        truth = "DEFECT" if s.y else "OK"
        draw.text((x + 2, y + tile + 2), f"thật {truth} p={s.p_defect:.2f} {s.label}", fill="red")
    grid.save(path)
    return len(wrong)


def to_jsonable(o):
    from dataclasses import asdict, is_dataclass
    if is_dataclass(o):
        return to_jsonable(asdict(o))
    if isinstance(o, dict):
        return {str(k): to_jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [to_jsonable(v) for v in o]
    if isinstance(o, (np.floating, np.integer)):
        return o.item()
    if isinstance(o, Path):
        return str(o)
    return o


def evaluate(model: Path, data: Path, out: Path, labels: Path | None, card: Path | None, target_recall: float,
             min_conf: float, update_card: bool, threads: int) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    cfg = AiCfg(backend="onnx", model_path=str(model), labels_path=str(labels) if labels else str(model.parent / "labels.json"),
                model_card_path=str(card) if card else "", min_confidence=min_conf, threads=threads)
    clf = OnnxClassifier(cfg)
    log.info("Model %s v%s, ngưỡng hiện tại %.3f", model.name, clf.model_version, clf.spec.defect_threshold)

    val, test = list_split(data, "val"), list_split(data, "test")
    if not val and not test:
        raise SystemExit(f"{data} không có val/ hoặc test/ (chạy prepare_dataset.py trước)")
    result: dict = {"model": str(model), "model_version": clf.model_version, "data": str(data),
                    "created": _dt.datetime.now().isoformat(timespec="seconds"), "target_recall": target_recall,
                    "min_confidence": min_conf, "threshold_before": clf.spec.defect_threshold}

    thr = clf.spec.defect_threshold
    if val:
        log.info("Chạy val: %d ảnh", len(val))
        run_model(clf, val)
        thr, sweep = choose_threshold(val, target_recall, min_conf)
        result["threshold_chosen"] = thr
        result["threshold_sweep_val"] = sweep
        result["val"] = metrics_at(val, thr, min_conf, "val")
        log.info("Ngưỡng chọn trên val: %.4f (recall DEFECT %.3f, acc %.3f)", thr,
                 result["val"].per_class[LABEL_DEFECT].recall, result["val"].accuracy)
    else:
        log.warning("Không có tập val — giữ ngưỡng %.3f từ model_card/config", thr)
        result["threshold_chosen"] = thr

    if test:
        log.info("Chạy test: %d ảnh", len(test))
        times = run_model(clf, test)
        rep = metrics_at(test, thr, min_conf, "test")
        rep.extra = {"infer_ms_mean": round(float(np.mean(times)), 2), "infer_ms_p95": round(float(np.percentile(times, 95)), 2)}
        result["test"] = rep
        if save_confusion_png(rep, out / "confusion_matrix.png"):
            log.info("Đã ghi confusion_matrix.png")
        n_wrong = save_misclassified_grid(test, out / "misclassified_grid.png")
        log.info("Ảnh sai trên test: %d (lưới ≤ 24 ảnh: misclassified_grid.png)", n_wrong)
        print_report(rep)

    (out / "metrics.json").write_text(json.dumps(to_jsonable(result), ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("Đã ghi %s", out / "metrics.json")

    if update_card:
        card_path = card or (model.parent / "model_card.json")
        card_obj = json.loads(card_path.read_text(encoding="utf-8")) if card_path.exists() else {
            "model_name": model.stem, "version": clf.model_version, "imgsz": clf.spec.input_size,
            "normalization": "0..1 RGB", "classes": clf.spec.classes,
            "input": {"name": clf.input_name, "size": clf.spec.input_size, "layout": clf.spec.layout, "color": clf.spec.color,
                      "range": list(clf.spec.value_range)}}
        card_obj["defect_threshold"] = thr
        card_obj["min_confidence"] = min_conf
        card_obj["target_recall"] = target_recall
        card_obj["threshold_chosen_on"] = f"val ({len(val)} ảnh)" if val else "không có val — giữ ngưỡng cũ"
        card_obj["evaluated"] = result["created"]
        card_obj.setdefault("metrics", {})
        if not isinstance(card_obj["metrics"], dict):
            card_obj["metrics"] = {}
        card_obj["metrics"]["evaluate"] = {k: to_jsonable(result[k]) for k in ("val", "test") if k in result}
        card_path.write_text(json.dumps(to_jsonable(card_obj), ensure_ascii=False, indent=2), encoding="utf-8")
        log.info("Đã cập nhật %s (defect_threshold=%.4f)", card_path, thr)
    return result


def print_report(rep: EvalReport) -> None:
    print(f"\n=== {rep.split}: {rep.n} ảnh, ngưỡng DEFECT {rep.threshold:.3f}, min_confidence {rep.min_confidence} ===")
    print(f"Accuracy {rep.accuracy:.4f} | AUC {rep.auc if rep.auc is not None else '-'} | UNCERTAIN {rep.uncertain_rate:.1%}")
    print(f"{'Lớp':8s} {'precision':>9s} {'recall':>7s} {'F1':>7s} {'n':>5s}")
    for name, m in rep.per_class.items():
        print(f"{name:8s} {m.precision:9.4f} {m.recall:7.4f} {m.f1:7.4f} {m.support:5d}")
    (tn, fp), (fn, tp) = rep.confusion
    print(f"Confusion [[TN {tn}, FP {fp}], [FN {fn}, TP {tp}]]  (dương = DEFECT)")
    if rep.extra:
        print(f"infer_ms: mean {rep.extra.get('infer_ms_mean')} p95 {rep.extra.get('infer_ms_p95')}")
    ok = rep.accuracy >= 0.90 and rep.per_class[LABEL_DEFECT].recall >= 0.95
    print(f"Tiêu chí 5.3 (acc ≥ 0.90, recall lỗi ≥ 0.95): {'ĐẠT' if ok else 'CHƯA ĐẠT'}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Đánh giá model ONNX + chọn ngưỡng an toàn (AI-05/AI-07)")
    ap.add_argument("--model", required=True)
    ap.add_argument("--data", default="ai/data", help="thư mục có val/ và test/")
    ap.add_argument("--out", help="thư mục ghi kết quả (mặc định cạnh model)")
    ap.add_argument("--labels", help="labels.json (mặc định cạnh model)")
    ap.add_argument("--model-card", dest="card", help="model_card.json (mặc định cạnh model)")
    ap.add_argument("--target-recall", type=float, default=0.95)
    ap.add_argument("--min-confidence", type=float, default=0.6)
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--update-card", action="store_true", help="ghi ngưỡng + metrics vào model_card.json")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    model = Path(args.model)
    evaluate(model, Path(args.data), Path(args.out) if args.out else model.parent,
             Path(args.labels) if args.labels else None, Path(args.card) if args.card else None,
             args.target_recall, args.min_confidence, args.update_card, args.threads)
    return 0


if __name__ == "__main__":
    sys.exit(main())
