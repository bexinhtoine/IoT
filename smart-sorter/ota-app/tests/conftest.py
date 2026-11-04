"""Fixture dùng chung cho test ota-app (không cần Qt)."""
from __future__ import annotations

from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric import ec

from ota_app import keys
from ota_app.firmware import inspect_firmware, make_fake_firmware
from ota_app.manifest import write_release
from ota_app.paths import AppPaths

PASSPHRASE = "test-passphrase-123"


@pytest.fixture
def app_paths(tmp_path: Path) -> AppPaths:
    return AppPaths(tmp_path / "data").ensure()


@pytest.fixture
def keypair(app_paths: AppPaths) -> tuple[ec.EllipticCurvePrivateKey, keys.KeyInfo]:
    info = keys.generate_keypair(app_paths, PASSPHRASE)
    priv = keys.load_private_key(app_paths, PASSPHRASE)
    return priv, info


@pytest.fixture
def fake_fw(tmp_path: Path) -> Path:
    return make_fake_firmware(tmp_path / "fw" / "app.bin", version="1.0.2", size=65536)


@pytest.fixture
def valid_release(tmp_path: Path, fake_fw: Path, keypair):
    """Thư mục phát hành hợp lệ version_code=3 đã ký."""
    priv, info = keypair
    rel = tmp_path / "releases" / "3"
    files = write_release(fake_fw, inspect_firmware(fake_fw), 3, "1.0.2", priv, info.key_id, rel)
    return files
