"""O-07 — Tab Demo tấn công (nền vàng, "CHỈ DÙNG ĐỂ DEMO").

3 nút phá bản phát hành hiện tại + "Khôi phục bản hợp lệ". Sau mỗi thao tác chạy
verify_release_dir ngay tại chỗ để hiện reason mà ESP32 sẽ báo.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Optional

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
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

from .. import keys
from ..demo_attacks import DemoError, DemoResult, downgrade_release, forge_manifest, restore_valid, tamper_firmware
from ..manifest import verify_release_dir
from . import show_error

if TYPE_CHECKING:
    from ..main import AppContext

log = logging.getLogger(__name__)


class DemoTab(QWidget):
    release_modified = Signal()

    def __init__(self, ctx: "AppContext") -> None:
        super().__init__()
        self.ctx = ctx
        self.setAutoFillBackground(True)
        self.setStyleSheet("DemoTab { background-color: #fff3b0; } QGroupBox { background-color: #fff8d6; }")
        root = QVBoxLayout(self)

        banner = QLabel("CHỈ DÙNG ĐỂ DEMO — các nút dưới đây cố ý phá bản phát hành đang phục vụ để chứng minh thiết bị từ chối")
        banner.setStyleSheet("font-weight: bold; color: #7a4b00; font-size: 14px; padding: 6px; border: 2px solid #c98a00;")
        banner.setWordWrap(True)
        root.addWidget(banner)

        cfg = QGroupBox("Mục tiêu")
        form = QFormLayout(cfg)
        self.ed_dir = QLineEdit()
        form.addRow("Thư mục phát hành đang phục vụ", self.ed_dir)
        self.spin_device_vc = QSpinBox()
        self.spin_device_vc.setRange(1, 2_000_000_000)
        form.addRow("version_code thiết bị đang chạy (để hạ phiên bản)", self.spin_device_vc)
        self.ed_pass = QLineEdit()
        self.ed_pass.setEchoMode(QLineEdit.Password)
        form.addRow("Passphrase khóa ký (chỉ cần cho 'Hạ phiên bản')", self.ed_pass)
        root.addWidget(cfg)

        row = QHBoxLayout()
        self.btn_tamper = QPushButton("DEMO: Sửa 1 byte firmware\n(→ HASH_MISMATCH)")
        self.btn_forge = QPushButton("DEMO: Giả manifest\n(→ SIGNATURE_INVALID)")
        self.btn_down = QPushButton("DEMO: Hạ phiên bản\n(→ ROLLBACK_REJECTED)")
        self.btn_restore = QPushButton("Khôi phục bản hợp lệ")
        self.btn_tamper.clicked.connect(lambda: self._run(lambda d: tamper_firmware(d)))
        self.btn_forge.clicked.connect(lambda: self._run(lambda d: forge_manifest(d, "hash")))
        self.btn_down.clicked.connect(self._on_downgrade)
        self.btn_restore.clicked.connect(lambda: self._run(restore_valid))
        for b in (self.btn_tamper, self.btn_forge, self.btn_down):
            b.setStyleSheet("background-color: #ffd166; font-weight: bold;")
            row.addWidget(b)
        self.btn_restore.setStyleSheet("background-color: #b7e4c7; font-weight: bold;")
        row.addWidget(self.btn_restore)
        root.addLayout(row)

        self.out = QPlainTextEdit()
        self.out.setReadOnly(True)
        root.addWidget(self.out, 1)

        can = bool(ctx.session and ctx.session.can_release)
        for b in (self.btn_tamper, self.btn_forge, self.btn_down, self.btn_restore):
            b.setEnabled(can)
        self.on_release_changed()

    def on_release_changed(self, *_: object) -> None:
        cur = self.ctx.history.current_release()
        if cur is not None:
            self.ed_dir.setText(cur.dir)
            self.spin_device_vc.setValue(cur.version_code)

    def _dir(self) -> Optional[Path]:
        d = Path(self.ed_dir.text().strip()) if self.ed_dir.text().strip() else None
        if d is None or not d.is_dir():
            show_error(self, "Thiếu thư mục", "Chưa có thư mục phát hành (hãy phát hành một bản trước).")
            return None
        return d

    def _run(self, fn: Callable[[Path], DemoResult]) -> None:
        d = self._dir()
        if d is None:
            return
        try:
            res = fn(d)
        except DemoError as exc:
            show_error(self, "Demo thất bại", str(exc))
            return
        self._report(res, d)

    def _on_downgrade(self) -> None:
        d = self._dir()
        if d is None:
            return
        info = self.ctx.key_info
        if info is None:
            show_error(self, "Thiếu khóa", "Chưa có khóa ký.")
            return
        try:
            priv = keys.load_private_key(self.ctx.paths, self.ed_pass.text())
            res = downgrade_release(d, priv, info.key_id, self.spin_device_vc.value())
            del priv
        except (keys.KeyError_, DemoError) as exc:
            show_error(self, "Demo thất bại", str(exc))
            return
        finally:
            self.ed_pass.clear()
        self._report(res, d)

    def _report(self, res: DemoResult, d: Path) -> None:
        self.out.appendPlainText(str(res))
        info = self.ctx.key_info
        if info is not None:
            pub = keys.load_public_key(info.public_path)
            # Bước 1: chữ ký / manifest / kích thước / hash (không xét rollback)
            vr = verify_release_dir(d, pub)
            self.out.appendPlainText(f"  -> xác minh chữ ký+hash tại chỗ: {vr}")
            # Bước 2: chống rollback so với version_code thiết bị đang chạy
            if vr.ok:
                dev = self.spin_device_vc.value()
                rb = verify_release_dir(d, pub, min_version_code=dev)
                self.out.appendPlainText(f"  -> chống rollback (thiết bị đang chạy version_code={dev}): {rb}")
        self.out.appendPlainText("")
        self.release_modified.emit()
