"""O-07 — Chế độ demo tấn công (CHỈ DÙNG ĐỂ DEMO).

Thao tác trực tiếp trên thư mục phát hành đang được server phục vụ, luôn sao lưu bản hợp lệ
vào ``<dir>/release_valid/`` trước khi phá. Server chỉ phục vụ 3 file cố định nên thư mục
sao lưu không bị lộ ra ngoài.

(a) tamper_firmware   : lật 1 bit ngẫu nhiên sau 4096 byte đầu của firmware.bin, giữ manifest
                        -> ESP32 phải báo HASH_MISMATCH (O-12).
(b) forge_manifest    : sửa fw_sha256 (hoặc version_code+1) trong manifest.json, giữ manifest.sig cũ
                        -> SIGNATURE_INVALID (O-10).
(c) downgrade_release : ký HỢP LỆ một bản có version_code = current-1 bằng firmware hiện tại
                        -> ROLLBACK_REJECTED (O-11) dù chữ ký đúng.
restore_valid         : chép lại 3 file từ release_valid/.
"""
from __future__ import annotations

import json
import logging
import random
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Optional

from cryptography.hazmat.primitives.asymmetric import ec

from .firmware import inspect_firmware
from .manifest import (
    FIRMWARE_NAME,
    MANIFEST_NAME,
    SIG_NAME,
    ManifestError,
    build_manifest,
    parse_manifest,
    serialize,
    sig_to_file,
    sign,
)

log = logging.getLogger(__name__)

BACKUP_DIRNAME = "release_valid"
HEADER_SKIP = 4096
RELEASE_FILES: tuple[str, ...] = (MANIFEST_NAME, SIG_NAME, FIRMWARE_NAME)

AttackKind = Literal["TAMPER_FIRMWARE", "FORGE_MANIFEST", "DOWNGRADE", "RESTORE"]


class DemoError(Exception):
    pass


@dataclass
class DemoResult:
    kind: AttackKind
    title: str
    description: str
    expected_reason: str  # reason ESP32/verify_cli phải báo
    details: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        return f"[{self.kind}] {self.title}: {self.description} (mong đợi: {self.expected_reason})"


def backup_dir(release_dir: Path | str) -> Path:
    return Path(release_dir) / BACKUP_DIRNAME


def has_backup(release_dir: Path | str) -> bool:
    b = backup_dir(release_dir)
    return all((b / n).is_file() for n in RELEASE_FILES)


def backup_valid(release_dir: Path | str, overwrite: bool = False) -> Path:
    """Sao lưu 3 file hợp lệ vào release_valid/ (chỉ lần đầu, trừ khi overwrite)."""
    d = Path(release_dir)
    b = backup_dir(d)
    if has_backup(d) and not overwrite:
        return b
    for n in RELEASE_FILES:
        if not (d / n).is_file():
            raise DemoError(f"Thiếu {n} trong {d}; thư mục phát hành chưa hợp lệ")
    b.mkdir(parents=True, exist_ok=True)
    for n in RELEASE_FILES:
        shutil.copyfile(d / n, b / n)
    log.info("Đã sao lưu bản hợp lệ vào %s", b)
    return b


def restore_valid(release_dir: Path | str) -> DemoResult:
    """Khôi phục 3 file từ release_valid/."""
    d = Path(release_dir)
    if not has_backup(d):
        raise DemoError("Chưa có bản sao lưu release_valid/ để khôi phục")
    b = backup_dir(d)
    for n in RELEASE_FILES:
        shutil.copyfile(b / n, d / n)
    log.info("Đã khôi phục bản hợp lệ tại %s", d)
    return DemoResult("RESTORE", "Khôi phục bản hợp lệ", "Đã chép lại manifest.json, manifest.sig, firmware.bin từ release_valid/", "NONE")


def tamper_firmware(release_dir: Path | str, rng: Optional[random.Random] = None) -> DemoResult:
    """(a) Lật 1 bit ở vị trí ngẫu nhiên sau 4096 byte đầu của firmware.bin; manifest giữ nguyên."""
    d = Path(release_dir)
    backup_valid(d)
    fw = d / FIRMWARE_NAME
    data = bytearray(fw.read_bytes())
    if len(data) <= HEADER_SKIP + 1:
        raise DemoError("firmware.bin quá nhỏ để sửa sau header 4096 byte")
    r = rng or random.Random()
    offset = r.randrange(HEADER_SKIP, len(data))
    bit = r.randrange(8)
    before = data[offset]
    data[offset] ^= 1 << bit
    fw.write_bytes(bytes(data))
    log.warning("DEMO: lật bit %d tại offset 0x%X của firmware.bin (0x%02X -> 0x%02X)", bit, offset, before, data[offset])
    return DemoResult(
        "TAMPER_FIRMWARE",
        "Sửa 1 byte firmware",
        f"Lật bit {bit} tại offset 0x{offset:X} (0x{before:02X} -> 0x{data[offset]:02X}); manifest và chữ ký giữ nguyên",
        "HASH_MISMATCH",
        {"offset": offset, "bit": bit, "before": before, "after": data[offset]},
    )


def forge_manifest(release_dir: Path | str, mode: Literal["hash", "version"] = "hash") -> DemoResult:
    """(b) Sửa nội dung manifest.json nhưng giữ manifest.sig cũ -> chữ ký không còn khớp."""
    d = Path(release_dir)
    backup_valid(d)
    m_path = d / MANIFEST_NAME
    try:
        manifest = parse_manifest(m_path.read_bytes())
    except ManifestError as exc:
        raise DemoError(f"manifest.json hiện tại không hợp lệ: {exc}") from exc
    if mode == "hash":
        old = manifest["fw_sha256"]
        # đổi ký tự đầu của hash sang một hex khác
        new_first = "0" if old[0] != "0" else "1"
        manifest["fw_sha256"] = new_first + old[1:]
        desc = f"Đổi fw_sha256 ({old[:12]}... -> {manifest['fw_sha256'][:12]}...), giữ manifest.sig cũ"
        changed = {"field": "fw_sha256", "old": old, "new": manifest["fw_sha256"]}
    else:
        old_vc = manifest["version_code"]
        manifest["version_code"] = old_vc + 1
        desc = f"Tăng version_code {old_vc} -> {old_vc + 1} trong manifest, giữ manifest.sig cũ"
        changed = {"field": "version_code", "old": old_vc, "new": old_vc + 1}
    m_path.write_bytes(serialize(manifest))
    log.warning("DEMO: giả manifest (%s)", desc)
    return DemoResult("FORGE_MANIFEST", "Giả manifest", desc, "SIGNATURE_INVALID", changed)


def downgrade_release(
    release_dir: Path | str,
    private_key: ec.EllipticCurvePrivateKey,
    key_id: str,
    current_device_version_code: int,
) -> DemoResult:
    """(c) Ký HỢP LỆ một manifest có version_code = current-1 (dùng đúng firmware hiện tại)."""
    d = Path(release_dir)
    backup_valid(d)
    target = int(current_device_version_code) - 1
    if target < 1:
        raise DemoError("version_code thiết bị phải >= 2 để hạ xuống phiên bản thấp hơn")
    m_path = d / MANIFEST_NAME
    try:
        old_manifest = parse_manifest(m_path.read_bytes())
    except ManifestError as exc:
        raise DemoError(f"manifest.json hiện tại không hợp lệ: {exc}") from exc
    fw_info = inspect_firmware(d / FIRMWARE_NAME)
    manifest = build_manifest(
        fw_info, target, f"{old_manifest['version_name']}-downgrade", key_id,
        fw_file=FIRMWARE_NAME, device_model=old_manifest["device_model"],
    )
    m_bytes = serialize(manifest)
    (d / MANIFEST_NAME).write_bytes(m_bytes)
    (d / SIG_NAME).write_bytes(sig_to_file(sign(m_bytes, private_key)))
    desc = (
        f"Ký hợp lệ manifest version_code={target} (thiết bị đang chạy {current_device_version_code});"
        " chữ ký và hash đều đúng, chỉ phiên bản thấp hơn"
    )
    log.warning("DEMO: hạ phiên bản -> version_code=%d", target)
    return DemoResult(
        "DOWNGRADE", "Hạ phiên bản", desc, "ROLLBACK_REJECTED",
        {"version_code": target, "device_version_code": current_device_version_code},
    )


def current_manifest(release_dir: Path | str) -> Optional[dict[str, Any]]:
    """Đọc manifest hiện tại để hiển thị (None nếu không có/không parse được)."""
    p = Path(release_dir) / MANIFEST_NAME
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
