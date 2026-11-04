# ota_secure — Secure OTA phía ESP32 (O-08 → O-15)

## File cần cung cấp trước khi build bản dùng OTA

| File | Nguồn | Ghi chú |
|---|---|---|
| `certs/ota_ca.pem` | `thingsboard/pki/out/ca.pem` (CA nội bộ ký cert máy chủ OTA, P5/P7) | `.gitignore` gốc chặn `*.pem` và `certs/` nên không bao giờ bị commit. Thiếu file → CMake cảnh báo và nhúng `ota_ca.pem.example` (placeholder) để vẫn build được; khi đó OTA luôn bị từ chối `TLS_ERROR`. |
| `include/ota_pubkey.h` | App Secure OTA (P7, O-02) xuất ra | Định dạng như `include/ota_pubkey.h.example`. Thiếu file → `#warning` và dùng file mẫu (chữ ký thật sẽ bị `SIGNATURE_INVALID`). |

```
cp ../../../thingsboard/pki/out/ca.pem certs/ota_ca.pem
cp <thư mục app OTA xuất>/ota_pubkey.h include/ota_pubkey.h
```

Cert máy chủ OTA (`make_ota_server_cert.sh`) phải có SAN chứa đúng IP/hostname trong `url` của NVS, vì ESP32
kiểm tra cả chuỗi tin cậy lẫn tên máy chủ (không bao giờ `skip_cert_common_name_check`).

## Cấu hình trong NVS (namespace `ota_cfg`)

| key | kiểu | ý nghĩa |
|---|---|---|
| `ssid` | string | Wi-Fi SSID |
| `pass` | string | mật khẩu (rỗng = mạng mở) |
| `url` | string | base URL **https** của máy chủ OTA, ví dụ `https://192.168.1.10:8443/ota` |
| `max_ver` | u32 | version_code lớn nhất đã nạp thành công (chống rollback O-11); firmware tự cập nhật |
| `rb_addr` | u32 | nội bộ: địa chỉ phân vùng đã báo ROLLED_BACK (tránh báo lặp mỗi lần boot) |

Nạp bằng `esp32/nvs/wifi_ota.csv` + `esp32/tools/provision_nvs.sh` (xem `esp32/nvs/README.md`).

## Hợp đồng với máy chủ OTA (P7)

Với `url = https://HOST:8443/ota`, ESP32 gọi:

- `GET  {url}/manifest.json` (≤ 1 KB) — JSON một dòng (4.5)
- `GET  {url}/manifest.sig`  (≤ 256 B) — base64 chữ ký ECDSA P-256/SHA-256 dạng DER trên đúng các byte manifest.json
- `GET  {url}/firmware.bin`  — đúng `fw_size` byte, SHA-256 = `fw_sha256`
- `POST {url}/report` — `{"device":"sorter-esp32-01","version_code":N,"result":"SUCCESS|REJECTED|ROLLED_BACK","reason":"..."}`

## Luồng và mã stage/reason

Xem `ota_secure.c` (đầu file) và `esp32/OTA_TEST.md`.
