"""Test O-07: 3 kiểu tấn công bị verify_release_dir từ chối đúng reason; khôi phục; chặn version."""
from __future__ import annotations

import random
from pathlib import Path

import pytest

from ota_app import keys
from ota_app.auth import AuthStore
from ota_app.demo_attacks import (
    DemoError,
    backup_dir,
    downgrade_release,
    forge_manifest,
    restore_valid,
    tamper_firmware,
)
from ota_app.history import HistoryStore
from ota_app.main import AppContext, ReleaseError
from ota_app.manifest import verify_release_dir
from ota_app.server import OtaServer
from tests.conftest import PASSPHRASE

DEVICE_VC = 3  # thiết bị đang chạy version_code 3 (bản hợp lệ vừa phát hành)


def test_tamper_firmware_hash_mismatch(valid_release, keypair):
    priv, _ = keypair
    pub = priv.public_key()
    d = valid_release.release_dir
    original = (d / "firmware.bin").read_bytes()
    res = tamper_firmware(d, rng=random.Random(42))
    assert res.expected_reason == "HASH_MISMATCH"
    assert res.details["offset"] >= 4096
    tampered = (d / "firmware.bin").read_bytes()
    assert len(tampered) == len(original)
    assert sum(a != b for a, b in zip(original, tampered)) == 1  # đúng 1 byte khác
    assert (d / "manifest.json").read_bytes() == valid_release.manifest_bytes  # manifest giữ nguyên
    r = verify_release_dir(d, pub, min_version_code=DEVICE_VC - 1)
    assert not r.ok and r.reason == "HASH_MISMATCH"
    # sao lưu tồn tại và khôi phục được
    assert (backup_dir(d) / "firmware.bin").read_bytes() == original
    restore_valid(d)
    assert verify_release_dir(d, pub, min_version_code=DEVICE_VC - 1).ok


def test_forge_manifest_signature_invalid(valid_release, keypair):
    priv, _ = keypair
    pub = priv.public_key()
    d = valid_release.release_dir
    old_sig = (d / "manifest.sig").read_bytes()
    res = forge_manifest(d, mode="hash")
    assert res.expected_reason == "SIGNATURE_INVALID"
    assert (d / "manifest.sig").read_bytes() == old_sig
    assert (d / "manifest.json").read_bytes() != valid_release.manifest_bytes
    r = verify_release_dir(d, pub, min_version_code=DEVICE_VC - 1)
    assert not r.ok and r.reason == "SIGNATURE_INVALID"
    restore_valid(d)
    res = forge_manifest(d, mode="version")
    assert res.details["new"] == 4
    assert verify_release_dir(d, pub).reason == "SIGNATURE_INVALID"
    restore_valid(d)
    assert verify_release_dir(d, pub).ok


def test_downgrade_rollback_rejected(valid_release, keypair):
    priv, info = keypair
    pub = priv.public_key()
    d = valid_release.release_dir
    res = downgrade_release(d, priv, info.key_id, current_device_version_code=DEVICE_VC)
    assert res.expected_reason == "ROLLBACK_REJECTED"
    assert res.details["version_code"] == DEVICE_VC - 1
    # Chữ ký và hash đều hợp lệ (không có min_version) ...
    r = verify_release_dir(d, pub)
    assert r.ok and r.manifest["version_code"] == DEVICE_VC - 1
    # ... nhưng thiết bị đang ở DEVICE_VC thì từ chối rollback
    r = verify_release_dir(d, pub, min_version_code=DEVICE_VC)
    assert not r.ok and r.reason == "ROLLBACK_REJECTED"
    restore_valid(d)
    assert verify_release_dir(d, pub, min_version_code=DEVICE_VC - 1).manifest["version_code"] == DEVICE_VC
    with pytest.raises(DemoError):
        downgrade_release(d, priv, info.key_id, current_device_version_code=1)


def test_restore_without_backup_raises(tmp_path: Path):
    with pytest.raises(DemoError):
        restore_valid(tmp_path)


def _ctx(app_paths, username: str, role: str) -> AppContext:
    auth = AuthStore(app_paths.db_path, bcrypt_rounds=4)
    auth.create_user(username, "correct-horse-battery", role)
    hist = HistoryStore(app_paths.db_path)
    ctx = AppContext(paths=app_paths, auth=auth, history=hist, server=OtaServer(history=hist))
    ctx.refresh_key_info()
    ctx.session = auth.login(username, "correct-horse-battery").session
    return ctx


def test_publish_flow_blocks_non_increasing_version(app_paths, keypair, fake_fw):
    ctx = _ctx(app_paths, "admin", "release_manager")
    files = ctx.publish_release(fake_fw, 3, "1.0.2", PASSPHRASE, note="first")
    assert files.release_dir == app_paths.release_dir(3)
    assert ctx.history.max_version_code() == 3
    rec = ctx.history.current_release()
    assert rec is not None and rec.signed_by == "admin" and rec.version_code == 3
    pub = keys.load_public_key(app_paths.public_key_path)
    assert verify_release_dir(files.release_dir, pub).ok

    with pytest.raises(ReleaseError, match="lớn hơn"):
        ctx.publish_release(fake_fw, 3, "1.0.2b", PASSPHRASE)
    with pytest.raises(ReleaseError, match="lớn hơn"):
        ctx.publish_release(fake_fw, 2, "0.9", PASSPHRASE)
    with pytest.raises(ReleaseError, match="Passphrase"):
        ctx.publish_release(fake_fw, 4, "1.0.3", "wrong-passphrase")
    with pytest.raises(ReleaseError, match="Secure Boot"):
        ctx.publish_release(fake_fw, 4, "1.0.3", PASSPHRASE, require_secure_boot_block=True)
    assert ctx.publish_release(fake_fw, 4, "1.0.3", PASSPHRASE).manifest["version_code"] == 4
    assert len(ctx.history.list_releases()) == 2
    ctx.close()


def test_viewer_cannot_publish(app_paths, keypair, fake_fw):
    ctx = _ctx(app_paths, "view", "viewer")
    with pytest.raises(ReleaseError, match="release_manager"):
        ctx.publish_release(fake_fw, 1, "1.0.0", PASSPHRASE)
    ctx.close()


def test_history_csv_export(app_paths, tmp_path):
    hist = HistoryStore(app_paths.db_path)
    hist.add_release(1, "1.0.0", "a" * 64, 100, "0" * 16, "admin", tmp_path / "r1")
    hist.add_device_report("sorter-esp32-01", 1, "SUCCESS", "", "192.168.1.5")
    with pytest.raises(ValueError):
        hist.add_device_report("x", 1, "WEIRD", "")
    out = tmp_path / "releases.csv"
    assert hist.export_csv(out, "releases") == 1
    text = out.read_text("utf-8")
    assert text.splitlines()[0].startswith("id,version_code,version_name,fw_sha256")
    assert hist.export_csv(tmp_path / "rep.csv", "device_reports") == 1
    assert hist.list_releases("1.0.0")[0].is_current
    assert hist.list_device_reports("esp32")[0].result == "SUCCESS"
    with pytest.raises(ValueError):
        hist.export_csv(tmp_path / "x.csv", "users")
    hist.close()
