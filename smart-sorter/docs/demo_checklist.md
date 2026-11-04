# Checklist buổi demo — Smart Logistics Sorter (in ra giấy)

Ngày demo: ____ / ____ / 2026    Địa điểm: ______________    Người điều phối: ______________

Mỗi kịch bản: ghi tên người phụ trách, tick ☐ → ☑ khi đạt, ghi thời gian/ghi chú. Thứ tự chạy theo số; 6a/6b/12 cần băng dừng (IDLE).
Trước khi bắt đầu: hoàn thành phần A. Khi có sự cố: xem `docs/RUNBOOK.md` mục 4.

## A. Chuẩn bị trước giờ demo (SPEC 5.4)

| ☐ | Việc | Người phụ trách | Cách kiểm tra |
|---|---|---|---|
| ☐ | Hộp demo đánh số: 2 hộp tốt (A, B), 1 hộp rách, 1 hộp móp | __________ | 4 hộp trên bàn, nhãn số rõ |
| ☐ | QR in sẵn (cạnh ≥ 3 cm): hợp lệ A, hợp lệ B, QR giả, QR đã dùng (bản photocopy của QR A) | __________ | `pi/tools/qr_generator.py` đúng khóa của Pi |
| ☐ | ThingsBoard chạy, Pi online (chấm xanh), ESP32 `esp_link = OK`, `conveyor_state = IDLE/RUNNING` | __________ | dashboard "Sorter — Điều khiển" |
| ☐ | Gọi RPC `resetUsedCodes` + `resetCounters` | __________ | 6 thẻ đếm = 0; `rpc_log` có 2 dòng ok |
| ☐ | 3 file OTA sẵn trong app OTA: hợp lệ (version mới), sửa 1 byte, hạ phiên bản; server 8443 đang chạy | __________ | tab Máy chủ: Running; `curl --cacert ca.pem https://<ip>:8443/ota/manifest.json` |
| ☐ | Đèn chiếu sáng bật ổn định ≥ 5 phút; ảnh thử không tối/nhòe | __________ | `capture_dataset.py` chụp 1 ảnh xem |
| ☐ | 2 cửa sổ trình duyệt: Admin (đã qua 2FA) và Viewer | __________ | Admin thấy nút điều khiển, Viewer không |
| ☐ | Board ESP32 dự phòng đã nạp cùng firmware, để cạnh | __________ | `fw_version` giống board chính |
| ☐ | `demo_check.py --mode sim` PASS toàn bộ trên laptop (sáng hôm demo) | __________ | bảng 12 PASS / 1 MANUAL |
| ☐ | Điện thoại quay video sẵn (kịch bản 5: đo E-stop ≤ 0,5 s) | __________ | — |

## B. Kịch bản bắt buộc (SPEC 5.1)

| # | Kịch bản | Người phụ trách | Thao tác | Kết quả mong đợi (tick từng ý) | Đạt |
|---|---|---|---|---|---|
| 1 | Hàng tốt, QR kho A | __________ | Đặt hộp tốt + QR A lên băng | ☐ vào khay A ☐ bíp ngắn ☐ `cnt_a` +1 ☐ thẻ "Kiện mới nhất" hiện pkg_id, SORT_A ☐ latency ≤ 1 s | ☐ |
| 2 | Hàng tốt, QR kho B | __________ | Hộp tốt + QR B | ☐ vào khay B ☐ `cnt_b` +1 ☐ log SORT_B | ☐ |
| 3 | Hàng rách, QR hợp lệ | __________ | Hộp rách + QR hợp lệ | ☐ vào khay Loại ☐ LED đỏ 1 s ☐ ảnh lỗi + nhãn (tear/dent…) hiện trên thẻ ảnh lỗi ☐ `cnt_reject_defect` +1 | ☐ |
| 4 | QR giả (chữ ký sai) | __________ | Hộp tốt + QR giả | ☐ khay Loại ☐ đỏ nháy 2 s + 3 bíp ☐ alarm "Security alert" MAJOR, loại QR_BAD_SIGNATURE ☐ AI không chạy (ai_label trống) | ☐ |
| 5 | DỪNG KHẨN CẤP trên dashboard | __________ | Băng đang chạy → bấm nút đỏ (không hỏi lại) | ☐ băng dừng ≤ 0,5 s (video) ☐ `conveyor_state = ESTOP` ☐ `rpc_log` có emergencyStop + requestedBy ☐ Reset E-stop (có xác nhận) → IDLE → Start lại chạy | ☐ |
| 6a | Secure OTA bản hợp lệ | __________ | Băng IDLE; app OTA phát hành bản mới; dashboard → "Kiểm tra cập nhật firmware" | ☐ LED xanh nháy nhanh ☐ `ota_stage`: CHECKING → DOWNLOADING → VERIFIED → REBOOTING → SUCCESS ☐ `fw_version` mới ☐ app OTA nhận report SUCCESS | ☐ |
| 6b | OTA bản sửa 1 byte | __________ | App OTA tab Demo → "Sửa 1 byte firmware" → otaCheck | ☐ `ota_stage = REJECTED`, `ota_reason = HASH_MISMATCH` ☐ ESP32 không reboot, `fw_version` giữ nguyên ☐ Start vẫn chạy | ☐ |

## C. Kịch bản bổ sung (SPEC 5.2)

| # | Kịch bản | Người phụ trách | Thao tác | Kết quả mong đợi | Đạt |
|---|---|---|---|---|---|
| 7 | Quét lại QR đã dùng | __________ | Hộp tốt + QR A đã quét ở kịch bản 1 (hoặc bản photocopy) | ☐ khay Loại ☐ `qr_status = DUPLICATE` ☐ alarm QR_DUPLICATE | ☐ |
| 8 | E-stop vật lý (kể cả rút mạng) | __________ | Băng chạy → bấm giữ nút; (tùy chọn) rút cáp mạng Pi trước | ☐ băng dừng ngay ☐ EVT_ESTOP(PHYSICAL) → `conveyor_state = ESTOP` ☐ Reset khi còn bấm → BAD_STATE ☐ nhả nút + Reset → IDLE | ☐ |
| 9 | Viewer rồi Admin | __________ | Cửa sổ Viewer: xem dashboard Giám sát; cửa sổ Admin: đăng xuất → đăng nhập lại | ☐ Viewer không có khối điều khiển, chỉ thấy sorter-view ☐ Admin bị hỏi mã TOTP ☐ (tùy chọn) sai mật khẩu 5 lần → khóa | ☐ |
| 10 | Mô phỏng tấn công | __________ | Laptop: `attack_sim.py --i-own-this-system --scenario rpc` (+ qr/ssh nếu có) | ☐ alarm "Security alert" RPC_REJECTED ☐ ≤ 20 s: `anomaly_flag = true`, alarm "Anomaly" kèm `anomaly_top_features` ☐ biểu đồ anomaly_score nhảy | ☐ |
| 11 | Rút dây UART khi băng chạy | __________ | Rút TX/RX giữa Pi và ESP32 | ☐ băng dừng sau ≤ 2 s ☐ `esp_link = MẤT` ☐ cắm lại → EVT_FAULT(LINK_LOST), alarm ESP_LINK_LOST ☐ Reset E-stop → IDLE → Start | ☐ |
| 12 | OTA hạ phiên bản | __________ | App OTA tab Demo → "Hạ phiên bản" → otaCheck | ☐ `ota_stage = REJECTED`, `ota_reason = ROLLBACK_REJECTED` ☐ không tải firmware ☐ sau đó "Khôi phục bản hợp lệ" | ☐ |

## D. Sau demo

| ☐ | Việc | Người phụ trách |
|---|---|---|
| ☐ | Chụp bảng alarm, rpc_log, `security_audit.sh`, `espefuse.py summary` cho slide | __________ |
| ☐ | `python tools/report_metrics.py --db /var/lib/sorter/sorter.db --expected expected.csv ...` → `docs/metrics.md` | __________ |
| ☐ | App OTA: "Khôi phục bản hợp lệ"; tắt server 8443 | __________ |
| ☐ | Tắt theo thứ tự: ESP32 (STOP) → `sudo systemctl stop sorter` → `docker compose stop` | __________ |

Ghi chú / sự cố trong buổi demo:

____________________________________________________________________________

____________________________________________________________________________
