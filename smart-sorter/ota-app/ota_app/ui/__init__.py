"""Giao diện PySide6 của app Secure OTA (O-01 -> O-07). Toàn bộ logic nằm ở ``ota_app.*``;
các module ở đây chỉ gọi vào logic và hiển thị, nên test không cần Qt.
"""
from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QMessageBox, QWidget


def show_error(parent: QWidget | None, title: str, text: str) -> None:
    QMessageBox.critical(parent, title, text)


def show_info(parent: QWidget | None, title: str, text: str) -> None:
    QMessageBox.information(parent, title, text)


def confirm(parent: QWidget | None, title: str, text: str) -> bool:
    return QMessageBox.question(parent, title, text, QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes


def fmt_ts(ts: Any) -> str:
    import time

    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(ts)))
    except (TypeError, ValueError):
        return str(ts)
