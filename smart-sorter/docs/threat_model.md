# Mô hình đe dọa (STRIDE rút gọn) — Smart Logistics Sorter

Phạm vi: mô hình demo trong LAN của nhóm (SPEC 1.4). Kẻ tấn công giả định: (1) người đứng cạnh băng chuyền có thể dán QR, chạm vào hộp máy, cắm USB vào ESP32; (2) người cùng mạng Wi-Fi/LAN; (3) người có tài khoản Viewer hoặc đã lấy được mật khẩu Admin; (4) người kiểm soát được máy chủ OTA hoặc đường HTTPS tới nó. Không xét: tấn công vật lý phá hoại, tấn công chuỗi cung ứng linh kiện, lỗ hổng 0-day trong ThingsBoard/ESP-IDF.

Ký hiệu STRIDE: **S** Spoofing (mạo danh), **T** Tampering (sửa đổi), **R** Repudiation (chối bỏ), **I** Information disclosure (lộ thông tin), **D** Denial of service (từ chối dịch vụ), **E** Elevation of privilege (leo thang đặc quyền). Mức: Cao / Trung bình / Thấp theo tác động lên luật phân loại (hàng sai khay hoặc hàng hỏng lọt) và an toàn (băng chạy ngoài ý muốn).

## Luồng dữ liệu

```
 [Kiện hàng + QR] --camera--> [Pi: qr_reader → qr_security → classifier → decision]
                                       |  UART (dây nội bộ)              | MQTT TLS 8883 (mTLS)
                                       v                                  v
                               [ESP32: băng, servo, LED, E-stop]   [ThingsBoard: dashboard, RPC, alarm]
                                       ^  HTTPS 8443 (chỉ khi OTA)        ^  HTTPS + JWT + 2FA
                               [App Secure OTA trên laptop]        [Admin / Viewer]
```

## 1. Tầng kiện hàng / mã QR

| STRIDE | Đe dọa | Mức | Biện pháp | S-xx |
|---|---|---|---|---|
| S | In QR giả chọn kho A/B tùy ý | Cao | Chữ ký HMAC-SHA256 khóa 32 byte, so sánh hằng thời gian; sai → REJECT_SECURITY + alarm | S-03, S-09 |
| S | Chụp lại/photocopy QR hợp lệ dán lên hộp khác (replay) | Cao | Bảng `used_codes`, mã đã dùng → DUPLICATE; timestamp quá hạn → EXPIRED | S-03 |
| T | Dán 2 QR, QR mờ/rách để ép hệ thống đoán | Trung bình | MULTI_QR / NO_QR → REJECT (fail-safe, không bao giờ A/B); tối đa 3 khung, tăng tương phản | S-03, P-03 |
| T | Hộp hỏng nhưng QR hợp lệ (không phải tấn công mạng nhưng là mục tiêu của AI) | Cao | AI kiểm bao bì, ngưỡng ưu tiên recall lớp Lỗi ≥ 95 %; dưới `min_confidence` → Lỗi | AI-07, P-05 |
| I | Đọc QR để biết mã kiện/kho | Thấp | Chấp nhận: QR không chứa bí mật; khóa HMAC không suy ra được từ chữ ký | S-03 |
| D | Rải QR giả liên tục làm băng chỉ loại hàng | Trung bình | Mỗi QR giả là 1 sự kiện MAJOR; ≥ 5 chữ ký sai/phút → luật cứng cảnh báo; Isolation Forest | S-09, A-04 |
| R | Nhân viên nói "QR tôi in là thật" | Thấp | Khóa HMAC chỉ nằm trên Pi (600) và máy in QR của nhóm; log `packages` + ảnh | S-03, S-09 |

## 2. Tầng UART Pi ↔ ESP32

| STRIDE | Đe dọa | Mức | Biện pháp | S-xx |
|---|---|---|---|---|
| T | Nhiễu/lỗi bit làm sai TYPE/target | Cao | CRC16 CCITT-FALSE mỗi khung, LEN ≤ 64, dò lại SOF; sai CRC → bỏ + đếm | S-06 |
| T | Khung lặp do gửi lại → gạt 2 lần | Trung bình | SEQ + ACK; bên nhận nhớ SEQ, trùng chỉ ACK lại | S-06 |
| S/E | Thiết bị khác cắm vào UART gửi START/SORT/SET_PARAM | Cao (nếu mở hộp) | Whitelist TYPE theo trạng thái (BAD_STATE), miền tham số (BAD_PARAM); OTA_CHECK không nhận URL; dây nội bộ trong hộp kín; **CRC không chống giả mạo** (giới hạn đã công bố) | S-06 |
| D | Rút dây/Pi treo khi băng đang chạy | Cao (an toàn) | Watchdog liên kết 2 s → dừng băng, FAULT LINK_LOST; DETECTED quá 3 s không SORT → tự REJECT; E-stop vật lý cắt nguồn động cơ bằng phần cứng | E-05, E-07, E-09 |
| I | Nghe trộm UART | Thấp | Không có bí mật trên UART (chỉ lệnh/trạng thái) | — |

## 3. Tầng Raspberry Pi / MQTT

| STRIDE | Đe dọa | Mức | Biện pháp | S-xx |
|---|---|---|---|---|
| S | Thiết bị giả đẩy telemetry/ảnh lên ThingsBoard; mạo danh Pi để nhận RPC | Cao | mTLS X.509: ThingsBoard nhận diện bằng cert thiết bị, không có access token | S-01 |
| S | MITM giả ThingsBoard để gửi RPC xuống Pi | Cao | Pi xác minh cert server bằng CA nội bộ + hostname; không có đường tắt xác minh trong code | S-04 |
| I | Nghe lén telemetry/ảnh lỗi trên Wi-Fi | Trung bình | TLS ≥ 1.2 cổng 8883; 1883 không publish | S-04 |
| I | Đọc `qr_hmac.key`, `device.key` trên Pi | Cao | `/etc/sorter/secrets`, `certs` 700/600 owner sorter; dịch vụ chạy user sorter; khóa không bao giờ ra log | S-07, S-03 |
| S/E | Đăng nhập SSH bằng `pi/raspberry`, brute-force SSH, root login | Cao | Phát hiện mật khẩu mặc định; SSH chỉ key, PermitRootLogin no, AllowUsers, MaxAuthTries 3; fail2ban; ufw chỉ 22/tcp từ LAN | S-07 |
| E | Lỗi trong sorter-core bị khai thác → root | Trung bình | `User=sorter`, `NoNewPrivileges=yes`, `ProtectSystem=strict`, `ReadWritePaths` hạn chế | S-07, P-15 |
| D | Quét cổng/khai thác dịch vụ thừa (avahi, cups, bluetooth) | Thấp | Tắt dịch vụ không dùng; chỉ cổng 22 lắng nghe; unattended-upgrades vá bảo mật | S-07 |
| T | Sửa đồng hồ Pi để QR hết hạn vẫn hợp lệ | Thấp | NTP đồng bộ (audit kiểm `timedatectl`); lệch quá 5 phút về tương lai → FUTURE_TIMESTAMP | S-03, S-07 |
| R | "Không có RPC nào tới Pi" | Thấp | `rpc_audit` + `rpc_log` telemetry; `security_events` | S-05, S-09 |

## 4. Tầng ThingsBoard / người dùng

| STRIDE | Đe dọa | Mức | Biện pháp | S-xx |
|---|---|---|---|---|
| S | Đoán/đánh cắp mật khẩu Admin rồi bấm Start/E-stop | Cao | 2FA TOTP bắt buộc cho Admin; chính sách mật khẩu; khóa sau 5 lần sai | S-02 |
| E | Viewer tìm cách gửi RPC tới thiết bị thật | Cao | Viewer là Customer User chỉ được gán `sorter-view` (thiết bị gương, không kết nối thật) và dashboard Giám sát → không có đường RPC | S-02, T-08 |
| T/E | Admin bị chiếm hoặc widget bị sửa gửi phương thức/tham số lạ | Cao | Whitelist 8 phương thức, schema kiểu/miền, kiểm trạng thái ESP32; lạ → METHOD_NOT_ALLOWED + RPC_REJECTED | S-05 |
| D | Spam RPC làm băng dừng/chạy liên tục | Trung bình | Token bucket 5 lệnh/10 s; emergencyStop luôn được nhận | S-05 |
| R | "Tôi không bấm E-stop" | Trung bình | Audit Logs của ThingsBoard (theo phiên JWT) là nguồn tin cậy; `rpc_audit` trên Pi ghi lệnh đã tới; `requestedBy` **chỉ hiển thị** | S-05 |
| I | Ảnh lỗi/telemetry lộ cho người không có quyền | Thấp | Phân quyền theo customer; HTTPS cho dashboard (cấu hình ThingsBoard) | S-02 |
| D | ThingsBoard sập/mất mạng | Trung bình (không ảnh hưởng an toàn) | Pi tiếp tục phân loại offline; hàng đợi 1000 bản tin; backoff kết nối lại; alarm thiết bị offline sau 30 s | T-05, P-10 |

## 5. Tầng OTA (app máy tính ↔ ESP32)

| STRIDE | Đe dọa | Mức | Biện pháp | S-xx / O-xx |
|---|---|---|---|---|
| T | Sửa 1 byte firmware trên đường tải | Cao | SHA-256 trong manifest, vừa ghi vừa băm; sai → HASH_MISMATCH, phân vùng cũ giữ nguyên; Secure Boot từ chối ảnh ký sai khi boot | O-12, S-08 |
| S | Giả manifest / giả máy chủ OTA | Cao | Manifest ký ECDSA P-256 (khóa chỉ trên máy dev, PEM mã hóa passphrase); HTTPS xác minh CA nhúng; URL cố định, không nhận URL qua UART | O-02, O-09, O-10, O-08 |
| T | Hạ phiên bản về bản có lỗi đã biết (ký hợp lệ) | Trung bình | `version_code` phải lớn hơn bản đang chạy và giá trị lớn nhất trong NVS → ROLLBACK_REJECTED | O-11 |
| E | Ai cũng ký và phát hành được từ app | Trung bình | Đăng nhập bcrypt + TOTP, khóa 5 phút sau 5 lần sai, chỉ vai trò release_manager được ký; hết phiên 15 phút | O-01 |
| D | Firmware mới treo | Trung bình | App rollback của bootloader; self-test (PING từ Pi trong 30 s) không đạt → quay về bản cũ | O-13 |
| S | Nạp firmware lạ trực tiếp qua USB, bỏ qua OTA | Cao | Secure Boot V2: chỉ ảnh ký bằng khóa nhóm mới boot; JTAG tắt | S-08 |
| I | Dump flash lấy Wi-Fi password, CA, pubkey OTA | Trung bình | Flash Encryption (Development) + NVS encryption; đọc flash ra dữ liệu mã hóa | S-08 |
| D | Wi-Fi của ESP32 bị quét/khai thác | Thấp | Wi-Fi chỉ bật trong lúc OTA, chỉ khi ở IDLE | O-08 |
| R | "Bản này không phải tôi phát hành" | Thấp | Bảng phát hành (phiên bản, hash, người ký, thời gian) và báo cáo thiết bị `POST /ota/report` | O-06 |

## Ma trận tóm tắt S-xx ↔ tầng

| | QR/kiện | UART | Pi/MQTT | ThingsBoard/người dùng | OTA |
|---|---|---|---|---|---|
| S-01 mTLS thiết bị | | | ● | ● | |
| S-02 Người dùng, 2FA | | | | ● | |
| S-03 QR HMAC + chống replay | ● | | ● | | |
| S-04 MQTT TLS | | | ● | ● | |
| S-05 RPC whitelist + audit | | ● | ● | ● | |
| S-06 UART CRC/SEQ/whitelist | | ● | | | |
| S-07 Hardening Pi | | | ● | | |
| S-08 Secure Boot / Flash Enc. | | ● | | | ● |
| S-09 Nhật ký sự kiện | ● | ● | ● | ● | ● |
| A-xx Phát hiện bất thường | ● | ● | ● | ● | |

## Rủi ro còn lại (chấp nhận có chủ đích)

Xem mục "Giới hạn" trong `docs/security_slide.md`: CRC không chống giả mạo (UART nội bộ), `requestedBy` chỉ hiển thị, Flash Encryption Development còn cho nạp lại ảnh đã ký, Secure Boot ESP32 không thu hồi khóa, không mã hóa thẻ SD của Pi, CA nội bộ không có CRL, Isolation Forest có thể báo nhầm, `resetUsedCodes` chỉ bật ở demo_mode.
