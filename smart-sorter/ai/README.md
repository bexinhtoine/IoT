# ai/ — AI kiểm tra bao bì (AI-01 → AI-07, P-05)

Phân loại ảnh hộp tại trạm chụp thành **OK / DEFECT** (tùy chọn loại lỗi tear / dent / crush / wet), chạy trên
Raspberry Pi 4 bằng ONNX Runtime (hoặc TFLite). Nguyên tắc AI-07: **ngưỡng ưu tiên an toàn** — thà loại nhầm hàng tốt
còn hơn để lọt hàng hỏng (recall lớp lỗi ≥ 95 %).

```
ai/
  train_packaging_damage.ipynb   notebook train CHÍNH THỨC của nhóm (ConvNeXt-V2 thầy → chưng cất MobileNetV4 trò, ONNX INT8)
  train_yolo_cls.py / .ipynb     đường 2: YOLOv8n-cls 224 (Colab, có ô mount Drive)
  train_mobilenetv2.py           đường 3: MobileNetV2 Keras → TFLite float16/int8
  prepare_dataset.py             AI-02: lọc ảnh hỏng, thống kê, chia 70/15/15 THEO HỘP, xuất cấu trúc ultralytics
  evaluate.py                    AI-05/AI-07: metrics, confusion_matrix.png, misclassified_grid.png, chọn ngưỡng, cập nhật model_card
  export_dummy_model.py          model ONNX giả (không cần torch) để test / sim / benchmark
  samples/                       dummy_packaging_cls.onnx + model_card.json + labels.json (được commit)
  requirements.txt               môi trường laptop/Colab (KHÔNG cài trên Pi)
pi/sorter/classifier_onnx.py     P-05/AI-06: OnnxClassifier + TfliteClassifier (đọc model_card.json)
pi/tools/benchmark_model.py      AI-05: 100 lần suy luận trên Pi → mean/p50/p95/max ms + RAM
```

## 1. Chụp dataset đúng cách (AI-01)

Dùng `pi/tools/capture_dataset.py` (module P3) **ngay tại trạm chụp**, với đúng đèn, đúng camera, đúng ROI sẽ dùng khi demo:

| Việc cần làm | Vì sao |
|---|---|
| Cùng đèn, cùng góc camera, cùng nền (mặt băng tải thật, không trải giấy) | Model học đúng điều kiện demo; ảnh khác đèn sẽ làm sai lệch xác suất |
| Khóa AE/AWB (P-02) trước khi chụp | Ảnh đồng đều giữa các lần chụp |
| Mỗi hộp chụp **6–10 ảnh**: xoay 0/90/180/270°, lệch trái/phải, hơi nghiêng, đặt lệch khỏi tâm ROI | Khi chia theo hộp vẫn đủ ảnh; model không học "vị trí" |
| Nhiều hộp khác nhau (kích thước, màu carton, nhãn dán, băng keo) | Tránh model học đặc điểm một hộp cụ thể |
| Lớp lỗi: nhiều **mức độ** (rách nhỏ 1 cm → rách lớn, móp nhẹ → bẹp góc, vết ướt nhỏ → loang), lỗi ở nhiều vị trí (cạnh, góc, mặt) | Ngưỡng recall 95 % chỉ có nghĩa khi có cả lỗi khó |
| Lớp OK: hộp lành nhưng có nhãn, băng keo, vết bẩn nhẹ, in logo, hộp cũ hơi xước | Giảm loại nhầm hàng tốt |
| Đặt tên theo hộp `dataset/<lớp>/<box_id>_<timestamp>.jpg` (capture_dataset làm sẵn) | Chia train/test theo hộp — ảnh cùng hộp không được ở cả hai tập |

Số lượng: **300–500 ảnh mỗi lớp** (AI-02), tương đương ~40–60 hộp/lớp. Tối thiểu để chạy được pipeline: 100 ảnh/lớp.
Lớp: mặc định 2 lớp `ok` / `defect`; nếu muốn 4 lớp thì chụp vào `tear`, `dent`, `wet` (`crush` nếu có) — `prepare_dataset.py --classes 2`
sẽ tự gộp các lớp lỗi về `defect`.

**Bổ sung ảnh khó khi model sai** (vòng active learning): ảnh bị loại được lưu ở `/var/lib/sorter/defects/` trên Pi, kèm
`ai_conf` (xác suất lớp đã chọn) và `ai_types_json` trong DB `packages`. Định kỳ chép về, xem lại:
- lỗi bị bỏ sót (thật DEFECT, model nói OK) → chụp thêm 20–30 ảnh cùng kiểu lỗi, nhiều hộp;
- hàng tốt bị loại (thật OK, model nói DEFECT/UNCERTAIN) → chụp thêm hộp lành có đặc điểm gây nhầm (nhãn tối, bóng đổ, băng keo đen);
- gán nhãn đúng vào `dataset/<lớp>/` với box_id mới, chạy lại `prepare_dataset.py` → train → `evaluate.py`;
- tăng `version` trong model_card (`--version 1.1.0`) để dashboard hiện đúng `model_version`.

## 2. Chuẩn bị dữ liệu (AI-02)

```
python ai/prepare_dataset.py --src dataset --out ai/data --classes 2        # hoặc --classes 4, --link để symlink
```
- loại ảnh hỏng (PIL verify), thống kê theo lớp và theo hộp, cảnh báo lớp lệch > 1,5 lần hoặc < 300 ảnh;
- chia 70/15/15 **theo box_id** (`GroupShuffleSplit`, seed 42), kiểm tra không hộp nào nằm ở 2 tập;
- xuất `ai/data/{train,val,test}/<lớp>/` (cấu trúc ultralytics classify) + `split.json`.

## 3. Ba đường huấn luyện (AI-03)

| | Notebook chính thức `train_packaging_damage.ipynb` | `train_yolo_cls.*` | `train_mobilenetv2.py` |
|---|---|---|---|
| Kiến trúc | ConvNeXt-V2-tiny (thầy) → chưng cất **MobileNetV4-conv-medium** (trò), 2 đầu: OK/Lỗi + loại lỗi | YOLOv8n-cls | MobileNetV2 (Keras) |
| Dữ liệu | ảnh tự chụp + Roboflow/Kaggle/HF/web (lọc CLIP, pHash), lỗi tổng hợp, sampler cân bằng | `ai/data` từ prepare_dataset | `ai/data` |
| Ảnh vào | 256×256 RGB 0..1, chuẩn hóa nhúng trong model | 224 RGB 0..1 | 224 RGB **−1..1** (`model_card.input.range`) |
| Xuất | `packaging_cls.onnx` (FP32 hoặc INT8 QDQ) — outputs `probs` [OK, DEFECT] + `type_probs` [tear, dent, crush, wet] | `packaging_cls.onnx` opset 12 — 1 output [N,C] | `packaging_cls_fp16.tflite`, `packaging_cls_int8.tflite` |
| Ngưỡng | temperature scaling + chọn trên val (recall ≥ 95 %) ngay trong notebook | `evaluate.py --update-card` | `evaluate.py` (chuyển sang ONNX) hoặc chọn tay |
| Backend Pi | `onnx` | `onnx` | `tflite` |
| Khi nào dùng | **Mặc định** — chính xác nhất, chạy Colab GPU ~1 h | khi cần train nhanh / ít ảnh | khi cần TFLite int8 |

Đường 2 trên Colab: mở `ai/train_yolo_cls.ipynb`, nén `dataset/` thành `MyDrive/smart_sorter/dataset.zip`, Runtime GPU, chạy
tất cả ô. Trên laptop:
```
python ai/train_yolo_cls.py --data ai/data --epochs 50 --out ai/runs/yolo_cls --version 1.0.0
python ai/evaluate.py --model ai/runs/yolo_cls/export/packaging_cls.onnx --data ai/data --update-card
```
Đường 3:
```
python ai/train_mobilenetv2.py --data ai/data --out ai/runs/mnv2
```

## 4. Đánh giá và chọn ngưỡng (AI-05, AI-07)

`ai/evaluate.py` chạy model **qua đúng `OnnxClassifier` của Pi** (tiền xử lý giống lúc triển khai):
- test: accuracy, precision/recall/F1 từng lớp, AUC, `confusion_matrix.png`, `misclassified_grid.png` (≤ 24 ảnh sai), `metrics.json`;
- val: quét `defect_threshold` 0,05–0,95, chọn ngưỡng **lớn nhất** còn đạt recall DEFECT ≥ 0,95 (tức `min_confidence`
  nhỏ nhất đạt mục tiêu — loại ít hàng tốt nhất có thể), ghi `defect_threshold`, `threshold_chosen_on`, `metrics` vào `model_card.json`.

Quy tắc quyết định trên Pi (`classifier_onnx.decide`):
```
p_defect >= defect_threshold → DEFECT  (confidence = p_defect)
p_ok     >= ai.min_confidence → OK     (confidence = p_ok)
còn lại                       → UNCERTAIN → orchestrator coi là lỗi (P-05), vào khay Loại
```

## 5. Đưa model lên Pi (AI-04, AI-06)

```
# trên laptop (3 file bắt buộc: model + labels + model_card)
scp packaging_cls.onnx labels.json model_card.json sorter@pi.local:/tmp/
# trên Pi
sudo mkdir -p /opt/sorter/models && sudo mv /tmp/{packaging_cls.onnx,labels.json,model_card.json} /opt/sorter/models/
sudo chown -R sorter:sorter /opt/sorter/models && sudo chmod 644 /opt/sorter/models/*
```
Trong `/etc/sorter/config.yaml`:
```yaml
ai:
  backend: onnx                  # dummy | onnx | tflite
  model_path: /opt/sorter/models/packaging_cls.onnx
  labels_path: /opt/sorter/models/labels.json
  model_card_path: ""            # rỗng = tự tìm model_card.json cạnh model (khuyên dùng)
  input_size: 224                # chỉ dùng khi KHÔNG có model_card (notebook nhóm dùng 256 — card tự ghi đè)
  min_confidence: 0.6
  threads: 4
```
`sudo systemctl restart sorter` → log phải có `OnnxClassifier sẵn sàng: packaging_cls.onnx v1.0.0 …`; `model_version`
được gửi lên ThingsBoard dạng attribute. Chạy sim trên laptop với model giả: đặt `backend: onnx`,
`model_path: ../ai/samples/dummy_packaging_cls.onnx`, `labels_path: ../ai/samples/labels.json` trong `pi/config.sim.yaml`.

TFLite: `backend: tflite`, `model_path: …/packaging_cls_int8.tflite`, cài `pip install tflite-runtime` trên Pi.

## 6. Benchmark trên Pi (AI-05)

```
cd ~/smart-sorter/pi
python tools/benchmark_model.py --config /etc/sorter/config.yaml --runs 100 --warmup 10 --image /var/lib/sorter/defects/<anh>.jpg
# hoặc không cần config:
python tools/benchmark_model.py --model /opt/sorter/models/packaging_cls.onnx --threads 4 --roi 320,160,640,640
```
In mean / p50 / p95 / max (ms, gồm crop + resize + chuẩn hóa + session.run), RAM RSS trước/sau, ghi `benchmark.json`.
Mục tiêu 5.3: **≤ 150 ms trung bình**. Nếu chậm: dùng bản INT8 của notebook, giảm `threads` về 4 (Pi 4 có 4 lõi), hoặc giảm
`img_size` xuống 224 trong notebook.

## 7. Bảng kết quả mẫu cho slide (điền số thật sau khi train)

| Chỉ số | Cách đo | Kết quả |
|---|---|---|
| Số ảnh dataset (OK / Lỗi), số hộp | `prepare_dataset.py` | ___ / ___ ảnh, ___ hộp |
| Accuracy trên test | `evaluate.py` → `metrics.json` | ___ % (mục tiêu ≥ 90 %) |
| Recall lớp Lỗi / Precision lớp Lỗi | `evaluate.py` | ___ % (≥ 95 %) / ___ % |
| Recall lớp OK (1 − tỉ lệ loại nhầm hàng tốt) | `evaluate.py` | ___ % |
| Ngưỡng DEFECT chọn trên val | `model_card.defect_threshold` | ___ |
| Thời gian suy luận trên Pi 4 (mean / p95) | `benchmark_model.py` 100 lần | ___ ms / ___ ms (≤ 150 ms) |
| RAM tiến trình khi nạp model | `benchmark_model.py` | ___ MB |
| Kích thước model | `ls -l` | ___ MB (FP32) / ___ MB (INT8) |
| Phiên bản model | `model_card.version` | ___ |

Số tham khảo với model giả trên laptop (chỉ để kiểm tra đường đi, KHÔNG đưa vào slide): mean ≈ 8 ms (trong đó session.run 0,3 ms),
RAM 37 → 72 MB. MobileNetV4-medium 256 px FP32 trên Pi 4 thường ở mức 60–120 ms, INT8 ~40–80 ms; YOLOv8n-cls 224 ~30–60 ms.

## 8. Test

```
cd pi && PYTHONUTF8=1 python -m pytest -q tests/test_classifier_onnx.py   # cần onnxruntime + onnx, tự skip nếu thiếu
python ai/export_dummy_model.py                                            # tạo lại ai/samples
```
