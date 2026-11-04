"""P9 / A-01 — Trích đặc trưng theo cửa sổ 10 s.

Đặc trưng lấy từ bộ đếm trong bộ nhớ (interfaces.metrics) mà các module khác
(orchestrator, rpc_handler, uart_link, tb_client) tăng bằng metrics.inc(F_xxx),
cộng thêm số lần SSH đăng nhập thất bại đọc từ journalctl.

Thứ tự vector đặc trưng luôn khớp interfaces.FEATURE_NAMES.
"""
from __future__ import annotations

import logging
import subprocess
from typing import Callable

import numpy as np

from .. import interfaces

log = logging.getLogger(__name__)

FEATURE_NAMES: tuple[str, ...] = interfaces.FEATURE_NAMES

# Mẫu dòng log SSH coi là thất bại đăng nhập
_SSH_FAIL_MARKERS = ("Failed password", "Invalid user", "authentication failure")

# Cờ cảnh báo một lần khi không có journalctl (Windows / máy dev)
_warned_no_journalctl = False


def read_ssh_failures(since_s: int) -> int:
    """Đếm số dòng log SSH thất bại trong `since_s` giây gần nhất qua journalctl.

    Dùng subprocess có timeout, KHÔNG shell=True. Nếu không có journalctl
    (Windows / không systemd) thì trả 0 và cảnh báo một lần.
    """
    global _warned_no_journalctl
    try:
        proc = subprocess.run(
            [
                "journalctl",
                "-u",
                "ssh",
                "--since",
                f"-{int(since_s)}s",
                "-o",
                "cat",
                "--no-pager",
            ],
            timeout=3,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, OSError):
        if not _warned_no_journalctl:
            log.warning("Không tìm thấy journalctl (không phải Linux/systemd); ssh_failed=0")
            _warned_no_journalctl = True
        return 0
    except subprocess.TimeoutExpired:
        log.warning("journalctl quá thời gian chờ; ssh_failed=0 cho cửa sổ này")
        return 0
    if proc.returncode != 0:
        # journalctl có thể lỗi quyền hoặc đơn vị tên khác; không làm sập detector
        log.debug("journalctl trả mã %s: %s", proc.returncode, (proc.stderr or "").strip()[:200])
        return 0
    count = 0
    for line in proc.stdout.splitlines():
        if any(marker in line for marker in _SSH_FAIL_MARKERS):
            count += 1
    return count


class FeatureCollector:
    """Gom một cửa sổ đặc trưng từ bộ đếm và log SSH.

    metrics    : bộ đếm MetricCounters (mặc định bộ đếm toàn cục).
    ssh_reader : hàm (since_s) -> int để bơm giả khi test; None = dùng journalctl thật.
    window_s   : độ dài cửa sổ (giây), dùng cho khoảng --since của journalctl.
    """

    def __init__(
        self,
        metrics: interfaces.MetricCounters = interfaces.metrics,
        ssh_reader: Callable[[int], int] | None = None,
        window_s: int = 10,
    ) -> None:
        self.metrics = metrics
        self.ssh_reader = ssh_reader or read_ssh_failures
        self.window_s = int(window_s)

    def collect(self, now_ms: int) -> dict[str, int]:
        """Trả về dict đặc trưng cho cửa sổ kết thúc tại now_ms.

        Lấy snapshot_and_reset() của bộ đếm rồi ghi đè ssh_failed bằng số đọc được
        từ log SSH trong window_s giây gần nhất.
        """
        d = self.metrics.snapshot_and_reset()
        try:
            ssh = int(self.ssh_reader(self.window_s))
        except Exception as e:  # noqa: BLE001 - không để lỗi đọc log làm sập detector
            log.warning("Lỗi đọc log SSH: %s; ssh_failed=0", e)
            ssh = 0
        # Cộng vào giá trị bộ đếm (bình thường bộ đếm ssh_failed = 0)
        d[interfaces.F_SSH_FAILED] = int(d.get(interfaces.F_SSH_FAILED, 0)) + ssh
        return d

    @staticmethod
    def to_vector(d: dict[str, int]) -> np.ndarray:
        """Vector đặc trưng float theo đúng thứ tự FEATURE_NAMES."""
        return np.array([float(d.get(name, 0)) for name in FEATURE_NAMES], dtype=float)
