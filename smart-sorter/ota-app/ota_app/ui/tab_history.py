"""O-06 — Tab Lịch sử: bảng phát hành + bảng báo cáo thiết bị, lọc, xuất CSV."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Sequence

from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import fmt_ts, show_error, show_info

if TYPE_CHECKING:
    from ..main import AppContext

log = logging.getLogger(__name__)

RELEASE_COLS = ("id", "version_code", "version_name", "fw_sha256", "fw_size", "key_id", "signed_by", "signed_at", "hiện tại", "note", "dir")
REPORT_COLS = ("id", "thời gian", "device", "version_code", "result", "reason", "remote_ip")


def _fill(table: QTableWidget, cols: Sequence[str], rows: Sequence[Sequence[object]]) -> None:
    table.setSortingEnabled(False)
    table.setRowCount(len(rows))
    table.setColumnCount(len(cols))
    table.setHorizontalHeaderLabels(list(cols))
    for r, row in enumerate(rows):
        for c, val in enumerate(row):
            item = QTableWidgetItem(str(val))
            item.setToolTip(str(val))
            table.setItem(r, c, item)
    table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
    table.resizeColumnsToContents()
    table.setSortingEnabled(True)


class HistoryTab(QWidget):
    def __init__(self, ctx: "AppContext") -> None:
        super().__init__()
        self.ctx = ctx
        root = QVBoxLayout(self)

        bar = QHBoxLayout()
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Lọc theo phiên bản / hash / người ký / thiết bị / reason…")
        self.filter.textChanged.connect(self.refresh)
        btn_refresh = QPushButton("Làm mới")
        btn_refresh.clicked.connect(self.refresh)
        btn_csv_rel = QPushButton("Xuất CSV phát hành")
        btn_csv_rel.clicked.connect(lambda: self._export("releases"))
        btn_csv_rep = QPushButton("Xuất CSV báo cáo thiết bị")
        btn_csv_rep.clicked.connect(lambda: self._export("device_reports"))
        bar.addWidget(self.filter, 1)
        bar.addWidget(btn_refresh)
        bar.addWidget(btn_csv_rel)
        bar.addWidget(btn_csv_rep)
        root.addLayout(bar)

        root.addWidget(QLabel("<b>Bản phát hành</b> (ai ký, lúc nào, hash gì)"))
        self.tbl_releases = QTableWidget()
        self.tbl_releases.setEditTriggers(QTableWidget.NoEditTriggers)
        root.addWidget(self.tbl_releases, 1)
        root.addWidget(QLabel("<b>Kết quả nạp do thiết bị báo về</b> (POST /ota/report)"))
        self.tbl_reports = QTableWidget()
        self.tbl_reports.setEditTriggers(QTableWidget.NoEditTriggers)
        root.addWidget(self.tbl_reports, 1)
        self.refresh()

    def refresh(self, *_: object) -> None:
        f = self.filter.text().strip()
        rel = self.ctx.history.list_releases(f)
        _fill(self.tbl_releases, RELEASE_COLS, [
            (r.id, r.version_code, r.version_name, r.fw_sha256, r.fw_size, r.key_id, r.signed_by,
             fmt_ts(r.signed_at), "✔" if r.is_current else "", r.note, r.dir) for r in rel
        ])
        rep = self.ctx.history.list_device_reports(f)
        _fill(self.tbl_reports, REPORT_COLS, [
            (r.id, fmt_ts(r.ts), r.device, r.version_code, r.result, r.reason, r.remote_ip) for r in rep
        ])

    def _export(self, table: str) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Xuất CSV", f"{table}.csv", "CSV (*.csv)")
        if not path:
            return
        try:
            n = self.ctx.history.export_csv(path, table)
        except (OSError, ValueError) as exc:
            show_error(self, "Không xuất được", str(exc))
            return
        show_info(self, "Đã xuất", f"Ghi {n} dòng bảng {table} ra {path}")
