"""Cửa sổ chính dạng tab: Phát hành, Lịch sử, Khóa, Máy chủ, Demo (O-02 -> O-07).

QTimer kiểm tra phiên mỗi 10 s; hết 15 phút không thao tác (O-01) -> phát tín hiệu
``session_expired`` và đóng cửa sổ, main.py mở lại LoginWindow. Mọi sự kiện chuột/bàn phím
trong app được coi là "thao tác" (event filter trên QApplication).
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import QEvent, QObject, QTimer, Signal
from PySide6.QtWidgets import QApplication, QLabel, QMainWindow, QTabWidget

from .tab_demo import DemoTab
from .tab_history import HistoryTab
from .tab_keys import KeysTab
from .tab_release import ReleaseTab
from .tab_server import ServerTab

if TYPE_CHECKING:
    from ..main import AppContext

log = logging.getLogger(__name__)

SESSION_CHECK_MS = 10_000
ACTIVITY_EVENTS = {
    QEvent.MouseButtonPress, QEvent.MouseButtonRelease, QEvent.KeyPress, QEvent.KeyRelease, QEvent.Wheel,
}


class MainWindow(QMainWindow):
    session_expired = Signal()

    def __init__(self, ctx: "AppContext") -> None:
        super().__init__()
        self.ctx = ctx
        session = ctx.session
        assert session is not None
        self.setWindowTitle(f"Smart Sorter OTA — {session.username} ({session.role})")
        self.resize(900, 640)

        self.tabs = QTabWidget()
        self.tab_release = ReleaseTab(ctx)
        self.tab_history = HistoryTab(ctx)
        self.tab_keys = KeysTab(ctx)
        self.tab_server = ServerTab(ctx)
        self.tab_demo = DemoTab(ctx)
        self.tabs.addTab(self.tab_release, "Phát hành")
        self.tabs.addTab(self.tab_history, "Lịch sử")
        self.tabs.addTab(self.tab_keys, "Khóa")
        self.tabs.addTab(self.tab_server, "Máy chủ")
        self.tabs.addTab(self.tab_demo, "Demo")
        self.setCentralWidget(self.tabs)

        # liên kết giữa các tab
        self.tab_release.released.connect(self.tab_history.refresh)
        self.tab_release.released.connect(self.tab_server.on_release_changed)
        self.tab_release.released.connect(self.tab_demo.on_release_changed)
        self.tab_keys.key_changed.connect(self.tab_release.refresh_key)
        self.tab_server.report_received.connect(self.tab_history.refresh)
        self.tab_demo.release_modified.connect(self.tab_history.refresh)

        self.status_label = QLabel()
        self.statusBar().addPermanentWidget(self.status_label)
        self._update_status()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._check_session)
        self._timer.start(SESSION_CHECK_MS)
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)
        self._expired = False

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802 - API Qt
        if event.type() in ACTIVITY_EVENTS and self.ctx.session is not None:
            self.ctx.session.touch()
        return super().eventFilter(obj, event)

    def _update_status(self) -> None:
        s = self.ctx.session
        if s is None:
            return
        idle = int(s.idle_seconds())
        srv = "đang chạy" if self.ctx.server.running else "dừng"
        self.status_label.setText(f"{s.username} · {s.role} · rảnh {idle}s · server {srv}")

    def _check_session(self) -> None:
        s = self.ctx.session
        self._update_status()
        if s is None or s.is_expired():
            self._expire()

    def _expire(self) -> None:
        if self._expired:
            return
        self._expired = True
        log.info("Phiên hết hạn sau 15 phút không thao tác -> quay về đăng nhập")
        self._timer.stop()
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        self.ctx.session = None
        self.session_expired.emit()
        self.close()

    def closeEvent(self, event) -> None:  # noqa: N802 - API Qt
        self._timer.stop()
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        super().closeEvent(event)
