"""MODULE P9 — Phát hiện truy cập bất thường bằng Isolation Forest (A-01 → A-06).

Các thành phần:
- features.py  (A-01): gom đặc trưng theo cửa sổ 10 s.
- store.py            : lưu/đọc cửa sổ trong bảng anomaly_windows (dùng chung sorter.db).
- train.py    (A-02): huấn luyện StandardScaler + IsolationForest, lưu joblib + metadata.
- detector.py (A-03): thread chấm điểm thời gian thực, gửi telemetry + sự kiện ANOMALY.
- rules.py    (A-04): luật cứng chạy song song (RULE_*).
- evaluate.py (A-06): đánh giá tỉ lệ phát hiện + báo nhầm.

Module chỉ phụ thuộc vào sorter.interfaces và sorter.config, không phụ thuộc chéo.
"""
from __future__ import annotations

__all__ = [
    "AnomalyStore",
    "FeatureCollector",
    "AnomalyDetector",
    "RuleEngine",
    "train_from_matrix",
]

# Lưu ý: import lười ở cấp hàm để tránh kéo theo sklearn khi chỉ cần store/features.


def __getattr__(name: str):  # pragma: no cover - cầu nối import lười
    if name == "AnomalyStore":
        from .store import AnomalyStore

        return AnomalyStore
    if name == "FeatureCollector":
        from .features import FeatureCollector

        return FeatureCollector
    if name == "AnomalyDetector":
        from .detector import AnomalyDetector

        return AnomalyDetector
    if name == "RuleEngine":
        from .rules import RuleEngine

        return RuleEngine
    if name == "train_from_matrix":
        from .train import train_from_matrix

        return train_from_matrix
    raise AttributeError(name)
