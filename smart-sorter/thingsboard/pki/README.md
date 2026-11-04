# PKI nội bộ cho Smart Sorter (S-01)

Thư mục này chứa script tạo CA nội bộ và các chứng chỉ cho **mutual TLS** giữa Raspberry Pi và
ThingsBoard, cộng cert cho máy chủ Secure OTA. Mọi file sinh ra nằm trong `out/` (đã `.gitignore`);
**không bao giờ commit `*.key`**.

## Mutual TLS là gì và vì sao dùng

TLS thông thường (như trình duyệt) chỉ có **server** chứng minh danh tính bằng chứng chỉ; client ẩn danh.
Mutual TLS (mTLS) thêm chiều ngược lại: **client cũng trình chứng chỉ** và server kiểm tra nó.

```
Pi (client)                                   ThingsBoard (server, 8883)
  |-- ClientHello ----------------------------------->|
  |<-- server.pem (CN=tb.local, ký bởi CA) -----------|  (1) Pi xác minh bằng ca.pem + so SAN với hostname
  |<-- CertificateRequest ----------------------------|
  |-- device.pem (CN=sorter-pi-01, ký bởi CA) ------->|  (2) ThingsBoard tìm device có credentials X.509
  |-- CertificateVerify (ký bằng device.key) -------->|      trùng chứng chỉ này -> nhận diện thiết bị
  |<==== kênh mã hóa TLS 1.2/1.3 =====================>|
```

1. **Pi xác minh server** (S-04): `ca.pem` là neo tin cậy duy nhất; cert server phải do CA này ký và
   SAN phải chứa `tb.local`/IP mà Pi kết nối (`check_hostname = True`). Không có tùy chọn bỏ qua.
2. **ThingsBoard xác minh thiết bị** (T-02): credentials của device là chính nội dung `device.pem`;
   ai không có `device.key` tương ứng thì không thể hoàn tất handshake, nên không cần access token.
   Kết nối không cert hoặc cert lạ bị từ chối ngay ở tầng TLS/đăng nhập MQTT.

Tất cả khóa là **EC P-256** (nhỏ, nhanh trên Pi và ESP32/mbedTLS). Chữ ký SHA-256.

## Thứ tự chạy (Git Bash / Linux / macOS, cần `openssl` ≥ 1.1.1)

```bash
cd thingsboard/pki
./make_ca.sh                                   # out/ca.pem + out/ca.key (5 năm)
./make_server_cert.sh tb.local 192.168.1.10    # out/server.pem + out/server_key.pem (2 năm, SAN DNS+IP)
./make_device_cert.sh sorter-pi-01             # out/sorter-pi-01.pem + .key (1 năm, clientAuth)
./make_ota_server_cert.sh 192.168.1.10         # out/ota_server.pem + .key (máy chủ OTA 8443)
```

Mỗi script in hướng dẫn copy file đi đâu. Script idempotent: đã có khóa thì giữ nguyên; `--force` tạo lại
(tạo lại CA làm mọi cert đã ký vô hiệu).

| File | Đi đâu | Quyền |
|---|---|---|
| `ca.pem` | Pi `/etc/sorter/certs/ca.pem`; ESP32 `esp32/main/certs/ota_ca.pem`; `ota-app/certs/ca.pem` | 644 |
| `ca.key` | Chỉ máy quản trị | 600 |
| `server.pem`, `server_key.pem` | `thingsboard/certs/` → mount `/config` trong container | 644 / 600 (uid 799 đọc được) |
| `<device>.pem` | Pi `/etc/sorter/certs/device.pem` **và** dán vào ThingsBoard (credentials X.509) | 644 |
| `<device>.key` | Pi `/etc/sorter/certs/device.key` | 600, owner `sorter` |
| `ota_server.pem/.key` | `ota-app/certs/` | 644 / 600 |

## Kiểm tra nhanh

```bash
openssl verify -CAfile out/ca.pem out/server.pem out/sorter-pi-01.pem
openssl x509 -in out/server.pem -noout -text | grep -A1 "Subject Alternative Name"
# Sau khi ThingsBoard chạy:
openssl s_client -connect tb.local:8883 -CAfile out/ca.pem -cert out/sorter-pi-01.pem -key out/sorter-pi-01.key </dev/null
#   -> "Verify return code: 0 (ok)"; không có -cert/-key: handshake xong nhưng MQTT CONNECT sẽ bị từ chối
```

## Gia hạn

Cert thiết bị hạn 1 năm: chạy lại `./make_device_cert.sh sorter-pi-01 --force`, copy lên Pi, dán cert mới
vào ThingsBoard, khởi động lại `sorter-core`. Cert server 2 năm: `make_server_cert.sh ... --force` rồi
`docker compose restart`. Thêm nhắc lịch gia hạn vào RUNBOOK.
