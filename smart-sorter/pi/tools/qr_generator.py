#!/usr/bin/env python3
"""P-16 / S-03 — Tạo mã QR có chữ ký HMAC (4.1): PNG từng mã + trang A4 PDF lưới 3x4 có nhãn.

Dùng:
  python tools/qr_generator.py --key qr_hmac.key --out out/qr --items PKG000001:A PKG000002:B
  python tools/qr_generator.py --key qr_hmac.key --out out/qr --count 12 --warehouse A --start 1
  thêm --forge để tạo mã chữ ký SAI (demo tấn công), --ts <epoch> để ép thời gian (demo hết hạn).
Mức sửa lỗi M; box_size 12 px/module -> ~3.3 cm cạnh khi in 300 dpi cho mã 25x25 module (có viền). Không in khóa.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sorter.qr_security import PKG_RE, load_hmac_key, make_code  # noqa: E402


def parse_items(args: argparse.Namespace) -> list[tuple[str, str]]:
    items: list[tuple[str, str]] = []
    if args.items:
        for it in args.items:
            if ":" not in it:
                raise SystemExit(f"--items cần dạng PKG000001:A, nhận {it}")
            pkg, wh = it.split(":", 1)
            items.append((pkg.strip(), wh.strip().upper()))
    elif args.count:
        for i in range(args.count):
            items.append((f"PKG{args.start + i:06d}", args.warehouse.upper()))
    else:
        raise SystemExit("Cần --items hoặc --count")
    for pkg, wh in items:
        if not PKG_RE.match(pkg):
            raise SystemExit(f"Mã kiện sai định dạng: {pkg} (cần PKG + 6 chữ số)")
        if wh not in ("A", "B") and not args.allow_bad_warehouse:
            raise SystemExit(f"Kho phải là A hoặc B: {wh} (dùng --allow-bad-warehouse để demo BAD_WAREHOUSE)")
    return items


def make_png(text: str, path: Path, box_size: int) -> None:
    import qrcode
    from qrcode.constants import ERROR_CORRECT_M

    q = qrcode.QRCode(error_correction=ERROR_CORRECT_M, box_size=box_size, border=4)
    q.add_data(text)
    q.make(fit=True)
    q.make_image(fill_color="black", back_color="white").save(str(path))


def make_pdf(entries: list[tuple[str, Path, str]], pdf_path: Path, title: str) -> None:
    """Lưới 3x4 mỗi trang A4, nhãn chữ dưới mỗi mã. entries: (nhãn, png, ghi chú)."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(str(pdf_path), pagesize=A4)
    W, H = A4
    cols, rows = 3, 4
    margin_x, margin_y, top = 15 * mm, 12 * mm, 18 * mm
    cell_w = (W - 2 * margin_x) / cols
    cell_h = (H - margin_y - top) / rows
    qr_side = min(cell_w, cell_h) - 18 * mm
    qr_side = max(qr_side, 30 * mm)  # cạnh tối thiểu 3 cm (4.1)
    for i, (label, png, note) in enumerate(entries):
        k = i % (cols * rows)
        if k == 0:
            if i:
                c.showPage()
            c.setFont("Helvetica-Bold", 11)
            c.drawString(margin_x, H - 12 * mm, title)
        r, col = divmod(k, cols)
        x0 = margin_x + col * cell_w
        y0 = H - top - (r + 1) * cell_h
        qx = x0 + (cell_w - qr_side) / 2
        qy = y0 + 12 * mm
        c.drawImage(str(png), qx, qy, qr_side, qr_side)
        c.setFont("Helvetica-Bold", 12)
        c.drawCentredString(x0 + cell_w / 2, y0 + 7 * mm, label)
        if note:
            c.setFont("Helvetica", 8)
            c.drawCentredString(x0 + cell_w / 2, y0 + 3 * mm, note)
    c.save()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Tạo QR có chữ ký HMAC-SHA256")
    ap.add_argument("--key", required=True, help="file khóa 32 byte (quyền 600)")
    ap.add_argument("--out", required=True, help="thư mục xuất")
    ap.add_argument("--items", nargs="*", help="PKG000001:A PKG000002:B ...")
    ap.add_argument("--count", type=int, default=0)
    ap.add_argument("--warehouse", default="A")
    ap.add_argument("--start", type=int, default=1)
    ap.add_argument("--ts", type=int, default=None, help="epoch giây ghi vào mã (mặc định: now)")
    ap.add_argument("--forge", action="store_true", help="tạo chữ ký SAI để demo BAD_SIGNATURE")
    ap.add_argument("--allow-bad-warehouse", action="store_true")
    ap.add_argument("--box-size", type=int, default=12, help="px mỗi module trong PNG")
    ap.add_argument("--pdf-name", default=None)
    args = ap.parse_args(argv)

    try:
        import qrcode  # noqa: F401
    except ImportError:
        print("Thiếu thư viện qrcode: pip install 'qrcode[pil]'", file=sys.stderr)
        return 2
    key = load_hmac_key(args.key)
    items = parse_items(args)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    ts = args.ts if args.ts is not None else int(time.time())
    entries: list[tuple[str, Path, str]] = []
    manifest = out / "codes.txt"
    with manifest.open("w", encoding="utf-8") as mf:
        for pkg, wh in items:
            text = make_code(key, pkg, wh, ts, forge=args.forge)
            suffix = "_FORGED" if args.forge else ""
            png = out / f"{pkg}_{wh}{suffix}.png"
            make_png(text, png, args.box_size)
            note = "CHU KY SAI (demo)" if args.forge else time.strftime("%Y-%m-%d %H:%M", time.gmtime(ts)) + " UTC"
            entries.append((f"{pkg}  kho {wh}", png, note))
            mf.write(text + "\n")
            print(f"{png.name:<28} {text}")
    try:
        import reportlab  # noqa: F401

        pdf = out / (args.pdf_name or ("qr_sheet_forged.pdf" if args.forge else "qr_sheet.pdf"))
        make_pdf(entries, pdf, f"Smart Logistics Sorter — QR {'GIA (demo)' if args.forge else 'hop le'} — in 100%, khong co dan trang")
        print(f"PDF: {pdf}")
    except ImportError:
        print("Thiếu reportlab -> bỏ qua PDF (pip install reportlab)", file=sys.stderr)
    print(f"Danh sách nội dung mã: {manifest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
