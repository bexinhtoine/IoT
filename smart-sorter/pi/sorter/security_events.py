"""S-09 — Nhật ký sự kiện bảo mật: ghi DB + gửi telemetry (alarm trên ThingsBoard) + log.

emit() không bao giờ ném exception ra ngoài (đường xử lý kiện không được chết vì ghi log).
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Callable

from .interfaces import DEFAULT_LEVELS, SecurityEvent, SecurityLevel, TelemetrySink
from .storage import Storage

log = logging.getLogger(__name__)

Listener = Callable[[SecurityEvent], None]


class SecurityLog:
    """emit(event_type, detail, level=None, **extra) -> SecurityEvent."""

    def __init__(self, storage: Storage | None, telemetry: TelemetrySink | None) -> None:
        self._storage = storage
        self._telemetry = telemetry
        self._listeners: list[Listener] = []
        self._lock = threading.Lock()
        self.count = 0
        self.last: SecurityEvent | None = None

    def add_listener(self, fn: Listener) -> None:
        """Ví dụ anomaly detector muốn nhận mọi sự kiện."""
        with self._lock:
            self._listeners.append(fn)

    def emit(self, event_type: str, detail: str, level: SecurityLevel | str | None = None, **extra: Any) -> SecurityEvent:
        if level is None:
            lvl = DEFAULT_LEVELS.get(event_type, SecurityLevel.WARNING)
        elif isinstance(level, SecurityLevel):
            lvl = level
        else:
            try:
                lvl = SecurityLevel(str(level).upper())
            except ValueError:
                lvl = SecurityLevel.WARNING
        ev = SecurityEvent(event_type=event_type, level=lvl, detail=str(detail), extra=dict(extra))
        self.emit_event(ev)
        return ev

    def emit_event(self, ev: SecurityEvent) -> None:
        """Dùng khi module khác (RPC handler, anomaly) đã tạo sẵn SecurityEvent."""
        log_fn = log.warning if ev.level in (SecurityLevel.MAJOR, SecurityLevel.CRITICAL) else log.info
        log_fn("SECURITY %s [%s] %s", ev.event_type, ev.level.value, ev.detail, extra={"security_event": ev.to_dict()})
        if self._storage is not None:
            try:
                self._storage.insert_security_event(ev)
            except Exception as e:
                log.error("Không ghi được security_event vào DB: %s", e)
        if self._telemetry is not None:
            try:
                self._telemetry.send_telemetry(ev.to_telemetry(), ts_ms=ev.ts_ms)
            except Exception as e:
                log.error("Không gửi được security_event lên telemetry: %s", e)
        with self._lock:
            self.count += 1
            self.last = ev
            listeners = list(self._listeners)
        for fn in listeners:
            try:
                fn(ev)
            except Exception as e:
                log.error("listener sự kiện bảo mật lỗi: %s", e)
