"""P-12 — Sức khỏe hệ thống: nhiệt độ CPU, % CPU, RAM, đĩa, uptime.

Chạy được trên Windows/laptop không có psutil (giá trị None hoặc 0.0 khi không đo được).
"""
from __future__ import annotations

import logging
import os
import shutil
import time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

THERMAL_PATH = Path("/sys/class/thermal/thermal_zone0/temp")
_START_MONO = time.monotonic()

try:
    import psutil  # type: ignore[import-not-found]

    HAVE_PSUTIL = True
except Exception:  # pragma: no cover
    psutil = None  # type: ignore[assignment]
    HAVE_PSUTIL = False


def read_cpu_temp() -> float | None:
    """Nhiệt độ CPU (°C) từ sysfs; None nếu không có (laptop/Windows)."""
    try:
        if THERMAL_PATH.exists():
            return round(int(THERMAL_PATH.read_text().strip()) / 1000.0, 1)
    except (OSError, ValueError):
        pass
    if HAVE_PSUTIL and hasattr(psutil, "sensors_temperatures"):
        try:
            temps = psutil.sensors_temperatures()  # type: ignore[union-attr]
            for key in ("cpu_thermal", "cpu-thermal", "coretemp", "k10temp"):
                if key in temps and temps[key]:
                    return round(float(temps[key][0].current), 1)
        except Exception:
            pass
    return None


def read_cpu_load() -> float:
    """% CPU (0-100)."""
    if HAVE_PSUTIL:
        try:
            return float(psutil.cpu_percent(interval=None))  # type: ignore[union-attr]
        except Exception:
            pass
    try:
        load1 = os.getloadavg()[0]
        ncpu = os.cpu_count() or 1
        return round(min(100.0, load1 / ncpu * 100.0), 1)
    except (AttributeError, OSError):
        return 0.0


def read_mem_used_pct() -> float | None:
    if HAVE_PSUTIL:
        try:
            return round(float(psutil.virtual_memory().percent), 1)  # type: ignore[union-attr]
        except Exception:
            pass
    try:
        info: dict[str, int] = {}
        with open("/proc/meminfo", "r", encoding="ascii") as f:
            for line in f:
                k, v = line.split(":", 1)
                info[k] = int(v.strip().split()[0])
        total, avail = info["MemTotal"], info.get("MemAvailable", info.get("MemFree", 0))
        return round((total - avail) / total * 100.0, 1) if total else None
    except (OSError, KeyError, ValueError):
        return None


def read_disk_used_pct(path: str = "/") -> float | None:
    try:
        p = path if os.path.exists(path) else os.getcwd()
        u = shutil.disk_usage(p)
        return round(u.used / u.total * 100.0, 1) if u.total else None
    except OSError:
        return None


def read_uptime_s() -> int:
    try:
        with open("/proc/uptime", "r", encoding="ascii") as f:
            return int(float(f.read().split()[0]))
    except (OSError, ValueError, IndexError):
        pass
    if HAVE_PSUTIL:
        try:
            return int(time.time() - psutil.boot_time())  # type: ignore[union-attr]
        except Exception:
            pass
    return int(time.monotonic() - _START_MONO)  # thời gian chạy tiến trình (fallback)


def read_health() -> dict[str, Any]:
    """Khóa đúng đặc tả 4.3 nhóm Sức khỏe + uptime_s."""
    return {
        "cpu_temp": read_cpu_temp(),
        "cpu_load": read_cpu_load(),
        "mem_used_pct": read_mem_used_pct(),
        "disk_used_pct": read_disk_used_pct(),
        "uptime_s": read_uptime_s(),
    }
