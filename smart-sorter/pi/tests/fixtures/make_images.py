"""Sinh ảnh mẫu 1280x960 cho test/sim (P-14): nền carton có gradient + nhiễu, mã QR thật ký bằng
tests/fixtures/qr_hmac.key, vết rách giả, ảnh không QR, QR giả, QR hết hạn, QR trùng.

Chạy: cd pi && PYTHONUTF8=1 python tests/fixtures/make_images.py [--out tests/fixtures/images]
Bộ mã hóa QR: thư viện qrcode (ưu tiên) -> cv2.QRCodeEncoder -> nếu cả hai thiếu thì thoát mã 3.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
PI_ROOT = HERE.parents[1]
sys.path.insert(0, str(PI_ROOT))

from sorter.qr_security import load_hmac_key, make_code  # noqa: E402

W, H = 1280, 960
QR_SIZE = 420  # px cạnh mã QR trong ảnh (~ hộp dưới camera)
# Tên file: DummyClassifier đọc nhãn từ tên ("tear" -> DEFECT, "uncertain" -> UNCERTAIN)
SPEC = [
    # (tên file, pkg_id, kho, kiểu)  kiểu: ok | tear | forged | noqr | expired | dup | uncertain
    ("01_ok_A.jpg", "PKG000001", "A", "ok"),
    ("02_ok_B.jpg", "PKG000002", "B", "ok"),
    ("03_defect_tear_A.jpg", "PKG000003", "A", "tear"),
    ("04_forged_A.jpg", "PKG000004", "A", "forged"),
    ("05_noqr.jpg", None, None, "noqr"),
    ("06_expired_A.jpg", "PKG000006", "A", "expired"),
    ("07_dup_A.jpg", "PKG000001", "A", "dup"),
    ("08_uncertain_A.jpg", "PKG000008", "A", "uncertain"),
]


def encode_qr(text: str, size: int) -> np.ndarray | None:
    """Ảnh xám (0/255) của mã QR cạnh `size` px; None nếu không có bộ mã hóa."""
    try:
        import qrcode  # type: ignore[import-not-found]
        from qrcode.constants import ERROR_CORRECT_M  # type: ignore[import-not-found]

        q = qrcode.QRCode(error_correction=ERROR_CORRECT_M, box_size=10, border=4)
        q.add_data(text)
        q.make(fit=True)
        img = q.make_image(fill_color="black", back_color="white").convert("L")
        arr = np.array(img, dtype=np.uint8)
        return cv2.resize(arr, (size, size), interpolation=cv2.INTER_NEAREST)
    except ImportError:
        pass
    if hasattr(cv2, "QRCodeEncoder"):
        try:
            params = cv2.QRCodeEncoder_Params()
            params.correction_level = cv2.QRCODE_ENCODER_CORRECT_LEVEL_M
            enc = cv2.QRCodeEncoder.create(params)
            small = enc.encode(text)
            if small is None or small.size == 0:
                return None
            # thêm viền trắng 4 module
            mod = max(1, small.shape[0] // 25)
            small = cv2.copyMakeBorder(small, 4 * mod, 4 * mod, 4 * mod, 4 * mod, cv2.BORDER_CONSTANT, value=255)
            return cv2.resize(small, (size, size), interpolation=cv2.INTER_NEAREST)
        except cv2.error:
            return None
    return None


def carton_background(seed: int) -> np.ndarray:
    """Nền màu carton có gradient sáng + nhiễu hạt + vài đường nếp (BGR)."""
    rng = np.random.default_rng(seed)
    base = np.array([96, 140, 190], dtype=np.float32)  # BGR ~ nâu carton
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    grad = 0.85 + 0.3 * (xx / W) * (1 - 0.4 * yy / H)
    img = base[None, None, :] * grad[:, :, None]
    noise = rng.normal(0, 6, size=(H, W, 1)).astype(np.float32)
    img = np.clip(img + noise, 0, 255).astype(np.uint8)
    # bề mặt bàn/băng xung quanh hộp tối hơn
    cv2.rectangle(img, (0, 0), (W, H), (60, 60, 60), 120)
    # vài nếp gấp mờ
    for i in range(4):
        y = int(200 + i * 180 + rng.integers(-20, 20))
        cv2.line(img, (140, y), (W - 140, y + int(rng.integers(-15, 15))), (80, 120, 165), 2, cv2.LINE_AA)
    return img


def draw_tear(img: np.ndarray, rng: np.random.Generator) -> None:
    """Vết rách màu tối, viền sáng lởm chởm ở góc dưới trái (không đè lên QR)."""
    pts = [(220, 620)]
    x, y = pts[0]
    for _ in range(14):
        x += int(rng.integers(15, 45))
        y += int(rng.integers(-25, 30))
        pts.append((x, y))
    poly = np.array(pts + [(x, y + 40), (220, 660)], dtype=np.int32)
    cv2.fillPoly(img, [poly], (25, 20, 18))
    cv2.polylines(img, [poly], True, (210, 225, 235), 3, cv2.LINE_AA)
    for (px, py) in pts[::2]:
        cv2.circle(img, (px, py + int(rng.integers(-6, 6))), 3, (240, 240, 240), -1)


def compose(qr_text: str | None, kind: str, seed: int) -> np.ndarray | None:
    rng = np.random.default_rng(seed + 1000)
    img = carton_background(seed)
    if qr_text is not None:
        qr = encode_qr(qr_text, QR_SIZE)
        if qr is None:
            return None
        # dán nhãn trắng + QR vào giữa ROI [320,160,640,640]
        x0, y0 = 320 + (640 - QR_SIZE) // 2, 160 + (640 - QR_SIZE) // 2
        cv2.rectangle(img, (x0 - 30, y0 - 30), (x0 + QR_SIZE + 30, y0 + QR_SIZE + 60), (245, 245, 245), -1)
        img[y0:y0 + QR_SIZE, x0:x0 + QR_SIZE] = cv2.cvtColor(qr, cv2.COLOR_GRAY2BGR)
        label = qr_text.split("|")[0] + " / " + qr_text.split("|")[1]
        cv2.putText(img, label, (x0, y0 + QR_SIZE + 42), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (30, 30, 30), 2, cv2.LINE_AA)
    if kind in ("tear",):
        draw_tear(img, rng)
    if kind == "uncertain":
        # làm mờ nhẹ + vệt sáng để "AI không chắc" (chỉ mang tính minh họa)
        cv2.ellipse(img, (900, 760), (160, 60), 20, 0, 360, (200, 215, 225), -1)
        img = cv2.GaussianBlur(img, (3, 3), 0)
    return img


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(HERE / "images"))
    ap.add_argument("--key", default=str(HERE / "qr_hmac.key"))
    ap.add_argument("--ts", type=int, default=None, help="epoch giây ghi vào QR hợp lệ (mặc định: now)")
    args = ap.parse_args(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    key = load_hmac_key(args.key)
    now = args.ts if args.ts is not None else int(time.time())
    expired_ts = now - 4000 * 86400  # > qr.max_age_days cả với 30 ngày lẫn 3650 ngày (config.sim.yaml)

    if encode_qr("test", 100) is None:
        print("KHÔNG có bộ mã hóa QR (cần `pip install qrcode[pil]` hoặc OpenCV có QRCodeEncoder)")
        return 3

    for idx, (name, pkg, wh, kind) in enumerate(SPEC):
        text = None
        if kind in ("ok", "tear", "dup", "uncertain"):
            text = make_code(key, pkg, wh, now)
        elif kind == "forged":
            text = make_code(key, pkg, wh, now, forge=True)
        elif kind == "expired":
            text = make_code(key, pkg, wh, expired_ts)
        img = compose(text, kind, seed=idx * 7919)
        if img is None:
            print("Không mã hóa được QR cho", name)
            return 3
        path = out / name
        cv2.imwrite(str(path), img, [int(cv2.IMWRITE_JPEG_QUALITY), 88])
        print(f"{path.name:<24} {kind:<9} {text or '-'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
