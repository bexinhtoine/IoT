#!/usr/bin/env python3
"""P-16 / S-03 — Sinh khóa HMAC 32 byte bằng secrets, ghi file quyền 600. KHÔNG in khóa.

Dùng: python tools/make_hmac_key.py --out /etc/sorter/secrets/qr_hmac.key [--force] [--hex]
Khóa này phải giống hệt khóa dùng để in QR (tools/qr_generator.py --key ...).
"""
from __future__ import annotations

import argparse
import os
import secrets
import stat
import sys
from pathlib import Path

KEY_LEN = 32


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Sinh khóa HMAC-SHA256 32 byte cho mã QR")
    ap.add_argument("--out", required=True, help="file đích (ví dụ /etc/sorter/secrets/qr_hmac.key)")
    ap.add_argument("--force", action="store_true", help="ghi đè nếu đã tồn tại")
    ap.add_argument("--hex", action="store_true", help="ghi dạng 64 ký tự hex thay vì 32 byte raw")
    args = ap.parse_args(argv)
    out = Path(args.out)
    if out.exists() and not args.force:
        print(f"Đã tồn tại {out}; dùng --force để ghi đè (mã QR cũ sẽ mất hiệu lực!)", file=sys.stderr)
        return 1
    out.parent.mkdir(parents=True, exist_ok=True)
    key = secrets.token_bytes(KEY_LEN)
    data = (key.hex() + "\n").encode("ascii") if args.hex else key
    # Mở với quyền 600 ngay từ đầu (POSIX); Windows bỏ qua mode
    fd = os.open(str(out), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    if os.name == "posix":
        os.chmod(out, stat.S_IRUSR | stat.S_IWUSR)
    print(f"Đã ghi khóa {KEY_LEN} byte vào {out} (quyền 600). Sao chép file này sang máy in QR một cách an toàn; không gửi qua chat.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
