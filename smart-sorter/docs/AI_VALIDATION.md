# Kiểm tra tích hợp AI — 2026-11-04

Phần mềm đã tích hợp model của nhóm; chất lượng phân loại thực tế và thời gian trên Pi chưa được nghiệm thu.

## Artifacts triển khai

- `ai/models/packaging_cls.onnx`: MobileNetV4 FP32 từ `best_model.pth`, RGB NCHW 256, đầu ra xác suất `[normal, damage]`. Mean/std nằm trong graph.
- `ai/models/box_detector.onnx`: YOLO-World v2 small với prompt duy nhất `cardboard box`, input 640, output `[1,5,8400]`. License AGPL-3.0 theo metadata model; nguồn và SHA-256 trong `detector_card.json`.
- Hai file ONNX đã xuất sẵn; runtime Pi chỉ dùng OpenCV và ONNX Runtime, không PyTorch/CLIP/Ultralytics và không tự chuyển đổi hoặc tải lại.
- `best_model.pth` và `packaging_model_weights.pth` được so sánh mọi tensor, giống hệt nhau. File full-module giữ nguyên, không unpickle để chạy.

## Kiểm tra chuyển đổi

Classifier: 5 đầu vào đen/trắng/gradient/ngẫu nhiên, max_abs_error `2.384185791015625e-7`, cùng argmax; ONNX checker đạt. Ngoài ra đối chiếu preprocessing trên ảnh RGB thật với PyTorch checkpoint.

Detector: kiểm tra ONNX checker, metadata một lớp, shape và dữ liệu hữu hạn. Đối chiếu PyTorch với ONNX trên cùng tensor letterbox của ảnh thử: raw max_abs_error `0.00799560546875`, allclose đạt `atol=1e-3, rtol=1e-3`. Phần tọa độ thô có đơn vị pixel nên không so sai lệch này với xác suất classifier.

Toàn bộ test phần Pi: **223 passed**. Bao gồm model thật/hash, RGB/resize/range, thứ tự nhãn, finite output, NMS, crop, zero/multiple boxes, factory, config, QR/UART/orchestrator. ONNX detector tổng hợp trong unit test chỉ kiểm tra đường đi mã, không đánh giá detection accuracy.

## Thử ảnh thật

Nguồn ảnh: [dataset3_resize/01_1_1.jpg](https://github.com/chanllon/corrugated-cardboard-boxes-dataset/blob/master/dataset3_resize/01_1_1.jpg), thuộc nguồn dataset mà notebook đã sử dụng. Đây không phải ảnh test độc lập và không dùng để kết luận accuracy.

- Detector tìm được 1 hộp, score `0.3944`; crop có padding theo cấu hình.
- Classifier ONNX: normal `0.9772`, damage `0.0228` → OK.
- Chạy PyTorch trực tiếp trên đúng crop: normal `0.9772002101`, damage `0.0227998532`. Kết quả khớp, không phải lỗi đảo nhãn/chuẩn hóa/chuyển ONNX.
- Bằng mắt có thể thấy vùng móp/biến dạng ở mép hộp; nếu tiêu chuẩn vận hành coi đó là lỗi thì đây là trường hợp cần đánh giá lại và bổ sung dữ liệu. Không thể bảo đảm checkpoint luôn phát hiện được lỗi này.
- Annotation nguồn chỉ ghi `box`, không có nhãn trạng thái normal/damage. Notebook hiện coi không có từ khóa defect là normal; quy tắc này có thể tạo nhãn sai nếu annotation chưa đầy đủ. Không coi thiếu nhãn defect là xác nhận hộp nguyên vẹn khi train tiếp.
- Khung RGB trống màu 114 → detector 0 hộp → UNCERTAIN; không gọi classifier để cho qua.

Ảnh vẽ box kiểm tra nằm tại `pi/run/vision-check.jpg` trên máy phát triển, không đưa ảnh dataset bên ngoài vào commit.

## Đo trên laptop

Windows, AMD Ryzen 7 6800H, CPUExecutionProvider, 4 threads, ONNX Runtime 1.31.1; ảnh 1512×2016, detector input 640, classifier input 256. Warm-up 3, đo 20 lần cả detector + crop + classifier:

| Chỉ số | Kết quả |
|---|---:|
| Mean | 243.77 ms |
| p50 | 248.73 ms |
| p95 | 277.07 ms |
| Max | 283.75 ms |
| RSS sau đo | 295.4 MiB |

Đây là số laptop, **không phải Pi 4**. Cả pipeline chưa đạt mục tiêu 150 ms ngay trên lượt đo laptop này; phải đo lại trên Pi trước khi chốt tốc độ/timeout. Không suy ra phần classifier riêng cũng chậm như cả detector. Benchmark không tính chụp ảnh, đọc/xác thực QR, UART hoặc toàn bộ thời gian từ sensor đến gạt.

## Việc cần để nghiệm thu chất lượng

1. Chốt định nghĩa lỗi (móp bao nhiêu, rách nhỏ, mép dập, ướt...), rà nhãn các nguồn chỉ có bounding box.
2. Thu val/test độc lập ở đúng camera và ánh sáng trạm; chia theo hộp và nguồn, khử ảnh trùng/gần trùng trước khi chia.
3. Đánh giá detector trên toàn cảnh (recall, crop sai, nền trống, nhiều hộp), classifier trên crop và toàn bộ luồng trên cùng tập độc lập.
4. Chọn threshold trên val, báo recall lỗi + tỷ lệ loại nhầm trên test; không tăng threshold tùy ý để làm đẹp một ảnh thử.
5. Đo mean/p95/max trên Pi, kiểm tra timeout ESP32 và thử các lỗi hệ thống trước khi chạy băng thật.

Số liệu notebook accuracy 97.25%, recall damage 97.03% là output được cung cấp, chưa tái đánh giá trên tập test gốc hoặc tại trạm.
