"""P-13 — Ghi log: console + file xoay vòng (5 x 5 MB) mỗi dòng một JSON để module A (anomaly) đọc được.

Dòng JSON: {"ts": ISO-8601, "level": ..., "logger": ..., "msg": ..., ...extra}
"""
from __future__ import annotations

import json
import logging
import logging.handlers
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

LOG_FILE = "sorter.jsonl"
MAX_BYTES = 5 * 1024 * 1024
BACKUP_COUNT = 5

# Các thuộc tính chuẩn của LogRecord (không coi là extra)
_STD_ATTRS = set(logging.LogRecord("x", 0, "", 0, "", (), None).__dict__) | {"message", "asctime", "taskName"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        d: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for k, v in record.__dict__.items():
            if k not in _STD_ATTRS and not k.startswith("_"):
                d[k] = v
        if record.exc_info:
            d["exc"] = self.formatException(record.exc_info)
        return json.dumps(d, ensure_ascii=False, default=str)


def setup_logging(level: str | int = "INFO", log_dir: str | Path | None = None, json: bool = True) -> logging.Logger:
    """Cấu hình root logger. json=True -> file dạng JSON lines; console luôn dạng text dễ đọc.

    Thư mục log không ghi được -> chỉ console + cảnh báo (không dừng chương trình).
    """
    root = logging.getLogger()
    lvl = logging.getLevelName(level.upper()) if isinstance(level, str) else int(level)
    if not isinstance(lvl, int):
        lvl = logging.INFO
    root.setLevel(lvl)
    for h in list(root.handlers):
        root.removeHandler(h)

    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%H:%M:%S"))
    root.addHandler(console)

    if log_dir:
        try:
            d = Path(log_dir)
            d.mkdir(parents=True, exist_ok=True)
            fh = logging.handlers.RotatingFileHandler(
                d / LOG_FILE, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8"
            )
            fh.setFormatter(JsonFormatter() if json else logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
            root.addHandler(fh)
        except OSError as e:
            root.warning("Không ghi được log vào %s (%s); chỉ dùng console", log_dir, e)
    # Giảm ồn của thư viện ngoài
    for noisy in ("PIL", "matplotlib", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    return root
