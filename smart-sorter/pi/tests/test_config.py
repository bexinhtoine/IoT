"""P-01 — Test load_config: ConfigError khi roi vượt ảnh, khóa lạ, sai kiểu; config.sim.yaml hợp lệ."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from sorter.config import ConfigError, load_config

PI = Path(__file__).resolve().parents[1]
KEY = PI / "tests" / "fixtures" / "qr_hmac.key"


def write_cfg(tmp_path: Path, data: dict) -> Path:
    base = {"qr": {"hmac_key_file": str(KEY)}, "camera": {"source": "folder", "folder": str(PI / "tests" / "fixtures" / "images")}, "uart": {"port": "sim"}}
    for k, v in data.items():
        if isinstance(v, dict):
            base.setdefault(k, {}).update(v)
        else:
            base[k] = v
    p = tmp_path / "cfg.yaml"
    p.write_text(yaml.safe_dump(base), encoding="utf-8")
    return p


def test_sim_config_loads():
    cfg = load_config(PI / "config.sim.yaml")
    assert cfg.sim and cfg.uart.port == "sim" and cfg.camera.source == "folder"
    assert Path(cfg.qr.hmac_key_file).is_absolute()


def test_roi_outside_image(tmp_path):
    with pytest.raises(ConfigError, match="roi"):
        load_config(write_cfg(tmp_path, {"camera": {"resolution": [640, 480], "roi": [320, 160, 640, 640]}}))


def test_unknown_key(tmp_path):
    with pytest.raises(ConfigError, match="không tồn tại"):
        load_config(write_cfg(tmp_path, {"camera": {"brightnes": 3}}))


def test_wrong_type(tmp_path):
    with pytest.raises(ConfigError):
        load_config(write_cfg(tmp_path, {"uart": {"ack_timeout_ms": "fast"}}))


def test_bad_backend_and_speed(tmp_path):
    with pytest.raises(ConfigError) as ei:
        load_config(write_cfg(tmp_path, {"ai": {"backend": "magic"}, "belt_speed": 150}))
    assert "ai.backend" in str(ei.value) and "belt_speed" in str(ei.value)


def test_missing_key_file(tmp_path):
    with pytest.raises(ConfigError, match="hmac_key_file"):
        load_config(write_cfg(tmp_path, {"qr": {"hmac_key_file": str(tmp_path / "nope.key")}}))


def test_missing_config_file(tmp_path):
    with pytest.raises(ConfigError):
        load_config(tmp_path / "missing.yaml")


@pytest.mark.parametrize("setting", [
    {"backend": "unknown"}, {"confidence": 0}, {"iou_threshold": 1.1},
    {"padding": -0.1}, {"min_area_ratio": 0}, {"input_size": 257},
    {"confidence": float("nan")},
])
def test_bad_detector_config(tmp_path, setting):
    with pytest.raises(ConfigError, match="ai.detection"):
        load_config(write_cfg(tmp_path, {"ai": {"detection": setting}}))


def test_detector_model_path_resolved_relative_to_yaml(tmp_path):
    cfg = load_config(write_cfg(tmp_path, {"ai": {"detection": {"model_path": "box.onnx"}}}),
                      validate_files=False)
    assert cfg.ai.detection.model_path == str((tmp_path / "box.onnx").resolve())
