"""Test O-03: inspect_firmware trên firmware giả, file lạ, khối Secure Boot V2."""
from __future__ import annotations

import hashlib
from pathlib import Path

from ota_app.firmware import (
    ESP_APP_DESC_OFFSET,
    ESP_IMAGE_MAGIC,
    inspect_firmware,
    make_fake_firmware,
)


def test_inspect_fake_firmware(tmp_path: Path):
    p = make_fake_firmware(tmp_path / "app.bin", version="1.0.2", size=65536, project_name="smart-sorter-esp32")
    info = inspect_firmware(p)
    assert info.is_valid_image
    assert info.size == 65536
    assert info.sha256_hex == hashlib.sha256(p.read_bytes()).hexdigest()
    assert info.app_version == "1.0.2"
    assert info.project_name == "smart-sorter-esp32"
    assert info.idf_version == "v5.2.1"
    assert not info.has_secure_boot_v2_block
    assert info.problems == []
    data = p.read_bytes()
    assert data[0] == ESP_IMAGE_MAGIC
    assert data[ESP_APP_DESC_OFFSET:ESP_APP_DESC_OFFSET + 4] == bytes.fromhex("3254CDAB")  # 0xABCD5432 LE


def test_versions_have_different_hash(tmp_path: Path):
    a = inspect_firmware(make_fake_firmware(tmp_path / "a.bin", version="1.0.0"))
    b = inspect_firmware(make_fake_firmware(tmp_path / "b.bin", version="1.0.1"))
    assert a.sha256_hex != b.sha256_hex


def test_secure_boot_block_detected(tmp_path: Path):
    p = make_fake_firmware(tmp_path / "sb.bin", version="2.0.0", size=70000, secure_boot=True)
    info = inspect_firmware(p)
    assert info.size % 4096 == 0
    assert info.has_secure_boot_v2_block
    assert info.app_version == "2.0.0"


def test_invalid_image_and_missing_desc(tmp_path: Path):
    bad = tmp_path / "bad.bin"
    bad.write_bytes(b"\x00" * 1024)
    info = inspect_firmware(bad)
    assert not info.is_valid_image and info.app_version is None
    assert any("0xE9" in s for s in info.problems)

    # magic đúng nhưng app_desc hỏng -> app_version None (UI cho nhập tay)
    nodesc = tmp_path / "nodesc.bin"
    nodesc.write_bytes(bytes([0xE9]) + b"\x00" * 600)
    info = inspect_firmware(nodesc)
    assert info.is_valid_image and info.app_version is None and info.project_name is None

    tiny = tmp_path / "tiny.bin"
    tiny.write_bytes(b"\xe9")
    info = inspect_firmware(tiny)
    assert info.is_valid_image and info.app_version is None
