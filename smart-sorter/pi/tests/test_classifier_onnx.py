"""P4 — Test OnnxClassifier (P-05, AI-06, AI-07) với model ONNX giả sinh bởi ai/export_dummy_model.py.

Skip gọn nếu máy không có onnxruntime/onnx (không bắt buộc trên laptop).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("onnxruntime")
pytest.importorskip("onnx")

ROOT = Path(__file__).resolve().parents[2]          # smart-sorter/
sys.path.insert(0, str(ROOT / "ai"))

from export_dummy_model import export_dummy, export_generic_dummy  # noqa: E402

from sorter.classifier_onnx import ModelLoadError, OnnxClassifier, build_spec, decide  # noqa: E402
from sorter.config import AiCfg  # noqa: E402
from sorter.interfaces import LABEL_DEFECT, LABEL_OK, LABEL_UNCERTAIN, ClassResult  # noqa: E402

S = 224


def bright_image(h: int = 480, w: int = 640, v: int = 200) -> np.ndarray:
    """Hộp carton sáng đều (RGB uint8)."""
    img = np.full((h, w, 3), v, np.uint8)
    img[..., 2] = v - 40  # hơi ngả vàng như carton
    return img


def torn_image(h: int = 480, w: int = 640) -> np.ndarray:
    """Hộp sáng có một vết rách tối ở giữa (đủ lớn hơn ô S/4 sau khi resize)."""
    img = bright_image(h, w)
    cy, cx = h // 2, w // 2
    img[cy - 120: cy + 120, cx - 160: cx + 160] = 10
    return img


def dark_image(h: int = 480, w: int = 640) -> np.ndarray:
    return np.full((h, w, 3), 30, np.uint8)


@pytest.fixture(scope="module")
def dummy_dir(tmp_path_factory) -> Path:
    d = tmp_path_factory.mktemp("dummy_model")
    export_dummy(d, size=S, defect_threshold=0.6)
    return d


def make_cfg(d: Path, **kw) -> AiCfg:
    base = dict(backend="onnx", model_path=str(d / "dummy_packaging_cls.onnx"), labels_path=str(d / "labels.json"),
                model_card_path="", input_size=S, min_confidence=0.6, threads=2)
    base.update(kw)
    return AiCfg(**base)


def test_spec_from_model_card(dummy_dir: Path):
    spec = build_spec(make_cfg(dummy_dir))
    assert spec.kind == "binary_types"
    assert spec.input_size == S
    assert spec.input_name == "image"
    assert spec.classes == ["OK", "DEFECT"]
    assert spec.damage_types == ["tear", "dent", "crush", "wet"]
    assert spec.defect_threshold == pytest.approx(0.6)
    assert spec.version == "0.0.0-dummy"


def test_predict_returns_classresult_ok(dummy_dir: Path):
    clf = OnnxClassifier(make_cfg(dummy_dir))
    clf.warmup()
    r = clf.predict(bright_image())
    assert isinstance(r, ClassResult)
    assert r.label == LABEL_OK and r.is_ok
    assert 0.6 <= r.confidence <= 1.0
    assert set(r.probs) == {LABEL_OK, LABEL_DEFECT}
    assert r.probs[LABEL_OK] + r.probs[LABEL_DEFECT] == pytest.approx(1.0, abs=1e-3)
    assert set(r.defect_types) == {"tear", "dent", "crush", "wet"}
    assert r.infer_ms > 0
    assert r.model_version == "0.0.0-dummy"
    assert clf.model_version == "0.0.0-dummy"


def test_dark_and_torn_are_defect(dummy_dir: Path):
    clf = OnnxClassifier(make_cfg(dummy_dir))
    r_dark = clf.predict(dark_image())
    assert r_dark.label == LABEL_DEFECT
    assert r_dark.confidence == pytest.approx(r_dark.probs[LABEL_DEFECT])
    r_torn = clf.predict(torn_image())
    assert r_torn.label == LABEL_DEFECT
    assert r_torn.top_defect_type == "tear"


def test_roi_is_cropped(dummy_dir: Path):
    """Vết rách nằm NGOÀI ROI → OK; ROI bao vết rách → DEFECT."""
    clf = OnnxClassifier(make_cfg(dummy_dir))
    img = bright_image(480, 640)
    img[20:80, 20:80] = 5            # vết tối nhỏ ở góc trên-trái (~1% ảnh)
    assert clf.predict(img, roi=(300, 200, 300, 250)).label == LABEL_OK      # ROI không chứa vết
    assert clf.predict(img, roi=(10, 10, 120, 120)).label == LABEL_DEFECT    # ROI bao vết → vết chiếm ~25%
    assert clf.predict(img, roi=None).label == LABEL_OK                      # cả ảnh: vết quá nhỏ so với ô S/4
    # ROI tràn biên vẫn chạy (crop_roi tự kẹp)
    assert clf.predict(img, roi=(600, 400, 500, 500)).label in (LABEL_OK, LABEL_DEFECT, LABEL_UNCERTAIN)


def test_threshold_from_model_card_is_respected(dummy_dir: Path, tmp_path: Path):
    """Ảnh xám trung bình: p_defect ≈ 0.5. Card ngưỡng 0.9 → không DEFECT; card ngưỡng 0.3 → DEFECT."""
    clf = OnnxClassifier(make_cfg(dummy_dir))
    mid = np.full((300, 300, 3), int(0.40 * 255), np.uint8)
    p = clf.predict(mid).probs[LABEL_DEFECT]
    assert 0.3 < p < 0.7, p

    card = json.loads((dummy_dir / "model_card.json").read_text(encoding="utf-8"))
    for thr, expect in ((0.9, LABEL_UNCERTAIN), (0.3, LABEL_DEFECT)):
        card["defect_threshold"] = thr
        cp = tmp_path / f"card_{int(thr * 10)}.json"
        cp.write_text(json.dumps(card), encoding="utf-8")
        c = OnnxClassifier(make_cfg(dummy_dir, model_card_path=str(cp)))
        assert c.spec.defect_threshold == pytest.approx(thr)
        assert c.predict(mid).label == expect


def test_uncertain_when_probability_in_between(dummy_dir: Path):
    clf = OnnxClassifier(make_cfg(dummy_dir, min_confidence=0.6))
    mid = np.full((300, 300, 3), int(0.40 * 255), np.uint8)
    r = clf.predict(mid)
    assert r.label == LABEL_UNCERTAIN
    assert r.confidence == pytest.approx(max(r.probs.values()))


def test_decide_rule():
    assert decide(0.2, 0.8, 0.6, 0.6) == (LABEL_DEFECT, 0.8)
    assert decide(0.9, 0.1, 0.6, 0.6) == (LABEL_OK, 0.9)
    assert decide(0.55, 0.45, 0.6, 0.6) == (LABEL_UNCERTAIN, 0.55)
    # Ngưỡng lỗi thấp (ưu tiên an toàn) → 0.35 đã là DEFECT
    assert decide(0.65, 0.35, 0.3, 0.6)[0] == LABEL_DEFECT


def test_generic_single_output_model(tmp_path: Path):
    """Model kiểu YOLOv8-cls: 1 output logits [N,C], labels.json dạng list, không có model_card."""
    paths = export_generic_dummy(tmp_path, classes=("defect", "ok"), size=S)
    cfg = AiCfg(backend="onnx", model_path=str(paths["model"]), labels_path=str(paths["labels"]),
                model_card_path="", input_size=S, min_confidence=0.6, threads=1)
    clf = OnnxClassifier(cfg)
    assert clf.spec.kind == "generic"
    assert clf.spec.classes == ["defect", "ok"]
    assert clf.model_version == "dummy_generic_cls"  # không có card → tên file
    r = clf.predict(bright_image())
    assert r.label == LABEL_OK
    assert r.probs["ok"] == pytest.approx(r.probs[LABEL_OK])
    assert r.defect_types == {}  # chỉ một lớp lỗi chung chung
    r2 = clf.predict(dark_image())
    assert r2.label == LABEL_DEFECT
    assert r2.probs[LABEL_OK] + r2.probs[LABEL_DEFECT] == pytest.approx(1.0, abs=1e-3)


def test_generic_four_classes_defect_types(tmp_path: Path):
    paths = export_generic_dummy(tmp_path, classes=("dent", "ok", "tear", "wet"), size=S, with_softmax=True)
    cfg = AiCfg(backend="onnx", model_path=str(paths["model"]), labels_path=str(paths["labels"]),
                input_size=S, min_confidence=0.6, threads=1)
    clf = OnnxClassifier(cfg)
    r = clf.predict(dark_image())
    assert r.label == LABEL_DEFECT
    assert set(r.defect_types) == {"dent", "tear", "wet"}
    assert r.probs[LABEL_DEFECT] == pytest.approx(sum(r.defect_types.values()), abs=1e-3)


def test_input_size_mismatch_with_card_raises(dummy_dir: Path, tmp_path: Path):
    card = json.loads((dummy_dir / "model_card.json").read_text(encoding="utf-8"))
    card["input"]["size"] = 256
    cp = tmp_path / "bad_card.json"
    cp.write_text(json.dumps(card), encoding="utf-8")
    with pytest.raises(ModelLoadError):
        OnnxClassifier(make_cfg(dummy_dir, model_card_path=str(cp)))


def test_missing_model_raises(tmp_path: Path):
    with pytest.raises(ModelLoadError):
        OnnxClassifier(AiCfg(backend="onnx", model_path=str(tmp_path / "nope.onnx"), labels_path=""))


def test_samples_dummy_model_committed():
    """ai/samples/ phải có sẵn model giả (dùng cho sim + benchmark)."""
    d = ROOT / "ai" / "samples"
    if not (d / "dummy_packaging_cls.onnx").exists():
        pytest.skip("chưa chạy python ai/export_dummy_model.py")
    clf = OnnxClassifier(AiCfg(backend="onnx", model_path=str(d / "dummy_packaging_cls.onnx"),
                               labels_path=str(d / "labels.json"), threads=1))
    assert clf.predict(bright_image()).label == LABEL_OK


# ---------------------------------------------------------------------------
# AI-02: prepare_dataset chia theo hộp (test nhỏ với ảnh giả)
# ---------------------------------------------------------------------------
def test_prepare_dataset_group_split(tmp_path: Path):
    from PIL import Image

    from prepare_dataset import prepare

    src = tmp_path / "dataset"
    rng = np.random.default_rng(0)
    for cls, n_box in (("ok", 10), ("tear", 5), ("dent", 5)):
        d = src / cls
        d.mkdir(parents=True)
        for b in range(n_box):
            for k in range(6):
                img = np.full((64, 80, 3), 200 if cls == "ok" else 40, np.uint8)
                img[0, 0] = rng.integers(0, 255, 3)
                Image.fromarray(img).save(d / f"box{cls}{b:02d}_20261028_12{k:04d}.jpg")
    (src / "ok" / "boxbad_20261028_120000.jpg").write_bytes(b"xx")  # ảnh hỏng

    out = tmp_path / "data"
    s = prepare(src, out, n_classes=2, seed=42)
    assert s.label_names == ["defect", "ok"]
    assert len(s.corrupted) == 1
    total = sum(sum(c.values()) for c in s.per_split.values())
    assert total == 120
    for split in ("train", "val", "test"):
        assert (out / split / "ok").is_dir() and (out / split / "defect").is_dir()
        assert s.per_split[split].get("ok", 0) > 0 and s.per_split[split].get("defect", 0) > 0
    # Không hộp nào nằm ở 2 tập
    split_json = json.loads((out / "split.json").read_text(encoding="utf-8"))
    box_split: dict[str, str] = {}
    for rec in split_json["images"]:
        assert box_split.setdefault(rec["box_id"], rec["split"]) == rec["split"]
    assert not any("xuất hiện ở" in w for w in s.warnings)
    # Gộp 4 lớp → defect, tên file có tiền tố lớp gốc
    assert any(p.name.startswith("tear__") for p in (out / "train" / "defect").iterdir())
