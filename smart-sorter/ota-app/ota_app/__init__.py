"""App Secure OTA cho Smart Logistics Sorter (MODULE P7, O-01 -> O-07).

Package này chứa toàn bộ logic (auth, khóa, firmware, manifest, server, lịch sử,
demo tấn công) tách biệt khỏi giao diện Qt trong ``ota_app.ui`` để có thể test
không cần PySide6.
"""
from __future__ import annotations

__version__ = "1.0.0"
