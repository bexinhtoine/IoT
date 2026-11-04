# Firmware ESP32 — Smart Logistics Sorter (MODULE P2 + P8)

ESP-IDF **v5.2 / v5.3**, target `esp32` (DevKit, flash 4 MB). Thực hiện E-01 → E-12 (chấp hành) và O-08 → O-15 (Secure OTA).
Nguồn sự thật: `docs/SPEC.md` (2.3 chân, 3.1, 3.6, 4.2, 4.5). Giao thức UART dùng component `protocol` (P1, không sửa).

## Cấu trúc

```
esp32/
  CMakeLists.txt            PROJECT_VER (esp_app_desc.version)
  partitions.csv            nvs 0x6000 | otadata 0x2000 | phy_init 0x1000 | ota_0 / ota_1 1,875 MB
  sdkconfig.defaults        partition custom, APP_ROLLBACK_ENABLE, TWDT 5 s, FreeRTOS 1 kHz, không ESP_TLS_INSECURE
  main/
    app_main.c              thứ tự init; CONFIG_SORTER_HW_TEST
    version.h               FW_VERSION_CODE / FW_VERSION_NAME
    Kconfig.projbuild        menu "Smart Sorter (P2/P8)"
  components/
    protocol/               P1 — codec khung, enum (KHÔNG sửa)
    board/      include/board.h            chân theo SPEC 2.3
    motor/      E-02  L298N, LEDC timer0 1 kHz/10 bit, ramp 200 ms (esp_timer), motor_stop_from_isr()
    servo/      E-04  LEDC timer1 50 Hz/14 bit, 0–180° → 500–2500 µs
    sensor/     E-03  HC-SR04 duy nhất (70 ms/mẫu, 2 mẫu 20..300 mm, latch chống đếm lặp)
    indicator/  E-06  LED/còi theo pattern, esp_timer 50 ms
    estop/      E-07  GPIO39 ANYEDGE, tắt PWM trong ISR
    link/       E-09 E-10 E-12  UART2, ACK/NACK, gửi lại, chống lặp SEQ, STATUS 1 s, watchdog liên kết
    params/     E-11  NVS "params", miền từ PROTO_PARAMS
    fsm/        E-01 E-05 E-08  máy trạng thái (bảng ở đầu fsm.c), 1 queue + 1 task
    ota_secure/ O-08..O-15  Wi-Fi tạm, HTTPS CA nhúng, ECDSA, chống rollback, esp_ota_*, self-test
  nvs/          wifi_ota.csv.example + README (nạp ssid/pass/url vào NVS)
  tools/        provision_nvs.sh
  OTA_TEST.md   kịch bản 6a, 6b, 12, rollback
```

Sơ đồ phụ thuộc component (không có vòng): `fsm` → {motor, servo, sensor, indicator, estop, link, params, ota_secure};
`ota_secure` → link; `estop` → motor; `link`/`params` → protocol. Các component thấp hơn không
biết `fsm`: chúng nhận callback lúc init (sensor_init(cb), estop_init(cb), link_init(&cfg), ota_secure_set_status_cb).

## Build / flash

```
cd esp32
idf.py set-target esp32
idf.py menuconfig          # (tùy chọn) menu "Smart Sorter (P2/P8)"
idf.py build
idf.py -p /dev/ttyUSB0 flash monitor      # Windows: -p COM5
```

Trước khi build bản dùng OTA: copy CA và public key theo `components/ota_secure/README.md`; nạp NVS theo `nvs/README.md`.
Khi chưa có hai file đó, build vẫn chạy (placeholder) nhưng OTA bị từ chối (TLS_ERROR / SIGNATURE_INVALID) — đúng hành vi.

Mỗi lần phát hành OTA: tăng `FW_VERSION_CODE` (và `FW_VERSION_NAME` + `PROJECT_VER`) rồi `idf.py build`;
file ký là `build/smart_sorter_esp32.bin`.

## Kconfig (`idf.py menuconfig` → Smart Sorter)

| Tùy chọn | Mặc định | Ý nghĩa |
|---|---|---|
| `SORTER_HW_TEST` | n | Chế độ kiểm tra đấu dây: motor 2 s → servo A, B → LED xanh, đỏ → còi → in cảm biến/E-stop mỗi 200 ms |
| `SORTER_OTA_DEVICE_ID` | sorter-esp32-01 | `device` trong POST /report |
| `SORTER_OTA_WIFI_TIMEOUT_S` | 20 | chờ IP khi OTA |
| `SORTER_OTA_SELFTEST_TIMEOUT_S` | 30 | chờ PING sau khi boot bản mới (O-13) |
| `SORTER_OTA_SELFTEST_FAIL_DEMO` | n | bản demo rollback: không trả PONG, self-test luôn thất bại |

## Đấu dây (SPEC 2.3)

| Chức năng | GPIO | Ghi chú |
|---|---|---|
| UART2 RX / TX | 16 / 17 | ↔ Pi TX GPIO14 (chân 8) / Pi RX GPIO15 (chân 10); GND chung |
| Motor ENA / IN1 / IN2 | 25 / 26 / 27 | L298N; nguồn động cơ riêng, GND chung |
| Servo A / B | 18 / 19 | nguồn 5 V riêng |
| HC-SR04 TRIG / ECHO | 13 / 35 | ECHO qua cầu chia áp 1 kΩ–2 kΩ |
| LED xanh / đỏ | 32 / 33 | qua 220 Ω |
| Còi | 23 | qua transistor NPN |
| E-stop | 39 | NC xuống GND + pull-up 10 kΩ; LOW = bình thường, HIGH = dừng (đứt dây cũng dừng) |

E-stop vật lý phải cắt nguồn động cơ bằng phần cứng; firmware chỉ là lớp thứ hai.

## Hành vi chính

- Máy trạng thái: bảng đầy đủ ở đầu `components/fsm/fsm.c`.
- Phát hiện kiện (RUNNING): `motor_stop()` ngay trong task fsm (ưu tiên 8, task đo 70 ms → xác nhận 2 mẫu → queue), pkg_seq++, `EVT_PKG_DETECTED`, chờ SORT tối đa `t_decision_timeout`, quá hạn → REJECT/FAILSAFE +
  `EVT_FAULT(DECISION_TIMEOUT)`.
- Mất liên kết > 2000 ms khi RUNNING/DETECTED/SORTING → FAULT(LINK_LOST); `EVT_FAULT` gửi khi liên kết trở lại;
  thoát bằng `ESTOP_RESET`.
- E-stop vật lý: ISR tắt PWM + IN1/IN2 bằng thanh ghi, fsm vào ESTOP; chỉ `ESTOP_RESET` khi nút đã nhả.
- STATUS mỗi 1 s: state, fw_version_code, uptime, pkg_count, crc_err, reset_reason. Boot do WDT → `EVT_FAULT(WDT_RESET)` một lần.
- OTA_CHECK (IDLE) → OTA: Wi-Fi bật, tải manifest+sig+firmware qua HTTPS (CA nhúng), ECDSA, chống rollback, ghi ota_x,
  reboot; bản mới self-test (driver init + PING ≤ 30 s) rồi mới `esp_ota_mark_app_valid_cancel_rollback`.

## Kiểm tra với Pi (pi/tools/esp_bench.py — P3)

`esp_bench.py` dùng codec P1 qua `/dev/serial0` (hoặc USB-UART trên laptop nối GPIO16/17):

1. PING → PONG(state=IDLE); STATUS tự đến mỗi 1 s.
2. START(60) → ACK, băng chạy, hai LED tắt. Đưa tay qua cảm biến → `EVT_PKG_DETECTED(pkg_seq)`, băng dừng.
3. SORT(A, OK, pkg_seq) → ACK; servo A gạt, băng chạy `t_clear_a`, servo về, bíp, `EVT_SORT_DONE`. Lặp với B, REJECT.
4. SORT với pkg_seq sai → NACK BAD_PARAM; SORT khi RUNNING → NACK BAD_STATE; khung sai CRC → bị bỏ, `crc_err` tăng
   trong STATUS; gửi lại cùng SEQ → chỉ nhận lại ACK, không thực hiện hai lần; SET_PARAM(1, 200) → NACK BAD_PARAM.
5. Không SORT trong 3 s → `EVT_FAULT(DECISION_TIMEOUT)` + hàng chạy tới khay Loại.
6. Ngừng PING ~2 s khi RUNNING → băng dừng, STATUS.state = FAULT; PING lại → `EVT_FAULT(LINK_LOST)`; ESTOP_RESET → IDLE.
7. ESTOP → ACK + `EVT_ESTOP(REMOTE)`; ESTOP_RESET → `EVT_ESTOP_CLEARED`.
8. E-stop vật lý: bấm khi chạy / khi gạt / rút dây nút — băng dừng ngay, `EVT_ESTOP(PHYSICAL)`; ESTOP_RESET khi còn
   bấm → NACK BAD_STATE.

## Chỗ cần đối chiếu tài liệu Espressif theo phiên bản (không build được trên máy soạn code)

| Nơi dùng | API / hành vi cần xác nhận |
|---|---|
| motor.c | `gpio_ll_set_level(&GPIO, pin, 0)` (hal/gpio_ll.h), `esp_rom_gpio_connect_out_signal(pin, SIG_GPIO_OUT_IDX, 0, 0)`, `ledc_set_pin()` để gắn lại |
| sensor.c | Đo xung ECHO bằng polling có timeout; kiểm tra độ ổn định trên board thật khi UART/OTA hoạt động |
| link.c | `UART_SCLK_DEFAULT`; `esp_task_wdt_add(NULL)` khi `CONFIG_ESP_TASK_WDT_INIT=y` |
| ota_secure.c | `esp_http_client`: `HTTP_EVENT_DISCONNECTED/ERROR` mang `esp_tls_error_handle_t` trong `evt->data`; `esp_tls_get_and_clear_last_error`; `ESP_ERR_MBEDTLS_SSL_HANDSHAKE_FAILED`, `ESP_ERR_MBEDTLS_X509_CRT_PARSE_FAILED` (esp_tls_errors.h) |
| ota_secure.c | `esp_wifi_init/deinit` lặp nhiều lần với cùng netif từ `esp_netif_create_default_wifi_sta()` |
| ota_secure.c | `esp_ota_get_last_invalid_partition()`, `esp_ota_mark_app_invalid_rollback_and_reboot()`, `OTA_WITH_SEQUENTIAL_WRITES` |
| ota_secure.c | mbedTLS 3.x: `mbedtls_sha256(in, len, out, 0)`, `mbedtls_sha256_starts/update/finish` trả int, `mbedtls_pk_parse_public_key(pk, pem, strlen+1)` |
| CMakeLists ota_secure | tên symbol `_binary_<file>_start` cho `EMBED_TXTFILES` (dấu `.` → `_`) |
| sdkconfig.defaults | tên option TWDT (`CONFIG_ESP_TASK_WDT_EN` / `_INIT`), `CONFIG_ESP_TIMER_TASK_STACK_SIZE` |

## Luồng siêu âm → hộp → QR → bao bì

Xem [điều kiện input/output và đèn báo](../docs/DEVICE_IO.md). HC-SR04 đã thay hoàn toàn TCRT5000; GPIO34 không dùng. Ngưỡng cố định 300 mm, hai mẫu liên tiếp; giải phóng latch sau ba mẫu >330 mm hoặc không có ECHO. ECHO stuck HIGH không giải phóng latch. Tham số UART id 11 chỉ nhận 300; NVS cũ 80 sẽ bị kiểm tra miền giá trị và dùng mặc định 300, không cần xóa NVS chứa Wi-Fi.

LED mới: OK xanh 300 ms; DEFECT đỏ 300 ms; NOT_BOX=4 đỏ 3 nhịp rồi tắt. Cần cập nhật cả Pi và ESP32 để hiểu reason mới. Không phát lại đèn khi kết thúc gạt.
