# thingsboard/ — Nền tảng IoT cho Smart Logistics Sorter (MODULE P5)

ThingsBoard CE chạy Docker trên laptop; Raspberry Pi kết nối **MQTT TLS 8883 với chứng chỉ X.509** (mutual TLS);
dashboard Điều khiển (Admin) và Giám sát (Viewer); RPC whitelist; alarm bảo mật. Mã chức năng: T-01 → T-08,
S-01, S-02, S-04, S-05.

```
thingsboard/
  docker-compose.yml   ThingsBoard CE tb-postgres, chỉ publish 8080 (web) + 8883 (MQTTS); KHÔNG publish 1883
  .env.example         biến cho compose + tools (copy thành .env)
  SETUP.md             hướng dẫn từng bước trên UI: profile, device X.509, rule chain, user/2FA, dashboard, kiểm tra
  pki/                 CA nội bộ EC P-256 + cert server / thiết bị / OTA (bash + openssl); out/ không commit
  certs/               (tạo khi cài) server.pem + server_key.pem mount vào container /config — không commit
  widgets/             2 widget tùy chỉnh: Defect image card, Sorter control panel (+ README cách dán)
  tools/               rpc_cli.py (gọi RPC qua REST để test whitelist), tb_provision.py (tạo profile/device/customer)
  dashboards/          nơi lưu JSON export của 2 dashboard sau khi dựng (README)
```

Phần Pi tương ứng: `pi/sorter/tb_client.py` (TelemetrySink MQTT TLS, hàng đợi, backoff, RPC),
`pi/sorter/rpc_handler.py` (whitelist 4.4, rate limit, audit), test `pi/tests/test_tb_client.py`,
`pi/tests/test_rpc_handler.py`.

## Bắt đầu nhanh

```bash
cd thingsboard/pki && ./make_ca.sh && ./make_server_cert.sh tb.local <IP-laptop> && ./make_device_cert.sh sorter-pi-01
cd .. && mkdir -p data logs certs && cp pki/out/server.pem pki/out/server_key.pem certs/ && cp .env.example .env
docker compose up -d          # http://localhost:8080 (tenant@thingsboard.org / tenant -> đổi ngay)
```
Rồi theo `SETUP.md` mục 2 → 8.

## Nguyên tắc bảo mật áp dụng ở đây

- Thiết bị xác thực bằng cert (S-01); Pi luôn xác minh server bằng CA nội bộ, TLS ≥ 1.2, kiểm tra hostname; code
  **không có** tùy chọn bỏ qua xác minh (S-04).
- Viewer chỉ có thiết bị gương `sorter-view` + dashboard Giám sát → không có đường gửi RPC tới thiết bị thật (T-08).
- Whitelist RPC 8 phương thức, kiểm tra kiểu/miền tham số, 5 lệnh/10 s, E-stop luôn được nhận; mọi RPC ghi
  `rpc_audit` và gửi `rpc_log`; phương thức lạ sinh alarm `Security alert` (S-05).
- 2FA TOTP + password policy + khóa sau 5 lần sai (S-02).
- Không commit khóa: `pki/out/`, `certs/`, `.env` nằm trong `.gitignore`.
