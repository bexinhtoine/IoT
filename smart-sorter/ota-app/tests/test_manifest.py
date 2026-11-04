"""Test O-04: ký/xác minh vòng tròn, định dạng manifest, verify_release_dir, chặn version."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric import ec

from ota_app import keys
from ota_app.firmware import inspect_firmware
from ota_app.manifest import (
    MANIFEST_KEYS,
    ManifestError,
    build_manifest,
    serialize,
    sig_to_file,
    sign,
    verify,
    verify_release_dir,
    write_release,
)

TOOLS = Path(__file__).resolve().parent.parent / "tools" / "verify_cli.py"


def test_manifest_format(fake_fw, keypair):
    _, info = keypair
    m = build_manifest(inspect_firmware(fake_fw), 3, "1.0.2", info.key_id, created_at="2026-11-02T09:15:00Z")
    assert tuple(sorted(m)) == MANIFEST_KEYS
    raw = serialize(m)
    assert b"\n" not in raw and b" " not in raw
    assert raw.startswith(b'{"created_at":"2026-11-02T09:15:00Z","device_model":"sorter-esp32","fw_file":"firmware.bin"')
    assert json.loads(raw)["version_code"] == 3
    assert len(m["key_id"]) == 16


def test_sign_verify_roundtrip(fake_fw, keypair):
    priv, info = keypair
    raw = serialize(build_manifest(inspect_firmware(fake_fw), 1, "1.0.0", info.key_id))
    sig_b64 = sig_to_file(sign(raw, priv))
    assert verify(raw, sig_b64, priv.public_key())
    # public key đọc lại từ PEM cũng xác minh được
    assert verify(raw, sig_b64, keys.load_public_key(info.public_path))
    # đổi 1 byte manifest -> sai
    assert not verify(raw[:-1] + b"]", sig_b64, priv.public_key())
    # khóa khác -> sai
    other = ec.generate_private_key(ec.SECP256R1()).public_key()
    assert not verify(raw, sig_b64, other)
    # chữ ký rác -> False chứ không ném
    assert not verify(raw, b"!!notbase64!!", priv.public_key())


def test_build_manifest_rejects_bad_params(fake_fw, keypair):
    _, info = keypair
    fw = inspect_firmware(fake_fw)
    with pytest.raises(ManifestError):
        build_manifest(fw, 0, "1.0.0", info.key_id)
    with pytest.raises(ManifestError):
        build_manifest(fw, 1, "", info.key_id)
    with pytest.raises(ManifestError):
        build_manifest(fw, 1, "1.0.0", "zz")


def test_write_release_self_verifies(valid_release, keypair):
    priv, info = keypair
    pub = keys.load_public_key(info.public_path)
    assert valid_release.manifest_path.read_bytes() == valid_release.manifest_bytes
    res = verify_release_dir(valid_release.release_dir, pub)
    assert res.ok and res.reason == "NONE"
    assert res.manifest["version_code"] == 3
    # version thiết bị thấp hơn -> OK; bằng hoặc cao hơn -> ROLLBACK_REJECTED
    assert verify_release_dir(valid_release.release_dir, pub, min_version_code=2).ok
    r = verify_release_dir(valid_release.release_dir, pub, min_version_code=3)
    assert not r.ok and r.reason == "ROLLBACK_REJECTED"
    r = verify_release_dir(valid_release.release_dir, pub, min_version_code=10)
    assert r.reason == "ROLLBACK_REJECTED"


def test_verify_model_size_and_bad_manifest(valid_release, keypair):
    priv, info = keypair
    pub = priv.public_key()
    d = valid_release.release_dir
    # sai model (ký lại hợp lệ với model khác)
    m = dict(valid_release.manifest)
    m["device_model"] = "other-board"
    raw = serialize(m)
    (d / "manifest.json").write_bytes(raw)
    (d / "manifest.sig").write_bytes(sig_to_file(sign(raw, priv)))
    assert verify_release_dir(d, pub).reason == "MODEL_MISMATCH"
    # sai kích thước (ký hợp lệ nhưng fw_size khác)
    m = dict(valid_release.manifest)
    m["fw_size"] = m["fw_size"] + 1
    raw = serialize(m)
    (d / "manifest.json").write_bytes(raw)
    (d / "manifest.sig").write_bytes(sig_to_file(sign(raw, priv)))
    assert verify_release_dir(d, pub).reason == "SIZE_MISMATCH"
    # manifest ký hợp lệ nhưng thiếu trường
    raw = b'{"version_code":3}'
    (d / "manifest.json").write_bytes(raw)
    (d / "manifest.sig").write_bytes(sig_to_file(sign(raw, priv)))
    assert verify_release_dir(d, pub).reason == "BAD_MANIFEST"
    # thiếu file
    (d / "manifest.sig").unlink()
    assert verify_release_dir(d, pub).reason == "BAD_MANIFEST"


def test_verify_cli(valid_release, keypair, tmp_path):
    _, info = keypair
    import os
    env = dict(os.environ, PYTHONUTF8="1")
    cmd = [sys.executable, str(TOOLS), str(valid_release.release_dir), "--pubkey", str(info.public_path)]
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", env=env)
    assert p.returncode == 0, p.stderr
    assert "reason=NONE" in p.stdout
    p = subprocess.run(cmd + ["--min-version", "3"], capture_output=True, text=True, encoding="utf-8", env=env)
    assert p.returncode == 1
    assert "ROLLBACK_REJECTED" in p.stdout


def test_pubkey_header_export(keypair, tmp_path):
    priv, info = keypair
    out = keys.export_pubkey_header(priv.public_key(), tmp_path / "inc" / "ota_pubkey.h")
    text = out.read_text("utf-8")
    assert 'static const char OTA_PUBKEY_PEM[] =' in text
    assert '"-----BEGIN PUBLIC KEY-----\\n"' in text
    assert '"-----END PUBLIC KEY-----\\n"' in text
    assert info.key_id in text
    # key_id = 8 byte đầu SHA-256(DER SPKI)
    import hashlib
    der = keys.public_der(priv.public_key())
    assert info.key_id == hashlib.sha256(der).hexdigest()[:16]
    assert info.fingerprint.replace(":", "") == hashlib.sha256(der).hexdigest()
    # private key không xuất hiện trong header hay public pem
    assert "PRIVATE" not in text and "PRIVATE" not in info.public_pem


def test_private_key_encrypted(app_paths, keypair):
    from ota_app.keys import KeyError_, load_private_key
    data = app_paths.private_key_path.read_bytes()
    assert b"ENCRYPTED PRIVATE KEY" in data
    with pytest.raises(KeyError_):
        load_private_key(app_paths, "wrong-passphrase")
    with pytest.raises(KeyError_):
        keys.generate_keypair(app_paths, "another-pass-123")  # không ghi đè khóa cũ
