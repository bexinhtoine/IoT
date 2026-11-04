"""O-02 — Quản lý cặp khóa ký OTA (ECDSA P-256).

- Private key: PEM mã hóa ``BestAvailableEncryption(passphrase)`` tại ``keys/ota_private.pem``
  quyền 600, chỉ nằm trên máy dev. KHÔNG BAO GIỜ log/hiển thị nội dung private key.
- Public key: ``keys/ota_pubkey.pem`` và header C ``ota_pubkey.h`` để nhúng vào firmware ESP32.
- key_id = 8 byte đầu SHA-256(public key DER SubjectPublicKeyInfo) -> hex 16 ký tự.
- fingerprint = SHA-256 đầy đủ của DER (hex, nhóm 2 ký tự bằng dấu ':').
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from .paths import AppPaths, find_repo_root, PUBKEY_HEADER_REL, restrict_file

log = logging.getLogger(__name__)


class KeyError_(Exception):
    """Lỗi khóa (sai passphrase, chưa có khóa, trùng khóa...)."""


@dataclass
class KeyInfo:
    key_id: str
    fingerprint: str
    public_pem: str
    private_path: Path
    public_path: Path


def public_der(public_key: ec.EllipticCurvePublicKey) -> bytes:
    return public_key.public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )


def public_pem(public_key: ec.EllipticCurvePublicKey) -> str:
    return public_key.public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode("ascii")


def key_id_of(public_key: ec.EllipticCurvePublicKey) -> str:
    """key_id = 8 byte đầu SHA-256(DER SPKI), hex 16 ký tự (ESP32 so khớp với giá trị nhúng)."""
    return hashlib.sha256(public_der(public_key)).hexdigest()[:16]


def fingerprint_of(public_key: ec.EllipticCurvePublicKey) -> str:
    digest = hashlib.sha256(public_der(public_key)).hexdigest()
    return ":".join(digest[i : i + 2] for i in range(0, len(digest), 2))


def generate_keypair(paths: AppPaths, passphrase: str, overwrite: bool = False) -> KeyInfo:
    """Tạo cặp khóa P-256 và lưu vào keys/. Từ chối ghi đè khóa cũ nếu overwrite=False."""
    if len(passphrase) < 8:
        raise KeyError_("Passphrase bảo vệ private key phải có ít nhất 8 ký tự")
    paths.ensure()
    if paths.private_key_path.exists() and not overwrite:
        raise KeyError_(f"Đã có khóa tại {paths.private_key_path}; cần xác nhận ghi đè")

    private_key = ec.generate_private_key(ec.SECP256R1())
    priv_pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.BestAvailableEncryption(passphrase.encode("utf-8")),
    )
    # Ghi file private với quyền 600 ngay từ lúc tạo
    paths.private_key_path.write_bytes(priv_pem)
    restrict_file(paths.private_key_path)
    del priv_pem

    pub = private_key.public_key()
    paths.public_key_path.write_text(public_pem(pub), encoding="ascii")
    info = KeyInfo(
        key_id=key_id_of(pub),
        fingerprint=fingerprint_of(pub),
        public_pem=public_pem(pub),
        private_path=paths.private_key_path,
        public_path=paths.public_key_path,
    )
    log.info("Đã tạo cặp khóa ECDSA P-256, key_id=%s", info.key_id)
    return info


def load_private_key(paths: AppPaths, passphrase: str) -> ec.EllipticCurvePrivateKey:
    """Giải mã private key bằng passphrase; sai passphrase -> KeyError_."""
    if not paths.private_key_path.exists():
        raise KeyError_("Chưa có private key; hãy tạo khóa ở tab Khóa")
    try:
        key = serialization.load_pem_private_key(
            paths.private_key_path.read_bytes(), password=passphrase.encode("utf-8")
        )
    except (ValueError, TypeError) as exc:
        raise KeyError_("Passphrase sai hoặc file khóa hỏng") from exc
    if not isinstance(key, ec.EllipticCurvePrivateKey):
        raise KeyError_("File khóa không phải ECDSA")
    return key


def load_public_key(path: Path) -> ec.EllipticCurvePublicKey:
    """Đọc public key PEM từ file (dùng bởi app, verify_cli và test)."""
    key = serialization.load_pem_public_key(Path(path).read_bytes())
    if not isinstance(key, ec.EllipticCurvePublicKey):
        raise KeyError_("Public key không phải ECDSA")
    return key


def load_public_key_pem(pem: str | bytes) -> ec.EllipticCurvePublicKey:
    data = pem.encode("ascii") if isinstance(pem, str) else pem
    key = serialization.load_pem_public_key(data)
    if not isinstance(key, ec.EllipticCurvePublicKey):
        raise KeyError_("Public key không phải ECDSA")
    return key


def current_key_info(paths: AppPaths) -> Optional[KeyInfo]:
    """Thông tin khóa hiện có (chỉ từ public key, không cần passphrase); None nếu chưa tạo."""
    if not paths.public_key_path.exists():
        return None
    pub = load_public_key(paths.public_key_path)
    return KeyInfo(
        key_id=key_id_of(pub),
        fingerprint=fingerprint_of(pub),
        public_pem=public_pem(pub),
        private_path=paths.private_key_path,
        public_path=paths.public_key_path,
    )


def pubkey_header_text(public_key: ec.EllipticCurvePublicKey) -> str:
    """Nội dung ota_pubkey.h: chuỗi PEM dạng C, mỗi dòng PEM là một literal nối nhau."""
    pem_lines = public_pem(public_key).strip().splitlines()
    kid = key_id_of(public_key)
    body = "\n".join(f'    "{line}\\n"' for line in pem_lines)
    return (
        "/* O-02 — Public key ECDSA P-256 dùng xác minh manifest OTA.\n"
        " * File này do ota-app sinh tự động (tab Khóa -> Xuất ota_pubkey.h). KHÔNG sửa tay.\n"
        f" * key_id (8 byte đầu SHA-256 của DER SPKI): {kid}\n"
        " */\n"
        "#pragma once\n\n"
        "static const char OTA_PUBKEY_PEM[] =\n"
        f"{body};\n\n"
        f'static const char OTA_PUBKEY_ID[] = "{kid}";\n'
    )


def default_header_path() -> Optional[Path]:
    root = find_repo_root()
    return (root / PUBKEY_HEADER_REL) if root else None


def export_pubkey_header(public_key: ec.EllipticCurvePublicKey, path: Optional[Path] = None) -> Path:
    """Ghi ota_pubkey.h. Mặc định vào esp32/components/ota_secure/include/ trong repo nếu tìm thấy."""
    target = Path(path) if path else default_header_path()
    if target is None:
        raise KeyError_("Không tìm thấy repo; hãy chỉ định đường dẫn xuất ota_pubkey.h")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(pubkey_header_text(public_key), encoding="utf-8", newline="\n")
    log.info("Đã xuất public key header: %s", target)
    return target


def export_pubkey_pem(public_key: ec.EllipticCurvePublicKey, path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(public_pem(public_key), encoding="ascii")
    return path
