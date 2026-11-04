# Số liệu cho slide — Smart Logistics Sorter (P10)

Sinh bởi `pi/tools/report_metrics.py` lúc 2026-10-28 16:09. **Môi trường đo: laptop Windows 11 (máy dev), chế độ --sim: FakeEsp32 + FolderCamera 8 ảnh fixture, DummyClassifier trong pipeline, benchmark ONNX với model dummy — KHÔNG phải số đo trên Pi 4.**
Mỗi bảng ghi rõ nguồn; ô *chưa có số liệu* nghĩa là chưa chạy bước tương ứng (xem docs/RUNBOOK.md).
Cột **Số đo trên Pi thật** để trống cho nhóm điền sau khi chạy `report_metrics.py` trên Pi.

## 1. Độ trễ xử lý một kiện (P-08, tiêu chí 5.3 ≤ 1 s)

Nguồn: bảng `packages` trong `run\sorter.db` (16 kiện, từ 2026-10-28 15:47 đến 15:58).

| Bước | Cột DB | n | Trung bình | p95 | Max | Số đo trên Pi thật |
|---|---|---|---|---|---|---|
| Tổng (dừng băng -> SORT đã ACK) | `latency_ms` | 16 | 149 ms | 463 ms | 682 ms |  |
| Chụp ảnh (settle + capture) | `t_capture_ms` | 16 | 16 ms | 32 ms | 44 ms |  |
| Đọc QR (pyzbar/cv2) | `t_qr_ms` | 16 | 122 ms | 420 ms | 627 ms |  |
| Xác thực HMAC + chống trùng | `t_verify_ms` | 16 | 0.2 ms | 0.4 ms | 0.4 ms |  |
| AI phân loại bao bì | `t_ai_ms` | 8 | 0.0 ms | 0.1 ms | 0.1 ms |  |

`t_capture_ms` gồm settle_ms + thời gian chụp các khung (tối đa max_qr_frames); `latency_ms` tính tới khi ESP32 ACK lệnh SORT.

## 2. Số kiện theo quyết định (P-06, P-11)

| Quyết định | Số kiện | Tỉ lệ |
|---|---|---|
| SORT_A | 2 | 12.5 % |
| SORT_B | 2 | 12.5 % |
| REJECT_DEFECT | 4 | 25.0 % |
| REJECT_SECURITY | 8 | 50.0 % |
| REJECT_FAILSAFE | 0 | 0.0 % |
| **Tổng** | 16 | 100 % |

Theo kết quả QR: BAD_SIGNATURE = 2, DUPLICATE = 2, EXPIRED = 2, NO_QR = 2, VALID = 8.
Theo nhãn AI (chỉ kiện QR hợp lệ): DEFECT = 2, OK = 4, UNCERTAIN = 2.

## 3. Tỉ lệ phân loại đúng khay (tiêu chí 5.3: 20/20 kiện ở kịch bản 1–4)

| Kiện có nhãn mong đợi | Đúng khay | Tỉ lệ | Trạng thái |
|---|---|---|---|
| 5 | 5 | 100.0 % | ĐẠT |

## 4. Model AI kiểm tra bao bì (AI-04, AI-05, AI-07)

| Thuộc tính | Giá trị |
|---|---|
| Model / phiên bản | dummy_packaging_cls v0.0.0-dummy |
| Kiến trúc | heuristic: darkest-patch brightness (không học từ dữ liệu) |
| Độ chính xác số (precision) | fp32 |
| Ảnh vào | 224 px, RGB, range [0.0, 1.0] |
| Ngưỡng DEFECT (chọn trên val) | 0.6 (chọn: không (model giả)) |
| Mục tiêu recall lớp lỗi | 0.95 |

Đánh giá trên tập test: *chưa có số liệu* — cần dataset thật: `ai/prepare_dataset.py` → train → `ai/evaluate.py --update-card` rồi truyền `--metrics-json`.

## 5. Benchmark suy luận (AI-05, tiêu chí 5.3 ≤ 150 ms trung bình)

Nguồn: `pi/tools/benchmark_model.py` (50 lần, warm-up 10, ảnh `random`), máy: Windows-11-10.0.26300-SP0, AMD64, ORT 1.31.1.

| Chỉ số | Giá trị (máy đo này) | Số đo trên Pi thật | Mục tiêu | Trạng thái |
|---|---|---|---|---|
| Model | dummy_packaging_cls.onnx v0.0.0-dummy (onnx, 224 px, 4 threads) |  |  |  |
| Thời gian 1 lần suy luận — trung bình (gồm tiền xử lý) | 6.4 ms |  | ≤ 150 ms | ĐẠT |
| p50 / p95 / max | 6.1 ms / 7.7 ms / 8.1 ms |  | — |  |
| Chỉ session.run (trung bình) | 0.20 ms |  | — |  |
| Nạp model | 253 ms |  | — |  |
| RAM RSS trước → sau nạp model | 38 → 73 MB (đỉnh 73 MB) |  | — |  |

## 6. Sự kiện bảo mật và RPC (S-05, S-09)

| Loại sự kiện | Mức | Số lần |
|---|---|---|
| QR_BAD_SIGNATURE | MAJOR | 2 |
| QR_DUPLICATE | MAJOR | 2 |
| QR_EXPIRED | WARNING | 2 |

Audit RPC: chưa có lệnh nào (chạy sim không có RPC; trên dashboard bấm Start/Stop/E-stop để có dữ liệu).

## 7. Secure OTA (O-07, O-10 → O-12, tiêu chí 5.3: 3/3 kiểu tấn công bị từ chối)

Xác minh phía app (không cần ESP32): `python tools/demo_check.py --only 6a,6b,12` → 6a hợp lệ, 6b HASH_MISMATCH + SIGNATURE_INVALID, 12 ROLLBACK_REJECTED (xem bảng demo_check).

Kết quả trên ESP32 thật: *chưa có số liệu* — chạy 4 ca theo `esp32/OTA_TEST.md`, xuất CSV `device_reports` từ app OTA rồi truyền `--ota-reports`.

## 8. Phát hiện bất thường (A-03, A-06, tiêu chí 5.3: báo nhầm ≤ 1 lần / 30 phút)

Bảng `anomaly_windows`: chưa có (anomaly.enabled=false hoặc chưa chạy detector).

Tỉ lệ phát hiện / báo nhầm: *chưa có số liệu* — thu ≥ 30 phút dữ liệu bình thường, `sorter.anomaly.train`, chạy `tools/attack_sim.py`, rồi `sorter.anomaly.evaluate --out docs/anomaly_eval.md` và truyền `--anomaly-eval`.

## 9. Đối chiếu tiêu chí nghiệm thu 5.3

| Tiêu chí | Số đo hiện có | Trạng thái |
|---|---|---|
| Accuracy AI trên tập test ≥ 90 %, recall lớp Lỗi ≥ 95 % | chưa có số liệu | chưa có số liệu |
| Suy luận trên Pi ≤ 150 ms trung bình | 6.4 ms | ĐẠT |
| Dừng băng → lệnh SORT ≤ 1 s (trung bình latency_ms) | 149 ms | ĐẠT |
| Phân loại đúng khay 20/20 (kịch bản 1–4) | 5/5 | ĐẠT |
| E-stop từ dashboard: băng dừng ≤ 0,5 s | đo bằng video | kiểm trên phần cứng |
| QR giả và QR trùng bị chặn 100 % | 4/4 kiện bị chặn, 4 sự kiện bảo mật | ĐẠT |
| OTA tấn công bị từ chối 3/3 kiểu | demo_check 6b + 12: 3/3 (phía app) | ĐẠT (app); ESP32: kiểm trên phần cứng |
| Cảnh báo nhầm Isolation Forest ≤ 1 / 30 phút | xem mục 8 | chưa có số liệu |

---
Ghi chú: số đo trong file này là của máy đã chạy `report_metrics.py` (xem dòng *Môi trường đo* ở đầu). Số đo của sim trên laptop với model dummy **không** dùng để kết luận chất lượng AI; chỉ chứng minh đường đi dữ liệu và công thức tính.
