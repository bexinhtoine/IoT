"""Điểm vào app Secure OTA: ``python -m ota_app.main`` (MODULE P7, O-01 -> O-07).

``AppContext`` gom toàn bộ dịch vụ (paths, auth, history, server) và luồng phát hành
(O-03/O-04/O-06) KHÔNG phụ thuộc Qt để test được; ``main()`` mới import PySide6.
"""
from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from . import keys
from .auth import AuthStore, Session
from .firmware import FirmwareInfo, inspect_firmware
from .history import HistoryStore
from .manifest import ReleaseFiles, write_release
from .paths import AppPaths
from .server import OtaServer

log = logging.getLogger(__name__)


class ReleaseError(Exception):
    """Không được phép phát hành (vai trò, version, khóa, firmware...)."""


@dataclass
class AppContext:
    """Các dịch vụ dùng chung giữa các cửa sổ/tab."""

    paths: AppPaths
    auth: AuthStore
    history: HistoryStore
    server: OtaServer
    session: Optional[Session] = None
    key_info: Optional[keys.KeyInfo] = field(default=None)

    @classmethod
    def create(cls, base_dir: Optional[Path] = None) -> "AppContext":
        paths = AppPaths(base_dir) if base_dir else AppPaths()
        paths.ensure()
        auth = AuthStore(paths.db_path)
        history = HistoryStore(paths.db_path)
        ctx = cls(paths=paths, auth=auth, history=history, server=OtaServer(history=history))
        ctx.refresh_key_info()
        return ctx

    def refresh_key_info(self) -> Optional[keys.KeyInfo]:
        self.key_info = keys.current_key_info(self.paths)
        return self.key_info

    def close(self) -> None:
        try:
            self.server.stop()
        finally:
            self.auth.close()
            self.history.close()

    # ---------------- O-03/O-04/O-06: luồng ký & phát hành ----------------
    def check_can_release(self, version_code: int) -> None:
        """Kiểm tra điều kiện trước khi ký: phiên hợp lệ, vai trò, version tăng, có khóa."""
        if self.session is None or self.session.is_expired():
            raise ReleaseError("Phiên đăng nhập không hợp lệ hoặc đã hết hạn")
        if not self.session.can_release:
            raise ReleaseError("Chỉ vai trò release_manager mới được ký và phát hành")
        max_vc = self.history.max_version_code()
        if version_code <= max_vc:
            raise ReleaseError(f"version_code phải lớn hơn bản phát hành gần nhất ({max_vc})")
        if self.key_info is None:
            raise ReleaseError("Chưa có khóa ký; hãy tạo khóa ở tab Khóa")

    def publish_release(
        self,
        fw_path: Path | str,
        version_code: int,
        version_name: str,
        passphrase: str,
        note: str = "",
        fw_info: Optional[FirmwareInfo] = None,
        require_secure_boot_block: bool = False,
    ) -> ReleaseFiles:
        """Ký và phát hành: kiểm tra -> giải mã khóa -> ghi releases/<vc>/ -> tự xác minh -> lưu lịch sử."""
        self.check_can_release(version_code)
        assert self.session is not None and self.key_info is not None
        info = fw_info or inspect_firmware(fw_path)
        if not info.is_valid_image:
            raise ReleaseError("File không phải ảnh app ESP32 hợp lệ (byte đầu phải là 0xE9)")
        if require_secure_boot_block and not info.has_secure_boot_v2_block:
            raise ReleaseError("Thiết bị đã bật Secure Boot nhưng firmware không có khối chữ ký Secure Boot V2")
        try:
            private_key = keys.load_private_key(self.paths, passphrase)
        except keys.KeyError_ as exc:
            raise ReleaseError(str(exc)) from exc
        if keys.key_id_of(private_key.public_key()) != self.key_info.key_id:
            raise ReleaseError("Private key không khớp public key hiện tại")

        rel_dir = self.paths.release_dir(version_code)
        files = write_release(
            fw_path, info, version_code, version_name, private_key, self.key_info.key_id, rel_dir,
        )
        del private_key
        self.history.add_release(
            version_code, version_name, info.sha256_hex, info.size, self.key_info.key_id,
            self.session.username, rel_dir, note=note, make_current=True,
        )
        self.session.touch()
        log.info("Phát hành thành công version_code=%d bởi %s", version_code, self.session.username)
        return files


def setup_logging(verbose: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="ota_app", description="App Secure OTA — Smart Logistics Sorter")
    parser.add_argument("--data-dir", type=Path, default=None, help="thư mục dữ liệu (mặc định theo HĐH)")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    setup_logging(args.verbose)

    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:
        log.error("Thiếu PySide6: pip install PySide6")
        return 2
    from .ui.login_window import LoginWindow
    from .ui.main_window import MainWindow

    ctx = AppContext.create(args.data_dir)
    app = QApplication(sys.argv[:1])
    app.setApplicationName("Smart Sorter OTA")

    state: dict[str, object] = {}

    def show_login() -> None:
        ctx.session = None
        win = LoginWindow(ctx)
        win.logged_in.connect(show_main)
        win.show()
        state["login"] = win

    def show_main() -> None:
        main_win = MainWindow(ctx)
        main_win.session_expired.connect(show_login)
        main_win.show()
        state["main"] = main_win
        login = state.pop("login", None)
        if login is not None:
            login.close()  # type: ignore[attr-defined]

    show_login()
    try:
        return app.exec()
    finally:
        ctx.close()


if __name__ == "__main__":
    sys.exit(main())
