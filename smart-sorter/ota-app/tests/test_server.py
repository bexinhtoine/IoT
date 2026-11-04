"""Test O-05: server HTTPS thật (thread riêng) với CA + cert tự tạo trong test.

Client dùng ssl context chỉ tin CA đó (không bao giờ tắt xác minh TLS).
"""
from __future__ import annotations

import datetime as dt
import ipaddress
import json
import ssl
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from ota_app.history import HistoryStore
from ota_app.server import OtaServer, RequestLogEntry, local_urls, validate_report


def _make_ca_and_server_cert(out: Path) -> tuple[Path, Path, Path]:
    """Tạo CA nội bộ + cert server (SAN 127.0.0.1/localhost) bằng cryptography."""
    now = dt.datetime.now(dt.timezone.utc)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "SmartSorter Test CA")])
    ca_cert = (
        x509.CertificateBuilder()
        .subject_name(ca_name).issuer_name(ca_name).public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5)).not_valid_after(now + dt.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(x509.KeyUsage(digital_signature=True, key_cert_sign=True, crl_sign=True,
                                     content_commitment=False, key_encipherment=False, data_encipherment=False,
                                     key_agreement=False, encipher_only=False, decipher_only=False), critical=True)
        .sign(ca_key, hashes.SHA256())
    )
    srv_key = ec.generate_private_key(ec.SECP256R1())
    srv_cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "ota-server")]))
        .issuer_name(ca_name).public_key(srv_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5)).not_valid_after(now + dt.timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([
            x509.IPAddress(ipaddress.ip_address("127.0.0.1")), x509.DNSName("localhost")]), critical=False)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(ca_key, hashes.SHA256())
    )
    out.mkdir(parents=True, exist_ok=True)
    ca_pem, cert_pem, key_pem = out / "ca.pem", out / "ota_server.pem", out / "ota_server.key"
    ca_pem.write_bytes(ca_cert.public_bytes(serialization.Encoding.PEM))
    cert_pem.write_bytes(srv_cert.public_bytes(serialization.Encoding.PEM))
    key_pem.write_bytes(srv_key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    return ca_pem, cert_pem, key_pem


@pytest.fixture
def running_server(tmp_path: Path, valid_release, app_paths):
    ca_pem, cert_pem, key_pem = _make_ca_and_server_cert(tmp_path / "pki")
    hist = HistoryStore(app_paths.db_path)
    seen: list[RequestLogEntry] = []
    srv = OtaServer(history=hist, on_request=seen.append)
    srv.start(valid_release.release_dir, cert_pem, key_pem, host="127.0.0.1", port=0)
    assert srv.running and srv.port > 0
    client_ctx = ssl.create_default_context(cafile=str(ca_pem))
    assert client_ctx.verify_mode == ssl.CERT_REQUIRED
    base = f"https://127.0.0.1:{srv.port}"
    yield srv, base, client_ctx, hist, seen
    srv.stop()
    assert not srv.running
    hist.close()


def _get(base: str, path: str, ctx: ssl.SSLContext) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(base + path, context=ctx, timeout=10) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def _post(base: str, path: str, ctx: ssl.SSLContext, body: bytes) -> tuple[int, bytes]:
    req = urllib.request.Request(base + path, data=body, method="POST", headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=10) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def test_get_three_files(running_server, valid_release):
    srv, base, ctx, hist, seen = running_server
    st, body = _get(base, "/ota/manifest.json", ctx)
    assert st == 200 and body == valid_release.manifest_bytes  # đúng byte file, không chuẩn hóa lại
    st, body = _get(base, "/ota/manifest.sig", ctx)
    assert st == 200 and body == valid_release.sig_b64
    st, body = _get(base, "/ota/firmware.bin", ctx)
    assert st == 200 and body == valid_release.firmware_path.read_bytes()
    assert len(body) == valid_release.manifest["fw_size"]
    # log request: callback + bảng request_log
    assert [e.route for e in seen] == ["/ota/manifest.json", "/ota/manifest.sig", "/ota/firmware.bin"]
    assert all(e.status == 200 and e.remote_ip == "127.0.0.1" for e in seen)
    assert len(hist.list_request_log()) == 3


def test_post_report_valid_and_invalid(running_server):
    srv, base, ctx, hist, seen = running_server
    good = json.dumps({"device": "sorter-esp32-01", "version_code": 3, "result": "SUCCESS", "reason": ""}).encode()
    st, body = _post(base, "/ota/report", ctx, good)
    assert st == 200 and json.loads(body)["ok"] is True
    reps = hist.list_device_reports()
    assert len(reps) == 1 and reps[0].device == "sorter-esp32-01" and reps[0].result == "SUCCESS"
    assert reps[0].remote_ip == "127.0.0.1"

    rejected = json.dumps({"device": "sorter-esp32-01", "version_code": 2, "result": "REJECTED",
                           "reason": "ROLLBACK_REJECTED"}).encode()
    assert _post(base, "/ota/report", ctx, rejected)[0] == 200
    assert hist.list_device_reports()[0].reason == "ROLLBACK_REJECTED"

    # sai: thiếu trường / result lạ / không phải JSON / quá 2 KB
    bad_result = json.dumps({"device": "x", "version_code": 1, "result": "HACKED", "reason": ""}).encode()
    assert _post(base, "/ota/report", ctx, bad_result)[0] == 400
    missing = json.dumps({"device": "x", "result": "SUCCESS"}).encode()
    assert _post(base, "/ota/report", ctx, missing)[0] == 400
    assert _post(base, "/ota/report", ctx, b"not json")[0] == 400
    wrong_type = json.dumps({"device": "x", "version_code": "3", "result": "SUCCESS", "reason": ""}).encode()
    assert _post(base, "/ota/report", ctx, wrong_type)[0] == 400
    big = json.dumps({"device": "x", "version_code": 1, "result": "SUCCESS", "reason": "a" * 3000}).encode()
    assert _post(base, "/ota/report", ctx, big)[0] == 413
    assert len(hist.list_device_reports()) == 2  # không ghi báo cáo sai
    assert any(e.status == 400 for e in seen) and any(e.status == 413 for e in seen)


def test_unknown_routes_404(running_server):
    srv, base, ctx, hist, seen = running_server
    assert _get(base, "/", ctx)[0] == 404
    assert _get(base, "/ota/", ctx)[0] == 404
    assert _get(base, "/ota/release_valid/firmware.bin", ctx)[0] == 404
    assert _get(base, "/ota/../manifest.json", ctx)[0] == 404
    assert _get(base, "/ota/report", ctx)[0] == 404  # GET trên route POST
    assert _post(base, "/ota/manifest.json", ctx, b"{}")[0] == 404
    assert _get(base, "/ota/manifest.json.bak", ctx)[0] == 404
    assert all(e.status == 404 for e in seen)


def test_tls_required_and_min_version(running_server):
    srv, base, ctx, hist, seen = running_server
    # client không tin CA -> bắt tay thất bại (xác minh TLS không bị tắt)
    untrusted = ssl.create_default_context()
    with pytest.raises(urllib.error.URLError):
        urllib.request.urlopen(base + "/ota/manifest.json", context=untrusted, timeout=10)
    # client chỉ cho phép tối đa TLS 1.1 -> server (minimum TLS 1.2) từ chối bắt tay
    old = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    old.load_verify_locations(cafile=ctx_cafile(running_server))
    try:
        old.maximum_version = ssl.TLSVersion.TLSv1_1
    except ValueError:
        pytest.skip("OpenSSL không cho phép TLS 1.1 phía client")
    with pytest.raises((urllib.error.URLError, ssl.SSLError, OSError)):
        urllib.request.urlopen(base + "/ota/manifest.json", context=old, timeout=10)


def ctx_cafile(running_server) -> str:
    """Đường dẫn ca.pem của fixture (server lưu cert cạnh thư mục pki của tmp_path)."""
    srv = running_server[0]
    return str(srv.release_dir.parent.parent / "pki" / "ca.pem")


def test_restart_and_double_start(running_server, valid_release, tmp_path):
    from ota_app.server import ServerError
    srv, base, ctx, hist, seen = running_server
    with pytest.raises(ServerError):
        srv.start(valid_release.release_dir, tmp_path / "pki" / "ota_server.pem", tmp_path / "pki" / "ota_server.key")
    srv.stop()
    srv.stop()  # idempotent
    assert not srv.running
    srv.start(valid_release.release_dir, tmp_path / "pki" / "ota_server.pem", tmp_path / "pki" / "ota_server.key",
              host="127.0.0.1", port=0)
    assert _get(f"https://127.0.0.1:{srv.port}", "/ota/manifest.sig", ctx)[0] == 200


def test_validate_report_and_local_urls():
    ok = validate_report({"device": "d", "version_code": 1, "result": "ROLLED_BACK", "reason": "SELFTEST_FAILED"})
    assert ok["result"] == "ROLLED_BACK"
    for bad in [[], {"device": "", "version_code": 1, "result": "SUCCESS"},
                {"device": "d", "version_code": True, "result": "SUCCESS"},
                {"device": "d", "version_code": -1, "result": "SUCCESS"},
                {"device": "d", "version_code": 1, "result": "SUCCESS", "reason": "x" * 201},
                {"device": "d" * 65, "version_code": 1, "result": "SUCCESS"}]:
        with pytest.raises(ValueError):
            validate_report(bad)
    urls = local_urls(8443)
    assert urls and all(u.startswith("https://") and u.endswith(":8443/ota/") for u in urls)
