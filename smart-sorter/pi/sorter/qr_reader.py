"""P-03 — Đọc mã QR từ ảnh.

Ưu tiên pyzbar (libzbar), dự phòng cv2.QRCodeDetector (detectAndDecodeMulti).
Thử lần lượt từng khung hình; khung nào không đọc được thì thử lại với ảnh tăng tương phản (CLAHE).
Nhiều hơn một mã khác nhau trong ảnh -> MULTI_QR (không hợp lệ, xem 1.2 hàng 4).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Sequence

import cv2
import numpy as np

log = logging.getLogger(__name__)

QR_VALID = "VALID"
QR_NO_QR = "NO_QR"
QR_MULTI_QR = "MULTI_QR"

try:  # pyzbar cần libzbar; thiếu thì dùng OpenCV
    from pyzbar import pyzbar as _pyzbar  # type: ignore[import-not-found]
    from pyzbar.pyzbar import ZBarSymbol as _ZBarSymbol  # type: ignore[import-not-found]

    HAVE_PYZBAR = True
except Exception:  # pragma: no cover - phụ thuộc môi trường
    _pyzbar = None
    _ZBarSymbol = None
    HAVE_PYZBAR = False

_cv_detector: "cv2.QRCodeDetector | None" = None


@dataclass
class QrReadResult:
    """status: VALID | NO_QR | MULTI_QR; text: nội dung mã khi VALID."""

    status: str
    text: str | None = None
    frames_tried: int = 0
    backend: str = ""


def _to_gray(img: np.ndarray) -> np.ndarray:
    if img.ndim == 2:
        return img
    if img.shape[2] == 4:
        return cv2.cvtColor(img, cv2.COLOR_RGBA2GRAY)
    return cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)


def _decode_pyzbar(gray: np.ndarray) -> list[str]:
    if _pyzbar is None:
        return []
    try:
        objs = _pyzbar.decode(gray, symbols=[_ZBarSymbol.QRCODE])
    except Exception as e:  # pragma: no cover - lỗi DLL/libzbar
        log.warning("pyzbar lỗi: %s", e)
        return []
    out: list[str] = []
    for o in objs:
        try:
            out.append(o.data.decode("utf-8"))
        except UnicodeDecodeError:
            out.append(o.data.decode("latin-1"))
    return out


def _decode_cv2(gray: np.ndarray) -> list[str]:
    global _cv_detector
    if _cv_detector is None:
        _cv_detector = cv2.QRCodeDetector()
    try:
        ok, infos, _pts, _straight = _cv_detector.detectAndDecodeMulti(gray)
    except cv2.error as e:  # pragma: no cover
        log.debug("cv2.QRCodeDetector lỗi: %s", e)
        return []
    if not ok or infos is None:
        return []
    return [s for s in infos if s]


def decode_once(gray: np.ndarray, use_cv2: bool | None = None) -> list[str]:
    """Giải mã mọi QR trong một ảnh xám; trả danh sách nội dung KHÁC NHAU (có thể rỗng).

    use_cv2: None = chỉ dùng cv2.QRCodeDetector khi thiếu pyzbar (cv2 chậm ~200 ms/ảnh 1280x960
    nên không chạy thêm sau pyzbar để giữ ngân sách thời gian 2.5); True = ép dùng cả cv2.
    """
    texts = _decode_pyzbar(gray) if HAVE_PYZBAR else []
    if not texts and (use_cv2 or (use_cv2 is None and not HAVE_PYZBAR)):
        texts = _decode_cv2(gray)
    # Cùng một mã có thể được phát hiện 2 lần (hai bộ dò) -> gộp theo nội dung
    uniq: list[str] = []
    for t in texts:
        if t not in uniq:
            uniq.append(t)
    return uniq


def enhance_contrast(gray: np.ndarray) -> np.ndarray:
    """CLAHE trên ảnh xám để cứu mã mờ/thiếu sáng."""
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    return clahe.apply(gray)


def read_qr(frames: Sequence[np.ndarray], use_cv2: bool | None = None) -> QrReadResult:
    """Thử từng khung (và bản tăng tương phản); dừng ngay khi đọc được.

    - 1 mã duy nhất     -> VALID + text
    - >1 mã khác nhau   -> MULTI_QR
    - không đọc được    -> NO_QR
    """
    tried = 0
    backend = "pyzbar" if HAVE_PYZBAR else "cv2"
    for frame in frames:
        tried += 1
        gray = _to_gray(frame)
        for variant in (gray, enhance_contrast(gray)):
            texts = decode_once(variant, use_cv2)
            if len(texts) > 1:
                log.warning("Phát hiện %d mã QR khác nhau trong ảnh", len(texts))
                return QrReadResult(QR_MULTI_QR, None, tried, backend)
            if len(texts) == 1:
                return QrReadResult(QR_VALID, texts[0], tried, backend)
    return QrReadResult(QR_NO_QR, None, tried, backend)
