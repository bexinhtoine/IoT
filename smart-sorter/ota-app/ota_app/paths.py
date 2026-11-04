"""Đường dẫn dữ liệu người dùng của app Secure OTA (dùng chung cho O-01 -> O-07).

- Thư mục gốc: biến môi trường ``SMART_SORTER_OTA_HOME`` (ưu tiên, dùng cho test),
  nếu không có thì ``%APPDATA%\\smart-sorter-ota`` trên Windows hoặc
  ``~/.smart-sorter-ota`` trên POSIX.
- Bên trong: ``ota_app.db`` (SQLite), ``keys/`` (quyền 700), ``releases/<version_code>/``.
- Không bao giờ ghi bí mật ra ngoài thư mục này.
"""
from __future__ import annotations

import logging
import os
import stat
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

ENV_HOME = "SMART_SORTER_OTA_HOME"
DB_NAME = "ota_app.db"
KEYS_DIRNAME = "keys"
RELEASES_DIRNAME = "releases"
PRIVATE_KEY_NAME = "ota_private.pem"
PUBLIC_KEY_NAME = "ota_pubkey.pem"
PUBKEY_HEADER_REL = Path("esp32") / "components" / "ota_secure" / "include" / "ota_pubkey.h"


def default_base_dir() -> Path:
    """Thư mục dữ liệu mặc định theo hệ điều hành (có thể ghi đè bằng biến môi trường)."""
    env = os.environ.get(ENV_HOME)
    if env:
        return Path(env).expanduser()
    if sys.platform.startswith("win"):
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / "smart-sorter-ota"
    return Path.home() / ".smart-sorter-ota"


def restrict_dir(path: Path) -> None:
    """Đặt quyền 700 cho thư mục (chỉ có tác dụng thật trên POSIX; Windows bỏ qua)."""
    if os.name == "posix":
        try:
            os.chmod(path, stat.S_IRWXU)
        except OSError as exc:  # pragma: no cover - phụ thuộc hệ thống file
            log.warning("Không đặt được quyền 700 cho %s: %s", path, exc)


def restrict_file(path: Path) -> None:
    """Đặt quyền 600 cho file (POSIX). Trên Windows chmod chỉ ảnh hưởng cờ read-only."""
    if os.name == "posix":
        try:
            os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
        except OSError as exc:  # pragma: no cover
            log.warning("Không đặt được quyền 600 cho %s: %s", path, exc)


def find_repo_root(start: Optional[Path] = None) -> Optional[Path]:
    """Tìm thư mục gốc monorepo (có docs/SPEC.md và esp32/) đi lên từ ``start``.

    Dùng để xuất ``ota_pubkey.h`` thẳng vào ``esp32/components/ota_secure/include/``.
    Trả về None nếu không tìm thấy (app chạy ngoài repo).
    """
    cur = (start or Path(__file__).resolve()).resolve()
    for cand in [cur, *cur.parents]:
        if (cand / "esp32").is_dir() and (cand / "docs" / "SPEC.md").is_file():
            return cand
    return None


@dataclass
class AppPaths:
    """Tập đường dẫn của một phiên app; ``base`` có thể trỏ vào tmp_path khi test."""

    base: Path = field(default_factory=default_base_dir)

    @property
    def db_path(self) -> Path:
        return self.base / DB_NAME

    @property
    def keys_dir(self) -> Path:
        return self.base / KEYS_DIRNAME

    @property
    def private_key_path(self) -> Path:
        return self.keys_dir / PRIVATE_KEY_NAME

    @property
    def public_key_path(self) -> Path:
        return self.keys_dir / PUBLIC_KEY_NAME

    @property
    def releases_dir(self) -> Path:
        return self.base / RELEASES_DIRNAME

    def release_dir(self, version_code: int) -> Path:
        """Thư mục chứa một bản phát hành: releases/<version_code>/."""
        return self.releases_dir / str(int(version_code))

    def default_pubkey_header(self) -> Optional[Path]:
        """Đường dẫn mặc định của ota_pubkey.h trong repo (None nếu không ở trong repo)."""
        root = find_repo_root()
        return (root / PUBKEY_HEADER_REL) if root else None

    def ensure(self) -> "AppPaths":
        """Tạo cây thư mục nếu chưa có và siết quyền keys/ (700)."""
        self.base.mkdir(parents=True, exist_ok=True)
        restrict_dir(self.base)
        self.keys_dir.mkdir(parents=True, exist_ok=True)
        restrict_dir(self.keys_dir)
        self.releases_dir.mkdir(parents=True, exist_ok=True)
        log.debug("Thư mục dữ liệu OTA: %s", self.base)
        return self
