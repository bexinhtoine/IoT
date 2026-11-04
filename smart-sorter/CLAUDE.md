# Smart Logistics Sorter — bối cảnh chung cho mọi phiên code

Đồ án "Hệ thống phân loại kiện hàng thông minh, kết hợp mã QR và AI kiểm tra bao bì, có bảo mật".
Nguồn sự thật duy nhất: `docs/SPEC.md` (phần 1–5). Mã chức năng E/P/AI/T/S/O/A trong phần 3; giao thức phần 4.

## Công nghệ đã chốt
- Raspberry Pi 4, Raspberry Pi OS 64-bit Bookworm, Python 3.11: **picamera2 (camera CSI, không dùng USB)**, pyzbar + OpenCV,
  onnxruntime, paho-mqtt 2.x, pyserial, PyYAML, sqlite3, psutil, scikit-learn, joblib, pytest.
- ESP32 DevKit, ESP-IDF v5.x, C; LEDC (motor + servo), UART2, NVS, esp_http_client + esp_ota_ops + mbedTLS.
- ThingsBoard CE (Docker), MQTT TLS 8883, X.509. App Secure OTA: PySide6 + cryptography + bcrypt + pyotp + aiohttp.

## Cấu trúc repo
```
smart-sorter/
  docs/              SPEC.md, RUNBOOK.md, security_slide.md, metrics.md, PROGRESS.md
  shared/protocol/   protocol.yaml (nguồn duy nhất giao thức UART) + gen.py + test_vectors.json
  pi/                package Python `sorter`, tools/, tests/, systemd/, scripts/
  esp32/             firmware ESP-IDF: main/, components/, partitions.csv, sdkconfig.defaults
  ai/                chuẩn bị dữ liệu, train, đánh giá, export
  thingsboard/       docker-compose, pki/, widgets/, tools/, SETUP.md
  ota-app/           app Secure OTA
```

## Quy tắc code
1. Bám đúng đặc tả; file/hàm chính ghi mã chức năng (`# P-06`, `/* E-05 */`).
2. Không hardcode bí mật; đọc từ file quyền 600 hoặc NVS; chỉ commit `*.example`.
3. Không bao giờ tắt xác minh TLS.
4. Fail-safe: mọi lỗi/timeout/ngoại lệ trong đường xử lý kiện → REJECT, không bao giờ A/B.
5. Python: type hints, dataclass, logging (không print trong package), pytest; mọi thứ chạy được `--sim` trên laptop.
6. C: tách component; ISR tối thiểu; FreeRTOS task + queue; kiểm tra mọi `esp_err_t`; không vTaskDelay để tạo hiệu ứng đèn.
7. Tên biến/hàm tiếng Anh; comment và tài liệu tiếng Việt.
8. Giao diện dùng chung nằm ở `pi/sorter/interfaces.py` và `pi/sorter/config.py`; không sửa chữ ký các lớp ở đó nếu không cập nhật mọi nơi dùng.

## Lệnh thường dùng
```
make gen-protocol          # sinh protocol_gen.h / protocol_gen.py
cd pi && python -m pytest  # test phần Pi (đặt PYTHONUTF8=1 trên Windows)
cd pi && python -m sorter.main --sim --config config.sim.yaml
```
