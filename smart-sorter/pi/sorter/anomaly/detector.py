"""P9 / A-03 — Chấm điểm bất thường thời gian thực.

Thread daemon chạy mỗi cfg.window_s giây:
  1. Gom đặc trưng (FeatureCollector) và lưu cửa sổ vào anomaly_windows.
  2. Chạy luật cứng (RuleEngine) song song.
  3. Nếu đã nạp model: tính điểm = -score_samples, gửi telemetry
     anomaly_score / anomaly_flag / anomaly_top_features; vượt ngưỡng thì bật flag
     và sinh SecurityEvent ANOMALY (CRITICAL) với cooldown cfg.cooldown_s.

Nếu chưa có model: chỉ ghi cửa sổ (để sau đem đi train) + chạy luật, log một lần.
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any, Callable

import numpy as np

from .. import interfaces
from ..config import AnomalyCfg
from ..interfaces import SecurityEvent, SecurityLevel, TelemetrySink
from .features import FeatureCollector
from .rules import RuleEngine
from .store import AnomalyStore

log = logging.getLogger(__name__)

FEATURE_NAMES: tuple[str, ...] = interfaces.FEATURE_NAMES


class AnomalyDetector:
    """Bộ phát hiện bất thường (A-03)."""

    def __init__(
        self,
        cfg: AnomalyCfg,
        db_path: str,
        telemetry: TelemetrySink,
        on_security_event: Callable[[SecurityEvent], None],
        metrics: interfaces.MetricCounters = interfaces.metrics,
        rules: RuleEngine | None = None,
        ssh_reader: Callable[[int], int] | None = None,
    ) -> None:
        self.cfg = cfg
        self.db_path = db_path
        self.telemetry = telemetry
        self.on_security_event = on_security_event
        self.metrics = metrics
        self.rules = rules if rules is not None else RuleEngine(on_security_event)
        self.collector = FeatureCollector(
            metrics=metrics, ssh_reader=ssh_reader, window_s=cfg.window_s
        )
        self.store = AnomalyStore(db_path)

        # Trạng thái model
        self._pipeline: Any = None
        self._threshold: float = float("inf")
        self._train_mean: np.ndarray | None = None
        self._train_std: np.ndarray | None = None

        # Thread
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

        # Cooldown cho sự kiện ANOMALY
        self._last_anomaly_s: float | None = None
        self._warned_no_model = False

        self.reload_model()

    # ------------------------------------------------------------------
    # Quản lý model
    # ------------------------------------------------------------------
    def reload_model(self) -> bool:
        """Nạp lại model từ cfg.model_path nếu file tồn tại. Trả True nếu nạp được."""
        path = Path(self.cfg.model_path)
        if not path.exists():
            self._pipeline = None
            return False
        try:
            import joblib

            bundle = joblib.load(path)
            self._pipeline = bundle["pipeline"]
            self._threshold = float(bundle["threshold"])
            self._train_mean = np.asarray(bundle.get("train_mean"), dtype=float)
            self._train_std = np.asarray(bundle.get("train_std"), dtype=float)
            log.info("Đã nạp model phát hiện bất thường: %s (threshold=%.4f)", path, self._threshold)
            self._warned_no_model = False
            return True
        except Exception as e:  # noqa: BLE001
            log.error("Không nạp được model %s: %s", path, e)
            self._pipeline = None
            return False

    @property
    def has_model(self) -> bool:
        return self._pipeline is not None

    # ------------------------------------------------------------------
    # Tính điểm
    # ------------------------------------------------------------------
    def _top_features(self, x: np.ndarray, k: int = 3) -> str:
        """Chuỗi 'feat=z;feat=z;feat=z' cho k đặc trưng có |z-score| lớn nhất."""
        if self._train_mean is None or self._train_std is None:
            return ""
        std = np.where(self._train_std > 1e-9, self._train_std, 1e-9)
        z = (x - self._train_mean) / std
        order = np.argsort(-np.abs(z))[:k]
        parts = [f"{FEATURE_NAMES[i]}={z[i]:.2f}" for i in order]
        return ";".join(parts)

    def score_once(self, features: dict[str, int]) -> tuple[float, bool, str]:
        """Chấm điểm một cửa sổ đặc trưng. Trả (score, flag, top_features).

        Dùng cho test. Yêu cầu model đã nạp, nếu chưa thì RuntimeError.
        """
        if self._pipeline is None:
            raise RuntimeError("Chưa nạp model — không thể chấm điểm")
        x = FeatureCollector.to_vector(features)
        score = float(-self._pipeline.score_samples(x.reshape(1, -1))[0])
        flag = score > self._threshold
        top = self._top_features(x)
        return score, flag, top

    # ------------------------------------------------------------------
    # Vòng chạy
    # ------------------------------------------------------------------
    def _process_window(self, now_ms: int) -> None:
        now_s = now_ms / 1000.0
        features = self.collector.collect(now_ms)

        score: float | None = None
        flag: bool | None = None
        top: str | None = None

        if self._pipeline is not None:
            x = FeatureCollector.to_vector(features)
            score = float(-self._pipeline.score_samples(x.reshape(1, -1))[0])
            flag = score > self._threshold
            top = self._top_features(x)

            # Gửi telemetry (đặc tả 4.3): anomaly_score float, anomaly_flag bool, anomaly_top_features str
            self.telemetry.send_telemetry(
                {
                    "anomaly_score": round(score, 4),
                    "anomaly_flag": bool(flag),
                    "anomaly_top_features": top,
                },
                ts_ms=now_ms,
            )

            if flag:
                self._maybe_emit_anomaly(score, top, now_s)
        else:
            if not self._warned_no_model:
                log.info(
                    "Chưa có model phát hiện bất thường, đang thu dữ liệu (ghi cửa sổ vào DB). "
                    "Chạy sorter.anomaly.train khi đủ >= 30 phút dữ liệu bình thường."
                )
                self._warned_no_model = True

        # Lưu cửa sổ (kể cả khi chưa có model — để sau đem train)
        try:
            self.store.insert_window(now_ms, features, score, flag, top)
        except Exception as e:  # noqa: BLE001
            log.error("Lỗi lưu cửa sổ anomaly: %s", e)

        # Luật cứng chạy song song mỗi cửa sổ (luôn chạy, không cần model)
        try:
            self.rules.update(features, now_s)
        except Exception as e:  # noqa: BLE001
            log.error("Lỗi chạy luật cứng: %s", e)

    def _maybe_emit_anomaly(self, score: float, top: str, now_s: float) -> None:
        """Sinh SecurityEvent ANOMALY với cooldown; flag vẫn luôn gửi True ở telemetry."""
        if (
            self._last_anomaly_s is not None
            and (now_s - self._last_anomaly_s) < self.cfg.cooldown_s
        ):
            return
        self._last_anomaly_s = now_s
        detail = f"ANOMALY score={score:.3f} > threshold={self._threshold:.3f}; top: {top}"
        ev = SecurityEvent(
            event_type=interfaces.SEC_ANOMALY,
            level=SecurityLevel.CRITICAL,
            detail=detail,
            ts_ms=int(now_s * 1000),
            extra={"score": score, "top_features": top},
        )
        try:
            self.on_security_event(ev)
        except Exception as e:  # noqa: BLE001
            log.error("Lỗi callback sự kiện ANOMALY: %s", e)

    def _run(self) -> None:
        log.info("AnomalyDetector bắt đầu, cửa sổ %d s", self.cfg.window_s)
        while not self._stop_event.is_set():
            # Chờ đúng window_s, cho phép dừng sớm
            if self._stop_event.wait(self.cfg.window_s):
                break
            try:
                self._process_window(int(time.time() * 1000))
            except Exception as e:  # noqa: BLE001 - thread không được chết
                log.exception("Lỗi trong vòng xử lý anomaly: %s", e)
        log.info("AnomalyDetector đã dừng")

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="anomaly-detector", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None
        try:
            self.store.close()
        except Exception:  # noqa: BLE001 - đóng DB best-effort
            pass
