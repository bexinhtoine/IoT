"""O-03 — Kiểm tra file firmware ESP32 (.bin) trước khi ký.

Bố cục ảnh app ESP-IDF (esp_image_header_t, components/bootloader_support/include/esp_app_format.h):

    offset 0   : esp_image_header_t (24 byte) — byte 0 = magic 0xE9
    offset 24  : esp_image_segment_header_t (8 byte: load_addr u32, data_len u32)
    offset 32  : esp_app_desc_t (256 byte) nằm ở đầu segment đầu tiên (.flash.rodata / DROM)
                 magic_word u32 = 0xABCD5432 (little-endian), secure_version u32, reserv1[2] u32,
                 version char[32], project_name char[32], time[16], date[16], idf_ver[32],
                 app_elf_sha256[32], ...

Secure Boot V2 (docs: security/secure-boot-v2): ảnh được pad tới bội số 4096 byte rồi nối
một sector chữ ký 4 KB; byte đầu của sector là magic 0xE7, byte thứ hai là version 0x02.
Ở đây chỉ kiểm tra heuristic: size % 4096 == 0, size > 4096 và byte[size-4096] == 0xE7.
"""
from __future__ import annotations

import hashlib
import logging
import random
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

ESP_IMAGE_MAGIC = 0xE9
ESP_APP_DESC_MAGIC = 0xABCD5432
ESP_APP_DESC_OFFSET = 32
ESP_APP_DESC_SIZE = 256
SECURE_BOOT_V2_MAGIC = 0xE7
SECURE_BOOT_SECTOR = 4096
IMAGE_HEADER_SIZE = 24
SEGMENT_HEADER_SIZE = 8

# magic u32, secure_version u32, reserv1[2] u32, version 32s, project 32s, time 16s, date 16s, idf 32s, sha 32s
_APP_DESC_STRUCT = struct.Struct("<IIII32s32s16s16s32s32s")


@dataclass
class FirmwareInfo:
    path: Path
    size: int
    sha256_hex: str
    is_valid_image: bool
    app_version: Optional[str]
    project_name: Optional[str]
    has_secure_boot_v2_block: bool
    idf_version: Optional[str]
    secure_version: Optional[int] = None
    build_time: Optional[str] = None
    build_date: Optional[str] = None

    @property
    def problems(self) -> list[str]:
        """Danh sách cảnh báo để UI hiển thị."""
        out: list[str] = []
        if not self.is_valid_image:
            out.append("Byte đầu không phải 0xE9: không phải ảnh app ESP32 hợp lệ")
        if self.app_version is None:
            out.append("Không đọc được esp_app_desc_t (magic 0xABCD5432) — nhập phiên bản tay")
        return out


def _cstr(raw: bytes) -> Optional[str]:
    """Chuỗi C cắt tại NUL; None nếu rỗng hoặc không phải UTF-8."""
    s = raw.split(b"\x00", 1)[0]
    if not s:
        return None
    try:
        return s.decode("utf-8")
    except UnicodeDecodeError:
        return None


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def parse_app_desc(data: bytes) -> Optional[dict]:
    """Đọc esp_app_desc_t ở offset 32; trả None nếu magic sai hoặc file quá ngắn."""
    end = ESP_APP_DESC_OFFSET + _APP_DESC_STRUCT.size
    if len(data) < end:
        return None
    (magic, secure_version, _r0, _r1, version, project, btime, bdate, idf, _sha) = _APP_DESC_STRUCT.unpack(
        data[ESP_APP_DESC_OFFSET:end]
    )
    if magic != ESP_APP_DESC_MAGIC:
        return None
    return {
        "secure_version": secure_version,
        "version": _cstr(version),
        "project_name": _cstr(project),
        "time": _cstr(btime),
        "date": _cstr(bdate),
        "idf_ver": _cstr(idf),
    }


def has_secure_boot_v2_block(path: Path, size: int) -> bool:
    """Heuristic: sector 4 KB cuối bắt đầu bằng 0xE7 (xem docstring module)."""
    if size <= SECURE_BOOT_SECTOR or size % SECURE_BOOT_SECTOR != 0:
        return False
    with open(path, "rb") as f:
        f.seek(size - SECURE_BOOT_SECTOR)
        first = f.read(2)
    return len(first) == 2 and first[0] == SECURE_BOOT_V2_MAGIC


def inspect_firmware(path: Path | str) -> FirmwareInfo:
    """Đọc thông tin firmware; không ném lỗi với file lạ (chỉ đánh dấu không hợp lệ)."""
    p = Path(path)
    size = p.stat().st_size
    with open(p, "rb") as f:
        head = f.read(ESP_APP_DESC_OFFSET + ESP_APP_DESC_SIZE)
    is_valid = len(head) > 0 and head[0] == ESP_IMAGE_MAGIC
    desc = parse_app_desc(head) if is_valid else None
    info = FirmwareInfo(
        path=p,
        size=size,
        sha256_hex=sha256_file(p),
        is_valid_image=is_valid,
        app_version=desc["version"] if desc else None,
        project_name=desc["project_name"] if desc else None,
        has_secure_boot_v2_block=has_secure_boot_v2_block(p, size),
        idf_version=desc["idf_ver"] if desc else None,
        secure_version=desc["secure_version"] if desc else None,
        build_time=desc["time"] if desc else None,
        build_date=desc["date"] if desc else None,
    )
    log.info(
        "Firmware %s: size=%d sha256=%s valid=%s version=%s project=%s secure_boot_block=%s",
        p.name, info.size, info.sha256_hex[:16] + "...", info.is_valid_image,
        info.app_version, info.project_name, info.has_secure_boot_v2_block,
    )
    return info


def make_fake_firmware(
    path: Path | str,
    version: str = "1.0.2",
    size: int = 65536,
    project_name: str = "smart-sorter-esp32",
    idf_ver: str = "v5.2.1",
    secure_boot: bool = False,
    seed: Optional[int] = None,
) -> Path:
    """Tạo firmware giả có header hợp lệ (0xE9 + esp_app_desc_t) để test/demo.

    Phần thân sau app_desc là dữ liệu giả lập ngẫu nhiên (seed theo version nếu không truyền)
    để các phiên bản khác nhau có hash khác nhau. ``secure_boot=True`` nối thêm sector
    chữ ký giả 4 KB bắt đầu bằng 0xE7 (chỉ để kiểm tra heuristic, không phải chữ ký thật).
    """
    p = Path(path)
    body_size = size
    if secure_boot:
        # pad tới bội 4096 rồi thêm sector chữ ký
        body_size = ((size + SECURE_BOOT_SECTOR - 1) // SECURE_BOOT_SECTOR) * SECURE_BOOT_SECTOR
    min_size = ESP_APP_DESC_OFFSET + ESP_APP_DESC_SIZE
    if body_size < min_size:
        raise ValueError(f"size phải >= {min_size}")

    # esp_image_header_t: magic, segment_count, spi_mode, spi_speed/size, entry_addr,
    # wp_pin, spi_pin_drv[3], chip_id(u16, 0=ESP32), min_chip_rev, min_rev_full, max_rev_full,
    # reserved[4], hash_appended
    header = struct.pack(
        "<BBBBIB3sHBHH4sB",
        ESP_IMAGE_MAGIC, 1, 0, 0x20, 0x400D0000, 0xEE, b"\x00\x00\x00", 0x0000, 0, 0, 0xFFFF, b"\x00" * 4, 0,
    )
    assert len(header) == IMAGE_HEADER_SIZE
    seg_len = body_size - IMAGE_HEADER_SIZE - SEGMENT_HEADER_SIZE
    segment = struct.pack("<II", 0x3F400020, seg_len)
    desc = _APP_DESC_STRUCT.pack(
        ESP_APP_DESC_MAGIC, 0, 0, 0,
        version.encode("utf-8")[:31].ljust(32, b"\x00"),
        project_name.encode("utf-8")[:31].ljust(32, b"\x00"),
        b"12:00:00".ljust(16, b"\x00"),
        b"Nov  2 2026".ljust(16, b"\x00"),
        idf_ver.encode("utf-8")[:31].ljust(32, b"\x00"),
        b"\x00" * 32,
    )
    desc = desc.ljust(ESP_APP_DESC_SIZE, b"\x00")
    rng = random.Random(seed if seed is not None else f"fake-fw-{version}")
    filler_len = body_size - len(header) - len(segment) - len(desc)
    filler = rng.randbytes(filler_len)
    data = header + segment + desc + filler
    if secure_boot:
        sig_block = bytes([SECURE_BOOT_V2_MAGIC, 0x02]).ljust(SECURE_BOOT_SECTOR, b"\xff")
        data += sig_block
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    log.debug("Đã tạo firmware giả %s (%d byte, version=%s)", p, len(data), version)
    return p
