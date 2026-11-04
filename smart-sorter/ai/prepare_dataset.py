"""AI-02 — Chuẩn bị dataset: lọc ảnh hỏng, thống kê, chia train/val/test 70/15/15 THEO HỘP (box_id).

Đầu vào (AI-01, chụp bằng pi/tools/capture_dataset.py):
    dataset/<lớp>/<box_id>_<timestamp>.jpg       lớp ∈ {ok, tear, dent, crush, wet} hoặc {ok, defect}
Đầu ra (cấu trúc ultralytics classify):
    out/train/<lớp>/*.jpg   out/val/<lớp>/*.jpg   out/test/<lớp>/*.jpg   + out/split.json

- Ảnh cùng box_id luôn nằm trong cùng một tập (GroupShuffleSplit, seed 42) → test không "nhìn trộm" hộp đã train.
- --classes 2: gộp tear/dent/crush/wet (và mọi lớp khác ok) → defect;  --classes 4: giữ nguyên tên lớp.
- Cảnh báo nếu lớp nhiều nhất / lớp ít nhất > 1.5 lần.

Ví dụ:
    python ai/prepare_dataset.py --src dataset --out ai/data --classes 2
    python ai/prepare_dataset.py --src dataset --out ai/data --classes 4 --link   # symlink thay vì copy
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path

log = logging.getLogger("prepare_dataset")

IMG_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
OK_NAMES = {"ok", "good", "intact", "lanh", "tot"}
DEFECT_NAMES = {"tear", "dent", "crush", "wet", "defect", "rach", "mop", "bep", "uot", "damaged"}
# Tên file: <box_id>_<timestamp>.jpg — box_id là phần trước dấu "_" cuối cùng gắn với chuỗi số/thời gian
NAME_RE = re.compile(r"^(?P<box>.+?)_(?P<ts>\d{8}[_T-]?\d{6}(?:[_.]\d+)?|\d{10,16})(?:_\d+)?$")


@dataclass
class ImageRec:
    path: str
    cls: str          # lớp gốc theo thư mục
    label: str        # lớp sau khi gộp (ok/defect hoặc giữ nguyên)
    box_id: str
    split: str = ""


@dataclass
class SplitSummary:
    seed: int
    classes: int
    label_names: list[str]
    fractions: dict[str, float]
    per_split: dict[str, dict[str, int]]
    boxes_per_split: dict[str, int]
    per_box_label: dict[str, dict[str, int]]
    corrupted: list[str]
    warnings: list[str] = field(default_factory=list)


def box_id_of(path: Path) -> str:
    """Trích box_id từ tên file; không khớp mẫu thì lấy phần trước dấu '_' đầu tiên (hoặc cả tên)."""
    stem = path.stem
    m = NAME_RE.match(stem)
    if m:
        return m.group("box")
    return stem.split("_")[0] if "_" in stem else stem


def map_label(cls: str, n_classes: int) -> str:
    c = cls.strip().lower()
    if n_classes == 2:
        return "ok" if c in OK_NAMES else "defect"
    return c


def is_valid_image(path: Path) -> bool:
    """PIL verify + mở lại để chắc chắn giải mã được (verify() chỉ kiểm tra cấu trúc)."""
    from PIL import Image
    try:
        with Image.open(path) as im:
            im.verify()
        with Image.open(path) as im:
            im.load()
            return im.size[0] >= 32 and im.size[1] >= 32
    except Exception:  # noqa: BLE001 — mọi lỗi giải mã đều coi là ảnh hỏng
        return False


def scan(src: Path, n_classes: int) -> tuple[list[ImageRec], list[str]]:
    recs: list[ImageRec] = []
    bad: list[str] = []
    for cls_dir in sorted(p for p in src.iterdir() if p.is_dir()):
        for p in sorted(cls_dir.rglob("*")):
            if p.suffix.lower() not in IMG_EXTS:
                continue
            if not is_valid_image(p):
                bad.append(str(p))
                continue
            recs.append(ImageRec(path=str(p), cls=cls_dir.name, label=map_label(cls_dir.name, n_classes),
                                 box_id=f"{cls_dir.name}/{box_id_of(p)}"))
    return recs, bad


def group_split(recs: list[ImageRec], val_frac: float, test_frac: float, seed: int) -> None:
    """Chia theo nhóm box_id, cố gắng giữ tỉ lệ nhãn: chia riêng từng nhãn rồi gộp lại.

    Dùng sklearn.GroupShuffleSplit nếu có; không có thì tự xáo trộn nhóm với random.Random(seed)."""
    import random
    try:
        from sklearn.model_selection import GroupShuffleSplit
    except ImportError:
        GroupShuffleSplit = None  # type: ignore[assignment]

    by_label: dict[str, list[ImageRec]] = defaultdict(list)
    for r in recs:
        by_label[r.label].append(r)
    for label, items in sorted(by_label.items()):
        groups = [r.box_id for r in items]
        n_groups = len(set(groups))
        if n_groups < 3:
            log.warning("Lớp %s chỉ có %d hộp — không đủ để chia 3 tập theo hộp; tất cả vào train", label, n_groups)
            for r in items:
                r.split = "train"
            continue
        idx = list(range(len(items)))
        if GroupShuffleSplit is not None:
            gss = GroupShuffleSplit(n_splits=1, test_size=test_frac, random_state=seed)
            rest, test = next(gss.split(idx, groups=groups))
            rest_groups = [groups[i] for i in rest]
            gss2 = GroupShuffleSplit(n_splits=1, test_size=val_frac / (1 - test_frac), random_state=seed)
            tr, va = next(gss2.split(rest, groups=rest_groups))
            train_idx, val_idx = [rest[i] for i in tr], [rest[i] for i in va]
            test_idx = list(test)
        else:
            uniq = sorted(set(groups))
            random.Random(seed).shuffle(uniq)
            n_test = max(1, round(len(uniq) * test_frac))
            n_val = max(1, round(len(uniq) * val_frac))
            g_test, g_val = set(uniq[:n_test]), set(uniq[n_test:n_test + n_val])
            train_idx = [i for i in idx if groups[i] not in g_test | g_val]
            val_idx = [i for i in idx if groups[i] in g_val]
            test_idx = [i for i in idx if groups[i] in g_test]
        for i in train_idx:
            items[i].split = "train"
        for i in val_idx:
            items[i].split = "val"
        for i in test_idx:
            items[i].split = "test"


def export(recs: list[ImageRec], out: Path, link: bool) -> None:
    for split in ("train", "val", "test"):
        for r in recs:
            if r.split != split:
                continue
            dst_dir = out / split / r.label
            dst_dir.mkdir(parents=True, exist_ok=True)
            src = Path(r.path)
            # Tiền tố lớp gốc để không trùng tên khi gộp 4 lớp → defect
            dst = dst_dir / (f"{r.cls}__{src.name}" if r.cls != r.label else src.name)
            if dst.exists() or dst.is_symlink():
                dst.unlink()
            if link:
                try:
                    os.symlink(src.resolve(), dst)
                    continue
                except (OSError, NotImplementedError):
                    log.warning("Không tạo được symlink (Windows cần quyền Developer Mode) — chuyển sang copy")
                    link = False
            shutil.copy2(src, dst)


def summarize(recs: list[ImageRec], bad: list[str], n_classes: int, seed: int,
              fractions: dict[str, float]) -> SplitSummary:
    labels = sorted({r.label for r in recs})
    per_split = {s: dict(Counter(r.label for r in recs if r.split == s)) for s in ("train", "val", "test")}
    boxes = {s: len({r.box_id for r in recs if r.split == s}) for s in ("train", "val", "test")}
    per_box: dict[str, dict[str, int]] = defaultdict(Counter)  # type: ignore[assignment]
    for r in recs:
        per_box[r.label][r.box_id] += 1
    warnings: list[str] = []
    counts = Counter(r.label for r in recs)
    if len(counts) >= 2:
        hi, lo = max(counts.values()), min(counts.values())
        if lo == 0 or hi / lo > 1.5:
            warnings.append(f"Lớp lệch {hi}/{lo} = {hi / max(lo, 1):.2f} lần (> 1.5): chụp thêm lớp "
                            f"{min(counts, key=counts.get)} hoặc dùng class weight khi train")
    for lab, n in counts.items():
        if n < 300:
            warnings.append(f"Lớp {lab} chỉ có {n} ảnh (< 300 theo AI-02)")
    # Kiểm tra không rò rỉ hộp giữa các tập
    seen: dict[str, str] = {}
    for r in recs:
        if r.box_id in seen and seen[r.box_id] != r.split:
            warnings.append(f"Hộp {r.box_id} xuất hiện ở {seen[r.box_id]} và {r.split}!")
        seen[r.box_id] = r.split
    return SplitSummary(seed=seed, classes=n_classes, label_names=labels, fractions=fractions, per_split=per_split,
                        boxes_per_split=boxes, per_box_label={k: dict(v) for k, v in per_box.items()},
                        corrupted=bad, warnings=warnings)


def prepare(src: Path, out: Path, n_classes: int, seed: int = 42, val_frac: float = 0.15, test_frac: float = 0.15,
            link: bool = False) -> SplitSummary:
    if not src.is_dir():
        raise SystemExit(f"Không thấy thư mục dataset: {src}")
    recs, bad = scan(src, n_classes)
    if not recs:
        raise SystemExit(f"Không có ảnh hợp lệ trong {src} (cần dataset/<lớp>/<box_id>_<timestamp>.jpg)")
    log.info("Quét %d ảnh hợp lệ, %d ảnh hỏng bị loại", len(recs), len(bad))
    for b in bad:
        log.warning("Ảnh hỏng: %s", b)
    group_split(recs, val_frac, test_frac, seed)
    if out.exists():
        for s in ("train", "val", "test"):
            shutil.rmtree(out / s, ignore_errors=True)
    export(recs, out, link)
    summary = summarize(recs, bad, n_classes, seed, {"train": 1 - val_frac - test_frac, "val": val_frac, "test": test_frac})
    out.mkdir(parents=True, exist_ok=True)
    split_json = {"summary": asdict(summary), "images": [asdict(r) for r in recs]}
    (out / "split.json").write_text(json.dumps(split_json, ensure_ascii=False, indent=1), encoding="utf-8")
    return summary


def print_summary(s: SplitSummary) -> None:
    print(f"\nLớp ({s.classes}): {s.label_names}  | seed {s.seed}")
    print("Số ảnh theo tập / lớp:")
    for split, c in s.per_split.items():
        print(f"  {split:5s}: {sum(c.values()):4d} ảnh, {s.boxes_per_split[split]:3d} hộp  {dict(sorted(c.items()))}")
    print("Số hộp theo lớp:")
    for lab, boxes in s.per_box_label.items():
        n = list(boxes.values())
        print(f"  {lab:8s}: {len(boxes)} hộp, {sum(n)} ảnh, trung bình {sum(n) / max(1, len(n)):.1f} ảnh/hộp")
    if s.corrupted:
        print(f"Ảnh hỏng bị loại: {len(s.corrupted)}")
    for w in s.warnings:
        print(f"CẢNH BÁO: {w}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Chuẩn bị dataset bao bì (AI-02)")
    ap.add_argument("--src", default="dataset", help="thư mục dataset/<lớp>/…")
    ap.add_argument("--out", default="ai/data", help="thư mục xuất train/val/test")
    ap.add_argument("--classes", type=int, choices=[2, 4], default=2)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--val", type=float, default=0.15)
    ap.add_argument("--test", type=float, default=0.15)
    ap.add_argument("--link", action="store_true", help="symlink thay vì copy")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    s = prepare(Path(args.src), Path(args.out), args.classes, args.seed, args.val, args.test, args.link)
    print_summary(s)
    print(f"Đã ghi {Path(args.out) / 'split.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
