"""S-03 / P-04 — Xác thực mã QR có chữ ký HMAC-SHA256 (đặc tả 4.1).

Định dạng: PKG000123|A|1761631080|<43 ký tự base64url>
- mã_kiện  : ^PKG\\d{6}$
- kho      : A | B
- thời_gian: Unix epoch giây (UTC) lúc tạo mã
- chữ_ký   : base64url không padding của HMAC-SHA256(key, "PKG000123|A|1761631080")

Thứ tự kiểm tra: format -> warehouse -> signature -> future -> expired -> duplicate.
Chỉ ghi used_codes khi mọi bước trước đều hợp lệ (trong một transaction, atomic).
KHÔNG BAO GIỜ log khóa.
"""
from __future__ import annotations

import base64
import hmac
import hashlib
import logging
import os
import re
import stat
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from .interfaces import F_QR_BAD_FORMAT, F_QR_BAD_SIGNATURE, F_QR_DUPLICATE, metrics
from .qr_reader import QR_MULTI_QR, QR_NO_QR, QrReadResult

if TYPE_CHECKING:  # tránh import vòng khi chỉ cần kiểu
    from .storage import Storage

log = logging.getLogger(__name__)

# Mã kết quả xác thực (4.1)
VALID = "VALID"
NO_QR = "NO_QR"
MULTI_QR = "MULTI_QR"
BAD_FORMAT = "BAD_FORMAT"
BAD_WAREHOUSE = "BAD_WAREHOUSE"
BAD_SIGNATURE = "BAD_SIGNATURE"
FUTURE_TIMESTAMP = "FUTURE_TIMESTAMP"
EXPIRED = "EXPIRED"
DUPLICATE = "DUPLICATE"
ALL_STATUSES: tuple[str, ...] = (
    VALID, NO_QR, MULTI_QR, BAD_FORMAT, BAD_WAREHOUSE, BAD_SIGNATURE, FUTURE_TIMESTAMP, EXPIRED, DUPLICATE,
)

PKG_RE = re.compile(r"^PKG\d{6}$")
TS_RE = re.compile(r"^\d{1,12}$")
SIG_RE = re.compile(r"^[A-Za-z0-9_-]{43}$")
WAREHOUSES = ("A", "B")
KEY_LEN = 32
MAX_TEXT_LEN = 256


@dataclass
class QrResult:
    """Kết quả xác thực. pkg_id/warehouse/ts chỉ có ý nghĩa khi parse được (status khác NO_QR/MULTI_QR/BAD_FORMAT)."""

    status: str
    pkg_id: str | None = None
    warehouse: str | None = None
    ts: int | None = None
    text: str | None = None

    @property
    def valid(self) -> bool:
        return self.status == VALID


def _message(pkg_id: str, warehouse: str, ts: int) -> bytes:
    return f"{pkg_id}|{warehouse}|{int(ts)}".encode("ascii")


def sign(key: bytes, pkg_id: str, warehouse: str, ts: int) -> str:
    """Chữ ký base64url không padding (43 ký tự) của HMAC-SHA256."""
    mac = hmac.new(key, _message(pkg_id, warehouse, ts), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac).rstrip(b"=").decode("ascii")


def make_code(key: bytes, pkg_id: str, warehouse: str, ts: int | None = None, forge: bool = False) -> str:
    """Tạo nội dung QR đầy đủ. forge=True -> chữ ký sai (dùng cho demo tấn công)."""
    if ts is None:
        ts = int(time.time())
    sig = sign(key, pkg_id, warehouse, ts)
    if forge:
        # Đổi ký tự đầu để chữ ký vẫn đúng độ dài/bảng chữ nhưng sai giá trị
        first = "A" if sig[0] != "A" else "B"
        sig = first + sig[1:]
    return f"{pkg_id}|{warehouse}|{int(ts)}|{sig}"


def load_hmac_key(path: str | os.PathLike[str]) -> bytes:
    """Đọc khóa 32 byte raw (hoặc 64 ký tự hex). Cảnh báo nếu quyền file lỏng (POSIX). Không log khóa."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Không tìm thấy file khóa HMAC: {p} (tạo bằng tools/make_hmac_key.py)")
    if os.name == "posix":
        mode = stat.S_IMODE(p.stat().st_mode)
        if mode & 0o077:
            log.warning("File khóa %s có quyền %s; nên chmod 600", p, oct(mode))
    raw = p.read_bytes()
    if len(raw) == KEY_LEN:
        return raw
    text = raw.strip()
    if len(text) == KEY_LEN * 2:
        try:
            return bytes.fromhex(text.decode("ascii"))
        except (ValueError, UnicodeDecodeError) as e:
            raise ValueError(f"File khóa {p}: 64 ký tự nhưng không phải hex") from e
    raise ValueError(f"File khóa {p}: cần đúng {KEY_LEN} byte raw hoặc {KEY_LEN * 2} ký tự hex, nhận {len(raw)} byte")


class QrVerifier:
    """Xác thực mã QR (S-03). storage phải có try_insert_used_code(pkg_id, ts_ms) -> bool."""

    def __init__(self, key: bytes, storage: "Storage | None", max_age_days: int = 30, future_skew_s: int = 300) -> None:
        if len(key) != KEY_LEN:
            raise ValueError(f"Khóa HMAC phải {KEY_LEN} byte")
        self._key = bytes(key)
        self._storage = storage
        self.max_age_days = int(max_age_days)
        self.future_skew_s = int(future_skew_s)

    def __repr__(self) -> str:  # không bao giờ lộ khóa
        return f"QrVerifier(max_age_days={self.max_age_days}, future_skew_s={self.future_skew_s})"

    def parse(self, text: str) -> QrResult:
        """Kiểm tra định dạng, chưa kiểm tra chữ ký. Trả status BAD_FORMAT hoặc VALID (tạm)."""
        if not isinstance(text, str) or not text or len(text) > MAX_TEXT_LEN:
            return QrResult(BAD_FORMAT, text=text if isinstance(text, str) else None)
        parts = text.split("|")
        if len(parts) != 4:
            return QrResult(BAD_FORMAT, text=text)
        pkg_id, wh, ts_s, sig = parts
        if not PKG_RE.match(pkg_id) or not TS_RE.match(ts_s) or not SIG_RE.match(sig):
            return QrResult(BAD_FORMAT, text=text)
        return QrResult(VALID, pkg_id, wh, int(ts_s), text)

    def verify(self, text: str | None, now: float | None = None) -> QrResult:
        """Xác thực đầy đủ; now = epoch giây (mặc định time.time())."""
        if text is None:
            return QrResult(NO_QR)
        res = self.parse(text)
        if res.status != VALID:
            metrics.inc(F_QR_BAD_FORMAT)
            return res
        assert res.pkg_id is not None and res.warehouse is not None and res.ts is not None
        if res.warehouse not in WAREHOUSES:
            metrics.inc(F_QR_BAD_FORMAT)
            res.status = BAD_WAREHOUSE
            return res
        expected = sign(self._key, res.pkg_id, res.warehouse, res.ts)
        sig = text.rsplit("|", 1)[1]
        if not hmac.compare_digest(expected.encode("ascii"), sig.encode("ascii")):
            metrics.inc(F_QR_BAD_SIGNATURE)
            res.status = BAD_SIGNATURE
            return res
        now_s = time.time() if now is None else float(now)
        if res.ts > now_s + self.future_skew_s:
            res.status = FUTURE_TIMESTAMP
            return res
        if res.ts < now_s - self.max_age_days * 86400:
            res.status = EXPIRED
            return res
        if self._storage is not None:
            if not self._storage.try_insert_used_code(res.pkg_id, int(now_s * 1000)):
                metrics.inc(F_QR_DUPLICATE)
                res.status = DUPLICATE
                return res
        res.status = VALID
        return res

    def verify_read(self, read: QrReadResult, now: float | None = None) -> QrResult:
        """Tiện ích: nhận thẳng kết quả của qr_reader.read_qr."""
        if read.status == QR_MULTI_QR:
            return QrResult(MULTI_QR)
        if read.status == QR_NO_QR or read.text is None:
            return QrResult(NO_QR)
        return self.verify(read.text, now)
