# Tiến độ dự án Smart Logistics Sorter

Cập nhật: 2026-10-28. Nguồn đặc tả: [SPEC.md](SPEC.md). Bảng truy vết từng mã chức năng: [traceability.md](traceability.md).

## 1. Tóm tắt trạng thái

| Module | Nội dung | Trạng thái | Kiểm chứng tại máy dev |
|---|---|---|---|
| P1 | Khung repo, `protocol.yaml`, `gen.py`, codec UART Python + C, test vectors | ✅ Hoàn thành | 22 test pytest pass; test C viết xong nhưng **chưa biên dịch** (máy dev không có gcc) |
| P2 | Firmware ESP32: FSM, motor, servo, sensor, LED/còi, E-stop, link UART, params NVS, STATUS, HW_TEST | ✅ Code xong | **Chưa build** (không có ESP-IDF trên máy dev); cần `idf.py build` + chạy `pi/tools/esp_bench.py --port` trên board thật |
| P3 | sorter-core trên Pi: config, Picamera2, QR, HMAC, decision, UART link, FakeEsp32, SQLite, counters, health, log JSON, orchestrator, CLI, tools, systemd, install script | ✅ Hoàn thành | pytest pass; `--sim` chạy đúng 8 ảnh mẫu; `esp_bench.py --fake` 16/16 PASS |
| P4 | OnnxClassifier/TfliteClassifier, benchmark, prepare_dataset, train (3 đường), evaluate, model dummy | ✅ Code xong | 13 test pass; chạy sim với backend onnx OK; **model thật chưa train** (cần dataset chụp tại trạm) |
| P5 | PKI (CA, cert server/device/OTA), docker-compose TB, tb_client TLS X.509, rpc_handler whitelist, widgets, rpc_cli, SETUP.md | ✅ Code xong | 74 test pass (mock MQTT); script PKI chạy thật; **chưa kết nối ThingsBoard thật** |
| P6 | harden_pi.sh, security_audit.sh, esp32/SECURITY.md, security_slide.md, threat_model.md | ✅ Hoàn thành | `bash -n` OK; chạy giả lập OK; **cần chạy trên Pi thật** |
| P7 | App Secure OTA (auth bcrypt+TOTP, khóa ECDSA, manifest, HTTPS server, lịch sử, demo tấn công, verify_cli) | ✅ Hoàn thành | 31 test pass (gồm HTTPS thật với CA tự tạo); UI smoke-test headless |
| P8 | ota_secure trên ESP32: Wi-Fi tạm, tải manifest/sig/bin qua HTTPS, ECDSA, chống rollback, SHA-256, app rollback + self-test, báo cáo | ✅ Code xong | **Chưa build/chạy** (cần ESP-IDF + board) |
| P9 | Isolation Forest: features, train, detector, rules, evaluate, attack_sim | ✅ Hoàn thành | 10 test pass; detector chạy trong sim |
| P10 | RUNBOOK, demo_check, report_metrics, traceability, demo_checklist | ✅ Hoàn thành | demo_check sim: 12 PASS / 1 MANUAL; 3 test mới; metrics.md sinh từ sim; truy vết 75/75 |

Tổng: `pi` **203 test pass**, `ota-app` **31 test pass** (Python 3.12, Windows). Khoảng 25 000 dòng mã và tài liệu.

## 2. Việc đã hoàn thành theo mã chức năng

- **E-01…E-12** (ESP32): đủ trong `esp32/components/{fsm,motor,servo,sensor,indicator,estop,link,params}`; E-13 → `ota_secure`; E-14 → tài liệu `esp32/SECURITY.md`.
- **P-01…P-16** (Pi): đủ trong `pi/sorter/` + `pi/tools/` + `pi/systemd/` + `pi/scripts/install_pi.sh`.
- **AI-01…AI-07**: `pi/tools/capture_dataset.py`, `ai/prepare_dataset.py`, 3 đường train, `ai/evaluate.py` (ngưỡng recall ≥ 95 %), `pi/sorter/classifier_onnx.py`, `pi/tools/benchmark_model.py`.
- **T-01…T-08**: `thingsboard/docker-compose.yml`, `tb_client.py`, `rpc_handler.py`, `widgets/`, hướng dẫn cấu hình trong `thingsboard/SETUP.md` (device profile, alarm, rule chain, user/2FA, dashboard).
- **S-01…S-09**: PKI + mutual TLS, 2FA (hướng dẫn), HMAC QR + chống trùng, TLS ≥ 1.2 không bao giờ bỏ xác minh, RPC whitelist + audit, UART CRC/SEQ, harden_pi.sh, SECURITY.md ESP32, security_events.
- **O-01…O-15**: `ota-app/` (O-01…O-07) và `esp32/components/ota_secure/` (O-08…O-15).
- **A-01…A-06**: `pi/sorter/anomaly/`, `pi/tools/attack_sim.py`, `docs/anomaly.md`.

## 3. Việc còn lại (cần phần cứng hoặc dữ liệu thật)

1. **Build & nạp firmware ESP32** (`idf.py build`), chạy `SORTER_HW_TEST` để kiểm tra đấu dây, rồi `pi/tools/esp_bench.py --port` 16 ca. Đối chiếu các API ESP-IDF được đánh dấu "đối chiếu docs" trong `esp32/README.md`.
2. **Chụp dataset thật** (≥ 300 ảnh/lớp) bằng `capture_dataset.py`, train bằng `ai/train_packaging_damage.ipynb` trên Colab, chạy `ai/evaluate.py`, copy model sang Pi, đo `benchmark_model.py` trên Pi 4 → điền `docs/metrics.md`.
3. **Dựng ThingsBoard** bằng Docker, tạo cert, cấu hình theo `thingsboard/SETUP.md` (device profile, alarm, rule chain, dashboard, user, 2FA). Chưa có file JSON dashboard import sẵn; dựng tay theo bảng widget rồi export.
4. **Cài Pi thật**: `install_pi.sh`, bật UART (`dtoverlay=disable-bt`), đặt khóa HMAC và cert quyền 600, `harden_pi.sh`, `security_audit.sh`.
5. **Secure Boot / Flash Encryption** trên board dự phòng theo `esp32/SECURITY.md` (ghi eFuse vĩnh viễn).
6. **OTA end-to-end** 4 ca (hợp lệ, sửa 1 byte, giả manifest, hạ phiên bản) + rollback theo `esp32/OTA_TEST.md`.
7. **Thu ≥ 30 phút dữ liệu bình thường** rồi `sorter.anomaly.train`; chạy `attack_sim.py` và `anomaly.evaluate`.
8. Chạy `pi/tools/demo_check.py --mode real` và diễn tập 12 kịch bản ít nhất 2 lần; đo latency thật, cập nhật `docs/metrics.md`.
9. Biên dịch và chạy `esp32/components/protocol/test/host_test.c` trên máy có gcc (`make test-c`).

## 4. Quyết định thiết kế đáng chú ý

- Camera CSI qua Picamera2, khóa AE/AWB sau 2 s; tham số `swap_rb` vì "RGB888" của picamera2 thường trả BGR — cần đối chiếu trên ảnh thật.
- Giao thức UART sinh từ một file YAML duy nhất cho cả C và Python; test vectors dùng chung.
- Mọi phụ thuộc chéo giữa module Pi đi qua `interfaces.py` (Classifier, TelemetrySink, SecurityEvent, MetricCounters) và `factories.py` (import lười) nên chạy được sim khi thiếu bất kỳ module nào.
- `OnnxClassifier` đọc `model_card.json` của notebook nhóm (input "image" NCHW 0..1, output `probs` + `type_probs`) và cũng chạy được model YOLOv8-cls 1 đầu ra.
- NVS key của tham số ESP32 là `p<id>` (giới hạn 15 ký tự); tên chỉ dùng để log.
- `key_id` OTA là 16 ký tự hex (8 byte đầu SHA-256 của public key DER); ESP32 so khớp `OTA_PUBKEY_ID` trong `ota_pubkey.h`.
- RPC `emergencyStop` được xử lý trước whitelist/rate limit; mọi lỗi nội bộ trả `{"ok": false}` chứ không ném ra MQTT.

## 5. Môi trường kiểm thử tại máy dev

Windows 11, Python 3.12.6, Node 22 (chỉ dùng để tạo báo cáo Word). Cần `set PYTHONUTF8=1` trước khi chạy python vì log tiếng Việt.
Không có gcc, ESP-IDF, Docker hay phần cứng tại máy dev; các mục "chưa build/chưa chạy" ở bảng trên là vì lý do này.

## 6. Sơ đồ trong báo cáo (hoàn thành 2026-10-29)

Báo cáo Word `docs/BAO_CAO_Smart_Logistics_Sorter.docx` (35 trang, kèm PDF) có 13 hình: kiến trúc 4 tầng (từ đặc tả) và 12 sơ đồ
vẽ bằng matplotlib trong `docs/report/make_diagrams.py` → `docs/report/assets/d01…d12_*.png`:
luồng dữ liệu, tuần tự xử lý một kiện, máy trạng thái ESP32, lưu đồ quyết định, khung UART + ACK/SEQ, tạo/xác thực QR,
tuần tự Secure OTA, pipeline AI, triển khai mạng, đấu nối ESP32, phát hiện bất thường, các lớp bảo mật.

Sinh lại: `cd docs/report && python make_diagrams.py && node build_report.js`, rồi mở bằng Word để cập nhật mục lục
(hoặc PowerShell COM: Documents.Open → Fields.Update → Save → ExportAsFixedFormat PDF).
