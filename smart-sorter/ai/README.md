# AI kiểm tra bao bì — model 3-best

Model triển khai chính thức là **MobileNetV4 từ `3-best.ipynb` của nhóm**. Không cần train lại. Chương trình Pi chỉ đọc các file đã xuất trong `models/`; không export, tải model hoặc import PyTorch khi khởi động.

## File cần dùng

| File | Vai trò |
|---|---|
| `3-best.ipynb` | Notebook train gốc; khóa Roboflow đọc từ `ROBOFLOW_API_KEY` |
| `best_model.pth` | Checkpoint tốt nhất theo validation average precision, nguồn export chính |
| `packaging_model_weights.pth` | Trọng số export; đã đối chiếu từng tensor và giống hệt `best_model.pth` |
| `packaging_model_full.pth` | Bản lưu nguyên module từ notebook; giữ làm bản gốc, runtime không dùng |
| `packaging_net.py` | Định nghĩa lại đúng kiến trúc notebook, nạp state_dict strict |
| `export_model.py` | Chỉ chạy khi đổi checkpoint, xuất ONNX và kiểm tra parity |
| `models/packaging_cls.onnx` | Model phân loại FP32 đã xuất sẵn, dùng trực tiếp trên Pi |
| `models/labels.json`, `models/model_card.json` | Nhãn, input, ngưỡng, SHA-256 và kết quả kiểm tra chuyển đổi |
| `export_detector.py` | Chuẩn bị YOLO-World khi thay model; `models/box_detector.onnx` đã xuất sẵn |
| `prepare_dataset.py`, `evaluate.py` | Chia tập theo hộp và đánh giá/hiệu chỉnh trên dữ liệu mới |
| `export_dummy_model.py`, `samples/` | Chỉ phục vụ unit test; không dùng đánh giá chất lượng model thật |

Đã bỏ các notebook trùng ở gốc và các đường train ConvNeXt/distillation, YOLO classification, Keras MobileNetV2 cũ. Chế độ dummy/samples vẫn được giữ để test QR, UART, orchestrator độc lập với model thật.

## Quy trình ảnh và quyết định

```text
Picamera2 RGB → xác thực QR → camera.roi
    → YOLO-World tìm cardboard box (nếu bật)
    → phải có đúng 1 hộp sau confidence, lọc diện tích và NMS
    → crop hộp + padding 10%, giới hạn trong ROI
    → resize 256×256 INTER_AREA → float32 / 255 → NCHW
    → MobileNetV4 ONNX (mean/std đã nằm trong graph)
    → probabilities [normal, damage]
    → OK / DEFECT / UNCERTAIN → luật QR + AI → UART
```

Không chuẩn hóa ImageNet lần thứ hai. Không đổi RGB thành BGR trước classifier. Không áp dụng center crop/letterbox của detector cho classifier: notebook dùng resize trực tiếp.

Nhãn **0 = normal → OK**, **1 = damage → DEFECT**. Đây là classifier nhị phân, không suy ra rách/móp/ướt; `defect_types` luôn rỗng. `p_damage >= 0.5` là DEFECT; nếu chưa đạt thì cần `p_normal >= 0.6` mới OK, còn lại UNCERTAIN. Ngưỡng 0.6 là chính sách thận trọng của hệ thống, chưa được hiệu chỉnh tại trạm. Vì vậy các metric argmax notebook không phải metric của chính sách này.

Detector không tìm được hộp hoặc thấy nhiều hộp → UNCERTAIN và loại hàng. Model lỗi/NaN/Inf → exception và orchestrator REJECT_FAILSAFE. Không âm thầm phân loại cả nền khi detector thất bại. QR vẫn đọc trên khung đầy đủ, không phụ thuộc việc crop AI.

## Thử ngay classifier đã xuất

Từ thư mục `smart-sorter`:

```bash
python -m pip install -r pi/requirements.txt
python pi/tools/inspect_package.py --config pi/config.model-check.yaml --image /path/to/box.jpg --full-frame --output pi/run/result.jpg
```

Ảnh ở lệnh này phải là ảnh một hộp/ảnh đã crop. Cấu hình `config.model-check.yaml` tắt detector để thử riêng classifier, không cần HMAC/camera/UART/ThingsBoard. Không dùng nó để điều khiển băng chuyền. JSON có xác suất hai lớp và ảnh output có nhãn kết quả.

## Dùng detector đã xuất sẵn

Repo đã có `models/box_detector.onnx` (YOLO-World v2 small, prompt cardboard box). Thử ngay cả luồng:

```bash
python pi/tools/inspect_package.py --config pi/config.vision-check.yaml --image /path/to/frame.jpg --full-frame --output pi/run/vision-result.jpg
```

`--full-frame` chạy tìm hộp trên toàn ảnh. Bỏ cờ này nếu đã căn `camera.roi`. Xem [báo cáo kiểm tra và giới hạn](../docs/AI_VALIDATION.md).

## Xuất lại detector khi cần

YOLO-World hỗ trợ prompt `cardboard box`, không dùng detector COCO thông thường rồi giả định COCO có lớp hộp carton. Đây là detector zero-shot, chưa có số liệu recall trên băng chuyền của nhóm.

Trên laptop có mạng (từ `smart-sorter`):

```bash
python -m pip install -r ai/requirements-detector.txt
python ai/export_detector.py --model yolov8s-worldv2.pt --out ai/models --size 640
```

Bước này tải trọng số YOLO-World và bộ mã hóa CLIP để nhúng prompt vào ONNX. Khi đã có `box_detector.onnx`, Pi không cần Ultralytics/CLIP/PyTorch và không tải lại. Runtime kiểm tra model có đúng lớp cardboard box và output [1,5,N]. Có thể đổi kích thước detector khi export nhưng phải cập nhật `ai.detection.input_size` tương ứng.

Bật detector trong bản sao `pi/config.model-check.yaml`:

```yaml
  detection:
    backend: yolo_world_onnx
    model_path: ../ai/models/box_detector.onnx
    confidence: 0.35
    iou_threshold: 0.5
    input_size: 640
    padding: 0.1
    min_area_ratio: 0.01
```

`confidence` của detector độc lập với `min_confidence` của classifier. Thử ảnh toàn cảnh bằng `inspect_package.py`; bỏ `--full-frame` để dùng ROI đã cấu hình. `boxes_in_roi` và log ghi tọa độ, score hộp; ảnh output vẽ box có padding. NMS gộp dự đoán trùng trước khi kiểm tra số hộp.

## Triển khai Raspberry Pi

Script `pi/scripts/install_pi.sh` tự cài bộ model xuất sẵn khi chưa có model trong `/opt/sorter/models/`; không ghi đè bộ model đã tồn tại. Nếu triển khai thủ công hoặc cập nhật model, copy **cùng phiên bản** các file ONNX, labels và model_card vào `/opt/sorter/models/`. Nếu bật detector, copy thêm `box_detector.onnx`. Không cần copy `.pth` lên Pi.

```bash
scp ai/models/packaging_cls.onnx ai/models/labels.json ai/models/model_card.json sorter@pi.local:/tmp/
# Nếu đã xuất detector:
scp ai/models/box_detector.onnx sorter@pi.local:/tmp/
```

Trên Pi, chuyển các file từ `/tmp/` vào `/opt/sorter/models/` bằng sudo, đảm bảo user sorter đọc được. Dùng `pi/config.example.yaml` làm cấu hình `/etc/sorter/config.yaml`; mẫu này bật detector, nên phải có file detector trước khi chạy. Nếu chưa chuẩn bị detector, đặt `ai.detection.backend: none` và căn ROI cố định chứa đúng một hộp; chế độ này **không xác minh có hộp**.

Classifier dùng input_size 256, threads 4; detector dùng 640. Chỉnh `camera.roi` theo camera thật, khóa AE/AWB, ánh sáng ổn định. Chạy:

```bash
python pi/tools/inspect_package.py --config /etc/sorter/config.yaml --image /path/to/camera-frame.jpg --output /tmp/check.jpg
python pi/tools/benchmark_model.py --config /etc/sorter/config.yaml --image /path/to/camera-frame.jpg --runs 100 --warmup 10 --out /tmp/benchmark.json
sudo systemctl restart sorter
```

Benchmark dùng cả detector nếu được bật. Phải đo thời gian đầy đủ trên Pi và đối chiếu timeout quyết định ESP32; không tăng timeout một cách tùy ý để che model quá chậm. Chỉ bật băng sau khi xác nhận crop hộp đúng, đủ trường hợp hộp lỗi/tốt, nền trống, nhiều hộp và mất kết nối. Nếu detector quá chậm, có thể export input nhỏ hơn rồi đánh giá lại độ phủ, hoặc fine-tune detector nhỏ trên ảnh trạm.

## Khi có checkpoint mới

```bash
python -m pip install -r ai/requirements.txt
python ai/export_model.py --checkpoint ai/best_model.pth --out ai/models
```

Script nạp `weights_only=True`, strict state_dict, export FP32 + Softmax + normalization bên trong graph; chạy ONNX checker và so sánh 5 đầu vào với PyTorch (rtol 1e-4, atol 1e-5, cùng argmax). Lần export hiện tại có max_abs_error 2.384185791015625e-7. Đây là kiểm tra chuyển đổi, không phải đo accuracy.

## Đánh giá và giới hạn số liệu

Output notebook báo accuracy 0.9725, precision damage 0.9827, recall damage 0.9703, F1 0.9765, AP 0.9915. Chưa tái đánh giá vì repo không có tập test gốc. Không diễn giải thành độ chính xác thực tế trên Pi hoặc của detector.

Notebook gộp dữ liệu rồi chia ngẫu nhiên theo ảnh (có nguồn đã chia train/valid/test), nên có nguy cơ ảnh cùng hộp/ảnh gần trùng nằm ở các tập khác nhau. Notebook cũng định nghĩa PATIENCE nhưng vòng train chưa áp dụng early stopping. Không sửa lại lịch sử train của checkpoint; nếu train mới, dùng group split theo hộp, khử trùng trước khi chia và giữ tập test độc lập. Các số liệu hiện có chỉ là tham khảo.

Thu ảnh camera tại trạm và gán nhãn `normal/damage`, tách val/test theo hộp; công cụ `prepare_dataset.py` hỗ trợ group split cho dữ liệu thu tại trạm. `evaluate.py` nhận thư mục `val/normal`, `val/damage`, `test/normal`, `test/damage`:

```bash
python ai/evaluate.py --model ai/models/packaging_cls.onnx --data ai/data --out ai/evaluation --update-card
```

Chỉ chọn ngưỡng trên val, đo lại trên test độc lập. Lệnh này đánh giá classifier trên ảnh hộp đã crop; đánh giá detector + classifier cần ảnh toàn cảnh và ground truth hộp riêng. Chưa có dữ liệu đó trong repo. Một ảnh nguồn chỉ có annotation box vẫn được notebook gán normal do không thấy từ khóa defect; cần rà soát cách gán nhãn này trước khi train tiếp. Khóa Roboflow từng có trong notebook cần được thu hồi/đổi trong tài khoản; bản notebook mới chỉ đọc biến môi trường.

## Kiểm thử

```bash
cd pi
python -m pytest -q
```

Test mới kiểm tra model thật và hash, tiền xử lý, nhãn, parity, NMS, crop đúng vùng, không hộp/nhiều hộp, sai model detector và kết nối factory với ONNX. Detector ONNX tổng hợp trong unit test chỉ xác minh đường đi mã; không chứng minh detector nhận ra hộp thật.

Nguồn: [YOLO-World Ultralytics](https://docs.ultralytics.com/models/yolo-world/), [torch.load](https://docs.pytorch.org/docs/stable/generated/torch.load.html).
