# Kiểm thử Secure OTA phía ESP32 (O-08 → O-15) — kịch bản 6a, 6b, 12 và rollback

## Chuẩn bị (một lần)

1. **PKI**: `thingsboard/pki/make_ca.sh` → `make_ota_server_cert.sh` với SAN = IP/hostname máy chạy app OTA
   (ví dụ `192.168.1.10`). Copy `thingsboard/pki/out/ca.pem` → `esp32/components/ota_secure/certs/ota_ca.pem`.
2. **Khóa ký**: trong app OTA (P7) tạo cặp khóa ECDSA P-256, xuất `ota_pubkey.h` →
   `esp32/components/ota_secure/include/ota_pubkey.h`.
3. **NVS**: `cp esp32/nvs/wifi_ota.csv.example esp32/nvs/wifi_ota.csv`, điền ssid/pass/`url=https://192.168.1.10:8443/ota`,
   `max_ver=0`; `bash esp32/tools/provision_nvs.sh esp32/nvs/wifi_ota.csv /dev/ttyUSB0`.
4. **Bản gốc v1**: `main/version.h` `FW_VERSION_CODE 1`, `PROJECT_VER "1.0.0"`; `idf.py build flash monitor`.
   Pi (sorter-core) đang chạy và PING mỗi 500 ms; dashboard ThingsBoard hiện `fw_version = 1`.
5. Máy chủ OTA (app P7) chạy HTTPS 8443, phục vụ `{url}/manifest.json`, `{url}/manifest.sig`, `{url}/firmware.bin`,
   nhận `POST {url}/report`.

Kích hoạt OTA từ dashboard: RPC `otaCheck` (Admin) → Pi gửi `OTA_CHECK` → ESP32 chỉ nhận ở IDLE (băng đang dừng).
Theo dõi: `idf.py monitor` (tag `ota`), telemetry `ota_stage/ota_reason/ota_progress` trên dashboard, bảng kết quả
nạp trong app OTA (từ `/report`).

Chuỗi stage mong đợi khi thành công: CHECKING → DOWNLOADING 0..100 % (mỗi 10 %) → VERIFIED → REBOOTING →
(boot bản mới, nhận PING) → SUCCESS.

## 6a — Bản hợp lệ → SUCCESS

1. Sửa `FW_VERSION_CODE 2`, `FW_VERSION_NAME "1.0.1"`, `PROJECT_VER "1.0.1"`; `idf.py build`.
2. App OTA: chọn `build/smart_sorter_esp32.bin` (byte đầu 0xE9, version 1.0.1), ký, phát hành.
3. Dashboard → `otaCheck`. Kỳ vọng: LED xanh nháy nhanh, log `manifest: model=sorter-esp32 version=2`,
   `SHA-256 khop`, `VERIFIED`, `REBOOTING`, ESP32 khởi động lại từ `ota_1`.
4. Sau boot: log `anh moi dang cho xac nhan — self-test`; Pi PING → `self-test DAT → mark valid`, `EVT_OTA_STATUS(SUCCESS)`,
   STATUS.fw_version_code = 2; app OTA nhận report `SUCCESS/SELFTEST_OK`. Băng chuyền không chạy trong suốt quá trình.
5. `otaCheck` lần nữa với cùng manifest → `UP_TO_DATE` (không tải firmware, về IDLE).

## 6b — Sửa 1 byte firmware → HASH_MISMATCH

1. App OTA: nút DEMO "Sửa 1 byte firmware" (manifest/chữ ký giữ nguyên, firmware.bin bị đổi 1 byte).
2. `otaCheck`. Kỳ vọng: chữ ký manifest hợp lệ → DOWNLOADING tới 100 % → log `SHA-256 SAI` → `esp_ota_abort` →
   `REJECTED reason=HASH_MISMATCH`, report `REJECTED/HASH_MISMATCH`, ESP32 **không** reboot, STATUS vẫn phiên bản cũ,
   fsm về IDLE (LED tắt), START vẫn chạy bình thường.

Biến thể: "Giả manifest" (chữ ký sai) → `REJECTED SIGNATURE_INVALID` ngay sau CHECKING, không tải firmware.

## 12 — Hạ phiên bản → ROLLBACK_REJECTED

1. Đang chạy v2. App OTA: nút DEMO "Hạ phiên bản" (manifest version_code=1 ký hợp lệ, hoặc phát hành lại bản v1).
2. `otaCheck`. Kỳ vọng: log `CHONG ROLLBACK: version 1 <= dang chay 2 hoac <= max_ver 2` → `REJECTED ROLLBACK_REJECTED`,
   không tải firmware. Lưu ý `max_ver` trong NVS được ghi khi OTA đặt boot partition (trước reboot) nên kể cả
   nạp lại v1 qua USB, OTA lên v2 lần nữa cũng bị từ chối cho tới khi nạp NVS mới (nvs/README.md).

## Thử rollback (self-test thất bại → quay về bản cũ)

1. Build bản v3 **cố ý hỏng**: `idf.py menuconfig` → Smart Sorter → bật `SORTER_OTA_SELFTEST_FAIL_DEMO`;
   `FW_VERSION_CODE 3`; `idf.py build`. Bản này không trả lời PING nên self-test O-13 không thể đạt.
2. Ký và phát hành v3 trong app OTA; `otaCheck` từ dashboard → SUCCESS tải, VERIFIED, REBOOTING.
3. Boot v3: log `self-test: cho PING tu Pi toi da 30 s`; Pi thấy mất liên kết (không PONG); sau 30 s:
   `self-test THAT BAI → quay ve ban cu` → `esp_ota_mark_app_invalid_rollback_and_reboot`.
4. Boot lại v2: log `phat hien vua quay lui tu ota_x → bao ROLLED_BACK`, `EVT_OTA_STATUS(ROLLED_BACK)` (sau khi có PING),
   report `ROLLED_BACK/SELFTEST_FAILED`; STATUS.fw_version_code = 2. Chỉ báo một lần (NVS `rb_addr`).
5. Biến thể "treo/crash": nếu bản mới abort trong init (ESP_ERROR_CHECK) hoặc bị TWDT reset trước khi mark valid,
   bootloader thấy ảnh PENDING_VERIFY đã boot hụt → đánh dấu ABORTED và boot bản cũ (CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE).

Sau demo rollback, tắt `SORTER_OTA_SELFTEST_FAIL_DEMO` và dùng version_code ≥ 4 cho bản tiếp theo (max_ver đã = 3).

## Lỗi TLS (kiểm tra CA)

Build với `ota_ca.pem` sai (hoặc placeholder) → `otaCheck` → log `TLS loi ... verify_flags` → `REJECTED TLS_ERROR`.
Không có tùy chọn nào trong firmware để bỏ qua xác minh (xem sdkconfig.defaults và `http_client_new()`).

## Checklist nghiệm thu P8

- [ ] 6a SUCCESS, dashboard fw_version mới, báo cáo trong app
- [ ] 6b HASH_MISMATCH, bản cũ còn nguyên
- [ ] Giả manifest → SIGNATURE_INVALID
- [ ] 12 ROLLBACK_REJECTED
- [ ] Self-test thất bại → ROLLED_BACK tự động
- [ ] Băng không chạy trong suốt OTA; ESTOP trong OTA vẫn được nhận (vào ESTOP, OTA bị hủy BAD_STATE)
- [ ] Wi-Fi tắt sau mỗi nhánh kết thúc (log `Wi-Fi da tat`)
