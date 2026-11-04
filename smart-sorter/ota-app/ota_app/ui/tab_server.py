"""O-05 — Tab Máy chủ: chọn thư mục phát hành + cert/key, Start/Stop, URL, log request.

Callback ``on_request`` của OtaServer chạy ở thread server -> chuyển sang thread Qt bằng Signal.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..server import DEFAULT_PORT, RequestLogEntry, ServerError, local_urls
from . import show_error

if TYPE_CHECKING:
    from ..main import AppContext

log = logging.getLogger(__name__)
CERTS_DIR = Path(__file__).resolve().parent.parent.parent / "certs"


class _Bridge(QObject):
    request = Signal(object)  # RequestLogEntry


class ServerTab(QWidget):
    report_received = Signal()

    def __init__(self, ctx: "AppContext") -> None:
        super().__init__()
        self.ctx = ctx
        self._bridge = _Bridge()
        self._bridge.request.connect(self._on_request_ui)
        ctx.server.on_request = self._bridge.request.emit  # gọi từ thread server, an toàn qua queued signal

        root = QVBoxLayout(self)
        box = QGroupBox("Cấu hình HTTPS (cert do CA nội bộ ký — xem certs/README.md)")
        form = QFormLayout(box)
        self.ed_dir = self._path_row(form, "Thư mục phát hành", self._pick_dir)
        self.ed_cert = self._path_row(form, "Cert máy chủ (ota_server.pem)", lambda: self._pick_file(self.ed_cert, "PEM (*.pem *.crt)"))
        self.ed_key = self._path_row(form, "Private key (ota_server.key)", lambda: self._pick_file(self.ed_key, "Key (*.key *.pem)"))
        self.ed_host = QLineEdit("0.0.0.0")
        self.spin_port = QSpinBox()
        self.spin_port.setRange(1, 65535)
        self.spin_port.setValue(DEFAULT_PORT)
        form.addRow("Host", self.ed_host)
        form.addRow("Cổng", self.spin_port)
        btns = QHBoxLayout()
        self.btn_start = QPushButton("Start")
        self.btn_stop = QPushButton("Stop")
        self.btn_start.clicked.connect(self._on_start)
        self.btn_stop.clicked.connect(self._on_stop)
        btns.addWidget(self.btn_start)
        btns.addWidget(self.btn_stop)
        form.addRow(btns)
        root.addWidget(box)

        self.urls = QPlainTextEdit()
        self.urls.setReadOnly(True)
        self.urls.setMaximumHeight(90)
        self.urls.setPlaceholderText("URL sẽ hiện ở đây khi server chạy")
        root.addWidget(self.urls)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setPlaceholderText("Log request: thời gian, IP, method, route, mã trả về")
        root.addWidget(self.log_view, 1)

        # giá trị mặc định
        for ed, name in ((self.ed_cert, "ota_server.pem"), (self.ed_key, "ota_server.key")):
            p = CERTS_DIR / name
            if p.is_file():
                ed.setText(str(p))
        self.on_release_changed()
        self._update_buttons()

    def _path_row(self, form: QFormLayout, label: str, on_pick) -> QLineEdit:
        row = QHBoxLayout()
        ed = QLineEdit()
        btn = QPushButton("…")
        btn.setMaximumWidth(32)
        btn.clicked.connect(on_pick)
        row.addWidget(ed, 1)
        row.addWidget(btn)
        form.addRow(label, row)
        return ed

    def _pick_dir(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Thư mục phát hành", self.ed_dir.text() or str(self.ctx.paths.releases_dir))
        if d:
            self.ed_dir.setText(d)

    def _pick_file(self, ed: QLineEdit, flt: str) -> None:
        p, _ = QFileDialog.getOpenFileName(self, "Chọn file", str(CERTS_DIR), flt)
        if p:
            ed.setText(p)

    def on_release_changed(self, *_: object) -> None:
        """Sau khi phát hành: trỏ thư mục mặc định tới bản hiện tại (nếu server chưa chạy)."""
        if self.ctx.server.running:
            return
        cur = self.ctx.history.current_release()
        if cur is not None:
            self.ed_dir.setText(cur.dir)

    def _update_buttons(self) -> None:
        running = self.ctx.server.running
        self.btn_start.setEnabled(not running)
        self.btn_stop.setEnabled(running)
        for w in (self.ed_dir, self.ed_cert, self.ed_key, self.ed_host, self.spin_port):
            w.setEnabled(not running)

    def _on_start(self) -> None:
        try:
            self.ctx.server.start(
                Path(self.ed_dir.text()), Path(self.ed_cert.text()), Path(self.ed_key.text()),
                host=self.ed_host.text().strip() or "0.0.0.0", port=self.spin_port.value(),
            )
        except (ServerError, OSError, ValueError) as exc:
            show_error(self, "Không khởi động được server", str(exc))
            return
        port = self.ctx.server.port
        urls = local_urls(port) if self.ed_host.text().strip() in ("", "0.0.0.0") else [f"https://{self.ed_host.text().strip()}:{port}/ota/"]
        self.urls.setPlainText("\n".join(u + "manifest.json" for u in urls))
        self.log_view.appendPlainText(f"--- server chạy, thư mục {self.ed_dir.text()} ---")
        self._update_buttons()

    def _on_stop(self) -> None:
        self.ctx.server.stop()
        self.log_view.appendPlainText("--- server dừng ---")
        self.urls.clear()
        self._update_buttons()

    def _on_request_ui(self, entry: RequestLogEntry) -> None:
        self.log_view.appendPlainText(str(entry))
        if entry.route == "/ota/report" and entry.status == 200:
            self.report_received.emit()
