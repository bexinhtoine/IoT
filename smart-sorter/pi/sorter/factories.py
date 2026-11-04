"""Nhà máy tạo các thành phần theo cấu hình (P-01, P-14). Import lười các module của P4/P5/P9
để P3 chạy được khi những module đó chưa có.
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from .camera import Camera, FolderCamera, Picamera2Camera
from .classifier import DummyClassifier
from .config import ConfigError, SorterConfig
from .interfaces import Classifier, LogTelemetrySink, SecurityEvent, TelemetrySink
from .uart_link import UartLink

log = logging.getLogger(__name__)


def make_camera(cfg: SorterConfig) -> Camera:
    if cfg.camera.source == "folder":
        return FolderCamera(cfg.camera.folder)
    return Picamera2Camera(cfg.camera)


def make_classifier(cfg: SorterConfig, label_hint: Callable[[], str | None] | None = None) -> Classifier:
    backend = cfg.ai.backend
    if backend == "dummy":
        return DummyClassifier(cfg.ai, label_hint)
    try:
        from .classifier_onnx import OnnxClassifier, TfliteClassifier  # type: ignore[import-not-found]
    except ImportError as e:
        raise ConfigError(f"ai.backend={backend} cần module sorter.classifier_onnx (P4) và onnxruntime/tflite: {e}") from e
    if backend == "onnx":
        return OnnxClassifier(cfg.ai)
    if backend == "tflite":
        return TfliteClassifier(cfg.ai)
    raise ConfigError(f"ai.backend không hợp lệ: {backend}")


def make_telemetry(cfg: SorterConfig) -> TelemetrySink:
    if not cfg.thingsboard.enabled:
        return LogTelemetrySink()
    try:
        from .tb_client import ThingsBoardClient  # type: ignore[import-not-found]
    except ImportError as e:
        raise ConfigError(f"thingsboard.enabled=true cần module sorter.tb_client (P5) và paho-mqtt: {e}") from e
    return ThingsBoardClient(cfg.thingsboard, device_name=cfg.device.name)


def make_uart(cfg: SorterConfig, time_scale: float = 0.1) -> tuple[UartLink, Any]:
    """Trả (UartLink, FakeEsp32 | None). FakeEsp32 đã start() khi port == 'sim'."""
    if cfg.uart.port == "sim":
        from .fake_esp32 import FakeEsp32

        # Ở sim: t_clear rút ngắn theo time_scale (0.1 -> 250-500 ms) nhưng giữ t_decision_timeout = 3 s
        # (30000 ms * 0.1) đúng như ESP32 thật, để laptop chậm vẫn kịp quét 3 khung không có QR
        fake = FakeEsp32(params={10: int(3000 / time_scale)}, time_scale=time_scale)
        fake.start()
        return UartLink(cfg.uart, transport=fake.pi_transport()), fake
    return UartLink(cfg.uart), None


def make_rpc_handler(
    cfg: SorterConfig,
    uart: UartLink,
    telemetry: TelemetrySink,
    *,
    reset_counters: Callable[[], dict[str, int]],
    reset_used_codes: Callable[[], int],
    get_status: Callable[[], dict[str, Any]],
    on_security_event: Callable[[SecurityEvent], None],
    audit: Callable[..., Any],
) -> Any:
    """Tạo RpcHandler của P5 và đăng ký vào telemetry; None nếu P5 chưa có."""
    try:
        from .rpc_handler import RpcCallbacks, RpcHandler  # type: ignore[import-not-found]
    except ImportError as e:
        log.warning("Không có sorter.rpc_handler (P5) — bỏ qua RPC: %s", e)
        return None
    handler = RpcHandler(
        uart=uart,
        cfg=cfg.rpc,
        demo_mode=cfg.device.demo_mode,
        telemetry=telemetry,
        callbacks=RpcCallbacks(
            reset_counters=reset_counters,
            reset_used_codes=reset_used_codes,
            get_status=get_status,
            on_security_event=on_security_event,
            audit=audit,
        ),
    )
    telemetry.set_rpc_handler(handler.handle)
    log.info("RPC handler đã đăng ký")
    return handler


def make_anomaly(cfg: SorterConfig, telemetry: TelemetrySink, on_security_event: Callable[[SecurityEvent], None]) -> Any:
    """AnomalyDetector của P9 (có start()/stop()); None nếu tắt hoặc chưa có module."""
    if not cfg.anomaly.enabled:
        return None
    try:
        from .anomaly.detector import AnomalyDetector  # type: ignore[import-not-found]
    except ImportError as e:
        log.warning("Không có sorter.anomaly (P9) — bỏ qua phát hiện bất thường: %s", e)
        return None
    return AnomalyDetector(cfg.anomaly, db_path=cfg.storage.db_path, telemetry=telemetry, on_security_event=on_security_event)
