"""Detector: letterbox/NMS/crop RGB và nhánh reject khi không đúng một hộp."""
import numpy as np
import pytest

from sorter.box_detector import Box, BoxDetector, DetectedBoxClassifier, decode_boxes, letterbox
from sorter.config import DetectionCfg, load_config
from sorter.interfaces import Classifier, ClassResult
from sorter.factories import make_classifier


def raw(*rows):
    return np.array(rows, np.float32).reshape(-1, 5).T[None]


def test_letterbox_rgb_and_coordinates():
    image = np.zeros((100, 200, 3), np.uint8)
    image[..., 0] = 255
    tensor, ratio, pad = letterbox(image, 640)
    assert tensor.shape == (1, 3, 640, 640)
    assert ratio == 3.2 and pad == (0, 160)
    assert tensor[0, 0, 300, 100] == 1
    assert tensor[0, 2, 300, 100] == 0
    boxes = decode_boxes(raw([320, 320, 320, 160, 0.9]), (100, 200), ratio, pad,
                         DetectionCfg(padding=0))
    assert boxes[0].xyxy == (50, 25, 150, 75)


def test_nms_duplicates_and_multiple_distinct_boxes():
    cfg = DetectionCfg(padding=0)
    boxes = decode_boxes(raw([100, 100, 80, 80, 0.9], [101, 101, 80, 80, 0.8],
                             [400, 400, 100, 100, 0.8], [300, 300, 80, 80, 0.1]),
                         (640, 640), 1, (0, 0), cfg)
    assert len(boxes) == 2
    assert decode_boxes(raw(), (640, 640), 1, (0, 0), cfg) == []
    with pytest.raises(ValueError, match="NaN"):
        decode_boxes(raw([0, 0, 50, 50, np.nan]), (640, 640), 1, (0, 0), cfg)
    with pytest.raises(ValueError, match="output"):
        decode_boxes(np.zeros((1, 84, 8400)), (640, 640), 1, (0, 0), cfg)


class RecordingClassifier(Classifier):
    model_version = "test"
    def __init__(self):
        self.images = []
    def predict(self, image, roi=None):
        self.images.append(image.copy())
        return ClassResult("OK", 0.9)


class FakeDetector:
    def __init__(self, boxes):
        self.boxes = boxes
    def detect(self, image):
        return self.boxes


@pytest.mark.parametrize("count", [0, 2])
def test_no_or_multiple_boxes_never_classified_ok(count):
    classifier = RecordingClassifier()
    wrapper = DetectedBoxClassifier(classifier, FakeDetector([Box((0, 0, 5, 5), 0.9)] * count))
    result = wrapper.predict(np.zeros((100, 100, 3), np.uint8))
    assert result.label == "UNCERTAIN" and not result.is_ok
    assert classifier.images == []


def test_classifier_receives_only_box_inside_roi():
    classifier = RecordingClassifier()
    wrapper = DetectedBoxClassifier(classifier, FakeDetector([Box((2, 3, 8, 9), 0.9)]))
    image = np.random.default_rng(0).integers(0, 255, (50, 60, 3), dtype=np.uint8)
    assert wrapper.predict(image, (10, 20, 30, 20)).is_ok
    np.testing.assert_array_equal(classifier.images[0], image[23:29, 12:18])


def test_factory_real_onnx_detector_and_classifier(tmp_path):
    import onnx
    from onnx import TensorProto, helper, numpy_helper
    from pathlib import Path
    # Model test chỉ kiểm tra nối pipeline, không phải detector đã train.
    output = raw([320, 320, 320, 320, 0.9])
    node = helper.make_node("Constant", [], ["output0"], value=numpy_helper.from_array(output))
    graph = helper.make_graph([node], "test-detector",
        [helper.make_tensor_value_info("images", TensorProto.FLOAT, [1, 3, 640, 640])],
        [helper.make_tensor_value_info("output0", TensorProto.FLOAT, list(output.shape))])
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 17)])
    model.ir_version = 8
    helper.set_model_props(model, {"names": "{0: 'cardboard box'}"})
    target = tmp_path / "detector.onnx"
    onnx.save(model, target)
    cfg = load_config(Path(__file__).resolve().parents[1] / "config.model-check.yaml", validate_files=False)
    cfg.ai.detection = DetectionCfg(backend="yolo_world_onnx", model_path=str(target))
    classifier = make_classifier(cfg)
    try:
        result = classifier.predict(np.full((640, 640, 3), 128, np.uint8))
        assert result.model_version == "3-best-mobilenetv4-fp32"
        assert set(result.probs) == {"normal", "damage", "OK", "DEFECT"}
        assert len(classifier.last_boxes) == 1
    finally:
        classifier.close()
    helper.set_model_props(model, {"names": "{0: 'person'}"})
    onnx.save(model, target)
    with pytest.raises(ValueError, match="sai lớp"):
        BoxDetector(cfg.ai.detection)
