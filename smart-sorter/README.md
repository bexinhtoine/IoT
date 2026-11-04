# Smart Logistics Sorter

Hệ thống phân loại kiện hàng thông minh trên băng chuyền: **mã QR có chữ ký HMAC-SHA256** quyết định hàng đi đâu
(kho A / kho B), **AI camera** (Picamera2 trên Raspberry Pi 4) quyết định hàng có được đi không (bao bì OK / lỗi),
mọi dữ liệu lên **ThingsBoard qua MQTT TLS 8883 với chứng chỉ X.509**, **ESP32** điều khiển băng chuyền, servo gạt,
đèn, còi, nút dừng khẩn cấp và nhận **Secure OTA** (ECDSA P-256, chống rollback). Mở rộng: **Isolation Forest**
phát hiện truy cập bất thường từ log RPC/QR/UART/SSH/MQTT.

Đặc tả đầy đủ: [docs/SPEC.md](docs/SPEC.md). Tiến độ và việc đã hoàn thành: [docs/PROGRESS.md](docs/PROGRESS.md).
Vận hành và demo: [docs/RUNBOOK.md](docs/RUNBOOK.md). Bảng truy vết 75 chức năng: [docs/traceability.md](docs/traceability.md).

## Luật phân loại (bảng 1.2 của đặc tả)

| QR | AI bao bì | Quyết định | Khay |
|---|---|---|---|
| Hợp lệ, kho A | OK | `SORT_A` | A |
| Hợp lệ, kho B | OK | `SORT_B` | B |
| Hợp lệ | Lỗi hoặc dưới ngưỡng tin cậy | `REJECT_DEFECT` | Loại |
| Không đọc được / sai định dạng / chữ ký sai / trùng / hết hạn | không chạy AI | `REJECT_SECURITY` | Loại |
| Hết thời gian chờ, mất kết nối, lỗi hệ thống | — | `REJECT_FAILSAFE` | Loại |

Fail-safe: mọi trường hợp không chắc chắn đều vào khay Loại; servo hỏng hoặc không có lệnh thì hàng tự rơi vào khay Loại.

## Kiến trúc

```
 ┌────────────────────┐  MQTT TLS 8883 / X.509   ┌───────────────────────┐
 │ Raspberry Pi 4     │ ───────────────────────▶ │ ThingsBoard CE (Docker)│
 │ sorter-core        │ ◀── RPC whitelist ────── │ Dashboard Admin/Viewer │
 │ Picamera2 · QR ·   │                          │ 2FA · Alarm · Audit    │
 │ HMAC · ONNX · IF   │                          └───────────────────────┘
 └─────────┬──────────┘
           │ UART 115200, khung CRC16, ACK/SEQ            ┌──────────────────┐
           ▼                                              │ App Secure OTA   │
 ┌────────────────────┐   HTTPS 8443 (chỉ khi OTA)        │ PySide6 · ECDSA  │
 │ ESP32 (ESP-IDF)    │ ◀─────────────────────────────────│ manifest + sig   │
 │ FSM · motor · servo│                                   └──────────────────┘
 │ sensor · LED/còi   │
 │ E-stop · OTA       │
 └────────────────────┘
```

## Cấu trúc repo

```
smart-sorter/
  shared/protocol/   protocol.yaml (nguồn duy nhất giao thức UART), gen.py, test_vectors.json
  pi/                package Python `sorter` (sorter-core), tools/, tests/, systemd/, scripts/
  esp32/             firmware ESP-IDF v5.x: main/, components/{protocol,board,motor,servo,sensor,indicator,estop,link,params,fsm,ota_secure}
  ai/                prepare_dataset, train (notebook ConvNeXt→MobileNetV4, YOLOv8n-cls, MobileNetV2), evaluate, export_dummy_model
  thingsboard/       docker-compose, pki/ (CA, cert server/device/OTA), widgets/, tools/ (rpc_cli, tb_provision), SETUP.md
  ota-app/           app Secure OTA (đăng nhập bcrypt+TOTP, ký ECDSA, HTTPS server, demo tấn công)
  docs/              SPEC, PROGRESS, RUNBOOK, traceability, metrics, security_slide, threat_model, anomaly, report/ (báo cáo Word)
```

## Chạy nhanh trên laptop (không cần phần cứng)

```bash
cd smart-sorter/pi
pip install -r requirements.txt            # Windows: set PYTHONUTF8=1 trước khi chạy python
python -m pytest                           # 200+ test
python -m sorter.main --sim --config config.sim.yaml   # chạy 8 ảnh mẫu qua toàn bộ pipeline với ESP32 giả lập
python tools/demo_check.py                 # tự chạy các kịch bản demo 5.1/5.2 ở chế độ sim
cd ../ota-app && python -m pytest          # 31 test app OTA
```

Kết quả mong đợi của `--sim`: 01 → SORT_A, 02 → SORT_B, 03 (rách) → REJECT_DEFECT, 04 (QR giả) → REJECT_SECURITY/BAD_SIGNATURE,
05 (không QR) → NO_QR, 06 → EXPIRED, 07 → DUPLICATE, 08 → UNCERTAIN → REJECT_DEFECT.

## Build / cài từng phần

| Phần | Lệnh | Tài liệu |
|---|---|---|
| Giao thức | `make gen-protocol` (sinh `protocol_gen.h`, `protocol_gen.py`) | [shared/protocol/protocol.yaml](shared/protocol/protocol.yaml) |
| Pi | `sudo bash pi/scripts/install_pi.sh` rồi `systemctl start sorter` | [pi/README.md](pi/README.md) |
| ESP32 | `cd esp32 && idf.py set-target esp32 && idf.py build && idf.py -p PORT flash monitor` | [esp32/README.md](esp32/README.md), [esp32/OTA_TEST.md](esp32/OTA_TEST.md), [esp32/SECURITY.md](esp32/SECURITY.md) |
| ThingsBoard | `cd thingsboard && bash pki/make_ca.sh && bash pki/make_server_cert.sh tb.local <ip> && docker compose up -d` | [thingsboard/SETUP.md](thingsboard/SETUP.md) |
| App OTA | `cd ota-app && pip install -r requirements.txt && python -m ota_app.main` | [ota-app/README.md](ota-app/README.md) |
| AI | chụp dataset bằng `pi/tools/capture_dataset.py`, train bằng `ai/train_packaging_damage.ipynb`, copy ONNX + model_card sang `/opt/sorter/models/` | [ai/README.md](ai/README.md) |
| Hardening | `sudo bash pi/scripts/harden_pi.sh --admin-user <user> --lan-subnet 192.168.1.0/24`; kiểm tra `security_audit.sh` | [pi/scripts/README.md](pi/scripts/README.md) |
| Bất thường | `python -m sorter.anomaly.train ...`, `python tools/attack_sim.py --i-own-this-system` | [docs/anomaly.md](docs/anomaly.md) |

## Camera

Camera là **Pi Camera Module (CSI)** điều khiển bằng thư viện `picamera2` (`pi/sorter/camera.py`, lớp `Picamera2Camera`):
mở một lần, chờ 2 s rồi khóa phơi sáng và cân bằng trắng để ảnh đồng đều giữa các lần chụp. Không dùng camera USB
hay `cv2.VideoCapture`. Chế độ mô phỏng dùng `FolderCamera` đọc ảnh từ thư mục.

## Bảo mật (tóm tắt)

X.509 mutual TLS cho MQTT · QR ký HMAC-SHA256 + chống replay · RPC whitelist, rate limit, audit · UART CRC16 + SEQ ·
hardening Pi (SSH key, ufw, fail2ban) · Secure Boot V2 + Flash Encryption ESP32 · OTA ký ECDSA P-256 + chống rollback ·
2FA TOTP trên ThingsBoard · Isolation Forest + luật cứng. Chi tiết: [docs/security_slide.md](docs/security_slide.md).

## Luồng thiết bị hiện hành

HC-SR04 phát hiện vật trong 30 cm → dừng băng, chụp → xác nhận hộp → quét QR → kiểm tra bao bì → gạt. LED báo theo từng vật rồi tắt. Xem [bảng input/output, đấu dây và điều kiện LED](docs/DEVICE_IO.md).
