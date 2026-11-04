"""Xác minh một thư mục phát hành OTA ngoài giao diện (O-04, O-07 — tiêu chí xong P7).

    python tools/verify_cli.py <release_dir> --pubkey ota_pubkey.pem [--min-version N]

In kết quả và lý do (NONE | SIGNATURE_INVALID | HASH_MISMATCH | ROLLBACK_REJECTED |
MODEL_MISMATCH | SIZE_MISMATCH | BAD_MANIFEST); exit code 0 nếu hợp lệ, 1 nếu bị từ chối,
2 nếu lỗi tham số.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Cho phép chạy trực tiếp từ thư mục ota-app/ hoặc bất kỳ đâu
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ota_app.keys import load_public_key  # noqa: E402
from ota_app.manifest import DEVICE_MODEL, verify_release_dir  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Xác minh thư mục phát hành Secure OTA")
    parser.add_argument("release_dir", type=Path, help="thư mục chứa manifest.json, manifest.sig, firmware.bin")
    parser.add_argument("--pubkey", type=Path, required=True, help="public key PEM (ota_pubkey.pem)")
    parser.add_argument("--min-version", type=int, default=None,
                        help="version_code thiết bị đang chạy; manifest phải lớn hơn giá trị này")
    parser.add_argument("--model", default=DEVICE_MODEL, help=f"device_model mong đợi (mặc định {DEVICE_MODEL})")
    args = parser.parse_args(argv)

    if not args.release_dir.is_dir():
        print(f"Lỗi: không có thư mục {args.release_dir}", file=sys.stderr)
        return 2
    try:
        pub = load_public_key(args.pubkey)
    except Exception as exc:  # noqa: BLE001
        print(f"Lỗi: không đọc được public key {args.pubkey}: {exc}", file=sys.stderr)
        return 2

    res = verify_release_dir(args.release_dir, pub, min_version_code=args.min_version, device_model=args.model)
    status = "HỢP LỆ" if res.ok else "TỪ CHỐI"
    print(f"{status}: reason={res.reason}  {res.detail}")
    if res.manifest:
        m = res.manifest
        print(
            f"  version_code={m.get('version_code')} version_name={m.get('version_name')} "
            f"fw_size={m.get('fw_size')} key_id={m.get('key_id')} created_at={m.get('created_at')}"
        )
        print(f"  fw_sha256={m.get('fw_sha256')}")
    return 0 if res.ok else 1


if __name__ == "__main__":
    sys.exit(main())
