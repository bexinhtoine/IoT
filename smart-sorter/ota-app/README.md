# ota-app — App Secure OTA trên máy dev (MODULE P7, O-01 → O-07, đặc tả 4.5)

App PySide6 ký và phát hành firmware ESP32 qua HTTPS, với đăng nhập bcrypt/TOTP, khóa ECDSA P-256
mã hóa passphrase, manifest chuẩn hóa + chữ ký DER/base64, lịch sử SQLite và tab demo tấn công.

```
ota-app/
  ota_app/
    paths.py          thư mục dữ liệu (~/.smart-sorter-ota hoặc %APPDATA%\smart-sorter-ota), DB, keys/, releases/
    auth.py           O-01 users + bcrypt cost 12, khóa 5 phút/5 lần sai, TOTP, Session 15 phút
    keys.py           O-02 ECDSA P-256, private PEM mã hóa (600), key_id, fingerprint, ota_pubkey.h
    firmware.py       O-03 inspect_firmware (0xE9, esp_app_desc_t @32, Secure Boot V2), make_fake_firmware
    manifest.py       O-04 build/serialize/sign/verify, verify_release_dir (thứ tự như ESP32), write_release
    history.py        O-06 releases, device_reports, request_log, export CSV
    server.py         O-05 aiohttp HTTPS 8443 (TLS ≥ 1.2) trong thread riêng, 4 route, log request
    demo_attacks.py   O-07 sửa 1 bit firmware / giả manifest / hạ phiên bản / khôi phục
    main.py           AppContext (luồng phát hành, không cần Qt) + python -m ota_app.main
    ui/               login_window, main_window, tab_release, tab_history, tab_keys, tab_server, tab_demo
  tools/verify_cli.py xác minh thư mục phát hành ngoài giao diện
  tests/              pytest (không cần Qt)
  certs/README.md     cách lấy cert máy chủ OTA từ P5 (không commit cert/key)
```

## Cài đặt

```bash
cd ota-app
python -m pip install -r requirements.txt        # Python 3.11+; Windows: export PYTHONUTF8=1
```

## Chạy

```bash
export PYTHONUTF8=1
python -m ota_app.main                # thư mục dữ liệu mặc định
python -m ota_app.main --data-dir /tmp/ota-demo -v
```

Biến môi trường `SMART_SORTER_OTA_HOME` ghi đè thư mục dữ liệu (DB `ota_app.db`, `keys/`, `releases/<version_code>/`).

## Luồng phát hành

1. **Lần chạy đầu** tạo tài khoản quản trị `release_manager` (mật khẩu ≥ 10 ký tự). Đăng nhập; tùy chọn bật TOTP
   (`AuthStore.begin_totp_setup` → QR → `confirm_totp_setup`). Sai 5 lần → khóa 5 phút (lưu DB). 15 phút không
   thao tác → về màn hình đăng nhập.
2. **Tab Khóa**: tạo cặp khóa ECDSA P-256 với passphrase. Private key chỉ nằm ở `keys/ota_private.pem` (PEM mã hóa,
   quyền 600), không bao giờ hiển thị. Bấm **Xuất ota_pubkey.h** → `esp32/components/ota_secure/include/ota_pubkey.h`
   (chuỗi PEM dạng C + `OTA_PUBKEY_ID`) rồi build lại firmware (P8). `key_id` = 8 byte đầu SHA-256(DER SPKI).
3. **Tab Phát hành**: chọn `.bin` → app kiểm tra byte đầu `0xE9`, đọc `esp_app_desc_t` (version, project, IDF),
   SHA-256, khối chữ ký Secure Boot V2. Nhập `version_code` (bắt buộc > bản gần nhất), `version_name`, passphrase →
   **Ký & phát hành** (chỉ `release_manager`). App ghi `releases/<version_code>/{firmware.bin,manifest.json,manifest.sig}`,
   tự xác minh lại bằng public key, rồi ghi bảng `releases`.
4. **Tab Máy chủ**: chọn cert/key (xem dưới) → **Start**. Server chỉ trả lời
   `GET /ota/manifest.json`, `GET /ota/manifest.sig`, `GET /ota/firmware.bin`, `POST /ota/report`; mọi thứ khác 404.
   URL theo IP máy hiện trong tab; nhúng URL này vào firmware/NVS của ESP32 (O-08).
5. **Tab Lịch sử**: bảng phát hành + báo cáo thiết bị (`POST /ota/report`), lọc, xuất CSV.
6. **Tab Demo** (nền vàng, CHỈ DÙNG ĐỂ DEMO): *Sửa 1 byte firmware* → `HASH_MISMATCH`; *Giả manifest* →
   `SIGNATURE_INVALID`; *Hạ phiên bản* (ký hợp lệ `version_code = thiết bị − 1`) → `ROLLBACK_REJECTED`;
   **Khôi phục bản hợp lệ** chép lại từ `release_valid/`.

### Manifest (4.5)

```
{"created_at":"2026-11-02T09:15:00Z","device_model":"sorter-esp32","fw_file":"firmware.bin","fw_sha256":"<64 hex>","fw_size":912384,"key_id":"<16 hex>","version_code":3,"version_name":"1.0.2"}
```
`manifest.sig` = base64(DER ECDSA-P256/SHA-256) ký trên đúng các byte của `manifest.json`. Thứ tự xác minh
(app và ESP32 giống nhau): chữ ký → parse → `device_model` → `version_code` tăng → `fw_size` → SHA-256.

## Chuẩn bị cert máy chủ OTA (từ P5)

```bash
cd thingsboard/pki && ./make_ota_server_cert.sh <ip-may-dev>   # ra out/ota_server.pem, out/ota_server.key, out/ca.pem
cp thingsboard/pki/out/ota_server.{pem,key} ota-app/certs/        # đã gitignore
```
Chi tiết và cách tạo cert tự ký tạm: `certs/README.md`. Server dùng `ssl.PROTOCOL_TLS_SERVER`, tối thiểu TLS 1.2;
không bao giờ tắt xác minh TLS phía client/ESP32.

## Kiểm tra bằng curl (tiêu chí xong)

```bash
curl --cacert thingsboard/pki/out/ca.pem https://<ip>:8443/ota/manifest.json
curl --cacert thingsboard/pki/out/ca.pem https://<ip>:8443/ota/manifest.sig
curl --cacert thingsboard/pki/out/ca.pem -o fw.bin https://<ip>:8443/ota/firmware.bin
curl --cacert thingsboard/pki/out/ca.pem -X POST -H 'Content-Type: application/json' \
     -d '{"device":"sorter-esp32-01","version_code":3,"result":"SUCCESS","reason":""}' https://<ip>:8443/ota/report
curl --cacert thingsboard/pki/out/ca.pem -i https://<ip>:8443/ota/        # 404, không liệt kê thư mục
```

## verify_cli

```bash
python tools/verify_cli.py ~/.smart-sorter-ota/releases/3 --pubkey ~/.smart-sorter-ota/keys/ota_pubkey.pem --min-version 2
# HỢP LỆ: reason=NONE ...        (exit 0)   |   TỪ CHỐI: reason=HASH_MISMATCH ...   (exit 1)
```

## Test

```bash
cd ota-app && PYTHONUTF8=1 python -m pytest -q
```
Test không cần PySide6: ký/xác minh vòng tròn; 3 kiểu tấn công bị từ chối đúng reason; chặn version không tăng;
khóa tài khoản sau 5 lần sai và mở sau 5 phút (clock giả); `inspect_firmware` trên firmware giả; server HTTPS thật
với CA tự tạo trong test (3 GET + POST hợp lệ/sai + route lạ 404 + từ chối TLS < 1.2).

## Ghi chú bảo mật
- Không log/hiển thị private key hay passphrase; ô passphrase được xóa ngay sau khi dùng.
- Private key server (`certs/*.key`) và khóa ký chỉ nằm trên máy dev, quyền 600 (POSIX).
- `POST /ota/report` giới hạn 2 KB và kiểm tra kiểu từng trường; server không phục vụ file nào ngoài 3 file cố định.
