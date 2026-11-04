"""O-01 — Cửa sổ đăng nhập: tạo tài khoản quản trị lần đầu, đăng nhập, ô TOTP."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFormLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..auth import MIN_PASSWORD_LEN, AuthError
from . import show_error, show_info

if TYPE_CHECKING:
    from ..main import AppContext

log = logging.getLogger(__name__)

ERROR_TEXT = {
    "WRONG_PASSWORD": "Sai tên đăng nhập hoặc mật khẩu.",
    "LOCKED": "Tài khoản bị khóa tạm thời sau nhiều lần sai. Thử lại sau {remaining}.",
    "TOTP_REQUIRED": "Tài khoản này yêu cầu mã TOTP. Nhập mã 6 số từ app xác thực.",
    "TOTP_INVALID": "Mã TOTP không đúng.",
}


class LoginWindow(QWidget):
    logged_in = Signal()

    def __init__(self, ctx: "AppContext") -> None:
        super().__init__()
        self.ctx = ctx
        self.setWindowTitle("Smart Sorter OTA — Đăng nhập")
        self.setMinimumWidth(380)

        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_first_run())
        self.stack.addWidget(self._build_login())
        layout = QVBoxLayout(self)
        layout.addWidget(self.stack)
        self.stack.setCurrentIndex(0 if not ctx.auth.has_users() else 1)

    # ---------------- lần chạy đầu ----------------
    def _build_first_run(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        form.addRow(QLabel("<b>Lần chạy đầu:</b> tạo tài khoản quản trị (release_manager)"))
        self.fr_user = QLineEdit()
        self.fr_pw = QLineEdit()
        self.fr_pw.setEchoMode(QLineEdit.Password)
        self.fr_pw2 = QLineEdit()
        self.fr_pw2.setEchoMode(QLineEdit.Password)
        form.addRow("Tên đăng nhập", self.fr_user)
        form.addRow(f"Mật khẩu (≥ {MIN_PASSWORD_LEN} ký tự)", self.fr_pw)
        form.addRow("Nhập lại mật khẩu", self.fr_pw2)
        btn = QPushButton("Tạo tài khoản quản trị")
        btn.clicked.connect(self._on_create_admin)
        form.addRow(btn)
        return w

    def _on_create_admin(self) -> None:
        if self.fr_pw.text() != self.fr_pw2.text():
            show_error(self, "Lỗi", "Hai mật khẩu không khớp.")
            return
        try:
            self.ctx.auth.create_admin_first_run(self.fr_user.text(), self.fr_pw.text())
        except AuthError as exc:
            show_error(self, "Không tạo được tài khoản", str(exc))
            return
        show_info(self, "Đã tạo", "Tài khoản quản trị đã tạo. Hãy đăng nhập.")
        self.lg_user.setText(self.fr_user.text().strip())
        self.fr_pw.clear()
        self.fr_pw2.clear()
        self.stack.setCurrentIndex(1)

    # ---------------- đăng nhập ----------------
    def _build_login(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        form.addRow(QLabel("<b>Đăng nhập</b>"))
        self.lg_user = QLineEdit()
        self.lg_pw = QLineEdit()
        self.lg_pw.setEchoMode(QLineEdit.Password)
        self.lg_totp = QLineEdit()
        self.lg_totp.setPlaceholderText("chỉ khi đã bật TOTP")
        self.lg_totp.setMaxLength(8)
        form.addRow("Tên đăng nhập", self.lg_user)
        form.addRow("Mật khẩu", self.lg_pw)
        form.addRow("Mã TOTP", self.lg_totp)
        self.lg_msg = QLabel("")
        self.lg_msg.setStyleSheet("color: #b00020")
        self.lg_msg.setWordWrap(True)
        form.addRow(self.lg_msg)
        btn = QPushButton("Đăng nhập")
        btn.setDefault(True)
        btn.clicked.connect(self._on_login)
        self.lg_pw.returnPressed.connect(self._on_login)
        self.lg_totp.returnPressed.connect(self._on_login)
        form.addRow(btn)
        return w

    def _on_login(self) -> None:
        res = self.ctx.auth.login(self.lg_user.text().strip(), self.lg_pw.text(), self.lg_totp.text() or None)
        if not res.ok:
            remaining = f"{int(res.locked_remaining // 60)} phút {int(res.locked_remaining % 60)} giây"
            self.lg_msg.setText(ERROR_TEXT.get(res.error or "", "Đăng nhập thất bại").format(remaining=remaining))
            if res.error != "TOTP_REQUIRED":
                self.lg_pw.clear()
            return
        self.lg_pw.clear()
        self.lg_totp.clear()
        self.lg_msg.clear()
        self.ctx.session = res.session
        self.logged_in.emit()
