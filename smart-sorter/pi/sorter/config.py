"""P-01 — Đọc và kiểm tra cấu hình /etc/sorter/config.yaml (đặc tả 4.6).

- Mọi khóa có giá trị mặc định hợp lý; file YAML chỉ cần ghi đè phần khác.
- Khóa bí mật (HMAC) KHÔNG nằm trong YAML, chỉ ghi đường dẫn file quyền 600.
- Sai cấu hình -> ConfigError với thông điệp rõ ràng, chương trình dừng ngay khi khởi động.
"""
from __future__ import annotations

import os
import stat
from dataclasses import MISSING, dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any

import yaml


class ConfigError(Exception):
    """Cấu hình sai hoặc thiếu."""


@dataclass
class DeviceCfg:
    name: str = "sorter-pi-01"
    demo_mode: bool = True
    sw_version: str = "1.0.0"
    qr_format_version: str = "1"


@dataclass
class CameraCfg:
    source: str = "picamera2"            # picamera2 | folder  (folder = chế độ sim)
    resolution: list[int] = field(default_factory=lambda: [1280, 960])
    settle_ms: int = 150
    max_qr_frames: int = 3
    roi: list[int] = field(default_factory=lambda: [320, 160, 640, 640])  # x, y, w, h
    lock_after_s: float = 2.0            # khóa AE/AWB sau bao nhiêu giây
    folder: str = "tests/fixtures/images"  # chỉ dùng khi source=folder
    hflip: bool = False
    vflip: bool = False


@dataclass
class QrCfg:
    hmac_key_file: str = "/etc/sorter/secrets/qr_hmac.key"
    max_age_days: int = 30
    future_skew_s: int = 300


@dataclass
class DetectionCfg:
    backend: str = "none"  # none (ROI cố định) | yolo_world_onnx
    model_path: str = ""
    confidence: float = 0.35
    iou_threshold: float = 0.5
    input_size: int = 640
    padding: float = 0.1
    min_area_ratio: float = 0.01


@dataclass
class AiCfg:
    backend: str = "dummy"               # dummy | onnx | tflite
    model_path: str = "/opt/sorter/models/packaging_cls.onnx"
    labels_path: str = "/opt/sorter/models/labels.json"
    model_card_path: str = ""            # rỗng = <thư mục model>/model_card.json nếu có
    input_size: int = 224
    min_confidence: float = 0.6
    threads: int = 4
    detection: DetectionCfg = field(default_factory=DetectionCfg)


@dataclass
class UartCfg:
    port: str = "/dev/serial0"           # chế độ sim: "sim" -> FakeEsp32
    baud: int = 115200
    ack_timeout_ms: int = 200
    retries: int = 3
    ping_interval_ms: int = 500


@dataclass
class ThingsboardCfg:
    enabled: bool = False
    host: str = "tb.local"
    port: int = 8883
    ca_cert: str = "/etc/sorter/certs/ca.pem"
    client_cert: str = "/etc/sorter/certs/device.pem"
    client_key: str = "/etc/sorter/certs/device.key"
    health_interval_s: int = 10
    image_max_kb: int = 40
    queue_max: int = 1000


@dataclass
class RpcRateLimit:
    count: int = 5
    window_s: int = 10


@dataclass
class RpcCfg:
    rate_limit: RpcRateLimit = field(default_factory=RpcRateLimit)
    esp_timeout_s: float = 2.0


@dataclass
class StorageCfg:
    backend: str = "sqlite"
    env_file: str = ".env"
    database_url: str = field(default="", repr=False)
    db_path: str = "/var/lib/sorter/sorter.db"
    defect_dir: str = "/var/lib/sorter/defects"
    max_defect_images: int = 500
    log_dir: str = "/var/log/sorter"

    @property
    def target(self) -> str:
        if self.backend == "postgresql":
            if not self.database_url.startswith(("postgresql://", "postgres://")):
                raise ConfigError("PostgreSQL cần SORTER_DATABASE_URL trong môi trường hoặc storage.env_file")
            return self.database_url
        return self.db_path


@dataclass
class AnomalyCfg:
    enabled: bool = True
    window_s: int = 10
    model_path: str = "/opt/sorter/models/anomaly_if.joblib"
    cooldown_s: int = 60


@dataclass
class SorterConfig:
    device: DeviceCfg = field(default_factory=DeviceCfg)
    camera: CameraCfg = field(default_factory=CameraCfg)
    qr: QrCfg = field(default_factory=QrCfg)
    ai: AiCfg = field(default_factory=AiCfg)
    uart: UartCfg = field(default_factory=UartCfg)
    thingsboard: ThingsboardCfg = field(default_factory=ThingsboardCfg)
    rpc: RpcCfg = field(default_factory=RpcCfg)
    storage: StorageCfg = field(default_factory=StorageCfg)
    anomaly: AnomalyCfg = field(default_factory=AnomalyCfg)
    belt_speed: int = 60                 # tốc độ gửi START (30-100)
    source_path: str = ""                # file đã đọc (để log)

    @property
    def sim(self) -> bool:
        return self.uart.port == "sim" or self.camera.source == "folder"


def _build(cls: type, data: Any, path: str) -> Any:
    """Dựng dataclass lồng nhau từ dict; khóa lạ hoặc sai kiểu -> ConfigError."""
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: cần một mục (mapping), nhận {type(data).__name__}")
    kwargs = {}
    known = {f.name: f for f in fields(cls)}
    for k, v in data.items():
        if k not in known:
            raise ConfigError(f"{path}.{k}: khóa không tồn tại (khóa hợp lệ: {', '.join(known)})")
        f = known[k]
        sub_cls = _dataclass_of(f)
        if sub_cls is not None:
            kwargs[k] = _build(sub_cls, v, f"{path}.{k}")
            continue
        default = f.default if f.default is not MISSING else f.default_factory()  # type: ignore[misc]
        kwargs[k] = _coerce(k, v, f"{path}.{k}", default, None)
    return cls(**kwargs)


def _dataclass_of(f) -> type | None:
    """Trả về lớp dataclass con nếu trường có default_factory là một dataclass."""
    df = f.default_factory
    if df is not MISSING and isinstance(df, type) and is_dataclass(df):
        return df
    return None


def _coerce(key: str, v: Any, path: str, default: Any, ftype: Any) -> Any:
    # Suy kiểu từ giá trị mặc định của dataclass
    if isinstance(default, bool):
        if not isinstance(v, bool):
            raise ConfigError(f"{path}: cần true/false")
        return v
    if isinstance(default, int) and not isinstance(default, bool):
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise ConfigError(f"{path}: cần số nguyên")
        return int(v)
    if isinstance(default, float):
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise ConfigError(f"{path}: cần số thực")
        return float(v)
    if isinstance(default, str):
        if not isinstance(v, str):
            raise ConfigError(f"{path}: cần chuỗi")
        return v
    if isinstance(v, list):
        return list(v)
    return v


def validate(cfg: SorterConfig) -> None:
    """Kiểm tra miền giá trị và sự tồn tại của file bắt buộc (P-01)."""
    errs: list[str] = []
    if cfg.storage.backend not in ("sqlite", "postgresql"):
        errs.append("storage.backend phải là sqlite hoặc postgresql")
    if cfg.storage.backend == "postgresql":
        cfg.storage.target
    if cfg.camera.source not in ("picamera2", "folder"):
        errs.append("camera.source phải là picamera2 hoặc folder")
    if len(cfg.camera.resolution) != 2 or any(int(x) <= 0 for x in cfg.camera.resolution):
        errs.append("camera.resolution phải là [w, h] dương")
    if len(cfg.camera.roi) != 4 or any(int(x) < 0 for x in cfg.camera.roi) or cfg.camera.roi[2] <= 0 or cfg.camera.roi[3] <= 0:
        errs.append("camera.roi phải là [x, y, w, h] với w, h > 0")
    else:
        W, H = cfg.camera.resolution
        x, y, w, h = cfg.camera.roi
        if x + w > W or y + h > H:
            errs.append(f"camera.roi {cfg.camera.roi} vượt khỏi ảnh {W}x{H}")
    if not 1 <= cfg.camera.max_qr_frames <= 10:
        errs.append("camera.max_qr_frames phải trong 1..10")
    if not 0 <= cfg.camera.settle_ms <= 5000:
        errs.append("camera.settle_ms phải trong 0..5000")
    if cfg.qr.max_age_days <= 0:
        errs.append("qr.max_age_days phải > 0")
    if cfg.qr.future_skew_s < 0:
        errs.append("qr.future_skew_s phải >= 0")
    if cfg.ai.backend not in ("dummy", "onnx", "tflite"):
        errs.append("ai.backend phải là dummy | onnx | tflite")
    if not 0.0 <= cfg.ai.min_confidence <= 1.0:
        errs.append("ai.min_confidence phải trong 0..1")
    if cfg.ai.backend != "dummy" and not Path(cfg.ai.model_path).exists():
        errs.append(f"ai.model_path không tồn tại: {cfg.ai.model_path}")
    det = cfg.ai.detection
    if det.backend not in ("none", "yolo_world_onnx"):
        errs.append("ai.detection.backend phải là none | yolo_world_onnx")
    if det.backend != "none":
        if cfg.ai.backend == "dummy":
            errs.append("ai.detection không được dùng với classifier dummy")
        if not det.model_path or not Path(det.model_path).is_file():
            errs.append("ai.detection.model_path không tồn tại")
    for name in ("confidence", "iou_threshold", "min_area_ratio"):
        if not 0 < getattr(det, name) <= 1:
            errs.append(f"ai.detection.{name} phải trong (0,1]")
    if not 0 <= det.padding <= 0.5:
        errs.append("ai.detection.padding phải trong [0,0.5]")
    if det.input_size < 32 or det.input_size % 32:
        errs.append("ai.detection.input_size phải là bội của 32, >= 32")
    if cfg.uart.baud not in (9600, 19200, 38400, 57600, 115200, 230400):
        errs.append("uart.baud không hợp lệ")
    if not 50 <= cfg.uart.ack_timeout_ms <= 5000:
        errs.append("uart.ack_timeout_ms phải trong 50..5000")
    if not 0 <= cfg.uart.retries <= 10:
        errs.append("uart.retries phải trong 0..10")
    if not 30 <= cfg.belt_speed <= 100:
        errs.append("belt_speed phải trong 30..100")
    if cfg.thingsboard.enabled:
        for name in ("ca_cert", "client_cert", "client_key"):
            p = getattr(cfg.thingsboard, name)
            if not Path(p).exists():
                errs.append(f"thingsboard.{name} không tồn tại: {p}")
        if not 1 <= cfg.thingsboard.port <= 65535:
            errs.append("thingsboard.port không hợp lệ")
    if cfg.rpc.rate_limit.count <= 0 or cfg.rpc.rate_limit.window_s <= 0:
        errs.append("rpc.rate_limit.count và window_s phải > 0")
    if cfg.storage.max_defect_images <= 0:
        errs.append("storage.max_defect_images phải > 0")
    if cfg.anomaly.window_s <= 0:
        errs.append("anomaly.window_s phải > 0")
    # Khóa HMAC: bắt buộc tồn tại; cảnh báo quyền file lỏng (chỉ kiểm tra trên POSIX)
    key = Path(cfg.qr.hmac_key_file)
    if not key.exists():
        errs.append(f"qr.hmac_key_file không tồn tại: {key} (tạo bằng tools/make_hmac_key.py)")
    elif os.name == "posix":
        mode = stat.S_IMODE(key.stat().st_mode)
        if mode & 0o077:
            errs.append(f"qr.hmac_key_file có quyền {oct(mode)}; cần 600 (chmod 600 {key})")
    if errs:
        raise ConfigError("Cấu hình sai:\n  - " + "\n  - ".join(errs))


def load_config(path: str | os.PathLike[str], validate_files: bool = True) -> SorterConfig:
    """Đọc YAML -> SorterConfig đã kiểm tra. Đường dẫn tương đối trong YAML tính từ thư mục chứa file."""
    p = Path(path)
    if not p.exists():
        raise ConfigError(f"Không tìm thấy file cấu hình: {p}")
    try:
        with p.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except yaml.YAMLError as e:
        raise ConfigError(f"YAML lỗi trong {p}: {e}") from e
    cfg: SorterConfig = _build(SorterConfig, data, "config")
    cfg.source_path = str(p)
    base = p.parent
    _resolve_paths(cfg, base)
    if cfg.storage.backend == "postgresql":
        from dotenv import dotenv_values
        local = dotenv_values(cfg.storage.env_file) if Path(cfg.storage.env_file).exists() else {}
        cfg.storage.database_url = os.environ.get("SORTER_DATABASE_URL") or local.get("SORTER_DATABASE_URL") or cfg.storage.database_url
    if validate_files:
        validate(cfg)
    return cfg


def _resolve_paths(cfg: SorterConfig, base: Path) -> None:
    def fix(v: str) -> str:
        if not v:
            return v
        q = Path(v)
        return str(q if q.is_absolute() else (base / q).resolve())

    cfg.qr.hmac_key_file = fix(cfg.qr.hmac_key_file)
    cfg.ai.model_path = fix(cfg.ai.model_path)
    cfg.ai.labels_path = fix(cfg.ai.labels_path)
    cfg.ai.model_card_path = fix(cfg.ai.model_card_path)
    cfg.ai.detection.model_path = fix(cfg.ai.detection.model_path)
    cfg.camera.folder = fix(cfg.camera.folder)
    cfg.thingsboard.ca_cert = fix(cfg.thingsboard.ca_cert)
    cfg.thingsboard.client_cert = fix(cfg.thingsboard.client_cert)
    cfg.thingsboard.client_key = fix(cfg.thingsboard.client_key)
    cfg.storage.db_path = fix(cfg.storage.db_path)
    cfg.storage.env_file = fix(cfg.storage.env_file)
    cfg.storage.defect_dir = fix(cfg.storage.defect_dir)
    cfg.storage.log_dir = fix(cfg.storage.log_dir)
    cfg.anomaly.model_path = fix(cfg.anomaly.model_path)


def default_config() -> SorterConfig:
    return SorterConfig()
