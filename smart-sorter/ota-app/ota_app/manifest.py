"""O-04 — Manifest Secure OTA và chữ ký ECDSA (đặc tả 4.5).

manifest.json (một dòng, khóa sắp xếp, không khoảng trắng):
    {"created_at":"...Z","device_model":"sorter-esp32","fw_file":"firmware.bin",
     "fw_sha256":"<64 hex>","fw_size":N,"key_id":"<16 hex>","version_code":N,"version_name":"..."}
manifest.sig = base64( DER( ECDSA-P256/SHA-256 trên ĐÚNG các byte của manifest.json ) ).

``verify_release_dir`` kiểm tra theo đúng thứ tự ESP32 (O-10 -> O-12):
    chữ ký trên byte file -> parse JSON -> device_model -> version_code -> fw_size -> sha256.
Tên reason lấy từ shared/protocol/protocol.yaml (ota_reasons); SIZE_MISMATCH và BAD_MANIFEST
là mã chi tiết riêng của app (ESP32 gộp cả hai vào HASH_MISMATCH / SIGNATURE_INVALID).
"""
from __future__ import annotations

import base64
import binascii
import json
import logging
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Optional

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

from .firmware import FirmwareInfo, sha256_file

log = logging.getLogger(__name__)

DEVICE_MODEL = "sorter-esp32"
MANIFEST_NAME = "manifest.json"
SIG_NAME = "manifest.sig"
FIRMWARE_NAME = "firmware.bin"

MANIFEST_KEYS: tuple[str, ...] = (
    "created_at", "device_model", "fw_file", "fw_sha256", "fw_size", "key_id", "version_code", "version_name",
)

VerifyReason = Literal[
    "NONE", "SIGNATURE_INVALID", "HASH_MISMATCH", "ROLLBACK_REJECTED",
    "MODEL_MISMATCH", "SIZE_MISMATCH", "BAD_MANIFEST",
]


@dataclass
class VerifyResult:
    ok: bool
    reason: VerifyReason
    manifest: Optional[dict[str, Any]] = None
    detail: str = ""

    def __str__(self) -> str:
        return f"{'OK' if self.ok else 'REJECTED'} ({self.reason}) {self.detail}".strip()


class ManifestError(Exception):
    """Manifest sai định dạng hoặc tham số phát hành không hợp lệ."""


def iso_utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_manifest(
    fw_info: FirmwareInfo,
    version_code: int,
    version_name: str,
    key_id: str,
    fw_file: str = FIRMWARE_NAME,
    device_model: str = DEVICE_MODEL,
    created_at: Optional[str] = None,
) -> dict[str, Any]:
    """Tạo dict manifest với đúng 8 khóa theo đặc tả 4.5."""
    if not isinstance(version_code, int) or isinstance(version_code, bool) or version_code <= 0:
        raise ManifestError("version_code phải là số nguyên dương")
    if not version_name or len(version_name) > 32:
        raise ManifestError("version_name phải có 1-32 ký tự")
    if len(key_id) != 16 or not all(c in "0123456789abcdef" for c in key_id):
        raise ManifestError("key_id phải là 16 ký tự hex")
    return {
        "created_at": created_at or iso_utc_now(),
        "device_model": device_model,
        "fw_file": fw_file,
        "fw_sha256": fw_info.sha256_hex,
        "fw_size": fw_info.size,
        "key_id": key_id,
        "version_code": version_code,
        "version_name": version_name,
    }


def serialize(manifest: dict[str, Any]) -> bytes:
    """JSON chuẩn hóa: sort_keys, không khoảng trắng, một dòng, không newline cuối."""
    return json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def sign(manifest_bytes: bytes, private_key: ec.EllipticCurvePrivateKey) -> bytes:
    """Chữ ký ECDSA P-256 / SHA-256 dạng DER (r,s ASN.1)."""
    return private_key.sign(manifest_bytes, ec.ECDSA(hashes.SHA256()))


def sig_to_file(sig_der: bytes) -> bytes:
    """Nội dung manifest.sig: base64 của DER (một dòng, có newline cuối cho dễ cat)."""
    return base64.b64encode(sig_der) + b"\n"


def sig_from_file(data: bytes) -> bytes:
    try:
        return base64.b64decode(data.strip(), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ManifestError("manifest.sig không phải base64 hợp lệ") from exc


def verify(manifest_bytes: bytes, sig_b64: bytes | str, public_key: ec.EllipticCurvePublicKey) -> bool:
    """Xác minh chữ ký base64(DER) trên đúng các byte manifest."""
    raw = sig_b64.encode("ascii") if isinstance(sig_b64, str) else sig_b64
    try:
        der = sig_from_file(raw)
        public_key.verify(der, manifest_bytes, ec.ECDSA(hashes.SHA256()))
        return True
    except (InvalidSignature, ManifestError, ValueError):
        return False


def parse_manifest(manifest_bytes: bytes) -> dict[str, Any]:
    """Parse + kiểm tra kiểu từng trường; sai -> ManifestError."""
    try:
        obj = json.loads(manifest_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ManifestError(f"manifest.json không phải JSON: {exc}") from exc
    if not isinstance(obj, dict):
        raise ManifestError("manifest.json phải là object")
    missing = [k for k in MANIFEST_KEYS if k not in obj]
    if missing:
        raise ManifestError(f"manifest thiếu trường: {missing}")
    for k in ("created_at", "device_model", "fw_file", "fw_sha256", "key_id", "version_name"):
        if not isinstance(obj[k], str):
            raise ManifestError(f"trường {k} phải là chuỗi")
    for k in ("fw_size", "version_code"):
        if not isinstance(obj[k], int) or isinstance(obj[k], bool) or obj[k] < 0:
            raise ManifestError(f"trường {k} phải là số nguyên không âm")
    if len(obj["fw_sha256"]) != 64 or any(c not in "0123456789abcdef" for c in obj["fw_sha256"]):
        raise ManifestError("fw_sha256 phải là 64 ký tự hex thường")
    if "/" in obj["fw_file"] or "\\" in obj["fw_file"] or obj["fw_file"] in ("", ".", ".."):
        raise ManifestError("fw_file không hợp lệ")
    return obj


def verify_release_dir(
    release_dir: Path | str,
    public_key: ec.EllipticCurvePublicKey,
    min_version_code: Optional[int] = None,
    device_model: str = DEVICE_MODEL,
) -> VerifyResult:
    """Xác minh thư mục phát hành theo đúng thứ tự ESP32.

    ``min_version_code``: version_code lớn nhất thiết bị đang chạy/đã lưu NVS; manifest phải
    có version_code > giá trị này, nếu không -> ROLLBACK_REJECTED (O-11).
    """
    d = Path(release_dir)
    m_path, s_path = d / MANIFEST_NAME, d / SIG_NAME
    if not m_path.is_file() or not s_path.is_file():
        return VerifyResult(False, "BAD_MANIFEST", detail="thiếu manifest.json hoặc manifest.sig")
    manifest_bytes = m_path.read_bytes()
    sig_bytes = s_path.read_bytes()

    # 1) chữ ký trên đúng byte file (O-10)
    if not verify(manifest_bytes, sig_bytes, public_key):
        return VerifyResult(False, "SIGNATURE_INVALID", detail="chữ ký ECDSA không khớp manifest.json")

    # 2) parse
    try:
        manifest = parse_manifest(manifest_bytes)
    except ManifestError as exc:
        return VerifyResult(False, "BAD_MANIFEST", detail=str(exc))

    # 3) device_model (O-11)
    if manifest["device_model"] != device_model:
        return VerifyResult(
            False, "MODEL_MISMATCH", manifest,
            detail=f"device_model={manifest['device_model']!r} khác {device_model!r}",
        )

    # 4) version_code phải tăng (O-11)
    if min_version_code is not None and manifest["version_code"] <= min_version_code:
        return VerifyResult(
            False, "ROLLBACK_REJECTED", manifest,
            detail=f"version_code={manifest['version_code']} <= {min_version_code}",
        )

    # 5) kích thước (O-12)
    fw_path = d / manifest["fw_file"]
    actual_size = fw_path.stat().st_size if fw_path.is_file() else -1
    if actual_size != manifest["fw_size"]:
        return VerifyResult(
            False, "SIZE_MISMATCH", manifest,
            detail=f"fw_size={manifest['fw_size']} nhưng file {actual_size} byte",
        )

    # 6) SHA-256 (O-12)
    actual_sha = sha256_file(fw_path)
    if actual_sha != manifest["fw_sha256"]:
        return VerifyResult(
            False, "HASH_MISMATCH", manifest,
            detail=f"sha256 file {actual_sha[:16]}... khác manifest {manifest['fw_sha256'][:16]}...",
        )

    return VerifyResult(True, "NONE", manifest, detail="hợp lệ")


@dataclass
class ReleaseFiles:
    """Kết quả ghi một thư mục phát hành."""

    release_dir: Path
    manifest: dict[str, Any] = field(default_factory=dict)
    manifest_bytes: bytes = b""
    sig_b64: bytes = b""

    @property
    def manifest_path(self) -> Path:
        return self.release_dir / MANIFEST_NAME

    @property
    def sig_path(self) -> Path:
        return self.release_dir / SIG_NAME

    @property
    def firmware_path(self) -> Path:
        return self.release_dir / self.manifest.get("fw_file", FIRMWARE_NAME)


def write_release(
    fw_path: Path | str,
    fw_info: FirmwareInfo,
    version_code: int,
    version_name: str,
    private_key: ec.EllipticCurvePrivateKey,
    key_id: str,
    release_dir: Path | str,
    device_model: str = DEVICE_MODEL,
    created_at: Optional[str] = None,
    copy_firmware: bool = True,
) -> ReleaseFiles:
    """Sao chép firmware, tạo manifest.json + manifest.sig và TỰ XÁC MINH LẠI bằng public key.

    Nếu tự xác minh thất bại thì ném ManifestError (không phát hành bản lỗi).
    """
    d = Path(release_dir)
    d.mkdir(parents=True, exist_ok=True)
    src = Path(fw_path)
    dst = d / FIRMWARE_NAME
    if copy_firmware and src.resolve() != dst.resolve():
        shutil.copyfile(src, dst)
    manifest = build_manifest(
        fw_info, version_code, version_name, key_id,
        fw_file=FIRMWARE_NAME, device_model=device_model, created_at=created_at,
    )
    m_bytes = serialize(manifest)
    sig_b64 = sig_to_file(sign(m_bytes, private_key))
    (d / MANIFEST_NAME).write_bytes(m_bytes)
    (d / SIG_NAME).write_bytes(sig_b64)

    result = verify_release_dir(d, private_key.public_key(), device_model=device_model)
    if not result.ok:
        raise ManifestError(f"Tự xác minh sau khi ký thất bại: {result}")
    log.info("Đã ký bản phát hành version_code=%d (%s) tại %s", version_code, version_name, d)
    return ReleaseFiles(d, manifest, m_bytes, sig_b64)
