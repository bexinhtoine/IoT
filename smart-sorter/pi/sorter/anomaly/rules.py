"""P9 / A-04 — Luật cứng chạy song song với Isolation Forest.

Các luật báo ngay không cần model, bắt những tấn công rõ ràng mà IF có thể bỏ sót
khi mới thu dữ liệu. Mỗi luật có cooldown riêng để không báo dồn.

Luật:
- RULE_QR_FORGERY    : >= 5 QR chữ ký sai trong 60 s.
- RULE_SSH_BRUTEFORCE: >= 5 SSH đăng nhập thất bại trong 60 s.
- RULE_RPC_UNKNOWN   : có >= 1 RPC bị từ chối trong cửa sổ hiện tại.
"""
from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from typing import Callable

from .. import interfaces
from ..interfaces import SecurityEvent, SecurityLevel

log = logging.getLogger(__name__)

# Cấu hình luật
WINDOW_S = 60          # cửa sổ trượt cho luật tần suất
QR_THRESHOLD = 5       # >= 5 QR chữ ký sai / phút
SSH_THRESHOLD = 5      # >= 5 SSH thất bại / phút
COOLDOWN_S = 60        # cooldown riêng mỗi luật


@dataclass
class _SlidingCounter:
    """Bộ đếm trượt theo thời gian: lưu (ts_s, count), tổng trong WINDOW_S giây."""

    window_s: float = WINDOW_S
    _events: deque[tuple[float, int]] = field(default_factory=deque)

    def add(self, now_s: float, count: int) -> None:
        if count:
            self._events.append((now_s, int(count)))
        self._evict(now_s)

    def _evict(self, now_s: float) -> None:
        cutoff = now_s - self.window_s
        while self._events and self._events[0][0] < cutoff:
            self._events.popleft()

    def total(self, now_s: float) -> int:
        self._evict(now_s)
        return sum(c for _, c in self._events)


class RuleEngine:
    """Động cơ luật cứng (A-04)."""

    def __init__(self, on_security_event: Callable[[SecurityEvent], None]) -> None:
        self.on_security_event = on_security_event
        self._qr = _SlidingCounter()
        self._ssh = _SlidingCounter()
        self._rpc = _SlidingCounter()
        # Thời điểm báo gần nhất của mỗi luật (để áp cooldown)
        self._last_fire: dict[str, float] = {}

    def _in_cooldown(self, rule: str, now_s: float) -> bool:
        last = self._last_fire.get(rule)
        return last is not None and (now_s - last) < COOLDOWN_S

    def _fire(self, rule: str, detail: str, now_s: float) -> SecurityEvent | None:
        if self._in_cooldown(rule, now_s):
            return None
        self._last_fire[rule] = now_s
        ev = SecurityEvent(
            event_type=interfaces.SEC_RULE,
            level=SecurityLevel.CRITICAL,
            detail=detail,
            ts_ms=int(now_s * 1000),
            extra={"rule": rule},
        )
        try:
            self.on_security_event(ev)
        except Exception as e:  # noqa: BLE001 - callback lỗi không được làm sập engine
            log.error("Lỗi callback sự kiện bảo mật cho %s: %s", rule, e)
        return ev

    def update(self, features: dict[str, int], now_s: float) -> list[SecurityEvent]:
        """Cập nhật bộ đếm với đặc trưng của cửa sổ kết thúc tại now_s; trả sự kiện phát sinh."""
        qr_bad = int(features.get(interfaces.F_QR_BAD_SIGNATURE, 0))
        ssh_bad = int(features.get(interfaces.F_SSH_FAILED, 0))
        rpc_bad = int(features.get(interfaces.F_RPC_REJECTED, 0))

        self._qr.add(now_s, qr_bad)
        self._ssh.add(now_s, ssh_bad)
        self._rpc.add(now_s, rpc_bad)

        events: list[SecurityEvent] = []

        qr_total = self._qr.total(now_s)
        if qr_total >= QR_THRESHOLD:
            ev = self._fire(
                "RULE_QR_FORGERY",
                f"RULE_QR_FORGERY: {qr_total} QR chữ ký sai trong {WINDOW_S}s (nghi giả mạo QR)",
                now_s,
            )
            if ev:
                events.append(ev)

        ssh_total = self._ssh.total(now_s)
        if ssh_total >= SSH_THRESHOLD:
            ev = self._fire(
                "RULE_SSH_BRUTEFORCE",
                f"RULE_SSH_BRUTEFORCE: {ssh_total} lần SSH thất bại trong {WINDOW_S}s (nghi dò mật khẩu)",
                now_s,
            )
            if ev:
                events.append(ev)

        # RPC lạ: báo ngay trong cửa sổ hiện tại (không cần tích lũy)
        if rpc_bad >= 1:
            ev = self._fire(
                "RULE_RPC_UNKNOWN",
                f"RULE_RPC_UNKNOWN: {rpc_bad} RPC bị từ chối trong cửa sổ (phương thức lạ/ngoài whitelist)",
                now_s,
            )
            if ev:
                events.append(ev)

        return events
