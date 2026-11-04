"""P9 / A-02 — Huấn luyện mô hình phát hiện bất thường (Isolation Forest).

Lấy các cửa sổ "bình thường" trong khoảng thời gian do người dùng chỉ định từ
bảng anomaly_windows, huấn luyện StandardScaler + IsolationForest, chọn ngưỡng =
phân vị 99 của điểm trên dữ liệu train, rồi lưu joblib + metadata.json.

CLI:
  python -m sorter.anomaly.train --db PATH --from "YYYY-mm-dd HH:MM" --to ... \
      --out /opt/sorter/models/anomaly_if.joblib [--min-windows 180]
"""
from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from .. import interfaces
from .features import FeatureCollector
from .store import AnomalyStore

log = logging.getLogger(__name__)

FEATURE_NAMES: tuple[str, ...] = interfaces.FEATURE_NAMES

# Tham số mô hình cố định theo đặc tả A-02
N_ESTIMATORS = 200
CONTAMINATION = 0.02
RANDOM_STATE = 42
THRESHOLD_PERCENTILE = 99.0


def train_from_matrix(X: np.ndarray) -> tuple[Any, float]:
    """Huấn luyện từ ma trận đặc trưng X (n_samples x 10).

    Trả về (pipeline đã fit, threshold). Dùng cho test không cần DB.
    """
    # Import lười sklearn để store/features không kéo theo khi không cần
    from sklearn.ensemble import IsolationForest
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler

    X = np.asarray(X, dtype=float)
    if X.ndim != 2 or X.shape[1] != len(FEATURE_NAMES):
        raise ValueError(
            f"X phải có shape (n, {len(FEATURE_NAMES)}), nhận {X.shape}"
        )
    pipeline = Pipeline(
        steps=[
            ("scaler", StandardScaler()),
            (
                "iforest",
                IsolationForest(
                    n_estimators=N_ESTIMATORS,
                    contamination=CONTAMINATION,
                    random_state=RANDOM_STATE,
                ),
            ),
        ]
    )
    pipeline.fit(X)
    # Điểm bất thường: càng lớn càng bất thường
    scores = -pipeline.score_samples(X)
    threshold = float(np.percentile(scores, THRESHOLD_PERCENTILE))
    return pipeline, threshold


def save_model(
    out_path: str,
    pipeline: Any,
    threshold: float,
    X: np.ndarray,
    window_from: str | None = None,
    window_to: str | None = None,
) -> dict[str, Any]:
    """Lưu joblib (pipeline + ngưỡng + mean/std) và metadata.json cạnh file."""
    import joblib

    X = np.asarray(X, dtype=float)
    train_mean = X.mean(axis=0)
    train_std = X.std(axis=0)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    bundle = {
        "pipeline": pipeline,
        "threshold": float(threshold),
        "features": list(FEATURE_NAMES),
        "train_mean": train_mean,
        "train_std": train_std,
    }
    joblib.dump(bundle, out)
    meta = {
        "features": list(FEATURE_NAMES),
        "threshold": float(threshold),
        "n_samples": int(X.shape[0]),
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "from": window_from,
        "to": window_to,
        "n_estimators": N_ESTIMATORS,
        "contamination": CONTAMINATION,
        "random_state": RANDOM_STATE,
        "threshold_percentile": THRESHOLD_PERCENTILE,
    }
    meta_path = out.with_name("metadata.json")
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return meta


def _parse_time_to_ms(s: str) -> int:
    """Chuyển chuỗi 'YYYY-mm-dd HH:MM' (giờ địa phương) sang epoch ms."""
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(s, fmt)
            return int(dt.timestamp() * 1000)
        except ValueError:
            continue
    raise ValueError(f"Không hiểu thời gian: {s!r} (định dạng 'YYYY-mm-dd HH:MM')")


def load_matrix_from_db(db_path: str, from_ms: int, to_ms: int) -> np.ndarray:
    """Đọc các cửa sổ bình thường và dựng ma trận đặc trưng."""
    store = AnomalyStore(db_path)
    try:
        rows = store.fetch(from_ms, to_ms)
    finally:
        store.close()
    if not rows:
        return np.empty((0, len(FEATURE_NAMES)), dtype=float)
    mat = np.array(
        [[float(r[name]) for name in FEATURE_NAMES] for r in rows], dtype=float
    )
    return mat


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="Huấn luyện Isolation Forest phát hiện bất thường (A-02)")
    ap.add_argument("--db", required=True, help="Đường dẫn sorter.db")
    ap.add_argument("--from", dest="t_from", required=True, help="Bắt đầu khoảng bình thường 'YYYY-mm-dd HH:MM'")
    ap.add_argument("--to", dest="t_to", required=True, help="Kết thúc khoảng bình thường")
    ap.add_argument("--out", required=True, help="File joblib đầu ra")
    ap.add_argument("--min-windows", type=int, default=180, help="Số cửa sổ tối thiểu (mặc định 180 ~ 30 phút)")
    args = ap.parse_args(argv)

    from_ms = _parse_time_to_ms(args.t_from)
    to_ms = _parse_time_to_ms(args.t_to)
    X = load_matrix_from_db(args.db, from_ms, to_ms)
    n = X.shape[0]
    log.info("Đọc %d cửa sổ trong khoảng %s .. %s", n, args.t_from, args.t_to)
    if n == 0:
        log.error("Không có cửa sổ nào trong khoảng đã cho — không thể huấn luyện.")
        return 1
    if n < args.min_windows:
        log.warning(
            "Chỉ có %d cửa sổ (< min-windows=%d). Mô hình có thể kém tin cậy; thu thêm dữ liệu bình thường.",
            n,
            args.min_windows,
        )

    pipeline, threshold = train_from_matrix(X)
    meta = save_model(args.out, pipeline, threshold, X, args.t_from, args.t_to)
    log.info("Đã lưu model: %s (threshold=%.4f, n_samples=%d)", args.out, threshold, meta["n_samples"])
    log.info("Metadata: %s", Path(args.out).with_name("metadata.json"))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
