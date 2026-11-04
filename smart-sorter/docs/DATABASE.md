# PostgreSQL / Supabase — IOT_Project

Ứng dụng thật dùng `storage.backend: postgresql` trong `pi/config.example.yaml`.
Database là `postgres` theo connection string Supabase; `IOT_Project` là tên
project, các bảng ứng dụng nằm trong schema **`iot_project`**.

## Cấu hình

`pi/.env` chứa connection string đã cấu hình cho project, được Git bỏ qua.
Không đưa mật khẩu vào YAML hoặc mã nguồn. `pi/.env.example` là mẫu không có
mật khẩu. Biến môi trường `SORTER_DATABASE_URL` được ưu tiên hơn file `.env`.
Driver dùng TLS, thời gian kết nối tối đa 10 giây và statement timeout 15 giây.
Publishable key không được dùng để ghi SQL: backend Python dùng PostgreSQL.
Schema riêng không được mở qua REST API; các bảng bật RLS, không có policy công khai.

```yaml
storage:
  backend: postgresql
  env_file: .env
  db_path: /var/lib/sorter/sorter.db
```

`db_path` xác định nơi đặt journal cục bộ (`sorter.db.outbox.db`) khi dùng
PostgreSQL. Nó không phải database chính. Giữ journal này khi khởi động lại.
`config.sim.yaml` vẫn dùng SQLite để dữ liệu demo mới không lẫn vào dữ liệu thật.

## Dữ liệu lưu

- `packages`: mã kiện, kho, QR, AI, các xác suất lỗi, quyết định, thời gian,
  đường dẫn ảnh lỗi, UUID bản ghi và kết quả ACK của lệnh SORT.
- `used_codes`: mã đã dùng, chống QR trùng bằng unique constraint nguyên tử.
- `counters`: tổng và bộ đếm theo kết quả.
- `rpc_audit`, `security_events`, `system_events`: nhật ký điều khiển và bảo mật.
- `anomaly_windows`: đặc trưng, điểm và cảnh báo phát hiện bất thường.
- `schema_version`: phiên bản cấu trúc bảng.

Kết quả kiện hàng và hai bộ đếm liên quan commit cùng giao dịch. Mỗi kiện có
`record_uid` duy nhất: ghi lại sau khi mất ACK COMMIT không làm tăng số đếm lần hai.
Trước khi ghi cloud, ứng dụng ghi kiện vào journal SQLite cục bộ với synchronous=FULL.
Nếu ghi cloud lỗi, journal còn giữ kiện, ứng dụng gửi STOP và ghi lỗi vào log.
Khởi động lại khi mạng phục hồi sẽ tự đẩy các kiện chờ lên trước khi chạy băng.
Đây không phải chế độ vận hành offline cho toàn bộ hệ thống: xác thực QR và
nhật ký vẫn cần PostgreSQL. Khi PostgreSQL không truy cập được từ lúc khởi động,
ứng dụng không khởi động băng chuyền.

`sort_sent=1` nghĩa là ESP32 đã ACK lệnh, không phải xác nhận hàng đã tới khay.
Ảnh lỗi vẫn lưu trên Pi trong `storage.defect_dir`; database lưu đường dẫn,
chưa tải ảnh lên Supabase Storage.

## Kiểm tra và xem dữ liệu

Từ thư mục `pi`, cài `python -m pip install -r requirements.txt`, rồi:

```bash
python tools/database_check.py --config /etc/sorter/config.yaml
python tools/report_metrics.py --config /etc/sorter/config.yaml
```

Trên Windows, khi kiểm tra cấu hình mẫu có đường dẫn Linux, chỉ định journal
trong workspace:

```powershell
python tools/database_check.py --config config.example.yaml --outbox run/sorter.db.outbox.db
```

`database_check.py` tạo một sự kiện `DATABASE_VERIFIED`, đóng kết nối và mở lại
để kiểm tra dữ liệu đã commit. Trong Supabase SQL Editor:

```sql
select * from iot_project.packages order by id desc limit 50;
select * from iot_project.counters order by name;
select * from iot_project.system_events order by id desc limit 20;
```

Chọn schema `iot_project` trong Table Editor để xem các bảng.

## Cài lên Pi và chuyển dữ liệu cũ

`scripts/install_pi.sh` cài driver và chép `.env` vào `/etc/sorter/.env` nếu chưa
có file này, quyền root:sorter 640. Script giữ nguyên config đã tồn tại: nếu Pi
đã cài trước đây, cập nhật `storage.backend` và `storage.env_file` trong
`/etc/sorter/config.yaml`, chép `.env` sang Pi rồi restart dịch vụ.

Để chuyển SQLite cũ, dừng sorter và chạy với database đích chưa có dữ liệu:

```bash
python tools/migrate_sqlite_to_postgres.py --config config.example.yaml --sqlite run/sorter.db
```

Tool chụp snapshot chỉ đọc, sao chép các bảng trong một giao dịch, giữ ID,
timestamp và bộ đếm, thêm UUID cho kiện cũ, đồng bộ sequence. Tool từ chối nếu
đích đã có dữ liệu; không ghi đè hay xóa SQLite gốc. Dữ liệu lịch sử trong
`pi/run/sorter.db` của workspace là dữ liệu mô phỏng; sự kiện `SQLITE_IMPORTED`
ghi lại lần chuyển dữ liệu này.

Kiểm thử PostgreSQL: đặt `SORTER_TEST_DATABASE_URL` rồi chạy
`python -m pytest tests/test_postgres.py`. Dữ liệu thử được rollback hoặc dọn
theo mã duy nhất, không xóa dữ liệu nghiệp vụ.
