#!/usr/bin/env python3
"""Runner nhanh kiểm tra Camera máy tính với AI Box Detector & Classifier.

Chạy trực tiếp từ thư mục gốc dự án:
    python test_camera.py
    python test_camera.py --cam 0
    python test_camera.py --source path/to/image.jpg
"""
import sys
from pathlib import Path

# Đảm bảo UTF-8 console Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

TOOLS_DIR = Path(__file__).resolve().parent / "smart-sorter" / "pi" / "tools"
sys.path.insert(0, str(TOOLS_DIR))

from test_camera_ai import main

if __name__ == "__main__":
    main()
