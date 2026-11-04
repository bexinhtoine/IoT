"""O-02 — Tab Khóa: tạo cặp khóa ECDSA P-256, hiện key_id/fingerprint, xuất ota_pubkey.h.

Private key không bao giờ được hiển thị; chỉ hiển thị public key và đường dẫn file.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .. import keys
from . import confirm, show_error, show_info

if TYPE_CHECKING:
    from ..main import AppContext

log = logging.getLogger(__name__)


class KeysTab(QWidget):
    key_changed = Signal()

    def __init__(self, ctx: "AppContext") -> None:
        super().__init__()
        self.ctx = ctx
        root = QVBoxLayout(self)

        box = QGroupBox("Khóa ký hiện tại")
        form = QFormLayout(box)
        self.lbl_key_id = QLabel("-")
        self.lbl_fp = QLabel("-")
        self.lbl_fp.setWordWrap(True)
        self.lbl_fp.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.lbl_priv = QLabel("-")
        self.lbl_pub = QLabel("-")
        form.addRow("key_id (8 byte đầu SHA-256 DER)", self.lbl_key_id)
        form.addRow("Fingerprint SHA-256", self.lbl_fp)
        form.addRow("Private key (PEM mã hóa, 600)", self.lbl_priv)
        form.addRow("Public key PEM", self.lbl_pub)
        self.pub_view = QPlainTextEdit()
        self.pub_view.setReadOnly(True)
        self.pub_view.setMaximumHeight(120)
        form.addRow(self.pub_view)
        root.addWidget(box)

        gen = QGroupBox("Tạo cặp khóa mới (chỉ release_manager)")
        gform = QFormLayout(gen)
        self.ed_pass = QLineEdit()
        self.ed_pass.setEchoMode(QLineEdit.Password)
        self.ed_pass2 = QLineEdit()
        self.ed_pass2.setEchoMode(QLineEdit.Password)
        gform.addRow("Passphrase (≥ 8 ký tự)", self.ed_pass)
        gform.addRow("Nhập lại", self.ed_pass2)
        self.btn_gen = QPushButton("Tạo khóa ECDSA P-256")
        self.btn_gen.clicked.connect(self._on_generate)
        gform.addRow(self.btn_gen)
        root.addWidget(gen)

        exp = QHBoxLayout()
        self.btn_header = QPushButton("Xuất ota_pubkey.h vào repo (esp32/components/ota_secure/include)")
        self.btn_header.clicked.connect(lambda: self._on_export_header(choose=False))
        self.btn_header_as = QPushButton("Xuất ota_pubkey.h tới…")
        self.btn_header_as.clicked.connect(lambda: self._on_export_header(choose=True))
        self.btn_pem = QPushButton("Xuất ota_pubkey.pem tới…")
        self.btn_pem.clicked.connect(self._on_export_pem)
        exp.addWidget(self.btn_header)
        exp.addWidget(self.btn_header_as)
        exp.addWidget(self.btn_pem)
        root.addLayout(exp)
        self.msg = QLabel("")
        self.msg.setWordWrap(True)
        root.addWidget(self.msg)
        root.addStretch(1)

        s = ctx.session
        self.btn_gen.setEnabled(bool(s and s.can_release))
        self.refresh()

    def refresh(self) -> None:
        info = self.ctx.refresh_key_info()
        has = info is not None
        self.lbl_key_id.setText(info.key_id if info else "chưa có khóa")
        self.lbl_fp.setText(info.fingerprint if info else "-")
        self.lbl_priv.setText(str(info.private_path) if info else "-")
        self.lbl_pub.setText(str(info.public_path) if info else "-")
        self.pub_view.setPlainText(info.public_pem if info else "")
        for b in (self.btn_header, self.btn_header_as, self.btn_pem):
            b.setEnabled(has)
        default = self.ctx.paths.default_pubkey_header()
        self.btn_header.setEnabled(has and default is not None)
        if default is None:
            self.btn_header.setToolTip("Không tìm thấy repo; dùng 'Xuất tới…'")

    def _on_generate(self) -> None:
        if self.ed_pass.text() != self.ed_pass2.text():
            show_error(self, "Lỗi", "Hai passphrase không khớp.")
            return
        overwrite = False
        if self.ctx.key_info is not None:
            if not confirm(
                self, "Ghi đè khóa?",
                "Đã có khóa. Tạo khóa mới sẽ làm firmware đã nhúng public key cũ KHÔNG chấp nhận bản phát hành mới "
                "cho tới khi cập nhật ota_pubkey.h và nạp lại. Tiếp tục?",
            ):
                return
            overwrite = True
        try:
            info = keys.generate_keypair(self.ctx.paths, self.ed_pass.text(), overwrite=overwrite)
        except keys.KeyError_ as exc:
            show_error(self, "Không tạo được khóa", str(exc))
            return
        finally:
            self.ed_pass.clear()
            self.ed_pass2.clear()
        self.msg.setText(f"Đã tạo khóa key_id={info.key_id}. Hãy xuất ota_pubkey.h và build lại firmware.")
        self.refresh()
        self.key_changed.emit()

    def _on_export_header(self, choose: bool) -> None:
        info = self.ctx.key_info
        if info is None:
            return
        target = None
        if choose:
            path, _ = QFileDialog.getSaveFileName(self, "Xuất ota_pubkey.h", "ota_pubkey.h", "C header (*.h)")
            if not path:
                return
            target = Path(path)
        try:
            out = keys.export_pubkey_header(keys.load_public_key(info.public_path), target)
        except (keys.KeyError_, OSError) as exc:
            show_error(self, "Không xuất được", str(exc))
            return
        self.msg.setText(f"Đã xuất {out}")
        show_info(self, "Đã xuất", f"ota_pubkey.h ghi tại:\n{out}")

    def _on_export_pem(self) -> None:
        info = self.ctx.key_info
        if info is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Xuất ota_pubkey.pem", "ota_pubkey.pem", "PEM (*.pem)")
        if not path:
            return
        try:
            keys.export_pubkey_pem(keys.load_public_key(info.public_path), Path(path))
        except OSError as exc:
            show_error(self, "Không xuất được", str(exc))
            return
        self.msg.setText(f"Đã xuất {path}")
