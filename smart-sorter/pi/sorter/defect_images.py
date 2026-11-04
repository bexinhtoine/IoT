"""P-09 (ảnh lỗi) — Lưu ảnh hàng lỗi và tạo bản thu nhỏ gửi dashboard (4.3: defect_image).

- JPEG gốc: <defect_dir>/<ts_ms>_<pkg_id>.jpg
- Bản gửi lên ThingsBoard: 320x240, chất lượng 70, giảm dần từng bước 10 (tối thiểu 20) cho tới khi
  chuỗi base64 <= image_max_kb KB.
- Giữ tối đa max_images ảnh; vượt thì xóa ảnh cũ nhất.
"""
from __future__ import annotations

import base64
import logging
import re
import threading
import time
from pathlib import Path

import cv2
import numpy as np

log = logging.getLogger(__name__)

THUMB_SIZE = (320, 240)
START_QUALITY = 70
MIN_QUALITY = 20
QUALITY_STEP = 10
_SAFE_ID = re.compile(r"[^A-Za-z0-9_-]")


def make_thumbnail_b64(image_rgb: np.ndarray, max_kb: int) -> tuple[str, int]:
    """Trả (base64, chất lượng đã dùng). Ảnh vào RGB."""
    bgr = cv2.cvtColor(image_rgb, cv2.COLOR_RGB2BGR)
    small = cv2.resize(bgr, THUMB_SIZE, interpolation=cv2.INTER_AREA)
    q = START_QUALITY
    limit = int(max_kb) * 1024
    while True:
        ok, buf = cv2.imencode(".jpg", small, [int(cv2.IMWRITE_JPEG_QUALITY), q])
        if not ok:
            raise RuntimeError("cv2.imencode thất bại")
        b64 = base64.b64encode(buf.tobytes()).decode("ascii")
        if len(b64) <= limit or q <= MIN_QUALITY:
            if len(b64) > limit:
                log.warning("Ảnh lỗi vẫn %d B > %d KB ở chất lượng %d", len(b64), max_kb, q)
            return b64, q
        q -= QUALITY_STEP


class DefectImageStore:
    def __init__(self, defect_dir: str | Path, max_images: int = 500, image_max_kb: int = 40) -> None:
        self.dir = Path(defect_dir)
        self.max_images = max(1, int(max_images))
        self.image_max_kb = max(1, int(image_max_kb))
        self._lock = threading.Lock()
        self.dir.mkdir(parents=True, exist_ok=True)

    def save(self, image_rgb: np.ndarray, pkg_id: str | None, ts_ms: int | None = None) -> tuple[Path, str]:
        """Lưu JPEG gốc, trả (đường dẫn, base64 bản thu nhỏ)."""
        ts = ts_ms or int(time.time() * 1000)
        safe = _SAFE_ID.sub("_", pkg_id or "UNKNOWN")[:32]
        path = self.dir / f"{ts}_{safe}.jpg"
        bgr = cv2.cvtColor(image_rgb, cv2.COLOR_RGB2BGR)
        with self._lock:
            if not cv2.imwrite(str(path), bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 90]):
                raise RuntimeError(f"Không ghi được ảnh {path}")
            self._prune()
        b64, q = make_thumbnail_b64(image_rgb, self.image_max_kb)
        log.info("Lưu ảnh lỗi %s (thumb q=%d, %d B base64)", path.name, q, len(b64))
        return path, b64

    def _prune(self) -> int:
        files = sorted(self.dir.glob("*.jpg"), key=lambda p: (p.stat().st_mtime, p.name))
        removed = 0
        while len(files) > self.max_images:
            old = files.pop(0)
            try:
                old.unlink()
                removed += 1
            except OSError as e:
                log.warning("Không xóa được %s: %s", old, e)
        return removed

    def count(self) -> int:
        return len(list(self.dir.glob("*.jpg")))


def save_defect_image(
    image_rgb: np.ndarray, defect_dir: str | Path, pkg_id: str | None, image_max_kb: int = 40, max_images: int = 500
) -> tuple[Path, str]:
    """Hàm tiện ích một lần (không giữ trạng thái)."""
    return DefectImageStore(defect_dir, max_images, image_max_kb).save(image_rgb, pkg_id)
