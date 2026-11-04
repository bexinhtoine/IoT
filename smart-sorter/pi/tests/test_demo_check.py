"""P10 — Test tools/demo_check.py ở chế độ sim qua import: các kịch bản 1, 2, 3, 4, 7 phải PASS.

TEST CHẬM: dựng App thật (FakeEsp32 + FolderCamera + SQLite tạm) nên mất ~3-10 s (phải < 60 s).
Bỏ qua khi vội: SORTER_SKIP_SLOW=1 python -m pytest. (Không dùng pytest.mark.slow vì pyproject.toml
của P3 chưa đăng ký marker — thêm `markers = ["slow"]` vào [tool.pytest.ini_options] nếu muốn.)
Tự skip khi không có bộ giải mã QR đọc được ảnh fixtures (giống tests/test_orchestrator.py).
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import cv2
import pytest

PI = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PI / "tools"))

from sorter.qr_reader import read_qr  # noqa: E402

import demo_check  # noqa: E402

IMAGES = PI / "tests" / "fixtures" / "images"


def _qr_decodable() -> bool:
    p = IMAGES / "01_ok_A.jpg"
    if not p.exists():
        return False
    img = cv2.cvtColor(cv2.imread(str(p)), cv2.COLOR_BGR2RGB)
    return read_qr([img]).status == "VALID"


pytestmark = [
    pytest.mark.skipif(os.environ.get("SORTER_SKIP_SLOW") == "1", reason="test chậm bị tắt bằng SORTER_SKIP_SLOW=1"),
    pytest.mark.skipif(not _qr_decodable(), reason="Không có ảnh fixtures hoặc không bộ giải mã QR nào đọc được chúng"),
]


def test_normalize_only_accepts_ints_and_strings():
    assert demo_check.normalize_only([1, 2, "6a", 12]) == ["1", "2", "6a", "12"]
    assert demo_check.normalize_only("7, 3,1") == ["1", "3", "7"]
    assert demo_check.normalize_only(None) == list(demo_check.ALL_SCENARIOS)
    with pytest.raises(ValueError):
        demo_check.normalize_only([13])


def test_run_sim_core_scenarios_pass(tmp_path):
    t0 = time.monotonic()
    results = demo_check.run_sim(only=[1, 2, 3, 4, 7], workdir=tmp_path / "work")
    elapsed = time.monotonic() - t0
    table = demo_check.format_table(results)
    assert [r.sid for r in results] == ["1", "2", "3", "4", "7"]
    assert all(r.status == demo_check.PASS for r in results), table
    assert elapsed < 60, f"demo_check sim quá chậm: {elapsed:.1f}s"
    # Thư mục tạm đã được dọn (không làm bẩn repo)
    assert not (tmp_path / "work").exists()
    assert not (PI / "run" / "demo_check_sorter.db").exists()


def test_scenario_9_is_manual_not_fail():
    results = demo_check.run_sim(only=["9"])
    assert len(results) == 1 and results[0].status == demo_check.MANUAL
    assert "Viewer" in demo_check.format_table(results)
