"""AI-05 / P-16 — Benchmark model phân loại bao bì trên Pi 4 (hoặc laptop).

Đo N lần suy luận (mặc định 100, warm-up 10) qua đúng đường OnnxClassifier/TfliteClassifier
(crop ROI + resize + chuẩn hóa + session.run) nên con số phản ánh thời gian thật trong pipeline P-05.
In mean / p50 / p95 / max (ms) + RAM RSS trước/sau, ghi benchmark.json để đưa vào slide.

Ví dụ:
  cd pi
  python tools/benchmark_model.py --model ../ai/samples/dummy_packaging_cls.onnx --runs 100 --warmup 10
  python tools/benchmark_model.py --config /etc/sorter/config.yaml --image /var/lib/sorter/defects/abc.jpg
  python tools/benchmark_model.py --model /opt/sorter/models/packaging_cls.onnx --backend onnx --threads 4
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import platform
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # pi/

from sorter.classifier_onnx import OnnxClassifier, TfliteClassifier  # noqa: E402
from sorter.config import AiCfg, load_config  # noqa: E402
from sorter.interfaces import Classifier  # noqa: E402

log = logging.getLogger("benchmark_model")


@dataclass
class BenchResult:
    model: str
    backend: str
    model_version: str
    input_size: int
    threads: int
    runs: int
    warmup: int
    image: str
    mean_ms: float
    p50_ms: float
    p95_ms: float
    max_ms: float
    min_ms: float
    std_ms: float
    infer_only_mean_ms: float     # chỉ session.run (ClassResult.infer_ms), không gồm tiền xử lý
    load_ms: float
    rss_before_mb: float
    rss_after_mb: float
    rss_peak_mb: float
    host: dict[str, str] = field(default_factory=dict)
    sample_result: dict[str, object] = field(default_factory=dict)
    timestamp: str = ""


def rss_mb() -> float:
    """RAM RSS của tiến trình (MB): psutil → resource (Linux) → 0."""
    try:
        import psutil
        return psutil.Process(os.getpid()).memory_info().rss / 2**20
    except ImportError:
        pass
    try:
        import resource
        peak_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return peak_kb / 1024.0 if sys.platform != "darwin" else peak_kb / 2**20
    except (ImportError, AttributeError):
        return 0.0


def peak_rss_mb() -> float:
    try:
        import resource
        peak_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return peak_kb / 1024.0 if sys.platform != "darwin" else peak_kb / 2**20
    except (ImportError, AttributeError):
        try:
            import psutil
            mi = psutil.Process(os.getpid()).memory_info()
            return getattr(mi, "peak_wset", mi.rss) / 2**20   # Windows có peak_wset
        except ImportError:
            return 0.0


def load_image(path: str, size: int, seed: int = 0) -> np.ndarray:
    """Ảnh RGB uint8: file thật (mọi định dạng cv2 đọc được) hoặc 'random' (ảnh 1280x960 ngẫu nhiên)."""
    if path == "random":
        rng = np.random.default_rng(seed)
        return rng.integers(0, 256, (960, 1280, 3), dtype=np.uint8)
    import cv2
    bgr = cv2.imread(path, cv2.IMREAD_COLOR)
    if bgr is None:
        raise SystemExit(f"Không đọc được ảnh: {path}")
    return cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)


def host_info() -> dict[str, str]:
    info = {"platform": platform.platform(), "python": platform.python_version(),
            "machine": platform.machine(), "cpu_count": str(os.cpu_count() or 0)}
    model_file = Path("/proc/device-tree/model")
    if model_file.exists():
        try:
            info["board"] = model_file.read_text(errors="ignore").strip("\x00\n ")
        except OSError:
            pass
    try:
        import onnxruntime as ort
        info["onnxruntime"] = ort.__version__
    except ImportError:
        pass
    return info


def build_cfg(args: argparse.Namespace) -> AiCfg:
    if args.config:
        cfg = load_config(args.config, validate_files=False).ai
        if args.model:
            cfg.model_path = args.model
    else:
        if not args.model:
            raise SystemExit("Cần --config hoặc --model")
        model = Path(args.model).resolve()
        labels = args.labels or str(model.parent / "labels.json")
        cfg = AiCfg(backend=args.backend or ("tflite" if model.suffix == ".tflite" else "onnx"),
                    model_path=str(model), labels_path=labels if Path(labels).exists() else "",
                    model_card_path=args.model_card or "", input_size=args.input_size, min_confidence=0.6)
    if args.backend:
        cfg.backend = args.backend
    if args.model_card:
        cfg.model_card_path = args.model_card
    if args.threads:
        cfg.threads = args.threads
    return cfg


def make_classifier(cfg: AiCfg) -> Classifier:
    if cfg.backend == "tflite":
        return TfliteClassifier(cfg)
    if cfg.backend == "onnx":
        return OnnxClassifier(cfg)
    raise SystemExit(f"ai.backend={cfg.backend}: benchmark chỉ hỗ trợ onnx | tflite (dummy không có model)")


def run_benchmark(cfg: AiCfg, image_path: str, runs: int, warmup: int,
                  roi: tuple[int, int, int, int] | None) -> BenchResult:
    rss0 = rss_mb()
    t0 = time.perf_counter()
    clf = make_classifier(cfg)
    load_ms = (time.perf_counter() - t0) * 1000
    size = getattr(clf, "spec").input_size
    img = load_image(image_path, size)
    log.info("Model %s (v%s) nạp trong %.0f ms; ảnh %s %s; warm-up %d, đo %d lần, threads=%d",
             Path(cfg.model_path).name, clf.model_version, load_ms, image_path, img.shape[:2], warmup, runs, cfg.threads)
    for _ in range(warmup):
        clf.predict(img, roi)
    total: list[float] = []
    infer_only: list[float] = []
    last = None
    for _ in range(runs):
        t = time.perf_counter()
        last = clf.predict(img, roi)
        total.append((time.perf_counter() - t) * 1000)
        infer_only.append(last.infer_ms)
    rss1 = rss_mb()
    arr = np.asarray(total)
    res = BenchResult(
        model=str(cfg.model_path), backend=cfg.backend, model_version=clf.model_version, input_size=size,
        threads=cfg.threads, runs=runs, warmup=warmup, image=image_path,
        mean_ms=round(float(arr.mean()), 2), p50_ms=round(float(np.percentile(arr, 50)), 2),
        p95_ms=round(float(np.percentile(arr, 95)), 2), max_ms=round(float(arr.max()), 2),
        min_ms=round(float(arr.min()), 2), std_ms=round(float(arr.std()), 2),
        infer_only_mean_ms=round(float(np.mean(infer_only)), 2), load_ms=round(load_ms, 1),
        rss_before_mb=round(rss0, 1), rss_after_mb=round(rss1, 1), rss_peak_mb=round(peak_rss_mb(), 1),
        host=host_info(), sample_result=asdict(last) if last else {},
        timestamp=time.strftime("%Y-%m-%dT%H:%M:%S"),
    )
    clf.close()
    return res


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Benchmark model phân loại bao bì (AI-05)")
    ap.add_argument("--config", help="config.yaml của sorter (lấy mục ai:)")
    ap.add_argument("--model", help="đường dẫn .onnx/.tflite (thay cho --config)")
    ap.add_argument("--model-card", dest="model_card", help="model_card.json (mặc định cạnh model)")
    ap.add_argument("--labels", help="labels.json (mặc định cạnh model)")
    ap.add_argument("--backend", choices=["onnx", "tflite"], help="ghi đè ai.backend")
    ap.add_argument("--threads", type=int, default=0, help="intra_op threads (mặc định theo config / 4)")
    ap.add_argument("--input-size", type=int, default=224, help="dùng khi không có model_card")
    ap.add_argument("--runs", type=int, default=100)
    ap.add_argument("--warmup", type=int, default=10)
    ap.add_argument("--image", default="random", help="đường dẫn ảnh thật hoặc 'random'")
    ap.add_argument("--roi", help="x,y,w,h (mặc định: cả ảnh)")
    ap.add_argument("--out", default="benchmark.json")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    cfg = build_cfg(args)
    roi = tuple(int(v) for v in args.roi.split(",")) if args.roi else None
    if roi is not None and len(roi) != 4:
        raise SystemExit("--roi phải là x,y,w,h")
    res = run_benchmark(cfg, args.image, args.runs, args.warmup, roi)  # type: ignore[arg-type]

    print(f"\nModel      : {res.model} (v{res.model_version}, backend {res.backend}, {res.input_size}px, "
          f"{res.threads} threads)")
    print(f"Host       : {res.host.get('board') or res.host.get('platform')} | ORT {res.host.get('onnxruntime', '-')}")
    print(f"Nạp model  : {res.load_ms:.0f} ms")
    print(f"Suy luận ({res.runs} lần, warm-up {res.warmup}, gồm tiền xử lý):")
    print(f"  mean {res.mean_ms:.2f} ms | p50 {res.p50_ms:.2f} | p95 {res.p95_ms:.2f} | max {res.max_ms:.2f} "
          f"| min {res.min_ms:.2f} | std {res.std_ms:.2f}")
    print(f"  chỉ session.run: mean {res.infer_only_mean_ms:.2f} ms")
    print(f"RAM RSS    : trước {res.rss_before_mb:.1f} MB → sau {res.rss_after_mb:.1f} MB (đỉnh {res.rss_peak_mb:.1f} MB)")
    target = 150.0
    print(f"Mục tiêu 5.3: ≤ {target:.0f} ms trung bình → {'ĐẠT' if res.mean_ms <= target else 'CHƯA ĐẠT'}")
    Path(args.out).write_text(json.dumps(asdict(res), ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Đã ghi {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
