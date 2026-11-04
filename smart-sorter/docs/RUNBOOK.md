# RUNBOOK — vận hành và demo Smart Logistics Sorter (P10)

Tài liệu một trang cho người trực máy: bật hệ thống đúng thứ tự, checklist trước demo (SPEC 5.4), xử lý sự cố
thường gặp. Lệnh lấy từ README của từng module: `pi/README.md`, `esp32/README.md`, `thingsboard/SETUP.md`,
`ota-app/README.md`, `ai/README.md`, `docs/anomaly.md`. Checklist in giấy cho buổi demo: `docs/demo_checklist.md`.

## 0. Bảng tra nhanh: cổng, đường dẫn, dịch vụ

| Thành phần | Máy | Cổng / cổng nối | Đường dẫn quan trọng | Lệnh xem trạng thái |
|---|---|---|---|---|
| ThingsBoard CE (Docker `tb-postgres`) | laptop | **8080** web UI/REST (map 9090 trong container), **8883** MQTT TLS; **1883 KHÔNG mở** | `thingsboard/docker-compose.yml`, `.env`, `certs/server.pem` + `server_key.pem`, `data/`, `logs/` | `docker compose ps`, `docker compose logs -f` |
| PKI nội bộ | laptop | — | `thingsboard/pki/out/{ca.pem, server.pem, sorter-pi-01.pem/.key, ota_server.pem/.key}` | `openssl x509 -in ... -noout -subject -dates -ext subjectAltName` |
| sorter-core (`sorter.service`) | Pi | UART `/dev/serial0` 115200 8N1; MQTT ra 8883 | `/etc/sorter/config.yaml`, `/etc/sorter/secrets/qr_hmac.key` (600), `/etc/sorter/certs/{ca,device}.pem, device.key` (600), `/opt/sorter` (code + venv), `/opt/sorter/models/`, `/var/lib/sorter/sorter.db`, `/var/lib/sorter/defects/`, `/var/log/sorter/sorter.jsonl` | `systemctl status sorter`, `journalctl -u sorter -f` |
| ESP32 firmware | board | UART2 GPIO16 RX / GPIO17 TX ↔ Pi GPIO14 TX (chân 8) / GPIO15 RX (chân 10), GND chung; USB `COMx`/`/dev/ttyUSB0` để nạp/monitor | `esp32/build/smart_sorter_esp32.bin`, NVS `wifi_ota.csv` (ssid/pass/url/max_ver) | `idf.py -p PORT monitor`, `pi/tools/uart_monitor.py` |
| App Secure OTA | laptop | **8443** HTTPS (`/ota/manifest.json`, `/ota/manifest.sig`, `/ota/firmware.bin`, `POST /ota/report`) | `~/.smart-sorter-ota/` hoặc `%APPDATA%\smart-sorter-ota\` (`ota_app.db`, `keys/`, `releases/<version_code>/`), `ota-app/certs/ota_server.{pem,key}` | tab Máy chủ; `curl --cacert ca.pem https://<ip>:8443/ota/manifest.json` |
| Tài khoản ThingsBoard | — | — | Admin `tenant@thingsboard.org` (Tenant admin, 2FA TOTP), Viewer `viewer@sorter.local` (Customer user), sysadmin `sysadmin@thingsboard.org` | SETUP.md mục 5, 6 |
| Thiết bị ThingsBoard | — | — | `sorter-pi-01` (X.509, thiết bị thật), `sorter-view` (gương cho Viewer, nhận telemetry qua rule chain) | Devices → Latest telemetry |
| Chế độ sim (laptop, không phần cứng) | laptop | — | `pi/config.sim.yaml`, dữ liệu trong `pi/run/` | `cd pi && python -m sorter.main --sim --config config.sim.yaml` |

Trên Windows luôn `export PYTHONUTF8=1` (Git Bash) trước khi chạy Python.

## 1. Thứ tự bật hệ thống

Thứ tự bắt buộc: **laptop ThingsBoard → Pi (sorter) → ESP32 → kiểm tra dashboard → (app OTA nếu demo OTA)**.
Lý do: Pi cần ThingsBoard sẵn để kết nối 8883 (nếu chưa có thì Pi xếp hàng telemetry và kết nối lại, không chết);
ESP32 bật sau để Pi gửi START/PING ngay khi ESP32 lên IDLE.

### Bước 1 — Laptop: ThingsBoard (T-01)

```bash
cd thingsboard
docker compose up -d
docker compose logs -f | grep -iE "started|ssl|8883"      # chờ "Started ThingsboardServerApplication" (~2-3 phút lần đầu)
```
Kiểm tra: mở http://localhost:8080 đăng nhập Admin (sẽ hỏi mã TOTP) → dashboard "Sorter — Điều khiển".
Kiểm TLS 8883 từ laptop (Verify return code: 0):
```bash
openssl s_client -connect tb.local:8883 -CAfile pki/out/ca.pem -cert pki/out/sorter-pi-01.pem -key pki/out/sorter-pi-01.key </dev/null 2>&1 | grep -E "Verify return|subject="
```
Laptop phải có **IP tĩnh** đúng với SAN trong `server.pem` (ví dụ `192.168.1.10`); đổi mạng Wi-Fi = phải tạo lại cert server
(`pki/make_server_cert.sh tb.local <ip-mới>`) và khởi động lại container.

### Bước 2 — Raspberry Pi: sorter-core (P-15)

```bash
ssh <admin>@<ip-pi>
timedatectl                              # NTP synchronized: yes (TLS cần giờ đúng)
sudo -u sorter /opt/sorter/venv/bin/python -c "from sorter.config import load_config; load_config('/etc/sorter/config.yaml'); print('OK')"   # chạy trong /opt/sorter
sudo systemctl restart sorter
journalctl -u sorter -f                  # chờ: "OnnxClassifier sẵn sàng", "connected (mTLS)", "RPC handler đã đăng ký"
```
Dịch vụ tự chạy cùng hệ thống (`enable`), tự khởi động lại khi lỗi (`Restart=on-failure`). Log JSON: `tail -f /var/log/sorter/sorter.jsonl`.
Trước khi ESP32 lên, log sẽ có "ESP32 chưa trả lời GET_STATUS; không gửi START" — bình thường.

### Bước 3 — ESP32

1. Cấp nguồn động cơ/servo (nguồn riêng, GND chung) rồi cắm ESP32. Nút E-stop ở trạng thái **nhả**.
2. Theo dõi: trên Pi `python /opt/sorter/tools/uart_monitor.py --port /dev/serial0 --ping 500` (tắt trước khi dùng sorter.service,
   vì chỉ một tiến trình được mở `/dev/serial0`), hoặc trên laptop `idf.py -p COMx monitor`.
3. Mong đợi: STATUS mỗi 1 s với `state=IDLE`, `fw_version_code` đúng; sorter-core gửi START → `RUNNING`, LED xanh sáng, băng chạy.
   Nếu không muốn băng chạy ngay: trên dashboard bấm **Stop**.

### Bước 4 — Kiểm tra dashboard (T-06)

- Thẻ "Băng chuyền" = RUNNING/IDLE, "Liên kết ESP32" = OK, phiên bản: `fw_version` (ESP32), `sw_version`, `model_version`.
- Sức khỏe Pi cập nhật mỗi 10 s (`cpu_temp`, `cpu_load`...). Thiết bị `sorter-pi-01` **Active**.
- Dashboard Giám sát (Viewer) cũng cập nhật (rule chain sao chép sang `sorter-view`).
- Gửi thử `getStatus` từ laptop: `cd thingsboard/tools && python rpc_cli.py --device sorter-pi-01 --method getStatus` → `{"ok": true, ...}`.

### Bước 5 — (chỉ khi demo OTA) App Secure OTA (O-05)

```bash
cd ota-app && export PYTHONUTF8=1 && python -m ota_app.main
```
Đăng nhập `release_manager` → tab Máy chủ chọn `certs/ota_server.pem` + `.key` → **Start** (8443). URL hiện trong tab phải
trùng `url` đã nạp vào NVS của ESP32 (`esp32/nvs/wifi_ota.csv`), SAN của `ota_server.pem` phải chứa đúng IP đó.

### Tắt hệ thống

Dashboard **Stop** (hoặc để băng IDLE) → `sudo systemctl stop sorter` → rút nguồn ESP32/động cơ → `cd thingsboard && docker compose stop`
(giữ `data/`; `down -v` sẽ xóa toàn bộ dashboard/cấu hình).

## 2. Checklist trước demo (SPEC 5.4) — làm theo thứ tự, trước giờ demo ≥ 30 phút

| ☐ | Việc | Lệnh / cách làm |
|---|---|---|
| ☐ | Bộ hộp demo đánh số: 2 hộp tốt (A, B), 1 hộp rách, 1 hộp móp | Dán nhãn số lên hộp; hộp lỗi phải có vết lỗi nhìn thấy trong ROI camera |
| ☐ | In QR hợp lệ A, B; QR giả; QR "đã dùng" (bản photocopy của QR A) — cạnh ≥ 3 cm, mức sửa lỗi M | Trên Pi (cùng khóa với dịch vụ): `cd /opt/sorter && sudo -u sorter venv/bin/python tools/qr_generator.py --key /etc/sorter/secrets/qr_hmac.key --out /tmp/qr --items PKG000101:A PKG000102:B PKG000103:A PKG000104:B` và `... --items PKG000199:A --forge` (QR chữ ký sai). In file PDF A4 trong `/tmp/qr`. Dùng mã PKG mới mỗi lần in (mã đã quét là DUPLICATE) |
| ☐ | Gọi RPC `resetUsedCodes` và `resetCounters` ngay trước demo | `cd thingsboard/tools && python rpc_cli.py --device sorter-pi-01 --method resetUsedCodes --params '{"confirm":"RESET"}'` rồi `python rpc_cli.py --device sorter-pi-01 --method resetCounters` (hoặc 2 nút "Reset mã đã dùng" / "Reset bộ đếm" trên dashboard). `resetUsedCodes` chỉ chạy khi `device.demo_mode: true` |
| ☐ | Sẵn 3 bản OTA: hợp lệ (version_code mới nhất), sửa 1 byte, hạ phiên bản | App OTA: tab Phát hành ký bản mới (`idf.py build` với `FW_VERSION_CODE` tăng) → tab Demo có 3 nút "Sửa 1 byte firmware" / "Giả manifest" / "Hạ phiên bản" + "Khôi phục bản hợp lệ". Thứ tự demo: 6a (hợp lệ) → 6b (sửa 1 byte) → Khôi phục → 12 (hạ phiên bản) → Khôi phục. Kiểm trước bằng `python tools/verify_cli.py <releases/N> --pubkey keys/ota_pubkey.pem --min-version <ver đang chạy>` |
| ☐ | Đèn chiếu sáng bật ổn định ≥ 5 phút trước khi chạy (AE/AWB khóa lúc dịch vụ khởi động) | Bật đèn → đợi 5 phút → `sudo systemctl restart sorter` để khóa phơi sáng theo đúng ánh sáng demo. Chụp thử 1 ảnh: `capture_dataset.py --config /etc/sorter/config.yaml --out /tmp/chk --box-id chk --label ok --max 1` rồi xem ảnh |
| ☐ | Đăng nhập sẵn Admin (đã qua 2FA) và Viewer trên 2 cửa sổ trình duyệt riêng (hoặc 2 profile) | Admin: `tenant@thingsboard.org` + mã TOTP; Viewer: `viewer@sorter.local`. Để sẵn dashboard tương ứng, time window Realtime |
| ☐ | Board ESP32 dự phòng đã nạp firmware giống board chính (+ cùng NVS, cùng `ota_pubkey.h`, `ota_ca.pem`) | `idf.py -p PORT flash` cùng `build/`; nạp NVS `bash esp32/tools/provision_nvs.sh esp32/nvs/wifi_ota.csv PORT`; kiểm `fw_version_code` bằng `uart_monitor.py` |
| ☐ | Chạy diễn tập tự động trên laptop và bảng audit trên Pi | `cd pi && python tools/demo_check.py` (12 PASS, 9 MANUAL); trên Pi `sudo /opt/sorter/scripts/security_audit.sh --admin-user <admin> --lan-subnet <cidr>` không còn FAIL |
| ☐ | Diễn tập 12 kịch bản trên phần cứng ít nhất một lần | `python /opt/sorter/tools/demo_check.py --mode real --port /dev/serial0 --config /etc/sorter/config.yaml` (dừng `sorter.service` trước vì tool mở cùng cổng UART và cùng DB) |
| ☐ | Sao lưu `sorter.db` + điện thoại quay video (đo E-stop ≤ 0,5 s) | `sudo cp /var/lib/sorter/sorter.db ~/sorter_backup_$(date +%F).db` |

## 3. Chạy từng kịch bản (tóm tắt; chi tiết + ô tick trong `docs/demo_checklist.md`)

| # | Thao tác | Nhìn thấy gì là đạt |
|---|---|---|
| 1, 2 | Hộp tốt + QR A / QR B | khay A / B, bíp ngắn, `cnt_a`/`cnt_b` tăng, thẻ kiện mới nhất, latency ≤ 1 s |
| 3 | Hộp rách + QR hợp lệ | khay Loại, LED đỏ 1 s, ảnh lỗi + nhãn trên thẻ ảnh lỗi |
| 4 | QR giả | khay Loại, đỏ nháy + 3 bíp, alarm "Security alert" QR_BAD_SIGNATURE (MAJOR) |
| 5 | Nút DỪNG KHẨN CẤP | băng dừng ≤ 0,5 s, `conveyor_state = ESTOP`, `rpc_log` có emergencyStop; Reset E-stop → IDLE → Start |
| 6a / 6b / 12 | otaCheck với bản hợp lệ / sửa 1 byte / hạ phiên bản (băng phải IDLE) | SUCCESS + `fw_version` mới / REJECTED HASH_MISMATCH / REJECTED ROLLBACK_REJECTED; app OTA nhận report |
| 7 | Quét lại QR A | khay Loại, DUPLICATE |
| 8 | Bấm giữ E-stop vật lý | dừng ngay; Reset khi còn bấm → BAD_STATE; nhả + Reset → IDLE |
| 9 | Viewer rồi Admin | Viewer không có nút; Admin phải nhập TOTP |
| 10 | `pi/tools/attack_sim.py --i-own-this-system --scenario rpc ...` | alarm RPC_REJECTED; ≤ 20 s anomaly_flag + alarm "Anomaly" (cần model IF đã train; chưa có model thì chỉ có alarm luật cứng RULE_*) |
| 11 | Rút dây UART khi băng chạy | dừng sau ≤ 2 s, `esp_link = MẤT`; cắm lại → FAULT LINK_LOST; Reset E-stop |

## 4. Xử lý sự cố

### 4.1 Pi không kết nối được ThingsBoard 8883 (S-01, S-04)

| Hiện tượng trong `journalctl -u sorter` | Nguyên nhân | Cách xử lý |
|---|---|---|
| `certificate verify failed: certificate is not yet valid` / `has expired` | **Giờ hệ thống Pi sai** (Pi không có RTC; mất NTP khi mạng LAN không ra Internet) | `timedatectl` phải `NTP synchronized: yes`. Không có Internet: đặt tay `sudo date -s "2026-11-02 09:00:00"` hoặc trỏ NTP về laptop (`sudo nano /etc/systemd/timesyncd.conf` → `NTP=192.168.1.10`, laptop chạy chrony/ntp), rồi `sudo systemctl restart systemd-timesyncd sorter` |
| `hostname mismatch` / `doesn't match` | `thingsboard.host` trong config không nằm trong **SAN** của `server.pem` (đổi IP laptop, dùng tên khác) | `openssl x509 -in thingsboard/pki/out/server.pem -noout -ext subjectAltName`; thiếu thì tạo lại: `./make_server_cert.sh tb.local <ip>` → chép vào `thingsboard/certs/` → `docker compose restart`; hoặc sửa `host:` cho đúng tên/IP có trong SAN |
| `unable to get local issuer certificate` | Pi dùng `ca.pem` khác CA đã ký `server.pem` (đã chạy lại `make_ca.sh`) | Chép lại `pki/out/ca.pem` → `/etc/sorter/certs/ca.pem`; kiểm `openssl verify -CAfile ca.pem server.pem` |
| `PermissionError ... device.key` / `check_tls_files` báo thiếu | **Quyền file key** sai: dịch vụ chạy user `sorter`, file phải owner `sorter` quyền 600; thư mục `/etc/sorter/certs` 700 | `sudo chown sorter:sorter /etc/sorter/certs/*; sudo chmod 600 /etc/sorter/certs/*; sudo chmod 700 /etc/sorter/certs` |
| Kết nối rồi ngắt ngay, `rc=5 Not authorized` | ThingsBoard không nhận diện cert thiết bị: chưa dán/dán cũ cert vào device `sorter-pi-01`, hoặc cert có **CN khác tên device** gây nhầm khi provisioning | Devices → sorter-pi-01 → Manage credentials → X.509 → dán toàn bộ `pki/out/sorter-pi-01.pem` hiện tại. Tạo cert thiết bị bằng `make_device_cert.sh sorter-pi-01` (CN = tên device) |
| `Connection refused` 8883 | Container chưa lên / chưa map 8883 / tường lửa laptop | `docker compose ps`, `docker compose logs | grep 8883`; `nmap -p 8883 <ip-laptop>` từ Pi; mở firewall Windows cho 8883/8080 |
| `tb.local` không phân giải | mDNS/avahi tắt (harden_pi tắt avahi) | Dùng IP trong `thingsboard.host` hoặc thêm `192.168.1.10 tb.local` vào `/etc/hosts` của Pi |
| Pi online nhưng dashboard Giám sát trống | Rule chain sao chép sang `sorter-view` chưa Apply / relation `MirroredBy` ngược chiều | SETUP.md mục 4 |

Kiểm tra nhanh từ Pi: `openssl s_client -connect <ip>:8883 -CAfile /etc/sorter/certs/ca.pem -cert /etc/sorter/certs/device.pem -key /etc/sorter/certs/device.key </dev/null | grep "Verify return"` (chạy bằng `sudo -u sorter`).

### 4.2 ESP32 không phản hồi UART (E-10, P-07)

Triệu chứng: log Pi "ESP32 chưa trả lời GET_STATUS", `esp_link = false`, `uart_monitor.py` im lặng hoặc toàn rác/CRC lỗi.

1. **Chẩn đoán bằng tool** (dừng dịch vụ trước: `sudo systemctl stop sorter`):
   `python /opt/sorter/tools/uart_monitor.py --port /dev/serial0 --ping 500 --raw`. Mong đợi: PONG + STATUS mỗi 1 s.
   Không có gì → kiểm 2–5. Có byte thô nhưng không có khung / `crc_errors` tăng → sai baud (phải 115200) hoặc nhiễu/dây dài.
2. **Đảo TX/RX**: Pi GPIO14 (TXD, chân 8) → ESP32 GPIO16 (RX2); Pi GPIO15 (RXD, chân 10) ← ESP32 GPIO17 (TX2). Sai thường gặp là nối TX–TX.
3. **GND chung** giữa Pi, ESP32, nguồn động cơ và nguồn servo; thiếu GND chung = khung ngẫu nhiên hoặc im lặng.
4. **Serial console của Pi chưa tắt**: `cat /proc/cmdline` không được có `console=serial0,115200`; `sudo raspi-config` → Interface
   Options → Serial Port → login shell: **No**, hardware: **Yes**. Còn console thì Linux in log ra chân TX và chiếm cổng.
5. **UART0 (PL011) chưa gán cho serial0**: `/boot/firmware/config.txt` phải có `enable_uart=1` và `dtoverlay=disable-bt`, rồi
   `sudo systemctl disable hciuart` và reboot. Kiểm: `ls -l /dev/serial0` → trỏ `ttyAMA0` (không phải `ttyS0`, mini-UART kém ổn định).
6. Quyền cổng: user `sorter` thuộc nhóm `dialout` (`groups sorter`); chỉ **một** tiến trình được mở cổng (tắt `uart_monitor`/`esp_bench` trước khi start dịch vụ).
7. ESP32 ở FAULT/ESTOP thì vẫn trả PONG/STATUS; nếu STATUS báo `state=FAULT` → cần ESTOP_RESET (nút Reset E-stop trên dashboard).
   ESP32 reset liên tục (log boot lặp) → nguồn yếu/sụt áp khi motor khởi động: cấp nguồn động cơ riêng.
8. Kiểm toàn bộ giao thức với board: `python tools/esp_bench.py --port /dev/serial0` (16 ca PING/ACK/NACK/CRC/SEQ/E-stop/link).

### 4.3 Ảnh tối hoặc nhòe, QR/AI kém (P-02, P-03)

| Hiện tượng | Cách xử lý |
|---|---|
| Ảnh tối, màu lệch, lúc sáng lúc tối giữa các kiện | Đèn phải bật ≥ 5 phút trước, chiếu đều, không đổi trong demo; AE/AWB được **khóa lúc dịch vụ khởi động** (`camera.lock_after_s`, mặc định 2 s) nên bật đèn **trước** rồi mới `restart sorter`. Ảnh vẫn tối: tăng `lock_after_s` lên 4–5 s để AE hội tụ, hoặc thêm đèn |
| Ảnh nhòe (hộp còn trôi) | Tăng `camera.settle_ms` (150 → 250–400 ms) để băng dừng hẳn mới chụp; giảm `belt_speed`; kiểm `t_rearm`/dừng tức thì của ESP32 (E-02) |
| Màu đỏ/xanh đảo trong ảnh lỗi trên dashboard | `swap_rb` của `Picamera2Camera` (camera.py) — đối chiếu một vật màu đỏ, chỉnh cho đúng |
| Ảnh lật / hộp ngoài khung | `camera.hflip/vflip`; sửa `camera.roi [x, y, w, h]` cho đúng vùng hộp (ROI phải nằm trong `resolution`) |

### 4.4 AI sai nhiều (AI-01, AI-07)

- Nguyên nhân hàng đầu: dataset chụp khác điều kiện demo (đèn, góc, nền). Chụp thêm **tại trạm chụp, đúng đèn** bằng
  `capture_dataset.py` (ai/README.md mục 1: 6–10 ảnh/hộp, nhiều hộp, nhiều mức lỗi), chia lại theo hộp, train lại, `evaluate.py --update-card`.
- Loại nhầm hàng tốt nhiều (UNCERTAIN/DEFECT): xem `ai_label`, `ai_conf`, `ai_types_json` trong DB `packages` và ảnh ở `/var/lib/sorter/defects/`; chỉnh ngưỡng
  `defect_threshold` trong `model_card.json` (evaluate.py chọn tự động trên val sao cho recall lỗi ≥ 95 %) hoặc `ai.min_confidence`
  (mặc định 0,6). **Không hạ recall lớp lỗi dưới 95 %** — thà loại nhầm hàng tốt.
- Lọt hàng hỏng: chụp thêm đúng kiểu lỗi bị bỏ sót (20–30 ảnh, nhiều hộp), train lại; tạm thời tăng `defect_threshold` xuống (ví dụ 0,5).
- Model chạy chậm (> 150 ms): dùng bản INT8, `ai.threads: 4`, ảnh 224.
- Tăng `version` trong model card sau mỗi lần train để `model_version` trên dashboard đúng.

### 4.5 QR không đọc được (NO_QR, BAD_FORMAT)

- Kích thước in: cạnh **≥ 3 cm** (qr_generator mặc định ~3,3 cm ở 300 dpi), mức sửa lỗi M, giấy không bóng; dán phẳng, không nhăn.
- QR phải nằm trong ảnh chụp và đủ nét: kiểm bằng ảnh chụp thử; QR nhỏ trong ảnh → nâng `camera.resolution` (1280×960 → 1640×1232) hoặc hạ camera thấp hơn.
- Lưu ý ROI chỉ dùng cho AI; QR được tìm trên **toàn khung** (tối đa `camera.max_qr_frames` = 3 khung, tự tăng tương phản). Nếu vẫn NO_QR: tăng `max_qr_frames` lên 5, tăng `settle_ms`.
- Hai mã trong khung → MULTI_QR (cố ý loại). Mã đọc được nhưng BAD_SIGNATURE với QR in thật: **khóa in QR khác khóa Pi** — in lại bằng đúng `/etc/sorter/secrets/qr_hmac.key`. EXPIRED: QR in quá `qr.max_age_days`; FUTURE_TIMESTAMP: giờ Pi chậm hơn máy in QR > 5 phút (xem 4.1 NTP).
- Trên Pi thiếu pyzbar: `sudo apt install libzbar0` + `pip install pyzbar`; dự phòng cv2.QRCodeDetector vẫn chạy nhưng chậm hơn.

### 4.6 E-stop không reset được

- Nút E-stop (NC, GPIO39) **còn bị bấm** hoặc dây nút đứt/lỏng → firmware vẫn thấy HIGH → ESTOP_RESET bị NACK BAD_STATE; RPC `resetEstop` trả `BAD_STATE`.
  Nhả nút (xoay/kéo nút giữ), kiểm dây và pull-up 10 kΩ; `uart_monitor.py` STATUS vẫn `state=ESTOP` khi nút chưa nhả.
- `resetEstop` trả `BAD_STATE` với chi tiết "ESP32 đang IDLE/RUNNING": ESP32 đã về IDLE rồi — chỉ cần **Start**.
- ESP32 tự về ESTOP ngay sau reset: nút chập chờn hoặc nhiễu trên GPIO39 (dây dài gần động cơ) → rút ngắn dây, thêm tụ 100 nF.
- Sau reset về **IDLE, không tự chạy** (thiết kế E-07): bấm Start trên dashboard hoặc để sorter-core gửi START (chỉ gửi lúc khởi động).

### 4.7 OTA báo TLS_ERROR / không tải được (O-09)

- `ota_ca.pem` nhúng trong firmware phải là **CA đã ký `ota_server.pem`** (`thingsboard/pki/out/ca.pem`); build lại firmware sau khi chép CA (esp32/components/ota_secure/README.md).
- **SAN của `ota_server.pem` phải chứa đúng IP/hostname trong URL** nạp NVS (`url=https://192.168.1.10:8443/ota`); đổi IP laptop = tạo lại `make_ota_server_cert.sh <ip>` và restart server trong app OTA.
- Giờ ESP32 lấy từ SNTP khi bật Wi-Fi; mạng không ra Internet có thể làm cert "chưa hiệu lực" → kiểm log `idf.py monitor` tag `ota`.
- `DOWNLOAD_ERROR`: server chưa Start, firewall laptop chặn 8443, ESP32 không vào được Wi-Fi (ssid/pass trong NVS), `OTA_WIFI_TIMEOUT_S` quá ngắn.
- `BAD_STATE`: `otaCheck` chỉ nhận khi ESP32 **IDLE** (băng dừng) — bấm Stop trước.
- `ROLLBACK_REJECTED` ngoài ý muốn: `max_ver` trong NVS đã lớn hơn (do demo 12 hoặc nạp lại bản cũ qua USB) → nạp lại NVS (`esp32/nvs/README.md`) hoặc tăng `FW_VERSION_CODE`.
- Không bao giờ sửa firmware/app để bỏ xác minh TLS (quy tắc 3 CLAUDE.md); cách đúng luôn là sửa cert/CA.

### 4.8 Sự cố khác

| Hiện tượng | Xử lý |
|---|---|
| Kiện nào cũng REJECT_FAILSAFE | Xem `journalctl -u sorter` dòng `PIPELINE_ERROR` (camera lỗi, model thiếu) hoặc `SORT_FAILED` (SORT không được ACK — ESP32 đã tự failsafe vì Pi quá 3 s: giảm `max_qr_frames`, kiểm model chậm) |
| ESP32 tự loại hàng (DECISION_TIMEOUT) | Pi xử lý > `t_decision_timeout` 3000 ms: xem `latency_ms`; nguyên nhân thường là NO_QR phải quét 3 khung + CLAHE. Tăng tham số 10 bằng SET_PARAM (tối đa 10 s) chỉ khi cần |
| Dashboard không hiện ảnh lỗi | Ảnh > 40 KB bị bỏ (log "Bỏ bản tin"): giảm `thingsboard.image_max_kb` không giúp — ảnh thumbnail đã ≤ giới hạn; kiểm `NETTY_MAX_PAYLOAD_SIZE` trong compose |
| RPC trả 504 trên dashboard | Pi offline hoặc ESP32 không ACK trong `rpc.esp_timeout_s`; kiểm `esp_link` |
| RATE_LIMITED | > 5 lệnh / 10 s (trừ emergencyStop) — chờ 2 s mỗi lệnh |
| Dịch vụ không khởi động: "Lỗi cấu hình" | Chạy lệnh kiểm config ở Bước 2; thiếu file khóa/cert/model → thông báo nêu đúng khóa YAML |
| Mất SSH vào Pi sau hardening | `pi/scripts/README.md` mục 3 (màn hình HDMI, ufw, fail2ban, 99-hardening.conf) |
| Băng không chạy dù IDLE | `belt_speed` 30–100; L298N có nguồn; `SORTER_HW_TEST` trong menuconfig để kiểm đấu dây từng phần |

## 5. Sau demo: lấy số liệu cho slide

```bash
# Trên Pi (hoặc chép sorter.db về laptop)
cd /opt/sorter && python tools/report_metrics.py --db /var/lib/sorter/sorter.db --expected ~/expected.csv \
    --metrics-json ~/ai_eval/metrics.json --model-card /opt/sorter/models/model_card.json \
    --benchmark-json ~/benchmark.json --anomaly-eval ~/anomaly_eval.md --ota-reports ~/device_reports.csv \
    --note "Pi 4, model v1.0.0, demo 02/11" --out ~/metrics.md
```
`expected.csv` dạng `pkg_id,expected_decision` cho 20 kiện kịch bản 1–4; `benchmark.json` từ `tools/benchmark_model.py --config /etc/sorter/config.yaml --runs 100`;
`device_reports.csv` xuất từ tab Lịch sử của app OTA; `anomaly_eval.md` từ `python -m sorter.anomaly.evaluate` (docs/anomaly.md mục 8).
Chép kết quả vào `docs/metrics.md` (bản hiện tại là số đo sim trên laptop, model dummy).
