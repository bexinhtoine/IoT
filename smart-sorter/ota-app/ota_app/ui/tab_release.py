"""O-03/O-04 — Tab Phát hành: chọn .bin, hiện FirmwareInfo, nhập version, ký & phát hành."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..firmware import FirmwareInfo, inspect_firmware
from ..main import ReleaseError
from . import show_error, show_info

if TYPE_CHECKING:
    from ..main import AppContext

log = logging.getLogger(__name__)


class ReleaseTab(QWidget):
    released = Signal(int)  # version_code vừa phát hành

    def __init__(self, ctx: "AppContext") -> None:
        super().__init__()
        self.ctx = ctx
        self.fw_info: Optional[FirmwareInfo] = None
        root = QVBoxLayout(self)

        # --- chọn firmware ---
        row = QHBoxLayout()
        self.fw_path = QLineEdit()
        self.fw_path.setReadOnly(True)
        btn_browse = QPushButton("Chọn firmware .bin…")
        btn_browse.clicked.connect(self._on_browse)
        row.addWidget(self.fw_path)
        row.addWidget(btn_browse)
        root.addLayout(row)

        # --- thông tin firmware ---
        info_box = QGroupBox("Thông tin firmware (O-03)")
        info_form = QFormLayout(info_box)
        self.lbl_valid = QLabel("-")
        self.lbl_size = QLabel("-")
        self.lbl_sha = QLabel("-")
        self.lbl_sha.setTextInteractionFlags(Qt.TextSelectableByMouse)  # cho phép chọn copy
        self.lbl_app_ver = QLabel("-")
        self.lbl_project = QLabel("-")
        self.lbl_idf = QLabel("-")
        self.lbl_sb = QLabel("-")
        for name, w in [
            ("Ảnh ESP32 hợp lệ (0xE9)", self.lbl_valid), ("Kích thước", self.lbl_size), ("SHA-256", self.lbl_sha),
            ("Phiên bản trong app_desc", self.lbl_app_ver), ("Project", self.lbl_project),
            ("ESP-IDF", self.lbl_idf), ("Khối chữ ký Secure Boot V2", self.lbl_sb),
        ]:
            info_form.addRow(name, w)
        root.addWidget(info_box)

        # --- tham số phát hành ---
        rel_box = QGroupBox("Phát hành (O-04)")
        rel_form = QFormLayout(rel_box)
        self.spin_vc = QSpinBox()
        self.spin_vc.setRange(1, 2_000_000_000)
        self.lbl_max_vc = QLabel()
        vc_row = QHBoxLayout()
        vc_row.addWidget(self.spin_vc)
        vc_row.addWidget(self.lbl_max_vc)
        self.ed_version_name = QLineEdit()
        self.ed_version_name.setMaxLength(32)
        self.chk_secure_boot = QCheckBox("Thiết bị đã bật Secure Boot (cảnh báo nếu .bin không có khối chữ ký)")
        self.ed_passphrase = QLineEdit()
        self.ed_passphrase.setEchoMode(QLineEdit.Password)
        self.ed_note = QLineEdit()
        self.lbl_key = QLabel()
        rel_form.addRow("version_code", vc_row)
        rel_form.addRow("version_name", self.ed_version_name)
        rel_form.addRow(self.chk_secure_boot)
        rel_form.addRow("Passphrase khóa ký", self.ed_passphrase)
        rel_form.addRow("Ghi chú", self.ed_note)
        rel_form.addRow("Khóa ký", self.lbl_key)
        self.btn_sign = QPushButton("Ký && phát hành")
        self.btn_sign.clicked.connect(self._on_sign)
        rel_form.addRow(self.btn_sign)
        root.addWidget(rel_box)

        self.out = QPlainTextEdit()
        self.out.setReadOnly(True)
        root.addWidget(self.out, 1)

        self.refresh_key()
        self._refresh_version_bounds()
        self._update_sign_enabled()

    # ---------------- helpers ----------------
    def refresh_key(self) -> None:
        self.ctx.refresh_key_info()
        info = self.ctx.key_info
        self.lbl_key.setText(f"key_id {info.key_id}" if info else "CHƯA CÓ KHÓA — tạo ở tab Khóa")
        self._update_sign_enabled()

    def _refresh_version_bounds(self) -> None:
        max_vc = self.ctx.history.max_version_code()
        self.spin_vc.setMinimum(max_vc + 1)
        if self.spin_vc.value() <= max_vc:
            self.spin_vc.setValue(max_vc + 1)
        self.lbl_max_vc.setText(f"(bản gần nhất: {max_vc}; phải > {max_vc})")

    def _update_sign_enabled(self) -> None:
        s = self.ctx.session
        can = bool(s and s.can_release and self.ctx.key_info and self.fw_info and self.fw_info.is_valid_image)
        self.btn_sign.setEnabled(can)
        if s and not s.can_release:
            self.btn_sign.setToolTip("Chỉ vai trò release_manager được ký và phát hành")

    def _on_browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Chọn firmware", "", "Firmware ESP32 (*.bin);;Mọi file (*)")
        if path:
            self.load_firmware(Path(path))

    def load_firmware(self, path: Path) -> None:
        try:
            info = inspect_firmware(path)
        except OSError as exc:
            show_error(self, "Không đọc được file", str(exc))
            return
        self.fw_info = info
        self.fw_path.setText(str(path))
        self.lbl_valid.setText("Có" if info.is_valid_image else "KHÔNG — không phải ảnh app ESP32")
        self.lbl_size.setText(f"{info.size:,} byte")
        self.lbl_sha.setText(info.sha256_hex)
        self.lbl_app_ver.setText(info.app_version or "(không đọc được — nhập tay version_name)")
        self.lbl_project.setText(info.project_name or "-")
        self.lbl_idf.setText(info.idf_version or "-")
        self.lbl_sb.setText("Có" if info.has_secure_boot_v2_block else "Không thấy")
        if info.app_version and not self.ed_version_name.text():
            self.ed_version_name.setText(info.app_version)
        for p in info.problems:
            self.out.appendPlainText(f"CẢNH BÁO: {p}")
        self._refresh_version_bounds()
        self._update_sign_enabled()

    def _on_sign(self) -> None:
        if self.fw_info is None:
            return
        vc = self.spin_vc.value()
        name = self.ed_version_name.text().strip()
        if not name:
            show_error(self, "Thiếu thông tin", "Nhập version_name.")
            return
        if self.chk_secure_boot.isChecked() and not self.fw_info.has_secure_boot_v2_block:
            show_error(
                self, "Cảnh báo Secure Boot",
                "Bạn đánh dấu thiết bị đã bật Secure Boot nhưng firmware không có khối chữ ký Secure Boot V2 "
                "(byte 0xE7 ở sector 4 KB cuối). Thiết bị sẽ từ chối ảnh này khi khởi động. Không phát hành.",
            )
            return
        try:
            files = self.ctx.publish_release(
                Path(self.fw_path.text()), vc, name, self.ed_passphrase.text(),
                note=self.ed_note.text().strip(), fw_info=self.fw_info,
                require_secure_boot_block=self.chk_secure_boot.isChecked(),
            )
        except ReleaseError as exc:
            show_error(self, "Không phát hành được", str(exc))
            return
        except Exception as exc:  # noqa: BLE001 - hiển thị mọi lỗi cho người dùng
            log.exception("Lỗi phát hành")
            show_error(self, "Lỗi", str(exc))
            return
        finally:
            self.ed_passphrase.clear()
        self.out.appendPlainText(
            f"ĐÃ PHÁT HÀNH version_code={vc} ({name}) tại {files.release_dir}\n"
            f"  manifest: {files.manifest_bytes.decode('utf-8')}\n  đã tự xác minh lại bằng public key: OK"
        )
        self._refresh_version_bounds()
        show_info(self, "Thành công", f"Đã ký và phát hành version_code={vc}. Bật server ở tab Máy chủ để thiết bị tải.")
        self.released.emit(vc)
