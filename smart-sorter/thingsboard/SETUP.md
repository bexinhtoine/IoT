# ThingsBoard — Hướng dẫn cài đặt từng bước (T-01 → T-08, S-01, S-02, S-04, S-05)

Mục tiêu: ThingsBoard CE chạy Docker trên laptop (`tb.local` hoặc IP tĩnh), Pi kết nối MQTT TLS 8883 bằng
chứng chỉ X.509, hai dashboard (Điều khiển cho Admin, Giám sát cho Viewer), alarm bảo mật, 2FA.

Viết theo giao diện **ThingsBoard CE 3.6 – 3.7**. Tên menu có thể khác một chút ở 3.3/3.4 (ghi chú `[3.4-]`)
hoặc 3.8+ (`[3.8+]`). Khi không tìm thấy mục nào, dùng ô tìm kiếm ở menu trái.

Thứ tự: **1 → 2 → 3 → 4 → 5 → 6 → 7 → 8**. Mục 2–4 có thể làm nhanh bằng `tools/tb_provision.py` (tùy chọn).

---

## 1. Khởi động ThingsBoard với MQTT TLS (T-01, S-01)

```bash
cd thingsboard/pki
./make_ca.sh
./make_server_cert.sh tb.local 192.168.1.10          # thay IP tĩnh của laptop; thêm IP khác nếu đổi mạng
./make_device_cert.sh sorter-pi-01
./make_ota_server_cert.sh 192.168.1.10
cd ..
mkdir -p data logs certs
cp pki/out/server.pem pki/out/server_key.pem certs/
chmod 644 certs/server.pem && chmod 600 certs/server_key.pem
cp .env.example .env
docker compose up -d
docker compose logs -f | grep -iE "started|ssl|8883"   # chờ "Started ThingsboardServerApplication" (~2-3 phút)
```

- Trên Linux, container chạy user uid 799; nếu log báo không đọc được khóa:
  `sudo chown 799:799 certs/server_key.pem` (hoặc `chmod 640` + `chgrp 799`).
- `data/` sau lần đầu cần `sudo chown -R 799:799 data logs` nếu TB báo lỗi quyền ghi.
- Tên `tb.local`: thêm vào `/etc/hosts` của Pi (`192.168.1.10 tb.local`) hoặc đặt mDNS. SAN cert đã có cả IP nên
  `thingsboard.host` trong config Pi có thể là IP.
- Web UI: http://localhost:8080 — tài khoản mặc định `tenant@thingsboard.org / tenant`, `sysadmin@thingsboard.org /
  sysadmin`. **Đổi cả hai mật khẩu ngay** (mục 5).
- Biến `MQTT_SSL_*` trong `docker-compose.yml` theo `thingsboard.yml` mục `transport.mqtt.ssl` của bản 3.7; đối chiếu
  file cùng tên ở tag bạn dùng nếu khác phiên bản.

## 2. Device profile "Sorter" (T-02, T-05)

**Menu:** Profiles → Device profiles `[3.4-: Device profiles]` → **+** → Add device profile.

a) **Device profile details**
- Name: `Sorter`; Default rule chain: Root Rule Chain; Queue: Main.
- **Transport configuration**: Transport type **MQTT**; Telemetry topic `v1/devices/me/telemetry`; Attributes topic
  `v1/devices/me/attributes`; Payload: JSON. (Không bật "Send PUBACK on validation exception".)
- Provisioning: Disabled.

b) **Alarm rules** (tab *Alarm rules* → + Add alarm rule). Mỗi rule: *Alarm type* → *Create alarm rules* (+ điều kiện
theo severity) → *Clear alarm rule*.

| Alarm type | Severity | Create condition (Key filter: Time series) | Clear condition |
|---|---|---|---|
| `Security alert` | CRITICAL | `security_alert` (Boolean) **equal** `true` **AND** `security_level` (String) **equal** `CRITICAL` | `security_alert` equal `false` |
| `Security alert` | MAJOR | `security_alert = true` AND `security_level = MAJOR` | (cùng clear rule) |
| `Security alert` | WARNING | `security_alert = true` AND `security_level = WARNING` | |
| `Security alert` | MINOR (dùng cho INFO) | `security_alert = true` AND `security_level = INFO` | |
| `Anomaly` | CRITICAL | `anomaly_flag` (Boolean) equal `true` | `anomaly_flag` equal `false` |
| `High CPU temperature` | WARNING | `cpu_temp` (Numeric) **greater than** `75` | `cpu_temp` less than `70` |

- Trong một alarm rule, bấm **+ Add create condition** cho từng severity; thứ tự ưu tiên CRITICAL → MAJOR → WARNING.
- *Alarm details* (tùy chọn): `Sự kiện ${security_event_type}: ${security_detail}` để text alarm có chi tiết.
- Pi gửi `security_alert: true` kèm `security_event_type/level/detail` mỗi sự kiện và định kỳ gửi
  `security_alert: false` khi không còn sự kiện (xem sorter-core P3/P9) → alarm tự clear.

c) **Thiết bị offline sau 30 s** (T-05): ThingsBoard tính "Inactive" theo server attribute `inactivityTimeout` (ms)
của từng device, mặc định lấy từ biến `DEFAULT_INACTIVITY_TIMEOUT` (đã đặt 30 s trong compose).
Để chắc chắn với từng device: Devices → sorter-pi-01 → tab **Attributes** → scope *Server attributes* → + →
key `inactivityTimeout`, type Integer, value `30000`. `[3.6+]` một số bản có mục *Device profile → Device profile
configuration → Inactivity timeout*; nếu thấy, đặt 30 s ở đó.

## 3. Devices (T-02, S-01)

**Menu:** Entities → Devices `[3.4-: Devices]` → **+** → Add new device.

a) **sorter-pi-01** (thiết bị thật)
- Name `sorter-pi-01`, Label `Máy phân loại`, Device profile **Sorter**.
- Tích **Add credentials** `[3.4-: Manage credentials sau khi tạo]` → Credentials type **X.509 Certificate** → ô
  *RSA public key* / *PEM certificate* `[tên ô khác theo bản]`: dán **toàn bộ** nội dung `pki/out/sorter-pi-01.pem`
  (`cat pki/out/sorter-pi-01.pem`, gồm cả dòng BEGIN/END CERTIFICATE). ThingsBoard lưu hash cert; khi Pi trình đúng
  cert này ở TLS handshake → nhận diện thiết bị, không cần username/token.
- Lưu ý: CN=`sorter-pi-01` chỉ để người đọc; ThingsBoard khớp **nội dung cert**, không khớp tên. Tạo lại cert
  (`--force`) thì phải dán lại.

b) **sorter-view** (thiết bị gương cho Viewer)
- Name `sorter-view`, Label `Bản sao cho Viewer`, Device profile **Sorter**, credentials để mặc định (Access token
  tự sinh) — **không dùng token này ở đâu cả**; thiết bị này không kết nối thật, chỉ nhận telemetry sao chép (mục 4).
- Đặt `inactivityTimeout = 30000` như 2c (telemetry sao chép tới mỗi 10 s nên vẫn Active).

c) Cấu hình Pi (`/etc/sorter/config.yaml`):
```yaml
device: {name: sorter-pi-01}
thingsboard:
  enabled: true
  host: tb.local          # hoặc IP, phải nằm trong SAN của server.pem
  port: 8883
  ca_cert: /etc/sorter/certs/ca.pem
  client_cert: /etc/sorter/certs/device.pem
  client_key: /etc/sorter/certs/device.key   # chmod 600, owner sorter
```

## 4. Rule chain sao chép telemetry sang sorter-view (T-05)

Mục đích: Viewer chỉ được gán `sorter-view`, nên dashboard Giám sát đọc dữ liệu từ `sorter-view`; dữ liệu phải được
sao chép từ `sorter-pi-01`.

a) **Tạo relation**: Devices → sorter-pi-01 → tab **Relations** → Direction *From* → **+** → Type: gõ
`MirroredBy` (custom type), Entity type Device, Entity `sorter-view` → Add.

b) **Sửa Root Rule Chain**: Rule chains → Root Rule Chain → mở editor.
1. Kéo node **Transformation → change originator**: Name `To sorter-view`; Originator source **Related**;
   Relations query: Direction **From**, Max relation level 1, Relation filters: type `MirroredBy`, entity type Device.
2. Kéo node **Action → save timeseries**: Name `Save mirrored ts`; TTL 0 (hoặc 7 ngày cho demo).
3. Nối: từ node **Message Type Switch**, đầu ra **Post telemetry** → `To sorter-view` (link label *Success*) →
   `Save mirrored ts`. Giữ nguyên link **Post telemetry → Save Timeseries** gốc (dữ liệu của sorter-pi-01 vẫn lưu).
4. (Tùy chọn) tương tự cho attributes: **Post attributes** → thêm một `change originator` thứ hai → **save attributes**
   (scope *Client attributes*) để fw_version/model_version hiện trên dashboard Giám sát.
5. **Apply changes** (nút ✓ góc phải).

c) Kiểm tra: chạy Pi (hoặc `sorter.main --sim` với thingsboard.enabled) → Devices → sorter-view → *Latest telemetry*
thấy `cnt_total`, `conveyor_state` cập nhật. Alarm chỉ tạo trên sorter-pi-01 (device profile áp cho cả hai nên
sorter-view cũng sinh alarm trùng — nếu không muốn, tạo profile `Sorter view` không alarm cho sorter-view).

Lưu ý: ảnh `defect_image` (≤ 40 KB) cũng được sao chép; dung lượng DB tăng ~2× — chấp nhận cho demo.

## 5. Người dùng, phân quyền, mật khẩu (T-08, S-02)

a) **Admin = Tenant Administrator**: tài khoản `tenant@thingsboard.org` có sẵn. Đổi mật khẩu: avatar góc phải →
*Profile* → *Security* `[3.4-: Change password]`. Có thể thêm Admin khác: Tenant → Tenant admins `[sysadmin]`.

b) **Customer "Viewer" + Customer User**
1. Entities → **Customers** → + → Title `Viewer`.
2. Customers → Viewer → **Manage users** `[icon người]` → + → Email `viewer@sorter.local`, Activation method
   *Display activation link* → mở link, đặt mật khẩu mạnh.
3. Gán **thiết bị**: Devices → sorter-view → **Assign to customer** → Viewer. **KHÔNG** gán sorter-pi-01.
4. Gán **dashboard**: Dashboards → "Sorter — Giám sát" → **Manage assigned customers** `[3.4-: Assign to customers]`
   → Viewer. Không gán dashboard Điều khiển.
5. Kết quả: Viewer đăng nhập chỉ thấy dashboard Giám sát; không thấy sorter-pi-01 → REST
   `/api/rpc/twoway/<id sorter-pi-01>` trả 403 (kiểm tra bằng `tools/rpc_cli.py --user viewer@sorter.local` → HTTP 403).

c) **Chính sách mật khẩu + khóa tài khoản** (S-02): đăng nhập **sysadmin** → *Security settings*
`[3.4-: System settings → Security settings]`:
- Password policy: Minimum length 12; Minimum uppercase 1, lowercase 1, digits 1, special 1; Password expiration 90
  ngày (tùy); Password reuse frequency 5.
- **Maximum failed login attempts = 5**; *Lockout notification email* để trống hoặc email admin. (Compose cũng đặt
  `MAX_FAILED_LOGIN_ATTEMPTS=5` phòng khi bản bạn dùng chỉ đọc từ env.)
- Sau khi khóa, mở lại: sysadmin/tenant → Users → user → *Enable account* `[tên khác: Activate]`.

## 6. 2FA TOTP (T-08, S-02)

Cần ThingsBoard **3.4+** (2FA xuất hiện từ 3.4).

a) **Bật nhà cung cấp** (sysadmin): Security settings → tab **Two-factor authentication** `[3.4: 2FA settings]` →
bật **Authenticator app (TOTP)**: Issuer name `Smart Sorter TB`; *Minimum verification code send period* 30 s;
*Maximum number of verification failures* 3; *Verification code check rate limit* 3/900 s; *Total allowed time for
verification* 3600 s.
- **Cưỡng chế**: tích **Force two-factor authentication** `[3.6+: "Enforce 2FA"; 3.4/3.5 không có — khi đó yêu cầu
  Admin tự bật theo bước b và ghi vào RUNBOOK]`. Có thể chỉ cưỡng chế với Tenant admin (tùy chọn *Enforce for ...*).
- Lưu (phần này ở sysadmin; tenant có thể ghi đè ở Tenant → Security settings → 2FA nếu bản cho phép).

b) **Admin bật 2FA cho mình**: đăng nhập tenant → avatar → Profile → tab **Security** → *Two-factor authentication*
→ **Authenticator app** → Quét QR bằng Google Authenticator/Aegis → nhập mã 6 số → *Continue*. Lưu mã dự phòng
(nếu bản có *Backup codes*). Đăng xuất, đăng nhập lại: TB hỏi mã TOTP.

c) Lưu ý khi dùng **REST API** (rpc_cli.py) với tài khoản bật 2FA: `/api/auth/login` trả token *pre-verification*
và cần `POST /api/auth/2fa/verification/check?providerType=TOTP&verificationCode=...`. Cho test tự động, dùng
Admin phụ không bật 2FA hoặc tắt cưỡng chế lúc test.

## 7. Dashboards (T-06, T-07)

Trước tiên dán 2 widget tùy chỉnh theo `widgets/README.md`. Không kèm file JSON import (định dạng khác nhau giữa các
bản 3.x); tạo bằng tay theo bảng rồi **Export** để lưu vào `dashboards/` (xem `dashboards/README.md`).

### 7.1 Aliases (Dashboard → Edit → Entity aliases)

| Alias | Filter type | Thiết bị | Dashboard |
|---|---|---|---|
| `Sorter` | Single entity → Device → sorter-pi-01 | thật | Điều khiển |
| `Sorter view` | Single entity → Device → sorter-view | gương | Giám sát |

Dashboard Giám sát dùng **đúng cùng bố cục** nhưng mọi widget trỏ alias `Sorter view` và **không có** widget điều khiển.
Mẹo: làm xong Điều khiển → Export → Import thành "Sorter — Giám sát" → sửa alias `Sorter` thành sorter-view → xóa
khối điều khiển.

### 7.2 Widget dashboard "Sorter — Điều khiển" (Admin)

Settings dashboard: Title `Sorter — Điều khiển`; *Time window* mặc định Realtime last 1 hour. Dữ liệu sau đây là
**Time series** (TS) hoặc **Attribute** (ATTR, scope Client).

| # | Widget (bundle → widget) | Loại | Data keys (alias Sorter) | Ghi chú |
|---|---|---|---|---|
| 1 | Cards → **Value card** `[3.4-: Simple card]` | Latest | TS `conveyor_state` | Label "Băng chuyền"; màu theo giá trị RUNNING/IDLE/ESTOP/FAULT (Settings → Value color: dùng *Data key → Advanced → cellStyle function* hoặc widget *Cards → Label value card* với *Value color function*) |
| 2 | Cards → **Value card** | Latest | TS `esp_link` | Label "Liên kết ESP32"; `true`→"OK" xanh, `false`→"MẤT" đỏ (Data key → *Post-processing function*: `return value ? 'OK' : 'MẤT';`) |
| 3 | Cards → **Entities table** (1 hàng) hoặc 3 **Value card** | Latest | ATTR `fw_version`, `sw_version`, `model_version`, `qr_format_version` | "Phiên bản" |
| 4–9 | Cards → **Value card** × 6 | Latest | TS `cnt_total`, `cnt_a`, `cnt_b`, `cnt_reject_defect`, `cnt_reject_security`, `cnt_reject_failsafe` | Label: Tổng / Cửa A / Cửa B / Loại-lỗi / Loại-bảo mật / Loại-failsafe |
| 10 | Charts → **Time series chart** `[3.4-: Timeseries - Flot]` | Timeseries | TS `cnt_total` với *Data aggregation* **COUNT** hoặc key `pkg_id` agg COUNT, interval 1 phút | "Sản lượng theo phút" — dùng *Aggregation: Count, Grouping interval: 1 min* trong Timewindow của widget |
| 11 | Charts → **Pie chart** `[Doughnut]` | Latest | TS `cnt_a`, `cnt_b`, `cnt_reject_defect`, `cnt_reject_security`, `cnt_reject_failsafe` | "Tỉ lệ quyết định" |
| 12 | Cards → **Entities table** hoặc **HTML value card** | Latest | TS `pkg_id`, `warehouse`, `qr_status`, `ai_label`, `ai_conf`, `decision`, `reason`, `latency_ms` | "Kiện mới nhất" |
| 13 | **Sorter → Defect image card** (tùy chỉnh) | Latest | TS `defect_image`, `ai_label`, `ai_conf`, `pkg_id`, `reason` | widgets/defect_image_card |
| 14 | Tables → **Timeseries table** | Timeseries | TS `pkg_id`, `warehouse`, `qr_status`, `ai_label`, `ai_conf`, `decision`, `reason`, `latency_ms` | "Log 50 kiện gần nhất": Timewindow Realtime last 24 h, *Limit* 50 (Advanced → Data limit) |
| 15 | Alarm widgets → **Alarms table** | Alarm | Alarm source alias Sorter; filter Alarm types `Security alert`, `Anomaly`, `High CPU temperature`; status Active + Cleared | "Alarm bảo mật"; bật cột *Details* để thấy security_detail |
| 16 | Charts → **Time series chart** | Timeseries | TS `anomaly_score` (line), `anomaly_flag` (bar/area, trục phụ) | "Anomaly score"; thêm *Threshold* 0.5 nếu bản có |
| 17 | Tables → **Timeseries table** | Timeseries | TS `rpc_log` | "Audit RPC" — mỗi hàng một JSON; Data key → *Post-processing function*: `try{var o=JSON.parse(value);return o.method+' '+(o.result.ok?'OK':o.result.error)+' by '+(o.requested_by||'-');}catch(e){return value;}` |
| 18 | **Sorter → Sorter control panel** (tùy chỉnh, Control widget) | RPC | Target device: alias Sorter | Start/Stop/DỪNG KHẨN CẤP/Reset E-stop/Reset bộ đếm/Reset mã đã dùng/Kiểm tra cập nhật FW |
| 19 | (tùy chọn) Cards → **Value card** × 4 | Latest | TS `cpu_temp`, `cpu_load`, `mem_used_pct`, `disk_used_pct` | "Sức khỏe Pi" |
| 20 | (tùy chọn) Cards → **Value card** | Latest | TS `ota_stage`, `ota_reason`, `ota_progress` | "OTA" |

Bố cục gợi ý (lưới 24 cột): hàng 1 = #1 #2 #3 (3×8); hàng 2 = 6 thẻ đếm (6×4); hàng 3 = #10 (12) + #11 (6) + #12 (6);
hàng 4 = #13 (8) + #14 (16); hàng 5 = #15 (12) + #16 (12); hàng 6 = #18 (12) + #17 (12).

### 7.3 Dashboard "Sorter — Giám sát" (Viewer)

- Cùng widget #1–#17, #19, #20, alias **Sorter view**; **không** có #18.
- Alarms table: alias Sorter view (alarm sinh trên sorter-view vì cùng device profile; nếu bạn tạo profile riêng
  không alarm cho sorter-view thì bảng này sẽ trống — chọn một trong hai cách và ghi vào RUNBOOK).
- Gán dashboard cho customer Viewer (mục 5b). Vào bằng tài khoản viewer để xác nhận: không có nút điều khiển;
  mở *Devices* chỉ thấy sorter-view.

## 8. Kiểm tra nghiệm thu (T-01, S-01, S-04)

```bash
# 1) TLS + mutual auth OK (Verify return code: 0)
openssl s_client -connect tb.local:8883 -CAfile pki/out/ca.pem \
  -cert pki/out/sorter-pi-01.pem -key pki/out/sorter-pi-01.key </dev/null 2>&1 | grep -E "Verify return|subject=|issuer="

# 2) Không cert: handshake TLS có thể xong (server chỉ *yêu cầu* cert), nhưng MQTT CONNECT bị từ chối
#    (mosquitto_pub: "Connection error: Not authorized" hoặc ngắt kết nối)
mosquitto_pub -h tb.local -p 8883 --cafile pki/out/ca.pem -t v1/devices/me/telemetry -m '{"x":1}' ; echo rc=$?

# 3) Cert do CA khác ký -> bị từ chối (tạo CA giả trong thư mục tạm bằng cùng script với PKI_OUT khác)
PKI_OUT=/tmp/fakepki pki/make_ca.sh && PKI_OUT=/tmp/fakepki pki/make_device_cert.sh sorter-pi-01
mosquitto_pub -h tb.local -p 8883 --cafile pki/out/ca.pem --cert /tmp/fakepki/sorter-pi-01.pem \
  --key /tmp/fakepki/sorter-pi-01.key -t v1/devices/me/telemetry -m '{"x":1}' ; echo rc=$?   # phải khác 0

# 4) Cổng 1883 đóng, 8883 mở
nmap -p 1883,8883 tb.local        # 1883 closed/filtered, 8883 open
# 5) RPC whitelist từ laptop (Admin) — A-05
cd tools && pip install -r requirements.txt
python rpc_cli.py --device sorter-pi-01 --method getStatus
python rpc_cli.py --device sorter-pi-01 --method reboot            # {"ok":false,"error":"METHOD_NOT_ALLOWED"} + alarm Security alert (MAJOR)
python rpc_cli.py --device sorter-pi-01 --method getStatus --repeat 6   # lệnh 6: RATE_LIMITED + alarm WARNING
python rpc_cli.py --user viewer@sorter.local --device sorter-pi-01 --method getStatus   # HTTP 403/404: Viewer không có quyền
```

Kết quả mong đợi ghi vào `docs/PROGRESS.md`/slide: ảnh chụp (1) Verify 0, (2)(3) từ chối, (4) nmap, (5) alarm trên
dashboard và dòng `rpc_log` tương ứng, Audit Logs của ThingsBoard (menu **Audit logs**, Action type *RPC call*).

## Sự cố thường gặp

| Hiện tượng | Nguyên nhân / cách xử lý |
|---|---|
| Pi log `certificate verify failed: hostname mismatch` | `thingsboard.host` không nằm trong SAN của server.pem → tạo lại cert server với đúng tên/IP |
| `unable to get local issuer certificate` | Pi dùng sai ca.pem hoặc server.pem không do CA này ký |
| Pi kết nối rồi bị ngắt ngay (rc=5 Not authorized) | Cert thiết bị chưa dán/khác cert trong TB → dán lại nội dung `.pem` hiện tại |
| TB không khởi động, log "PEM key" | server_key.pem quyền không đọc được (uid 799) hoặc file là PKCS#1 bị mã hóa; script đã sinh EC PEM không mật khẩu |
| Telemetry có nhưng dashboard Giám sát trống | Rule chain mục 4 chưa Apply, hoặc relation `MirroredBy` ngược chiều |
| Ảnh lỗi không hiện | Payload > `NETTY_MAX_PAYLOAD_SIZE` hoặc > giới hạn client (image_max_kb + 8 KB) — xem log Pi "Bỏ bản tin" |
| RPC trả 504 | Pi offline / handler chậm > 10 s; kiểm tra `esp_link`, log sorter-core |
