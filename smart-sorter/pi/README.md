# sorter-core — phần mềm điều phối trên Raspberry Pi 4 (MODULE P3)

Lưu trữ thật hiện dùng **PostgreSQL / Supabase IOT_Project**, schema `iot_project`.
Xem [cấu hình, cơ chế lưu bền và chuyển dữ liệu](../docs/DATABASE.md).
File `.env` giữ connection string riêng; cấu hình mô phỏng vẫn dùng SQLite.

Package Python `sorter` thực hiện P-01 → P-16, S-03, S-09 của `docs/SPEC.md`: đọc cấu hình, camera CSI
(picamera2), đọc + xác thực QR (HMAC-SHA256), AI kiểm tra bao bì (qua giao diện `Classifier`), quyết định
theo bảng 1.2, liên kết UART với ESP32, lưu SQLite, bộ đếm, sức khỏe, nhật ký bảo mật, chế độ mô phỏng.

## Cấu trúc

```
pi/
  sorter/                 package chính
    config.py             P-01  đọc + kiểm tra YAML (dataclass SorterConfig)        [dùng chung]
    interfaces.py         Classifier/ClassResult, TelemetrySink, SecurityEvent, metrics [dùng chung]
    protocol_gen.py       sinh từ shared/protocol/protocol.yaml — KHÔNG sửa tay
    uart_codec.py         E-10/P-07  khung + CRC16 + FrameDecoder
    camera.py             P-02  Picamera2Camera (khóa AE/AWB), FolderCamera (sim)
    qr_reader.py          P-03  pyzbar, dự phòng cv2.QRCodeDetector, CLAHE, MULTI_QR
    qr_security.py        S-03/P-04  QrVerifier, sign/make_code, load_hmac_key
    classifier.py         DummyClassifier (P4 cung cấp classifier_onnx.py)
    decision.py           P-06  decide(qr, ai) -> Decision (thuần, có test mọi hàng)
    uart_link.py          P-07  UartLink (thread đọc, PING, send_command chờ ACK + gửi lại)
    fake_esp32.py         P-14  FakeEsp32 + LoopbackTransport (máy trạng thái E-01)
    storage.py            P-09  SQLite WAL (packages, used_codes, rpc_audit, security_events, system_events, counters)
    counters.py           P-11  6 bộ đếm bền
    health.py             P-12  cpu_temp/cpu_load/mem/disk/uptime
    security_events.py    S-09  SecurityLog.emit -> DB + telemetry + log
    defect_images.py      ảnh lỗi: JPEG gốc + thumbnail 320x240 <= image_max_kb
    logging_setup.py      P-13  console + sorter.jsonl xoay vòng 5x5 MB (JSON mỗi dòng)
    factories.py          tạo camera/classifier/telemetry/uart/rpc/anomaly theo config (import lười P4/P5/P9)
    orchestrator.py       P-08  vòng chính + pipeline một kiện (2.5)
    main.py / __main__.py CLI
  tools/                  make_hmac_key, qr_generator, capture_dataset, uart_monitor, esp_bench
  tests/                  pytest (+ fixtures/make_images.py sinh ảnh mẫu, fixtures/qr_hmac.key chỉ để test)
  systemd/sorter.service  P-15
  scripts/install_pi.sh   cài lên Pi
  config.example.yaml     mẫu /etc/sorter/config.yaml ; config.sim.yaml  chế độ mô phỏng
```

## Chạy mô phỏng trên laptop (không cần phần cứng)

```bash
cd pi
export PYTHONUTF8=1                       # Windows: bắt buộc
python -m pip install -r requirements.txt # tối thiểu: numpy opencv PyYAML pyzbar qrcode[pil] pyserial (picamera2/onnxruntime/paho không bắt buộc)
python tests/fixtures/make_images.py      # sinh 8 ảnh mẫu vào tests/fixtures/images (đã commit sẵn)
python -m sorter.main --sim --config config.sim.yaml [--packages 8] [--log-level DEBUG]
```

`--sim` ép `camera.source=folder`, `uart.port=sim` (FakeEsp32 trong tiến trình), `thingsboard.enabled=false`
(LogTelemetrySink ghi telemetry ra log), chạy lần lượt từng ảnh như một kiện rồi in bảng quyết định:

```
  # image                    pkg_id     qr_status         ai_label   decision          lat_ms
  1 01_ok_A.jpg              PKG000001  VALID             OK         SORT_A               123
  2 02_ok_B.jpg              PKG000002  VALID             OK         SORT_B                93
  3 03_defect_tear_A.jpg     PKG000003  VALID             DEFECT     REJECT_DEFECT         84
  4 04_forged_A.jpg          PKG000004  BAD_SIGNATURE     -          REJECT_SECURITY       93
  5 05_noqr.jpg              -          NO_QR             -          REJECT_SECURITY      380
  6 06_expired_A.jpg         PKG000006  EXPIRED           -          REJECT_SECURITY      274
  7 07_dup_A.jpg             PKG000001  DUPLICATE         -          REJECT_SECURITY      144
  8 08_uncertain_A.jpg       PKG000008  VALID             UNCERTAIN  REJECT_DEFECT        120
```

Dữ liệu sim nằm ở `pi/run/` (DB, ảnh lỗi, log JSON). Ở sim + `demo_mode` bảng `used_codes` được xóa lúc khởi
động để chạy lại cho cùng kết quả. DummyClassifier đọc nhãn từ **tên file** ảnh (`tear/dent/wet/crush/rach/mop/uot`
→ DEFECT, `uncertain` → UNCERTAIN, còn lại OK).

## Test

```bash
cd pi && PYTHONUTF8=1 python -m pytest -q        # ~200 test, ~25 s
```
Test không cần phần cứng; nếu thiếu cả pyzbar lẫn bộ giải mã QR của OpenCV thì các test end-to-end tự skip.
`tools/esp_bench.py --fake` tự kiểm tra kịch bản bench với FakeEsp32.

## Demo trực tiếp trên Pi: camera → LED + còi + một servo

Đây là bài test độc lập trước khi ghép ESP32, nút bấm và HC-SR04. Detector phải thấy đúng một hộp
trong 3 frame liên tiếp; một hộp chỉ kích hoạt đúng một lần và phải rời khỏi camera trước khi hệ thống
nhận hộp tiếp theo. Mặc định dùng số chân **BCM**: LED GPIO17 (chân 11), buzzer chủ động GPIO27
(chân 13, qua transistor), servo GPIO18 (chân 12).

```bash
cd ~/smart-sorter
python pi/tools/pi_gpio_demo.py --self-test
python pi/tools/pi_gpio_demo.py --dry-run --preview
python pi/tools/pi_gpio_demo.py --preview --max-events 1
```

Lệnh `--self-test` chỉ thử phần đấu dây, không mở camera/model. Lệnh `--dry-run` kiểm tra camera/model
nhưng chỉ log output; lệnh cuối mới điều khiển GPIO thật và tự thoát sau một lần nhận hộp. Bỏ
`--max-events 1` để chạy liên tục. Nếu servo rung, cài/chạy `pigpiod` rồi thêm `--pigpio`. Servo dùng
nguồn 5 V riêng đủ dòng, còi qua transistor NPN, LED qua điện trở 220–330 Ω; mọi GND phải nối chung.
Không cấp servo hoặc còi trực tiếp từ GPIO/Pi 3.3 V.

## Cài lên Raspberry Pi (Bookworm 64-bit)

```bash
cd pi && sudo bash scripts/install_pi.sh
```
Script tạo user `sorter` (video, dialout, gpio), `/etc/sorter/{secrets,certs}` (700), `/var/lib/sorter`,
`/var/log/sorter`, `/opt/sorter` + venv `--system-site-packages` (để dùng `python3-picamera2` từ apt),
cài `requirements.txt`, sinh khóa HMAC nếu chưa có, cài `systemd/sorter.service`. Cuối script in việc còn
lại làm tay: `raspi-config` bật UART + tắt serial console, thêm `enable_uart=1` và `dtoverlay=disable-bt`
vào `/boot/firmware/config.txt`, chép chứng chỉ ThingsBoard và model vào đúng chỗ, rồi reboot.

```bash
sudo systemctl start sorter && journalctl -u sorter -f
tail -f /var/log/sorter/sorter.jsonl
```

In mã QR (dùng CÙNG khóa với Pi; khóa không bao giờ in ra màn hình):
```bash
python tools/qr_generator.py --key /etc/sorter/secrets/qr_hmac.key --out out/qr --count 12 --warehouse A
python tools/qr_generator.py --key ... --out out/qr --items PKG000020:B --forge      # QR chữ ký sai để demo
```
Kiểm tra ESP32 thật: `python tools/esp_bench.py --port /dev/serial0`; theo dõi khung: `python tools/uart_monitor.py --port /dev/serial0`.
Thu dataset (AI-01): `python tools/capture_dataset.py --config /etc/sorter/config.yaml --out dataset --box-id box01 [--auto]`.

## Đổi backend AI / telemetry bằng config

| Khóa | Giá trị | Lớp được tạo (factories.py) |
|---|---|---|
| `ai.backend` | `dummy` | `DummyClassifier` |
| | `onnx` / `tflite` | `sorter.classifier_onnx.OnnxClassifier(cfg.ai)` / `TfliteClassifier(cfg.ai)` (P4) |
| `thingsboard.enabled` | `false` | `LogTelemetrySink` (ghi log) |
| | `true` | `sorter.tb_client.ThingsBoardClient(cfg.thingsboard, device_name=...)` (P5); RPC qua `sorter.rpc_handler` nếu có |
| `anomaly.enabled` | `true` | `sorter.anomaly.detector.AnomalyDetector` (P9) nếu có |
| `uart.port` | `sim` | FakeEsp32 ; đường dẫn `/dev/serial0` → pyserial |
| `camera.source` | `folder` / `picamera2` | `FolderCamera(camera.folder)` / `Picamera2Camera` |

Thiếu module P4/P5 khi bật → `ConfigError` rõ ràng lúc khởi động; thiếu P9/RPC → cảnh báo và bỏ qua.

## API cho các module khác

- `UartLink.send_command(msg_type, payload=b"" | Payload, timeout_ms=None, retries=None) -> CommandResult(ok, error, nack_err, reply, seq, attempts)`;
  `error ∈ {None, TIMEOUT, NACK_CRC, NACK_UNKNOWN_TYPE, NACK_BAD_STATE, NACK_BAD_PARAM, NACK_BAD_LEN, LINK_DOWN}`;
  thuộc tính `state: State|None`, `connected: bool`, `crc_errors`, `stats`, `last_status`, `events: Queue[Frame]`; `get_status(timeout_ms)`, `ping()`.
- `Storage(db_path)`: `insert_package(dict)`, `try_insert_used_code(pkg_id, ts_ms) -> bool`, `clear_used_codes() -> int`,
  `insert_rpc_audit(request_id, method, params, requested_by, result, ok)`, `insert_security_event(SecurityEvent)`,
  `insert_system_event(kind, detail)`, `get_counter/set_counter/incr_counter`, `recent_packages(n)`, `count_packages_by_decision()`, `close()`.
- `Counters(storage)`: `record(decision_code)`, `reset()`, `to_telemetry()` (cnt_total, cnt_a, cnt_b, cnt_reject_defect, cnt_reject_security, cnt_reject_failsafe).
- `SecurityLog(storage, telemetry)`: `emit(event_type, detail, level=None, **extra) -> SecurityEvent`, `emit_event(ev)`, `add_listener(fn)`.
- `QrVerifier(key, storage, max_age_days, future_skew_s).verify(text|None, now=None) -> QrResult(status, pkg_id, warehouse, ts)`;
  `sign(key, pkg_id, warehouse, ts)`, `make_code(key, pkg_id, warehouse, ts=None, forge=False)`, `load_hmac_key(path)`.
- `decide(qr: QrResult|None, ai: ClassResult|None, failsafe=False) -> Decision(target: SortTarget, code, reason: SortReason, detail)`.
- `Orchestrator.health_snapshot()` (cho RPC getStatus), `run_once_sim(n)`, `records`, `conveyor_state`.

## Model AI hiện hành (2026-11-04)

Dùng MobileNetV4 từ `ai/3-best.ipynb`, ONNX FP32 256×256 đã xuất sẵn trong `ai/models/`. Cách thử ảnh, bật YOLO-World tìm hộp và copy model lên Pi: [ai/README.md](../ai/README.md). Runtime không export hoặc tải model khi chạy.
