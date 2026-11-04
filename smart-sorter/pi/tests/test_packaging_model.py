"""Model thật từ 3-best: mapping nhãn, preprocessing và PyTorch/ONNX parity."""
import json
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

from sorter.classifier_onnx import OnnxClassifier, ModelLoadError, build_spec, interpret, preprocess
from sorter.config import AiCfg, load_config

AI = Path(__file__).resolve().parents[2] / "ai"
PI = AI.parent / "pi"


def config():
    return load_config(PI / "config.model-check.yaml", validate_files=False).ai


def test_real_model_contract_and_integrity():
    import hashlib
    cfg = config()
    card = json.loads(Path(cfg.model_card_path).read_text(encoding="utf-8"))
    assert hashlib.sha256(Path(cfg.model_path).read_bytes()).hexdigest() == card["onnx_sha256"]
    clf = OnnxClassifier(cfg)
    try:
        assert clf.spec.classes == ["normal", "damage"]
        assert clf.spec.input_size == 256
        assert clf.spec.mean is None and clf.spec.std is None  # nằm trong graph
        for p, label in [([0.9, 0.1], "OK"), ([0.1, 0.9], "DEFECT"), ([0.55, 0.45], "UNCERTAIN")]:
            result = interpret({"probabilities": np.array([p])}, clf.spec, 0)
            assert result.label == label
            assert result.defect_types == {}  # model chỉ phân loại nhị phân
        with pytest.raises(ModelLoadError, match="NaN/Inf"):
            interpret({"probabilities": np.array([[np.nan, 0]])}, clf.spec, 0)
        with pytest.raises(ModelLoadError, match="không hợp lệ"):
            interpret({"probabilities": np.array([[0.9, 0.9]])}, clf.spec, 0)
    finally:
        clf.close()


def test_rgb_resize_and_divide_match_notebook():
    spec = build_spec(config())
    image = np.random.default_rng(42).integers(0, 256, (81, 143, 3), dtype=np.uint8)
    actual = preprocess(image, (7, 9, 100, 60), spec)
    expected = cv2.resize(image[9:69, 7:107], (256, 256), interpolation=cv2.INTER_AREA)
    expected = expected.transpose(2, 0, 1)[None].astype(np.float32) / 255.0
    np.testing.assert_allclose(actual, expected, atol=1e-7)
    with pytest.raises(ValueError, match="uint8"):
        preprocess(image.astype(np.float32), None, spec)


def test_labels_cannot_silently_swap(tmp_path):
    labels = tmp_path / "labels.json"
    labels.write_text('["damage", "normal"]')
    cfg = config()
    cfg.labels_path = str(labels)
    with pytest.raises(ModelLoadError, match="thứ tự"):
        build_spec(cfg)


def test_wrong_model_hash_rejected(tmp_path):
    cfg = config()
    card = json.loads(Path(cfg.model_card_path).read_text(encoding="utf-8"))
    card["onnx_sha256"] = "0" * 64
    target = tmp_path / "model_card.json"
    target.write_text(json.dumps(card), encoding="utf-8")
    cfg.model_card_path = str(target)
    with pytest.raises(ModelLoadError, match="SHA-256"):
        OnnxClassifier(cfg)


def test_dataset_tools_recognize_normal_label(tmp_path):
    sys.path.insert(0, str(AI))
    from prepare_dataset import map_label
    from evaluate import list_split
    assert map_label("normal", 2) == "ok"
    for cls in ("normal", "damage"):
        directory = tmp_path / "test" / cls
        directory.mkdir(parents=True)
        cv2.imwrite(str(directory / "box.jpg"), np.zeros((30, 30, 3), np.uint8))
    samples = list_split(tmp_path, "test")
    assert {s.cls: s.y for s in samples} == {"normal": 0, "damage": 1}


def test_real_model_matches_checkpoint_on_rgb_image():
    torch = pytest.importorskip("torch")
    pytest.importorskip("timm")
    sys.path.insert(0, str(AI))
    from packaging_net import load_model
    torch.set_num_threads(2)
    model = load_model(AI / "best_model.pth")
    clf = OnnxClassifier(config())
    try:
        bgr = cv2.imread(str(PI / "tests/fixtures/images/01_ok_A.jpg"))
        image = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        tensor = preprocess(image, None, clf.spec)
        with torch.inference_mode():
            expected = torch.softmax(model(torch.from_numpy(tensor)), dim=1).numpy()
        actual = clf.session.run(None, {clf.input_name: tensor})[0]
        np.testing.assert_allclose(actual, expected, atol=1e-5, rtol=1e-4)
        result = clf.predict(image)
        assert result.probs["normal"] == pytest.approx(float(expected[0, 0]), abs=1e-4)
    finally:
        clf.close()
