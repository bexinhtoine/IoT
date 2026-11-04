"""P-02 / P-14 — Camera cho sorter-core.

- Picamera2Camera: camera CSI qua thư viện picamera2 (KHÔNG dùng cv2.VideoCapture/USB).
  Mở một lần, giữ suốt phiên; sau `lock_after_s` giây khóa phơi sáng (AE) và cân bằng trắng (AWB)
  để ảnh đồng đều giữa các lần chụp.
- FolderCamera: chế độ mô phỏng — đọc lần lượt ảnh trong thư mục (sorted), xoay vòng.

Mọi camera trả về numpy uint8 HxWx3 theo thứ tự kênh RGB.
"""
from __future__ import annotations

import logging
import threading
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

import numpy as np

from .config import CameraCfg

log = logging.getLogger(__name__)

IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".bmp")


class Camera(ABC):
    """Giao diện camera: capture() -> ảnh RGB uint8 HxWx3."""

    @abstractmethod
    def capture(self) -> np.ndarray:
        """Chụp một khung hình RGB. Ném exception nếu không chụp được (orchestrator sẽ fail-safe)."""

    def close(self) -> None:  # pragma: no cover - mặc định không làm gì
        return None

    def next_name(self) -> str | None:
        """Tên nguồn của khung hình sắp chụp (chỉ có ý nghĩa với FolderCamera, dùng cho DummyClassifier)."""
        return None

    def on_package_start(self) -> None:
        """Được orchestrator gọi đầu mỗi kiện; FolderCamera dùng để chuyển sang ảnh kế tiếp."""
        return None


class Picamera2Camera(Camera):
    """Camera CSI qua picamera2 (P-02).

    Lưu ý: định dạng "RGB888" của picamera2 trên nhiều phiên bản thực tế trả mảng theo thứ tự BGR
    (giống OpenCV). Mặc định `swap_rb=True` đảo kênh để đầu ra là RGB; khi lắp camera thật cần
    đối chiếu một ảnh chụp (ví dụ vật màu đỏ) và chỉnh `swap_rb` cho đúng.
    """

    def __init__(self, cfg: CameraCfg, swap_rb: bool = True) -> None:
        self._cfg = cfg
        self.swap_rb = swap_rb
        self._lock = threading.Lock()
        # Import lười để laptop (không có picamera2) vẫn import được package.
        try:
            from picamera2 import Picamera2  # type: ignore[import-not-found]
        except ImportError as e:  # pragma: no cover - chỉ xảy ra khi thiếu thư viện
            raise RuntimeError("Thiếu thư viện picamera2 (apt install python3-picamera2)") from e

        transform: Any = None
        try:
            from libcamera import Transform  # type: ignore[import-not-found]

            transform = Transform(hflip=1 if cfg.hflip else 0, vflip=1 if cfg.vflip else 0)
        except Exception:  # pragma: no cover - libcamera không có hoặc khác phiên bản
            log.warning("Không import được libcamera.Transform; bỏ qua hflip/vflip")

        self._cam = Picamera2()
        kwargs: dict[str, Any] = {"main": {"size": tuple(int(v) for v in cfg.resolution), "format": "RGB888"}}
        if transform is not None:
            kwargs["transform"] = transform
        config = self._cam.create_still_configuration(**kwargs)
        self._cam.configure(config)
        self._cam.start()
        log.info("Picamera2 đã mở %sx%s; chờ %.1fs rồi khóa AE/AWB", cfg.resolution[0], cfg.resolution[1], cfg.lock_after_s)
        time.sleep(max(0.0, float(cfg.lock_after_s)))
        self._lock_exposure()

    def _lock_exposure(self) -> None:
        """Đọc metadata hiện tại rồi cố định ExposureTime/AnalogueGain/ColourGains (P-02)."""
        try:
            md = self._cam.capture_metadata()
            controls: dict[str, Any] = {"AeEnable": False, "AwbEnable": False}
            for k in ("ExposureTime", "AnalogueGain", "ColourGains"):
                if k in md:
                    controls[k] = md[k]
            self._cam.set_controls(controls)
            log.info("Đã khóa AE/AWB: %s", {k: v for k, v in controls.items() if k not in ("AeEnable", "AwbEnable")})
        except Exception as e:  # pragma: no cover - phụ thuộc phần cứng
            log.warning("Không khóa được AE/AWB: %s", e)

    def capture(self) -> np.ndarray:
        with self._lock:
            arr = self._cam.capture_array("main")
        arr = np.ascontiguousarray(arr[:, :, :3])
        if self.swap_rb:
            arr = np.ascontiguousarray(arr[:, :, ::-1])
        return arr.astype(np.uint8, copy=False)

    def close(self) -> None:
        try:
            self._cam.stop()
            self._cam.close()
        except Exception as e:  # pragma: no cover
            log.warning("Lỗi khi đóng camera: %s", e)


class FolderCamera(Camera):
    """Camera mô phỏng (P-14): đọc ảnh jpg/png trong thư mục theo thứ tự tên, xoay vòng.

    Mỗi kiện (on_package_start) chuyển sang ảnh kế tiếp; capture() nhiều lần trong cùng một kiện
    trả cùng một ảnh (giống chụp nhiều khung của cùng một hộp).
    """

    def __init__(self, folder: str | Path) -> None:
        self.folder = Path(folder)
        self._files: list[Path] = sorted(p for p in self.folder.iterdir() if p.suffix.lower() in IMAGE_EXTS) if self.folder.is_dir() else []
        if not self._files:
            raise RuntimeError(f"Thư mục ảnh rỗng hoặc không tồn tại: {self.folder} (chạy tests/fixtures/make_images.py)")
        self._idx = 0
        self._started = False
        self._lock = threading.Lock()
        log.info("FolderCamera: %d ảnh trong %s", len(self._files), self.folder)

    @property
    def files(self) -> list[Path]:
        return list(self._files)

    def __len__(self) -> int:
        return len(self._files)

    def on_package_start(self) -> None:
        with self._lock:
            if self._started:
                self._idx = (self._idx + 1) % len(self._files)
            self._started = True

    def advance(self) -> None:
        """Chuyển sang ảnh kế tiếp (tương đương on_package_start)."""
        self.on_package_start()

    def next_name(self) -> str | None:
        with self._lock:
            return self._files[self._idx].name

    def capture(self) -> np.ndarray:
        import cv2

        with self._lock:
            path = self._files[self._idx]
        bgr = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if bgr is None:
            raise RuntimeError(f"Không đọc được ảnh {path}")
        return np.ascontiguousarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
