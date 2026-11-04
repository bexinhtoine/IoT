# ai/samples — model giả để test, chạy sim và benchmark

| File | Mô tả |
|---|---|
| `dummy_packaging_cls.onnx` | Model ONNX ~0.7 KB sinh bởi `python ai/export_dummy_model.py` (không học từ dữ liệu): tính độ sáng ô tối nhất (ô 56×56) → `probs` [OK, DEFECT] + `type_probs` [tear, dent, crush, wet]. Ảnh sáng đều → OK; ảnh có vết tối (rách/thủng) → DEFECT. |
| `model_card.json` | Cùng định dạng với notebook `ai/train_packaging_damage.ipynb`: `input.size/name/layout/range`, `outputs`, `defect_threshold`, `damage_types`, `version`. |
| `labels.json` | `{"classes": ["OK", "DEFECT"], "types": [...]}` |

Dùng:
```
# test
cd pi && PYTHONUTF8=1 python -m pytest -q tests/test_classifier_onnx.py
# benchmark đường ONNX Runtime (laptop hoặc Pi)
cd pi && python tools/benchmark_model.py --model ../ai/samples/dummy_packaging_cls.onnx --runs 100 --warmup 10
# chạy sorter sim với backend onnx: trong config.sim.yaml đặt
#   ai: {backend: onnx, model_path: ../ai/samples/dummy_packaging_cls.onnx, labels_path: ../ai/samples/labels.json}
```
Model này **không** dùng để demo thật — thay bằng `packaging_cls.onnx` train từ dataset của nhóm (xem `ai/README.md`).
`.gitignore` có ngoại lệ `!ai/samples/*.onnx` nên file này được commit.
