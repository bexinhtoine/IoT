# Bảng truy vết 75 mã chức năng (P10)

Đối chiếu `docs/SPEC.md` mục 3 với mã nguồn trong repo (ngày 2026-10-28). Mỗi mã có: file hiện thực (đường dẫn thật, mã
xuất hiện trong comment/docstring hoặc nội dung file), test/kịch bản chứng minh, trạng thái.

Quy ước trạng thái:

| Trạng thái | Nghĩa |
|---|---|
| **Đã code + test** | Có mã và có pytest tự động chạy xanh trên máy dev (`cd pi && python -m pytest` → 203 test; `cd ota-app && python -m pytest` → 31 test) |
| **Đã code** | Có mã, chạy được bằng tay/sim nhưng chưa có pytest riêng hoặc cần dữ liệu thật mới ra số liệu |
| **Cần kiểm trên phần cứng** | Có mã (firmware ESP32 chưa build vì máy dev không có ESP-IDF; phần Pi cần camera/UART/ThingsBoard thật); cách kiểm ghi ở cột test |
| **Tài liệu/cấu hình thủ công** | Thực hiện bằng cấu hình ThingsBoard, eFuse, tài khoản… theo tài liệu trong repo; không phải mã chạy được |

Kịch bản demo "KB n" = bảng 5.1/5.2 của SPEC, chạy tự động ở sim bằng `pi/tools/demo_check.py` (KB 1–8, 10–12; KB 9 thủ công),
hoặc trên phần cứng bằng `demo_check.py --mode real`. "esp_bench ca n" = `pi/tools/esp_bench.py` (16 ca, `--fake` tự kiểm với FakeEsp32).

## 3.1 ESP32 — firmware chấp hành (E)

| Mã | Hiện thực | Test / kịch bản chứng minh | Trạng thái |
|---|---|---|---|
| E-01 | `esp32/components/fsm/fsm.c` (bảng trạng thái đầu file), `fsm.h`; bản giả lập `pi/sorter/fake_esp32.py` (`ALLOWED_STATES` → NACK BAD_STATE) | `pi/tests/test_uart_link.py::test_start_ack_and_bad_state`, `::test_sensor_only_when_running` (trên FakeEsp32); esp_bench ca 3, 8; KB 5, 8 | Cần kiểm trên phần cứng |
| E-02 | `esp32/components/motor/motor.c` (LEDC 1 kHz, ramp 200 ms bằng esp_timer, `motor_stop_from_isr`) | `SORTER_HW_TEST` (menuconfig) quay motor 2 s; esp_bench ca 8 (START → băng chạy); KB 1 | Cần kiểm trên phần cứng |
| E-03 | `esp32/components/sensor/sensor.c` (TCRT5000 ngắt + 20 ms / HC-SR04 task 30 ms, 2 lần dưới ngưỡng), `main/Kconfig.projbuild` | esp_bench ca 10 (đưa tay qua cảm biến → EVT_PKG_DETECTED); KB 1–4 | Cần kiểm trên phần cứng |
| E-04 | `esp32/components/servo/servo.c` (LEDC 50 Hz), `fsm.c` (SORT → gạt A/B, chạy t_clear, EVT_SORT_DONE) | esp_bench ca 10, 11 (SORT A/B/REJECT → EVT_SORT_DONE); KB 1, 2, 3 | Cần kiểm trên phần cứng |
| E-05 | `esp32/components/fsm/fsm.c` (DETECTED quá t_decision_timeout → REJECT FAILSAFE + EVT_FAULT(DECISION_TIMEOUT)); giả lập `fake_esp32.py::_tick` | `pi/tests/test_uart_link.py::test_decision_timeout_failsafe`, `test_orchestrator.py::test_sort_nack_recorded_as_failsafe`; esp_bench ca 5 | Cần kiểm trên phần cứng |
| E-06 | `esp32/components/indicator/indicator.c` (pattern LED/còi, esp_timer 50 ms) | `SORTER_HW_TEST`; quan sát KB 1 (bíp ngắn), 3 (đỏ 1 s), 4 (đỏ nháy + 3 bíp), 5/8 (ESTOP), 11 (FAULT) | Cần kiểm trên phần cứng |
| E-07 | `esp32/components/estop/estop.c` (GPIO39 ANYEDGE, tắt PWM trong ISR), `motor.c::motor_stop_from_isr`, `fsm.c` (ESTOP_RESET chỉ khi nút nhả) | `pi/tests/test_uart_link.py::test_estop_physical_and_remote` (FakeEsp32); esp_bench ca 14; KB 8 (`demo_check` sim: reset khi còn bấm → BAD_STATE) | Cần kiểm trên phần cứng |
| E-08 | `esp32/components/fsm/fsm.c` (lệnh ESTOP → EVT_ESTOP(REMOTE)); Pi: `pi/sorter/rpc_handler.py` `emergencyStop` → UART ESTOP | `pi/tests/test_rpc_handler.py::test_emergency_stop_ok_in_any_state`; esp_bench ca 13; KB 5 | Cần kiểm trên phần cứng |
| E-09 | `esp32/components/link/link.c` (watchdog 2000 ms → FAULT LINK_LOST), `fsm.c`, `sdkconfig.defaults` (TWDT 5 s); Pi gửi PING: `pi/sorter/uart_link.py::_ping_loop` | `pi/tests/test_uart_link.py::test_fake_link_watchdog_fault`, `::test_link_lost_pi_side`; esp_bench ca 12; KB 11 | Cần kiểm trên phần cứng |
| E-10 | `esp32/components/protocol/protocol.c` + `include/protocol_gen.h` (sinh từ `shared/protocol/protocol.yaml`), `link/link.c` (ACK/NACK, gửi lại, chống lặp SEQ, đếm CRC) | `pi/tests/test_uart_codec.py::test_vectors` (test_vectors.json chung Python/C), `esp32/components/protocol/test/host_test.c` (`make test-c`, chưa biên dịch trên máy dev); esp_bench ca 6, 7, 9 | Cần kiểm trên phần cứng |
| E-11 | `esp32/components/params/params.c` (NVS "params", miền từ `PROTO_PARAMS`), `fsm.c` SET_PARAM | `pi/tests/test_uart_link.py::test_set_param_range` (FakeEsp32 cùng bảng miền); esp_bench ca 4, 5 | Cần kiểm trên phần cứng |
| E-12 | `esp32/components/link/link.c` (STATUS mỗi 1 s), `main/version.h` (fw_version_code); Pi đọc: `orchestrator.py::_update_fw_version` | esp_bench ca 2; `test_orchestrator.py::test_end_to_end_eight_images` (attribute `fw_version`) | Cần kiểm trên phần cứng |
| E-13 | Trỏ O-08 → O-15: `esp32/components/ota_secure/ota_secure.c`, `esp32/partitions.csv` (ota_0/ota_1, otadata) | `esp32/OTA_TEST.md` (6a, 6b, 12, rollback); KB 6a/6b/12 | Cần kiểm trên phần cứng |
| E-14 | `esp32/SECURITY.md` (Secure Boot V2, Flash Encryption Development, JTAG_DISABLE), `esp32/sdkconfig.defaults` | Kiểm tra tay trên board dự phòng: `espefuse.py summary` trước/sau (`ABS_DONE_1`, `FLASH_CRYPT_CNT`, `JTAG_DISABLE`); nạp app không ký → không boot | Tài liệu/cấu hình thủ công |

## 3.2 Raspberry Pi — sorter-core (P)

| Mã | Hiện thực | Test / kịch bản chứng minh | Trạng thái |
|---|---|---|---|
| P-01 | `pi/sorter/config.py` (dataclass, kiểm tra kiểu/miền/file, không hardcode bí mật), `pi/config.example.yaml`, `pi/sorter/factories.py` | `pi/tests/test_config.py` (7 test: sim config, ROI, khóa lạ, sai kiểu, thiếu file khóa…) | Đã code + test |
| P-02 | `pi/sorter/camera.py::Picamera2Camera` (mở một lần, khóa AE/AWB sau `lock_after_s`, `swap_rb`) | Kiểm tra tay trên Pi: `tools/capture_dataset.py` chụp thử, log "Đã khóa AE/AWB"; `FolderCamera` (sim) được test trong `test_orchestrator.py` | Cần kiểm trên phần cứng |
| P-03 | `pi/sorter/qr_reader.py` (pyzbar → cv2.QRCodeDetector, CLAHE, MULTI_QR), vòng tối đa `max_qr_frames` trong `orchestrator.py::_process_package` | `pi/tests/test_orchestrator.py::test_end_to_end_eight_images` (8 ảnh fixture: VALID/NO_QR/…), `test_qr_security.py::test_multi_qr`; KB 1–4, 7 | Đã code + test |
| P-04 | `pi/sorter/qr_security.py::QrVerifier.verify/verify_read` (theo S-03) | `pi/tests/test_qr_security.py` (18 test) | Đã code + test |
| P-05 | `pi/sorter/classifier_onnx.py` (OnnxClassifier/TfliteClassifier: ROI → 224 → ONNX Runtime, UNCERTAIN dưới `min_confidence`), `pi/sorter/classifier.py` (DummyClassifier sim), `interfaces.py::Classifier` | `pi/tests/test_classifier_onnx.py` (13 test, model dummy `ai/samples/`); KB 3 | Đã code + test |
| P-06 | `pi/sorter/decision.py::decide` (hàm thuần theo bảng 1.2) | `pi/tests/test_decision.py` (10 test, mọi hàng bảng 1.2 + fail-safe) | Đã code + test |
| P-07 | `pi/sorter/uart_link.py::UartLink` (thread đọc, send_command chờ ACK + gửi lại, PING, `connected`, đếm CRC), `pi/sorter/uart_codec.py` | `pi/tests/test_uart_link.py` (16 test), `test_uart_codec.py` (9 test) | Đã code + test |
| P-08 | `pi/sorter/orchestrator.py::Orchestrator` (`_main_loop`, `_dispatch` EVT_*, `process_package`, `_pipeline_lock`) | `pi/tests/test_orchestrator.py` (5 test), `pi/tests/test_demo_check.py`; KB 1–5, 7, 8, 11 | Đã code + test |
| P-09 | `pi/sorter/storage.py` (SQLite WAL: packages, used_codes, rpc_audit, security_events, system_events, counters), `pi/sorter/defect_images.py` (giữ ≤ `max_defect_images`) | `pi/tests/test_storage.py` (6 test); `test_orchestrator.py` (ảnh lỗi, used_codes) | Đã code + test |
| P-10 | `pi/sorter/tb_client.py::ThingsBoardClient` (paho-mqtt TLS, hàng đợi khi mất kết nối, RPC topic), `pi/sorter/interfaces.py::TelemetrySink` | `pi/tests/test_tb_client.py` (18 test, mock MQTT); kết nối ThingsBoard thật: `thingsboard/SETUP.md` mục 8 | Đã code + test |
| P-11 | `pi/sorter/counters.py::Counters` (6 bộ đếm bền trong bảng counters, reset qua RPC `resetCounters` trong `main.py::_rpc_reset_counters`) | `pi/tests/test_storage.py::test_counters_persist_and_reset`, `test_rpc_handler.py::test_reset_counters_calls_callback_no_uart`; KB 1, 2 (`cnt_a`/`cnt_b` tăng) | Đã code + test |
| P-12 | `pi/sorter/health.py::read_health`, `orchestrator.py::_health_loop/_send_health` (mỗi `health_interval_s`), `health_snapshot` cho getStatus | `pi/tests/test_orchestrator.py::test_end_to_end_eight_images` (telemetry `cpu_load`, `esp_link`, `conveyor_state`) | Đã code + test |
| P-13 | `pi/sorter/logging_setup.py` (console + `sorter.jsonl` xoay vòng 5×5 MB, JSON mỗi dòng; journald qua systemd) | Kiểm tay: `pi/run/log/sorter.jsonl` sau `--sim`; trên Pi `/var/log/sorter/sorter.jsonl`, `journalctl -u sorter -o json` | Đã code |
| P-14 | `pi/sorter/fake_esp32.py::FakeEsp32`, `camera.py::FolderCamera`, `main.py::apply_sim` (`--sim`), `pi/config.sim.yaml`, `pi/tests/fixtures/make_images.py` | `pi/tests/test_orchestrator.py`, `test_uart_link.py`, `test_demo_check.py`; `python -m sorter.main --sim --config config.sim.yaml` | Đã code + test |
| P-15 | `pi/systemd/sorter.service` (User=sorter, Restart=on-failure, hardening), `pi/scripts/install_pi.sh` | Kiểm tay trên Pi: `systemctl status sorter`, `security_audit.sh` mục sorter.service; `sudo kill -9` tiến trình → tự khởi động lại sau 3 s | Cần kiểm trên phần cứng |
| P-16 | `pi/tools/qr_generator.py`, `make_hmac_key.py`, `capture_dataset.py`, `benchmark_model.py`, `uart_monitor.py`, `esp_bench.py`, `attack_sim.py`; P10 thêm `demo_check.py`, `report_metrics.py` | `esp_bench.py --fake` 16/16 PASS; `tests/fixtures/make_images.py` dùng `qr_security.make_code`; `test_anomaly.py::test_attack_sim_uart_sim_writes_log`; `test_demo_check.py` | Đã code |

## 3.3 AI kiểm tra bao bì (AI)

| Mã | Hiện thực | Test / kịch bản chứng minh | Trạng thái |
|---|---|---|---|
| AI-01 | `pi/tools/capture_dataset.py` (chụp tại trạm, phím/`--auto` theo EVT_PKG_DETECTED, `dataset/<lớp>/<box_id>_<time>.jpg` + metadata.jsonl) | Kiểm tay trên Pi với camera + đèn demo (`ai/README.md` mục 1); `--sim` chạy được trên laptop | Cần kiểm trên phần cứng |
| AI-02 | `ai/prepare_dataset.py` (lọc ảnh hỏng, thống kê, chia 70/15/15 **theo hộp** GroupShuffleSplit, xuất cấu trúc ultralytics) | `pi/tests/test_classifier_onnx.py::test_prepare_dataset_group_split` | Đã code + test |
| AI-03 | `ai/train_packaging_damage.ipynb` (ConvNeXt-V2 → MobileNetV4, chính thức), `ai/train_yolo_cls.py/.ipynb` (YOLOv8n-cls 224, 50 epoch, augmentation), `ai/train_mobilenetv2.py` | Cần dataset thật (≥ 300 ảnh/lớp) mới chạy; notebook chạy lại từ đầu trên Colab | Đã code |
| AI-04 | Xuất ONNX/TFLite trong 3 script train, `ai/export_dummy_model.py`, `ai/samples/{dummy_packaging_cls.onnx, labels.json, model_card.json}` | `pi/tests/test_classifier_onnx.py::test_samples_dummy_model_committed`, `::test_spec_from_model_card` | Đã code + test |
| AI-05 | `ai/evaluate.py` (accuracy, P/R/F1, confusion_matrix.png, misclassified_grid.png, metrics.json), `pi/tools/benchmark_model.py` (100 lần: mean/p95/RAM) → `pi/tools/report_metrics.py` gom vào `docs/metrics.md` | Benchmark đã chạy trên laptop (docs/metrics.md mục 5); số trên Pi 4 và tập test thật: cần dataset + Pi | Đã code |
| AI-06 | `pi/sorter/classifier_onnx.py` (suy luận trên Pi theo P-05), `pi/sorter/main.py::App.start` gửi attribute `model_version` | `pi/tests/test_classifier_onnx.py::test_predict_returns_classresult_ok`, `test_orchestrator.py` (attributes) ; KB 3 | Đã code + test |
| AI-07 | `ai/evaluate.py::choose_threshold` (ngưỡng lớn nhất đạt recall DEFECT ≥ 0,95 trên val, ghi model_card), `pi/sorter/classifier_onnx.py::decide` (`defect_threshold`) | `pi/tests/test_classifier_onnx.py::test_threshold_from_model_card_is_respected`, `::test_decide_rule`, `::test_uncertain_when_probability_in_between` | Đã code + test |

## 3.4 ThingsBoard và dashboard (T)

| Mã | Hiện thực | Test / kịch bản chứng minh | Trạng thái |
|---|---|---|---|
| T-01 | `thingsboard/docker-compose.yml` (MQTT_SSL 8883, không map 1883), `thingsboard/pki/make_ca.sh`, `make_server_cert.sh`, `thingsboard/SETUP.md` mục 1 | Kiểm tay: `docker compose up -d`; SETUP.md mục 8 (`openssl s_client`, `nmap -p 1883,8883` → chỉ 8883 mở) | Tài liệu/cấu hình thủ công |
| T-02 | `thingsboard/SETUP.md` mục 2–3 (profile "Sorter", device `sorter-pi-01` X.509, `sorter-view`), `thingsboard/tools/tb_provision.py` (tạo nhanh), `pki/make_device_cert.sh` | Kiểm tay: Devices → sorter-pi-01 Active sau khi Pi chạy; `tb_provision.py` | Tài liệu/cấu hình thủ công |
| T-03 | `pi/sorter/orchestrator.py::PackageRecord.to_telemetry`, `_send_health`, `counters.py::to_telemetry`, `interfaces.py::SecurityEvent.to_telemetry`, `rpc_handler.py` (`rpc_log`), `anomaly/detector.py` (anomaly_*), `tb_client.py` (topic 4.3) | `pi/tests/test_orchestrator.py::test_end_to_end_eight_images` (đúng tên khóa 4.3), `test_tb_client.py::test_telemetry_published_with_ts_and_without`, `test_anomaly.py::test_detector_process_window_sends_telemetry_and_event` | Đã code + test |
| T-04 | `pi/sorter/rpc_handler.py` (whitelist 4.4), `pi/sorter/tb_client.py` (topic rpc/request → response), `thingsboard/widgets/sorter_control_panel.js`, `thingsboard/tools/rpc_cli.py` | `pi/tests/test_rpc_handler.py` (34 test), `test_tb_client.py::test_rpc_request_calls_handler_and_publishes_response`; KB 5 | Đã code + test |
| T-05 | `thingsboard/SETUP.md` mục 2b (alarm Security alert theo mức, Anomaly, High CPU), 2c (inactivityTimeout 30 s), mục 4 (rule chain sao chép sang sorter-view); `tb_provision.py` | Kiểm tay: KB 4 (alarm BAD_SIGNATURE), KB 10 (alarm Anomaly), rút mạng Pi > 30 s → Inactive; Devices → sorter-view → Latest telemetry | Tài liệu/cấu hình thủ công |
| T-06 | `thingsboard/SETUP.md` mục 7.2 (20 widget), `thingsboard/widgets/defect_image_card.*`, `sorter_control_panel.*`, `thingsboard/dashboards/README.md` (export sau khi dựng) | Kiểm tay theo bảng widget; KB 1, 3, 5 | Tài liệu/cấu hình thủ công |
| T-07 | `thingsboard/SETUP.md` mục 7.3 (dashboard Giám sát: alias `Sorter view`, không có khối điều khiển) | KB 9 (Viewer chỉ xem) | Tài liệu/cấu hình thủ công |
| T-08 | `thingsboard/SETUP.md` mục 5 (Customer Viewer, gán sorter-view + dashboard Giám sát), mục 6 (2FA TOTP, cưỡng chế Admin); `tb_provision.py` (tùy chọn) | KB 9; `rpc_cli.py --user viewer@sorter.local` → HTTP 403 | Tài liệu/cấu hình thủ công |

## 3.5 Bảo mật (S)

| Mã | Hiện thực | Test / kịch bản chứng minh | Trạng thái |
|---|---|---|---|
| S-01 | `thingsboard/pki/make_ca.sh` (EC P-256), `make_server_cert.sh`, `make_device_cert.sh` (CN = tên device, 1 năm), `pi/sorter/tb_client.py` (`tls_set` CA + cert + key, không access token) | `pi/tests/test_tb_client.py::test_tls_set_called_with_three_files_and_never_insecure`, `::test_check_tls_files_reports_missing`; mutual TLS thật: SETUP.md mục 8 (1)(2)(3) | Đã code + test |
| S-02 | `thingsboard/SETUP.md` mục 5c (chính sách mật khẩu, khóa sau 5 lần sai), mục 6 (2FA TOTP), `docker-compose.yml` (`MAX_FAILED_LOGIN_ATTEMPTS`) | KB 9: Admin phải nhập TOTP, Viewer không có nút; sai mật khẩu 5 lần → khóa | Tài liệu/cấu hình thủ công |
| S-03 | `pi/sorter/qr_security.py` (định dạng 4.1, HMAC-SHA256 32 byte, `hmac.compare_digest`, future_skew 5 phút, max_age_days, DUPLICATE qua `storage.try_insert_used_code`, không log khóa), `pi/tools/make_hmac_key.py`, `pi/tools/qr_generator.py --forge` | `pi/tests/test_qr_security.py` (18 test: chữ ký, hết hạn, tương lai, trùng, không lộ khóa); KB 4, 7 | Đã code + test |
| S-04 | `pi/sorter/tb_client.py` (`ssl.PROTOCOL_TLS_CLIENT`, `CERT_REQUIRED`, TLS ≥ 1.2, không có tùy chọn insecure), `thingsboard/docker-compose.yml` (chỉ 8883) | `pi/tests/test_tb_client.py::test_source_has_no_insecure_option` (grep mã nguồn), `::test_tls_set_called_with_three_files_and_never_insecure` | Đã code + test |
| S-05 | `pi/sorter/rpc_handler.py` (whitelist, kiểu/miền tham số, token bucket 5/10 s, emergencyStop không giới hạn, RPC_REJECTED), `pi/sorter/main.py::_rpc_audit` + `storage.py` bảng `rpc_audit`, telemetry `rpc_log`; `thingsboard/tools/rpc_cli.py` | `pi/tests/test_rpc_handler.py` (whitelist đúng 4.4, rate limit, audit mọi RPC, security event); KB 5, 10 | Đã code + test |
| S-06 | `shared/protocol/protocol.yaml` + `gen.py` (nguồn duy nhất), `pi/sorter/uart_codec.py` (CRC-16/CCITT-FALSE, SOF, LEN ≤ 64), `pi/sorter/uart_link.py` (SEQ, gửi lại), `esp32/components/protocol/protocol.c`, `link/link.c` (whitelist TYPE, miền tham số, NACK) | `pi/tests/test_uart_codec.py` (9 test + test_vectors.json), `test_uart_link.py` (`test_retry_on_lost_ack_and_no_double_execute`, `test_crc_error_counted`, `test_unknown_type_and_bad_len_nack`), `esp32/components/protocol/test/host_test.c` (chưa biên dịch); esp_bench ca 4, 6, 7, 9 | Đã code + test |
| S-07 | `pi/scripts/harden_pi.sh` (mật khẩu, SSH key-only, ufw, fail2ban, quyền 600, tắt dịch vụ thừa), `pi/scripts/security_audit.sh` (bảng PASS/FAIL), `pi/systemd/sorter.service` (User=sorter, NoNewPrivileges, ProtectSystem), `install_pi.sh` | `bash -n` trên máy dev; kiểm tay trên Pi: `security_audit.sh` toàn PASS, `ssh pi@<ip>` bị từ chối, `ss -tlnp` chỉ 22 | Cần kiểm trên phần cứng |
| S-08 | `esp32/SECURITY.md` (quy trình Secure Boot V2 + Flash Encryption Development + JTAG_DISABLE, cảnh báo eFuse vĩnh viễn, board dự phòng), `esp32/sdkconfig.defaults`, `partitions.csv` (ghi chú nvs_keys) | Kiểm tay trên board dự phòng: `espefuse.py summary` trước/sau; nạp app không ký → "signature verification failed"; `read_flash` → byte mã hóa | Tài liệu/cấu hình thủ công |
| S-09 | `pi/sorter/security_events.py::SecurityLog` (loại, mức INFO/WARNING/MAJOR/CRITICAL, DB + telemetry + log), `interfaces.py::SecurityEvent/DEFAULT_LEVELS`, `storage.py` bảng `security_events` | `pi/tests/test_orchestrator.py::test_end_to_end_eight_images` (BAD_SIGNATURE/DUPLICATE/EXPIRED vào DB + telemetry), `test_rpc_handler.py::test_unknown_method_rejected_with_security_event`, `test_storage.py::test_other_tables`; KB 4, 5, 10, 11 | Đã code + test |

## 3.6 Secure OTA (O)

| Mã | Hiện thực | Test / kịch bản chứng minh | Trạng thái |
|---|---|---|---|
| O-01 | `ota-app/ota_app/auth.py` (bcrypt cost 12, khóa 5 phút/5 lần, TOTP, Session 15 phút, vai trò release_manager), `ui/login_window.py` | `ota-app/tests/test_auth.py` (6 test), `test_demo_attacks.py::test_viewer_cannot_publish` | Đã code + test |
| O-02 | `ota-app/ota_app/keys.py` (ECDSA P-256, PEM mã hóa passphrase quyền 600, key_id, fingerprint, xuất `ota_pubkey.h`), `ui/tab_keys.py` | `ota-app/tests/test_manifest.py::test_pubkey_header_export`, `::test_private_key_encrypted` | Đã code + test |
| O-03 | `ota-app/ota_app/firmware.py::inspect_firmware` (0xE9, esp_app_desc, SHA-256, khối Secure Boot), `main.py` (version_code phải > bản gần nhất), `ui/tab_release.py` | `ota-app/tests/test_firmware.py` (4 test), `test_demo_attacks.py::test_publish_flow_blocks_non_increasing_version` | Đã code + test |
| O-04 | `ota-app/ota_app/manifest.py` (serialize sort_keys không khoảng trắng, sign/verify DER base64, `verify_release_dir` đúng thứ tự ESP32), `tools/verify_cli.py` | `ota-app/tests/test_manifest.py` (8 test: định dạng, vòng tròn ký/xác minh, verify_cli) | Đã code + test |
| O-05 | `ota-app/ota_app/server.py` (aiohttp HTTPS 8443, TLS ≥ 1.2, 3 GET + POST /ota/report, mọi route khác 404, không liệt kê thư mục), `ui/tab_server.py` | `ota-app/tests/test_server.py` (6 test với HTTPS thật, CA tự tạo: 3 file, report hợp lệ/sai, 404, từ chối TLS < 1.2) | Đã code + test |
| O-06 | `ota-app/ota_app/history.py` (releases, device_reports, request_log, `export_csv`), `ui/tab_history.py` | `ota-app/tests/test_demo_attacks.py::test_history_csv_export`, `test_server.py::test_post_report_valid_and_invalid` | Đã code + test |
| O-07 | `ota-app/ota_app/demo_attacks.py` (tamper_firmware, forge_manifest, downgrade_release, restore_valid), `ui/tab_demo.py` (3 nút DEMO) | `ota-app/tests/test_demo_attacks.py` (3 kiểu tấn công đúng reason); `pi/tools/demo_check.py` KB 6b, 12 | Đã code + test |
| O-08 | `esp32/components/ota_secure/ota_secure.c` (`ota_secure_start`, URL chỉ từ NVS `ota_cfg/url` https://, Wi-Fi bật tạm), `fsm.c` (OTA_CHECK chỉ ở IDLE), `esp32/nvs/README.md`, `tools/provision_nvs.sh`; Pi: `rpc_handler.py` `otaCheck` (IDLE) → UART OTA_CHECK | `pi/tests/test_rpc_handler.py::test_ota_check_ok_when_idle`, `::test_ota_check_bad_state_when_running`; `esp32/OTA_TEST.md` | Cần kiểm trên phần cứng |
| O-09 | `ota_secure.c::http_client_new` (`.cert_pem` = CA nhúng `ota_ca.pem`, không crt_bundle, không skip CN), `sdkconfig.defaults` (không ESP_TLS_INSECURE), `ota-app/certs/README.md` | `esp32/OTA_TEST.md` mục "Lỗi TLS" (CA sai → REJECTED TLS_ERROR) | Cần kiểm trên phần cứng |
| O-10 | `ota_secure.c` phần "xác minh chữ ký (O-10)" (mbedTLS ECDSA P-256 trên đúng byte manifest, `ota_pubkey.h`) | `esp32/OTA_TEST.md` biến thể "Giả manifest" → SIGNATURE_INVALID; đối chiếu phía app `ota-app/tests/test_demo_attacks.py::test_forge_manifest_signature_invalid` | Cần kiểm trên phần cứng |
| O-11 | `ota_secure.c` (version_code > bản chạy và > `max_ver` NVS → ROLLBACK_REJECTED; device_model → MODEL_MISMATCH), `main/version.h` | `esp32/OTA_TEST.md` KB 12; phía app `test_demo_attacks.py::test_downgrade_rollback_rejected`, `test_manifest.py::test_verify_model_size_and_bad_manifest` | Cần kiểm trên phần cứng |
| O-12 | `ota_secure.c` phần "tải firmware (O-12)" (esp_ota_begin/write vừa ghi vừa SHA-256, sai size/hash → esp_ota_abort), `partitions.csv` | `esp32/OTA_TEST.md` KB 6b (HASH_MISMATCH, bản cũ còn nguyên); phía app `test_demo_attacks.py::test_tamper_firmware_hash_mismatch` | Cần kiểm trên phần cứng |
| O-13 | `ota_secure.c::ota_secure_on_boot` (PENDING_VERIFY → self-test driver + chờ PING ≤ `SORTER_OTA_SELFTEST_TIMEOUT_S` → mark valid / rollback), `sdkconfig.defaults` (`APP_ROLLBACK_ENABLE`), `main/Kconfig.projbuild` (`SELFTEST_FAIL_DEMO`) | `esp32/OTA_TEST.md` mục "Thử rollback" (v3 cố ý hỏng → ROLLED_BACK) | Cần kiểm trên phần cứng |
| O-14 | `ota_secure.c` status callback → `link.c` EVT_OTA_STATUS + POST /ota/report; Pi: `orchestrator.py::_dispatch` EVT_OTA_STATUS → telemetry `ota_stage/ota_reason/ota_progress`, attribute `fw_version`; `fake_esp32.py::_cmd_ota_check` | `pi/tests/test_rpc_handler.py::test_ota_check_ok_when_idle`; `test_orchestrator.py` (dispatch EVT_*); dashboard thẻ OTA khi chạy OTA_TEST.md | Cần kiểm trên phần cứng |
| O-15 | `esp32/SECURITY.md` + `esp32/components/ota_secure/README.md` (file .bin ký bằng khóa Secure Boot lúc build; `esp_ota_end` kiểm chữ ký SB khi bật), `ota-app/ota_app/firmware.py::has_secure_boot_v2_block` | `ota-app/tests/test_firmware.py::test_secure_boot_block_detected`; OTA thật với Secure Boot trên board dự phòng (SECURITY.md mục 6) | Cần kiểm trên phần cứng |

## 3.7 Phát hiện truy cập bất thường (A)

| Mã | Hiện thực | Test / kịch bản chứng minh | Trạng thái |
|---|---|---|---|
| A-01 | `pi/sorter/anomaly/features.py::FeatureCollector` (10 đặc trưng theo `interfaces.FEATURE_NAMES`, ssh_failed từ journalctl), `anomaly/store.py` (bảng anomaly_windows), `interfaces.py::MetricCounters` (các module gọi `metrics.inc`) | `pi/tests/test_anomaly.py::test_feature_collector_with_fake_ssh_reader`, `::test_store_create_insert_fetch`, `test_rpc_handler.py::test_metrics_total_counts_every_rpc` | Đã code + test |
| A-02 | `pi/sorter/anomaly/train.py` (StandardScaler + IsolationForest 200 cây, contamination 0,02, random_state 42; joblib + metadata; cảnh báo < 180 cửa sổ) | `pi/tests/test_anomaly.py::test_train_from_matrix_detects_attack_and_low_false_positive`; model thật cần ≥ 30 phút dữ liệu bình thường | Đã code + test |
| A-03 | `pi/sorter/anomaly/detector.py::AnomalyDetector` (mỗi window_s: score, anomaly_flag, top-3 z-score, sự kiện ANOMALY CRITICAL có cooldown) | `pi/tests/test_anomaly.py::test_detector_score_once_flags_attack`, `::test_detector_process_window_sends_telemetry_and_event`, `::test_detector_runs_without_model`; KB 10 | Đã code + test |
| A-04 | `pi/sorter/anomaly/rules.py::RuleEngine` (RULE_QR_FORGERY ≥ 5/60 s, RULE_SSH_BRUTEFORCE ≥ 5/60 s, RULE_RPC_UNKNOWN ngay) | `pi/tests/test_anomaly.py::test_rule_qr_forgery_fires_once_in_cooldown`, `::test_rule_rpc_unknown_fires_immediately`; `demo_check.py` KB 10 (3 RPC lạ + 8 QR giả → 2 luật CRITICAL) | Đã code + test |
| A-05 | `pi/tools/attack_sim.py` (cờ `--i-own-this-system`; rpc/ssh/qr/uart), `thingsboard/tools/rpc_cli.py` (phương thức lạ) | `pi/tests/test_anomaly.py::test_attack_sim_requires_ownership_flag`, `::test_attack_sim_uart_sim_writes_log`; KB 10 trên hệ thống thật (rpc qua REST, ssh) | Đã code + test |
| A-06 | `pi/sorter/anomaly/evaluate.py` (tỉ lệ phát hiện theo kịch bản từ attack_log.jsonl, báo nhầm trong khoảng bình thường, Markdown + PNG) → `report_metrics.py --anomaly-eval` | Chạy được; số liệu cần ≥ 30 phút chạy thật + attack_sim (docs/anomaly.md mục 8) — `docs/metrics.md` mục 8 ghi "chưa có số liệu" | Đã code |

## Tổng hợp

| Trạng thái | Số mã | Mã |
|---|---|---|
| Đã code + test | **36** | P-01, P-03…P-12, P-14; AI-02, AI-04, AI-06, AI-07; T-03, T-04; S-01, S-03, S-04, S-05, S-06, S-09; O-01…O-07; A-01…A-05 |
| Đã code | **5** | P-13, P-16, AI-03, AI-05, A-06 |
| Cần kiểm trên phần cứng | **25** | E-01…E-13; P-02, P-15; AI-01; S-07; O-08…O-15 |
| Tài liệu/cấu hình thủ công | **9** | E-14; T-01, T-02, T-05, T-06, T-07, T-08; S-02, S-08 |
| **CHƯA CÓ** | **0** | — |
| Tổng | **75** | |

Ghi chú trung thực:
- Toàn bộ firmware ESP32 (E-01…E-13, O-08…O-15) đã viết đủ nhưng **chưa từng được build** trên máy dev (không có ESP-IDF); các API cần
  đối chiếu docs được liệt kê ở cuối `esp32/README.md`. Hành vi tương đương đã được kiểm bằng `FakeEsp32` (máy trạng thái E-01, E-05, E-09, E-10, E-11, E-12).
- `esp32/components/protocol/test/host_test.c` (test C codec, S-06/E-10) chưa biên dịch (máy dev không có gcc) — chạy `make test-c` trên máy có gcc.
- ThingsBoard (T-01, T-02, T-05…T-08, S-02) chưa dựng thật; `tb_provision.py` và `SETUP.md` là hướng dẫn, chưa có file JSON dashboard import.
- Model AI thật (AI-03, AI-05 số liệu) và model Isolation Forest (A-02, A-06 số liệu) cần dữ liệu thu tại trạm; hiện dùng model dummy để kiểm đường đi.
