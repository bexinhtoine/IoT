# MODULE P9 — Phát hiện truy cập bất thường (Isolation Forest)

Tài liệu mô tả cách hoạt động, cách thu dữ liệu, huấn luyện, mô phỏng tấn công và đánh giá
cho module phát hiện bất thường (mã chức năng A-01 → A-06, đặc tả `SPEC.md` mục 3.7).

## 1. Tổng quan

Mỗi cửa sổ `anomaly.window_s` giây (mặc định 10 s), hệ thống gom một **vector đặc trưng**
10 chiều từ hoạt động gần nhất, lưu vào SQLite, rồi:

- **Luật cứng** (A-04) báo ngay những tấn công rõ ràng, không cần model.
- **Isolation Forest** (A-03) chấm điểm bất thường cho các tổ hợp mà luật không lường trước;
  vượt ngưỡng thì bật `anomaly_flag` và sinh sự kiện `ANOMALY` (CRITICAL).

Kết quả gửi lên ThingsBoard qua 3 khóa telemetry: `anomaly_score`, `anomaly_flag`,
`anomaly_top_features` (đặc tả 4.3).

## 2. Đặc trưng (A-01)

Theo đúng thứ tự `interfaces.FEATURE_NAMES`:

| # | Đặc trưng | Nguồn |
|---|---|---|
| 1 | `rpc_total` | tổng RPC nhận trong cửa sổ (rpc_handler) |
| 2 | `rpc_rejected` | RPC ngoài whitelist / vượt rate limit |
| 3 | `qr_bad_signature` | QR chữ ký HMAC sai |
| 4 | `qr_duplicate` | QR mã kiện đã dùng (replay) |
| 5 | `qr_bad_format` | QR sai định dạng |
| 6 | `uart_crc_errors` | khung UART sai CRC (uart_link) |
| 7 | `mqtt_reconnects` | số lần tb_client kết nối lại |
| 8 | `ssh_failed` | SSH đăng nhập thất bại, đọc từ `journalctl -u ssh` |
| 9 | `estop_count` | số lần E-stop |
| 10 | `packages` | số kiện xử lý trong cửa sổ |

Các module khác tăng bộ đếm bằng `metrics.inc(F_xxx)`; `FeatureCollector.collect()` lấy
`snapshot_and_reset()` rồi ghi đè `ssh_failed` bằng số dòng chứa "Failed password",
"Invalid user" hoặc "authentication failure" đọc từ journalctl (subprocess có timeout,
không `shell=True`). Trên Windows/máy không có journalctl → `ssh_failed = 0` và cảnh báo một lần.

## 3. Thu dữ liệu bình thường (≥ 30 phút)

Model học từ hành vi bình thường, nên trước khi train cần để hệ thống **chạy bình thường
≥ 30 phút (≥ 180 cửa sổ 10 s)** mà không có tấn công. `AnomalyDetector` vẫn ghi mọi cửa sổ
vào bảng `anomaly_windows` ngay cả khi chưa có model (lúc này chỉ log "đang thu dữ liệu" +
chạy luật cứng). Chỉ cần cho hệ thống hoạt động như lúc demo (quét QR hợp lệ, phân loại,
telemetry) để DB tích đủ cửa sổ.

## 4. Huấn luyện (A-02)

```bash
cd pi && PYTHONUTF8=1 python -m sorter.anomaly.train \
    --db /var/lib/sorter/sorter.db \
    --from "2026-10-28 09:00" --to "2026-10-28 09:40" \
    --out /opt/sorter/models/anomaly_if.joblib \
    --min-windows 180
```

- Pipeline: `StandardScaler` + `IsolationForest(n_estimators=200, contamination=0.02, random_state=42)`.
- Điểm bất thường = `-score_samples` (càng lớn càng bất thường).
- Ngưỡng = **phân vị 99** của điểm trên dữ liệu train.
- Lưu `joblib` gồm `{pipeline, threshold, features, train_mean, train_std}` và `metadata.json`
  cạnh file (features, threshold, n_samples, trained_at, from/to).
- Cảnh báo nếu số cửa sổ < `--min-windows` (vẫn train nhưng độ tin cậy thấp).

Hàm `train_from_matrix(X) -> (pipeline, threshold)` dùng được trực tiếp (ví dụ trong test,
không cần DB).

## 5. Chấm điểm thời gian thực (A-03)

`AnomalyDetector.start()` chạy một thread daemon mỗi `window_s` giây:

1. `collect()` đặc trưng → lưu cửa sổ vào DB.
2. Nếu có model: `score = -pipeline.score_samples([x])[0]`; gửi telemetry
   `anomaly_score` (float), `anomaly_flag` (bool), `anomaly_top_features`
   (3 đặc trưng có |z-score| lớn nhất, dạng `feat=z;feat=z;feat=z`).
3. Vượt ngưỡng → `anomaly_flag = True` + `SecurityEvent(ANOMALY, CRITICAL)` kèm điểm và
   top features; có **cooldown `cooldown_s`** (mặc định 60 s) để không báo dồn — nhưng
   `anomaly_flag = True` vẫn được gửi mỗi cửa sổ.
4. Luật cứng chạy song song mỗi cửa sổ.

`reload_model()` nạp lại model sau khi train xong mà không cần khởi động lại dịch vụ.

## 6. Luật cứng (A-04)

Chạy song song, báo ngay cả khi chưa có model, mỗi luật có cooldown 60 s:

- `RULE_QR_FORGERY`: ≥ 5 QR chữ ký sai trong 60 s.
- `RULE_SSH_BRUTEFORCE`: ≥ 5 SSH thất bại trong 60 s.
- `RULE_RPC_UNKNOWN`: có ≥ 1 RPC bị từ chối trong cửa sổ hiện tại.

## 7. Mô phỏng tấn công (A-05)

> Chỉ chạy trên hệ thống của chính nhóm. Bắt buộc cờ `--i-own-this-system`, thiếu thì thoát mã 2.

```bash
cd pi && PYTHONUTF8=1 python tools/attack_sim.py --i-own-this-system --scenario all \
    --tb-url https://localhost:8443 --user admin@local --device <DEVICE_ID> \
    --host 192.168.1.50 --port /dev/serial0 --log attack_log.jsonl
```

Các kịch bản (`--scenario rpc|ssh|qr|uart|all`):

- **rpc**: đăng nhập ThingsBoard REST (mật khẩu hỏi qua getpass), gọi phương thức lạ
  `dumpSecrets` 5 lần + spam `getStatus` 15 lần trong 10 s. Luôn verify TLS (dùng `--ca`
  cho CA nội bộ; **không bao giờ** tắt xác minh).
- **ssh**: thử SSH sai mật khẩu 8 lần tới `--host` (cần `pip install paramiko`).
- **qr**: sinh 8 QR giả bằng `tools/qr_generator.py --forge` (nếu có); nếu không thì bơm
  bộ đếm `qr_bad_signature` khi chạy chung tiến trình với detector.
- **uart**: mở `--port` và ghi 20 khung UART sai CRC (dựng bằng `uart_codec.encode_frame`
  rồi lật byte CRC); không có `--port` thì chỉ tạo khung ở chế độ sim.

Mỗi kịch bản ghi một dòng `{scenario, start_ms, end_ms}` vào `attack_log.jsonl` để đánh giá.

## 8. Đánh giá (A-06)

```bash
cd pi && PYTHONUTF8=1 python -m sorter.anomaly.evaluate \
    --db /var/lib/sorter/sorter.db --attack-log attack_log.jsonl \
    --normal-from "2026-10-28 09:00" --normal-to "2026-10-28 09:40" \
    --out docs/anomaly_eval.md --png docs/anomaly_scores.png
```

- Mỗi kịch bản coi là **phát hiện được** nếu có cửa sổ bị gắn cờ trong `[start, end + 20 s]`.
- Đếm **báo nhầm** (false positive) trong khoảng bình thường.
- Xuất bảng Markdown (`--out`) và biểu đồ điểm theo thời gian tô vùng tấn công (`--png`,
  matplotlib import lười — thiếu thì bỏ qua biểu đồ, bảng vẫn có).

Tiêu chí xong (SPEC): attack_sim bật alarm Anomaly trong ≤ 20 s; 30 phút bình thường
báo nhầm ≤ 1 lần.

## 9. Giới hạn

- `ssh_failed` cần journalctl (chỉ Linux/systemd); trên máy dev Windows luôn là 0.
- Model chỉ tốt khi dữ liệu train thật sự "bình thường"; nếu lúc thu có tấn công lẫn vào thì
  ngưỡng bị lệch — cần thu lại.
- Isolation Forest không giải thích nhân quả; `anomaly_top_features` chỉ gợi ý đặc trưng lệch
  nhiều nhất theo z-score, không khẳng định nguyên nhân.
- CRC UART chống lỗi đường truyền, không chống giả mạo (UART là dây nội bộ, xem S-06).
- Cửa sổ 10 s nghĩa là độ trễ phát hiện tối đa ~10 s + thời gian xử lý.
