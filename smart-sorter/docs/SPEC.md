
# Smart Logistics Sorter — Đặc tả & Prompt code

Oct 28, 2026 · @Chính Lê

## 1. Tổng quan đề tài và luật phân loại

Hệ thống tự phân loại kiện hàng trên băng chuyền vào 3 khay (A, B, Loại): mã QR có chữ ký quyết định hàng đi đâu, AI camera quyết định hàng có được đi không, mọi dữ liệu lên ThingsBoard qua MQTT/TLS. Tài liệu này là đặc tả chung cho cả nhóm và là nguồn để dán vào AI khi code (phần 6–7).

Tên đề tài: Hệ thống phân loại kiện hàng thông minh, kết hợp mã QR và AI kiểm tra bao bì, có bảo mật (Đề tài A — Smart Logistics Sorter).

### 1.1 Vai trò của QR và AI

- Mã QR trả lời "hàng đi đâu": kho A hay kho B, mã kiện, thời gian tạo mã, chữ ký HMAC-SHA256 để chống QR giả.
- AI (camera) trả lời "hàng có được đi không": bao bì nguyên vẹn (OK) hay lỗi (rách, móp, vỡ, ướt-bẩn).
- Hai phần không thay thế được nhau: tình trạng vật lý của hộp không thể mã hóa sẵn trong QR, nên phải nhìn bằng camera. Đây là câu trả lời khi bị hỏi "AI để làm gì".

### 1.2 Luật phân loại

| # | QR | AI bao bì | Mã quyết định | Khay | Tín hiệu và dashboard |
|---|---|---|---|---|---|
| 1 | Hợp lệ, kho A | OK | SORT_A | A | LED xanh, còi bíp ngắn, log |
| 2 | Hợp lệ, kho B | OK | SORT_B | B | LED xanh, còi bíp ngắn, log |
| 3 | Hợp lệ | Lỗi, hoặc độ tin cậy dưới ngưỡng | REJECT_DEFECT | Loại | LED đỏ, ảnh lỗi + nhãn lỗi trên dashboard |
| 4 | Không đọc được, sai định dạng, chữ ký sai, mã trùng, hết hạn | — (không chạy AI) | REJECT_SECURITY | Loại | LED đỏ nhấp nháy, còi dài, cảnh báo bảo mật (alarm) |
| 5 | Hết thời gian chờ lệnh, mất kết nối, lỗi hệ thống | — | REJECT_FAILSAFE | Loại | LED đỏ, log lỗi |

Thứ tự xét: kiểm tra QR trước, QR không hợp lệ thì loại ngay và bỏ qua AI. Nguyên tắc fail-safe: mọi trường hợp không chắc chắn đều vào khay Loại, không bao giờ vào A hoặc B.

### 1.3 Yêu cầu bắt buộc của đề tài

- Mô hình phần cứng chạy thật, lắp hoàn chỉnh: băng chuyền, cảm biến, camera, cơ cấu gạt 3 hướng (A, B, Loại). Không chấp nhận demo từng module rời.
- Đọc QR và tự nhận diện lỗi bao bì bằng AI.
- Gửi và nhận dữ liệu qua ThingsBoard bằng MQTT. Dashboard có số lượng, log, ảnh hàng lỗi và nút dừng khẩn cấp.
- Có cơ chế xác thực và bảo mật (dùng cho slide bảo mật).
- Cộng điểm: app nạp firmware bảo mật (Secure OTA).
- Mở rộng nhóm đã chốt thêm: AI phát hiện truy cập bất thường từ log MQTT/RPC/đăng nhập (Isolation Forest).

### 1.4 Giả định và phạm vi

- Mỗi lúc chỉ có 1 kiện trong vùng phân loại; kiện sau đặt cách kiện trước ít nhất bằng quãng từ camera đến cuối băng.
- Hộp carton nhỏ, QR dán ở mặt trên, hướng về camera.
- Đèn chiếu sáng cố định quanh vị trí chụp, không phụ thuộc ánh sáng phòng.
- Pi, ESP32 (khi OTA) và laptop chạy ThingsBoard + app OTA dùng chung một mạng Wi-Fi/LAN.
- ThingsBoard Community Edition tự cài bằng Docker trên laptop (cần để bật MQTT TLS và chứng chỉ X.509).
- Câu hỏi mở cần nhóm chốt: dùng TCRT5000 hay HC-SR04; servo SG90 hay MG996R; kích thước hộp và chiều dài băng. Code hỗ trợ cả hai loại cảm biến qua file cấu hình.

## 2. Kiến trúc hệ thống và phần cứng

Raspberry Pi 4 là bộ não (camera, QR, AI, bảo mật, ThingsBoard); ESP32 lo phần thời gian thực (băng chuyền, cảm biến, servo, đèn, còi, dừng khẩn cấp) và nhận lệnh từ Pi qua UART. ESP32 tự dừng băng khi thấy hàng và tự loại hàng nếu Pi không trả lời, nên hệ thống vẫn an toàn khi Pi treo.

kiến trúc hệ thống · 4 tầng thiết bị

Pi chỉ nói chuyện với ThingsBoard qua MQTT TLS 8883 bằng chứng chỉ X.509; ESP32 chỉ bật Wi-Fi khi OTA (đường nét đứt); nút E-stop cắt nguồn động cơ bằng phần cứng.

### 2.1 Các khối chính

| Khối | Thiết bị / phần mềm | Nhiệm vụ |
|---|---|---|
| Bộ xử lý chính | Raspberry Pi 4 + Pi Camera, Python 3.11 | Chụp ảnh, đọc QR, xác thực HMAC, chạy AI, ra quyết định, gửi lệnh cho ESP32, MQTT TLS lên ThingsBoard, lưu log SQLite |
| Bộ chấp hành | ESP32 (ESP-IDF v5.x, C) | Điều khiển động cơ qua L298N, 2 servo gạt, đọc cảm biến, LED, còi, nút E-stop vật lý, watchdog, Secure OTA |
| Nền tảng IoT | ThingsBoard CE (Docker trên laptop) | Nhận telemetry, gửi RPC, dashboard, alarm, phân quyền, 2FA |
| App Secure OTA | Python + PySide6 trên máy dev | Đăng nhập, ký firmware ECDSA P-256, tạo manifest, host HTTPS, lưu lịch sử nạp |
| Huấn luyện AI | Laptop hoặc Google Colab | Train YOLOv8n-cls hoặc MobileNetV2, xuất ONNX/TFLite, đánh giá |
| Phát hiện bất thường | Python + scikit-learn trên Pi | Isolation Forest trên log RPC, QR, UART, SSH, MQTT |

### 2.2 Danh sách linh kiện

| Linh kiện | Số lượng | Ghi chú |
|---|---|---|
| Raspberry Pi 4 (≥ 2 GB) + thẻ nhớ 32 GB | 1 | Raspberry Pi OS 64-bit Bookworm |
| Pi Camera (Module v2 hoặc Module 3) | 1 | Gắn cố định nhìn thẳng xuống, cách mặt hộp khoảng 20–30 cm |
| ESP32 DevKit (chip revision ≥ v3.0) | 2 | 1 board chính, 1 board dự phòng để thử Secure Boot/Flash Encryption |
| Động cơ DC giảm tốc + băng chuyền | 1 | Băng tự chế hoặc mua bộ có sẵn |
| Driver L298N | 1 | Bỏ jumper ENA để điều tốc bằng PWM |
| Servo SG90 hoặc MG996R | 2 | Servo A và servo B, mỗi servo một tay gạt chéo |
| Cảm biến TCRT5000 hoặc HC-SR04 | 1 | Đặt ngay dưới vùng camera |
| LED xanh, LED đỏ, còi buzzer | 1 mỗi loại | Còi qua transistor NPN nếu dòng > 20 mA |
| Nút dừng khẩn cấp loại nấm, có 2 tiếp điểm NC | 1 | 1 tiếp điểm cắt nguồn động cơ, 1 tiếp điểm báo về ESP32 |
| Đèn LED chiếu sáng cố định (dải LED hoặc vòng LED) | 1 | Rất quan trọng cho độ ổn định của AI |
| Nguồn 12 V ≥ 2 A, nguồn 5 V 3 A USB-C cho Pi, module hạ áp 12 V → 5 V ≥ 3 A cho servo | 1 mỗi loại | GND nối chung |
| 3 khay hứng (A, B, Loại), hộp carton nhỏ | — | Hộp dùng để chụp dataset và demo |

### 2.3 Sơ đồ chân ESP32

| Chức năng | Chân ESP32 | Nối tới | Ghi chú |
|---|---|---|---|
| UART2 RX | GPIO16 | Pi TX — GPIO14 (chân 8) | Cả hai 3,3 V, nối thẳng |
| UART2 TX | GPIO17 | Pi RX — GPIO15 (chân 10) | 115200 baud, 8N1 |
| GND chung | GND | Pi GND (chân 6), L298N, nguồn servo | Bắt buộc |
| PWM động cơ | GPIO25 | L298N ENA | LEDC 1 kHz, độ phân giải 10 bit |
| Chiều động cơ | GPIO26, GPIO27 | L298N IN1, IN2 | Chỉ chạy một chiều |
| Servo A | GPIO18 | Tín hiệu servo A | LEDC 50 Hz, nguồn 5 V riêng |
| Servo B | GPIO19 | Tín hiệu servo B | LEDC 50 Hz, nguồn 5 V riêng |
| Cảm biến TCRT5000 | GPIO34 | Chân DO | Chân chỉ đọc |
| HC-SR04 (nếu dùng) | GPIO13 / GPIO35 | TRIG / ECHO | ECHO qua cầu chia áp 1 kΩ – 2 kΩ |
| LED xanh / LED đỏ | GPIO32 / GPIO33 | Qua điện trở 220 Ω |  |
| Còi | GPIO23 | Qua transistor NPN |  |
| Nút E-stop | GPIO39 | Tiếp điểm NC xuống GND, điện trở kéo lên 10 kΩ | Bình thường LOW; bấm hoặc đứt dây thành HIGH thì dừng (fail-safe) |

Phía Pi: bật UART phần cứng, tắt serial console, thêm dtoverlay=disable-bt để dùng UART PL011 ổn định trên /dev/serial0.

### 2.4 Bố trí cơ khí dọc băng chuyền

- Vùng đặt hàng ở đầu băng.
- Trạm chụp: cảm biến phát hiện hàng nằm ngay dưới camera; đèn LED bao quanh, che chắn ánh sáng ngoài.
- Cổng gạt A: servo A, tay gạt ở vị trí nghỉ song song mép băng; khi gạt thì xoay chéo khoảng 45° chắn ngang băng, hàng trượt theo tay gạt vào khay A bên trái.
- Cổng gạt B: tương tự, đẩy sang khay B bên phải.
- Cuối băng: khay Loại. Hàng không bị gạt sẽ rơi vào đây.
Cách này để lỗi nghiêng về an toàn: servo hỏng hoặc không có lệnh thì hàng tự rơi vào khay Loại.

### 2.5 Luồng xử lý một kiện (kiểu dừng rồi chụp)

- ESP32 đang chạy băng; cảm biến báo có hàng thì ESP32 dừng băng ngay (mục tiêu < 20 ms) và gửi EVT_PKG_DETECTED kèm số thứ tự kiện.
- Pi chờ băng ổn định 150 ms rồi chụp ảnh (tối đa 3 khung nếu chưa đọc được QR).
- Pi giải mã QR và xác thực: định dạng, kho, chữ ký HMAC, thời gian, chống trùng. Sai thì quyết định ngay REJECT_SECURITY, bỏ qua AI.
- QR hợp lệ thì Pi cắt vùng ROI và chạy AI phân loại OK/Lỗi.
- Pi ra quyết định theo bảng 1.2, gửi lệnh SORT qua UART, chờ ACK tối đa 200 ms, gửi lại tối đa 3 lần.
- ESP32 đặt servo, chạy băng trong t_clear ms, trả servo về, bật LED/còi theo lý do, gửi EVT_SORT_DONE. Quá 3 s không nhận SORT thì ESP32 tự xử lý như REJECT_FAILSAFE.
- Pi ghi SQLite, gửi telemetry, ảnh lỗi (nếu có) và cảnh báo lên ThingsBoard.
Ngân sách thời gian từ lúc dừng băng đến lúc gửi SORT: ≤ 1 s (khớp mức dừng 0,5–1 s). Thời gian thật đo được đưa vào slide.

## 3. Đặc tả chức năng từng module

Hệ thống có 7 module với 75 chức năng đánh mã (E, P, AI, T, S, O, A); mỗi prompt ở phần 7 trỏ tới đúng các mã này để AI code không bỏ sót.

### 3.1 ESP32 — firmware chấp hành (E)

- E-01 Máy trạng thái: BOOT → IDLE → RUNNING → DETECTED → SORTING → RUNNING; thêm ESTOP, FAULT, OTA. Lệnh không hợp lệ ở trạng thái hiện tại bị NACK mã BAD_STATE.
- E-02 Điều khiển băng chuyền: START(speed %), STOP; tăng tốc mềm 200 ms để hàng không trượt; dừng tức thì khi phát hiện hàng hoặc E-stop.
- E-03 Phát hiện kiện hàng: TCRT5000 đọc bằng ngắt + chống dội 20 ms; HC-SR04 đo mỗi 30 ms, xác nhận khi 2 lần đo liên tiếp dưới ngưỡng. Chỉ nhận khi đang RUNNING; sau mỗi lần gạt có thời gian khóa t_rearm.
- E-04 Gạt hàng: nhận SORT(target, reason, pkg_seq); A thì xoay servo A sang góc gạt, B thì servo B, REJECT thì không gạt; chạy băng t_clear_A/B/R ms; trả servo về góc nghỉ; gửi EVT_SORT_DONE.
- E-05 Tự loại khi quá hạn: ở DETECTED quá t_decision_timeout (mặc định 3000 ms) không có SORT thì tự xử lý REJECT lý do FAILSAFE và gửi EVT_FAULT(DECISION_TIMEOUT).
- E-06 Đèn và còi: RUNNING sáng xanh; OK bíp ngắn 100 ms; DEFECT đỏ sáng 1 s; SECURITY đỏ nháy 2 s + 3 tiếng bíp; ESTOP đỏ nháy liên tục + còi 1 s; FAULT xanh–đỏ nháy luân phiên; OTA xanh nháy nhanh.
- E-07 Dừng khẩn cấp vật lý: ngắt GPIO39; trong ISR tắt PWM động cơ ngay, vào ESTOP, gửi EVT_ESTOP(PHYSICAL). Chỉ thoát khi nút đã nhả và nhận ESTOP_RESET; về IDLE, không tự chạy lại.
- E-08 Dừng khẩn cấp từ xa: lệnh ESTOP từ Pi (nút trên dashboard), xử lý như E-07, nguồn REMOTE.
- E-09 Watchdog liên kết: Pi gửi PING mỗi 500 ms. Đang RUNNING/DETECTED mà 2000 ms không có khung hợp lệ thì dừng băng, vào FAULT(LINK_LOST). Có thêm task watchdog của ESP-IDF cho chính ESP32.
- E-10 Giao thức UART: đóng/mở khung, CRC16, ACK/NACK, chống lặp theo SEQ, whitelist loại lệnh, kiểm tra miền tham số, đếm lỗi CRC (phần 4.2).
- E-11 Tham số hiệu chỉnh: SET_PARAM cho danh sách tham số cho phép (tốc độ, góc servo nghỉ/gạt, t_clear, t_rearm, ngưỡng HC-SR04); kiểm tra miền giá trị; lưu NVS.
- E-12 Báo trạng thái: gửi STATUS mỗi 1 s: trạng thái, phiên bản firmware, uptime, số kiện, số lỗi CRC, lý do reset gần nhất.
- E-13 Secure OTA phía thiết bị: xem O-08 đến O-15.
- E-14 Bảo mật thiết bị: Secure Boot v2, Flash Encryption, tắt JTAG (S-08).

### 3.2 Raspberry Pi — phần mềm điều phối sorter-core (P)

- P-01 Cấu hình: đọc /etc/sorter/config.yaml; khóa bí mật đọc từ file riêng quyền 600; kiểm tra cấu hình khi khởi động, sai thì dừng và báo rõ; không hardcode bí mật.
- P-02 Camera: picamera2, mở một lần và giữ suốt phiên; khóa phơi sáng và cân bằng trắng sau khi khởi động để ảnh đồng đều giữa các lần chụp.
- P-03 Đọc QR: pyzbar, dự phòng cv2.QRCodeDetector; thử tối đa 3 khung, tăng tương phản nếu thất bại; nhiều hơn 1 mã trong ảnh thì coi là không hợp lệ.
- P-04 Xác thực QR: theo S-03.
- P-05 Phân loại bao bì: cắt ROI theo cấu hình, resize 224×224, chạy ONNX Runtime (hoặc TFLite), trả nhãn, độ tin cậy, thời gian suy luận; độ tin cậy dưới ai.min_confidence (mặc định 0,6) thì coi là Lỗi (UNCERTAIN).
- P-06 Quyết định: hàm thuần decide(qr_result, ai_result) trả (target, decision_code, reason) đúng bảng 1.2; có unit test cho mọi hàng của bảng.
- P-07 Liên kết UART: lớp UartLink chạy thread riêng; gửi lệnh có chờ ACK và gửi lại; đẩy sự kiện nhận được vào hàng đợi; PING định kỳ; theo dõi trạng thái liên kết; đếm lỗi CRC.
- P-08 Vòng điều phối: chờ EVT_PKG_DETECTED → chạy pipeline → gửi SORT; xử lý EVT_ESTOP, EVT_FAULT, EVT_OTA_STATUS; mỗi lúc chỉ xử lý 1 kiện.
- P-09 Lưu trữ: SQLite /var/lib/sorter/sorter.db với các bảng packages, used_codes, rpc_audit, security_events, system_events; ảnh lỗi lưu /var/lib/sorter/defects/, giữ tối đa 500 ảnh.
- P-10 Kết nối ThingsBoard: theo T-02 đến T-04 và S-01, S-04, S-05.
- P-11 Bộ đếm: tổng, A, B, loại do lỗi, loại do bảo mật, loại do failsafe; lưu bền, khôi phục sau khởi động lại, reset bằng RPC.
- P-12 Sức khỏe hệ thống: nhiệt độ CPU, % CPU, RAM, đĩa, uptime, trạng thái liên kết ESP32; gửi mỗi 10 s.
- P-13 Ghi log: file xoay vòng /var/log/sorter/ + journald, mỗi dòng một JSON để module A đọc được.
- P-14 Chế độ mô phỏng: cờ --sim chạy không cần phần cứng: camera đọc ảnh từ thư mục, ESP32 được giả lập bằng lớp FakeEsp32, để test trên laptop.
- P-15 Dịch vụ systemd: chạy bằng user sorter (không phải root), tự khởi động lại khi lỗi, chạy cùng hệ thống.
- P-16 Công cụ kèm theo: tạo QR có chữ ký (in ra A4), thu dataset, benchmark model, theo dõi UART, mô phỏng tấn công.

### 3.3 AI kiểm tra bao bì (AI)

Model OpenCV hiện tại của Hùng còn yếu, nên chuyển sang phân loại ảnh bằng transfer learning.

- AI-01 Thu dataset: script chạy trên Pi, chụp ngay tại trạm chụp với đúng đèn và góc khi demo; gán nhãn bằng phím hoặc tự chụp khi cảm biến kích hoạt; lưu dataset/<lớp>/<box_id>_<thời gian>.jpg + file metadata.
- AI-02 Bài toán và dữ liệu: mặc định 2 lớp OK/Lỗi; tùy chọn 4 lớp OK/Rách/Móp/Ướt-bẩn; 300–500 ảnh mỗi lớp; chia train/val/test 70/15/15 theo hộp (ảnh của cùng một hộp không nằm ở cả train và test).
- AI-03 Huấn luyện: YOLOv8n-cls, ảnh 224, khoảng 50 epoch, augmentation xoay ±15°, lật, đổi độ sáng/tương phản; phương án 2 là MobileNetV2 transfer learning (đóng băng backbone rồi fine-tune). Chạy trên Colab hoặc laptop, notebook chạy lại được từ đầu.
- AI-04 Xuất model: ONNX và/hoặc TFLite, kèm labels.json và model_card.json (phiên bản, ngày, kích thước ảnh, chuẩn hóa, kết quả đánh giá).
- AI-05 Đánh giá: accuracy, precision/recall/F1 từng lớp, confusion matrix (PNG), lưới ảnh dự đoán sai; benchmark trên Pi 100 lần suy luận: trung bình, p95, RAM. Các số này đưa vào slide.
- AI-06 Suy luận trên Pi: theo P-05; gửi model_version lên ThingsBoard dạng attribute.
- AI-07 Ngưỡng ưu tiên an toàn: chọn ngưỡng trên tập val sao cho recall lớp Lỗi ≥ 95% — thà loại nhầm hàng tốt còn hơn để lọt hàng hỏng.

### 3.4 ThingsBoard và dashboard (T)

- T-01 Cài đặt: ThingsBoard CE bằng Docker Compose trên laptop; bật MQTT SSL cổng 8883 với chứng chỉ server do CA nội bộ ký; không mở cổng MQTT thường 1883 ra mạng.
- T-02 Thiết bị: device sorter-pi-01 dùng credentials X.509 (S-01), device profile riêng; thiết bị gương sorter-view cho Viewer.
- T-03 Telemetry và attributes: đúng tên khóa ở phần 4.3.
- T-04 RPC xuống Pi: chỉ các phương thức trong whitelist (phần 4.4), timeout 10 s, kết quả hiển thị trên dashboard.
- T-05 Cảnh báo và rule chain: alarm "Security alert" khi có sự kiện bảo mật (mức theo loại sự kiện); alarm "Anomaly" khi anomaly_flag bật; thiết bị offline khi quá 30 s không có dữ liệu; rule chain sao chép telemetry sang sorter-view.
- T-06 Dashboard "Sorter — Điều khiển" (Admin): trạng thái băng chuyền, liên kết ESP32, phiên bản firmware/model; 6 thẻ đếm; biểu đồ sản lượng theo phút; biểu đồ tròn tỉ lệ quyết định; thẻ kiện mới nhất; thẻ ảnh hàng lỗi mới nhất; bảng log 50 kiện gần nhất; bảng alarm bảo mật; biểu đồ anomaly score; khối điều khiển: Start, Stop, DỪNG KHẨN CẤP (nút đỏ lớn, bấm là dừng, không hỏi lại), Reset E-stop (có xác nhận), Reset bộ đếm, Reset mã đã dùng (cho demo), Kiểm tra cập nhật firmware.
- T-07 Dashboard "Sorter — Giám sát" (Viewer): cùng phần hiển thị nhưng không có khối điều khiển, đọc dữ liệu từ sorter-view.
- T-08 Người dùng và phân quyền: Admin là Tenant Administrator; Viewer là Customer User chỉ được gán dashboard Giám sát và thiết bị gương, nên không có đường gửi RPC tới thiết bị thật. Bật 2FA bằng app xác thực (TOTP) và cưỡng chế 2FA cho Admin.

### 3.5 Bảo mật (S) — 8 mục cho slide

- S-01 Xác thực thiết bị bằng X.509 (mutual TLS): CA nội bộ (EC P-256); cert server cho ThingsBoard; cert thiết bị cho Pi (CN = sorter-pi-01, hạn 1 năm); Pi xác minh server bằng CA; ThingsBoard nhận diện thiết bị qua cert, không dùng access token.
- S-02 Xác thực người dùng dashboard: 2 vai trò Admin/Viewer (T-08), 2FA TOTP, chính sách mật khẩu mạnh, khóa tài khoản sau nhiều lần đăng nhập sai.
- S-03 Xác thực mã QR: định dạng mã_kiện|kho|thời_gian|chữ_ký; chữ ký HMAC-SHA256 bằng khóa bí mật 32 byte; so sánh hằng thời gian; thời gian không vượt hiện tại quá 5 phút và chưa quá qr.max_age_days; mã kiện đã dùng thì báo DUPLICATE (chống replay). Khóa lưu file quyền 600, không bao giờ in ra log.
- S-04 MQTT qua TLS cổng 8883: TLS ≥ 1.2, luôn xác minh chứng chỉ server; code không có tùy chọn bỏ qua xác minh.
- S-05 RPC whitelist và audit: phương thức ngoài whitelist bị từ chối và sinh sự kiện RPC_REJECTED; kiểm tra kiểu và miền tham số; giới hạn 5 lệnh/10 s; ghi rpc_audit (thời gian, id, phương thức, tham số, người gửi, kết quả) và gửi lên dashboard. Người gửi trong tham số chỉ để hiển thị; nguồn đáng tin là Audit Logs của ThingsBoard. Nút E-stop vật lý hoạt động độc lập với mạng.
- S-06 UART Pi ↔ ESP32: CRC16 mỗi khung, whitelist lệnh và miền tham số trên ESP32, SEQ chống lặp. Lưu ý trung thực trên slide: CRC chống lỗi đường truyền chứ không chống giả mạo; UART là dây nội bộ trong hộp máy.
- S-07 Hardening Raspberry Pi: đổi mật khẩu mặc định, SSH chỉ dùng key, cấm root đăng nhập, ufw chặn mọi kết nối vào trừ SSH từ LAN, fail2ban, dịch vụ chạy bằng user sorter, file khóa/chứng chỉ quyền 600; script kiểm tra in checklist đạt/không đạt để chụp slide.
- S-08 Bảo vệ ESP32: Secure Boot v2 (chip revision ≥ v3.0), Flash Encryption (chế độ Development khi demo), tắt JTAG. Hai tính năng này ghi eFuse vĩnh viễn, nên thử trên board dự phòng trước và cất giữ khóa ký cẩn thận — mất khóa là không nạp được nữa.
- S-09 Nhật ký sự kiện bảo mật: mỗi sự kiện có loại, mức (INFO/WARNING/MAJOR/CRITICAL), chi tiết, thời gian; lưu DB và gửi lên ThingsBoard.

### 3.6 Secure OTA — app máy tính và phía ESP32 (O)

App máy tính (Python + PySide6):

- O-01 Đăng nhập: tài khoản lưu SQLite, mật khẩu băm bcrypt; khóa 5 phút sau 5 lần sai; tùy chọn TOTP; hết phiên sau 15 phút không thao tác; chỉ vai trò release_manager được ký và phát hành.
- O-02 Quản lý khóa: tạo cặp khóa ECDSA P-256 một lần; private key lưu PEM mã hóa bằng passphrase, chỉ nằm trên máy dev; xuất public key ra ota_pubkey.h để nhúng vào firmware; hiển thị fingerprint.
- O-03 Chọn firmware và ký: kiểm tra file là ảnh ESP32 hợp lệ (byte đầu 0xE9), đọc phiên bản từ esp_app_desc; tính SHA-256; bắt buộc version_code lớn hơn bản phát hành gần nhất; ký manifest.
- O-04 Manifest: JSON chuẩn hóa (sắp xếp khóa, không khoảng trắng) gồm phiên bản, hash, kích thước, thời gian, key id; chữ ký ECDSA dạng DER base64 tách riêng (phần 4.5).
- O-05 Phát hành: app chạy HTTPS server cổng 8443 (cert do CA nội bộ ký), chỉ phục vụ 3 file manifest.json, manifest.sig, firmware.bin và một endpoint nhận báo cáo; không liệt kê thư mục.
- O-06 Lịch sử: bảng phát hành (phiên bản, hash, người ký, thời gian) và bảng kết quả nạp do thiết bị báo về; xem và xuất CSV.
- O-07 Chế độ demo tấn công: 3 nút gắn nhãn DEMO: "Sửa 1 byte firmware" (hash sai), "Giả manifest" (chữ ký sai), "Hạ phiên bản" (ký hợp lệ nhưng phiên bản cũ, để chứng minh chống rollback).
Phía ESP32:

- O-08 Kích hoạt: lệnh OTA_CHECK qua UART (từ RPC otaCheck của Admin), chỉ nhận khi ở IDLE; URL máy chủ cố định trong firmware/NVS, không nhận URL qua UART; Wi-Fi chỉ bật trong lúc OTA.
- O-09 Tải manifest: qua HTTPS, xác minh chứng chỉ máy chủ bằng CA nhúng sẵn.
- O-10 Kiểm tra chữ ký: ECDSA P-256 bằng public key nhúng (mbedTLS); sai thì từ chối SIGNATURE_INVALID.
- O-11 Chống rollback: version_code không lớn hơn bản đang chạy và giá trị lớn nhất đã lưu trong NVS thì từ chối ROLLBACK_REJECTED; sai device_model thì MODEL_MISMATCH.
- O-12 Tải và ghi firmware: ghi vào phân vùng OTA dự phòng, vừa ghi vừa tính SHA-256; sai kích thước hoặc hash thì hủy, báo HASH_MISMATCH, phân vùng đang chạy không đổi.
- O-13 Tự kiểm tra và quay lui: bật app rollback của bootloader; bản mới khởi động ở trạng thái chờ xác nhận, chạy self-test (khởi tạo driver, nhận PING từ Pi trong 30 s); đạt thì xác nhận, không đạt hoặc treo thì tự quay về bản cũ.
- O-14 Báo trạng thái: EVT_OTA_STATUS qua UART (CHECKING, DOWNLOADING %, VERIFIED, REJECTED + lý do, REBOOTING, SUCCESS, ROLLED_BACK) để Pi đẩy lên dashboard; đồng thời gửi báo cáo về app OTA.
- O-15 Tương thích Secure Boot v2: khi bật Secure Boot, file .bin phải được ký bằng khóa secure boot lúc build; chữ ký ECDSA của manifest là lớp kiểm tra thứ hai ở tầng ứng dụng.

### 3.7 Phát hiện truy cập bất thường (A)

- A-01 Trích đặc trưng: cửa sổ 10 s từ log JSON, SQLite và journald: số RPC, RPC bị từ chối, QR chữ ký sai, QR trùng, QR sai định dạng, lỗi CRC UART, số lần MQTT kết nối lại, SSH đăng nhập thất bại, số lần E-stop, số kiện xử lý.
- A-02 Huấn luyện: dữ liệu chạy bình thường ≥ 30 phút (≥ 180 cửa sổ); StandardScaler + IsolationForest (200 cây, contamination 0,02, random_state 42); lưu model joblib + metadata.
- A-03 Chấm điểm thời gian thực: mỗi 10 s gửi anomaly_score; vượt ngưỡng thì bật anomaly_flag, sinh sự kiện ANOMALY (CRITICAL) kèm 3 đặc trưng lệch nhiều nhất để giải thích.
- A-04 Luật cứng đi kèm: ≥ 5 QR chữ ký sai/phút, ≥ 5 SSH thất bại/phút, RPC lạ vẫn báo ngay không cần model; Isolation Forest bắt các tổ hợp bất thường mà luật không lường trước.
- A-05 Mô phỏng tấn công: chỉ chạy trên hệ thống của nhóm: in QR giả, gửi RPC sai phương thức qua REST API, thử SSH sai mật khẩu, bơm khung UART lỗi ở chế độ sim.
- A-06 Đánh giá: tỉ lệ phát hiện trên các kịch bản tấn công và số cảnh báo nhầm trong 30 phút chạy bình thường, đưa vào slide.

## 4. Giao thức và định dạng dữ liệu

Đây là "hợp đồng" chung giữa Pi, ESP32, ThingsBoard và app OTA; mọi module phải theo đúng tên, mã và kích thước ở đây, không tự đặt lại.

### 4.1 Định dạng mã QR

```
PKG000123|A|1761631080|<chữ ký 43 ký tự base64url>
```

| Trường | Quy tắc |
|---|---|
| mã_kiện | PKG + 6 chữ số, regex ^PKG\d{6}$ |
| kho | A hoặc B |
| thời_gian | Unix epoch giây (UTC) lúc tạo mã |
| chữ_ký | base64url không padding của HMAC-SHA256(khóa, 3 trường đầu nối bằng dấu gạch đứng, ví dụ PKG000123\|A\|1761631080) — 43 ký tự |

Mã kết quả xác thực: VALID, NO_QR, MULTI_QR, BAD_FORMAT, BAD_WAREHOUSE, BAD_SIGNATURE, FUTURE_TIMESTAMP, EXPIRED, DUPLICATE. In QR mức sửa lỗi M, cạnh tối thiểu 3 cm.

### 4.2 Khung UART Pi ↔ ESP32

115200 baud, 8N1. Số nhiều byte theo little-endian.

| Trường | Kích thước | Giá trị |
|---|---|---|
| SOF | 2 byte | 0xAA 0x55 |
| VER | 1 byte | 0x01 |
| SEQ | 1 byte | 0–255, tăng vòng, mỗi bên một bộ đếm |
| TYPE | 1 byte | Theo bảng dưới |
| LEN | 1 byte | 0–64 |
| PAYLOAD | LEN byte | Theo bảng dưới |
| CRC16 | 2 byte | CRC-16/CCITT-FALSE (poly 0x1021, init 0xFFFF) tính trên VER…PAYLOAD |

Quy tắc: mọi khung (trừ ACK, NACK, PONG, STATUS) phải được bên nhận ACK trong 200 ms (PING được trả bằng PONG, GET_STATUS bằng STATUS thay cho ACK), quá hạn gửi lại tối đa 3 lần cùng SEQ. Bên nhận nhớ SEQ vừa xử lý; khung trùng SEQ chỉ ACK lại, không thực hiện lần hai. Khung sai CRC hoặc LEN > 64 bị bỏ và đếm lỗi; bộ giải mã tự dò lại SOF.

Pi → ESP32:

| TYPE | Tên | Payload | Trạng thái được phép |
|---|---|---|---|
| 0x01 | PING | — | Mọi trạng thái |
| 0x10 | START | speed u8 (30–100 %) | IDLE |
| 0x11 | STOP | — | IDLE, RUNNING |
| 0x12 | ESTOP | — | Mọi trạng thái |
| 0x13 | ESTOP_RESET | — | ESTOP (nút đã nhả), FAULT |
| 0x20 | SORT | target u8 (1=A, 2=B, 3=REJECT), reason u8 (0=OK, 1=DEFECT, 2=SECURITY, 3=FAILSAFE), pkg_seq u16 | DETECTED |
| 0x40 | SET_PARAM | param_id u8, value u16 | IDLE |
| 0x50 | OTA_CHECK | — | IDLE |
| 0x60 | GET_STATUS | — | Mọi trạng thái |

ESP32 → Pi:

| TYPE | Tên | Payload |
|---|---|---|
| 0x80 | ACK | acked_seq u8 |
| 0x81 | NACK | acked_seq u8, err u8 (1=CRC, 2=UNKNOWN_TYPE, 3=BAD_STATE, 4=BAD_PARAM, 5=BAD_LEN) |
| 0x82 | PONG | state u8 |
| 0x90 | EVT_PKG_DETECTED | pkg_seq u16 |
| 0x91 | EVT_SORT_DONE | pkg_seq u16, target u8 |
| 0x92 | EVT_ESTOP | source u8 (1=PHYSICAL, 2=REMOTE) |
| 0x93 | EVT_ESTOP_CLEARED | — |
| 0x94 | EVT_FAULT | code u8 (1=DECISION_TIMEOUT, 2=LINK_LOST, 3=SENSOR, 4=WDT_RESET) |
| 0xA0 | EVT_OTA_STATUS | stage u8, reason u8, progress u8, version_code u32 |
| 0xA1 | STATUS | state u8, fw_version_code u32, uptime_s u32, pkg_count u16, crc_err u16, reset_reason u8 |

Mã trạng thái: 0 BOOT, 1 IDLE, 2 RUNNING, 3 DETECTED, 4 SORTING, 5 ESTOP, 6 FAULT, 7 OTA.

Tham số SET_PARAM (ngoài danh sách hoặc ngoài miền thì NACK BAD_PARAM):

| id | Tham số | Đơn vị | Miền | Mặc định |
|---|---|---|---|---|
| 1 | belt_speed | % | 30–100 | 60 |
| 2 | servo_a_home | độ | 0–180 | 0 |
| 3 | servo_a_divert | độ | 0–180 | 45 |
| 4 | servo_b_home | độ | 0–180 | 0 |
| 5 | servo_b_divert | độ | 0–180 | 45 |
| 6 | t_clear_a | ms | 500–8000 | 2500 |
| 7 | t_clear_b | ms | 500–8000 | 3500 |
| 8 | t_clear_reject | ms | 500–10000 | 5000 |
| 9 | t_rearm | ms | 0–3000 | 500 |
| 10 | t_decision_timeout | ms | 1000–10000 | 3000 |
| 11 | hcsr04_threshold | mm | 20–400 | 80 |

Các giá trị mặc định là điểm xuất phát; nhóm hiệu chỉnh lại theo băng chuyền thật.

### 4.3 MQTT với ThingsBoard

Topic (Device MQTT API v1): telemetry v1/devices/me/telemetry; client attributes v1/devices/me/attributes; nhận RPC v1/devices/me/rpc/request/+; trả RPC v1/devices/me/rpc/response/{request_id}.

| Nhóm | Khóa | Kiểu | Khi gửi |
|---|---|---|---|
| Kiện hàng | pkg_id, warehouse, qr_status, ai_label, ai_conf, decision, reason, latency_ms | string / float / int | Mỗi kiện |
| Ảnh lỗi | defect_image | string, JPEG 320×240 base64, ≤ 40 KB | Chỉ khi REJECT_DEFECT |
| Bộ đếm | cnt_total, cnt_a, cnt_b, cnt_reject_defect, cnt_reject_security, cnt_reject_failsafe | int | Mỗi kiện và khi reset |
| Trạng thái | conveyor_state, esp_link | string / bool | Khi đổi và mỗi 10 s |
| Sức khỏe | cpu_temp, cpu_load, mem_used_pct, disk_used_pct | float | Mỗi 10 s |
| Bảo mật | security_alert, security_event_type, security_level, security_detail | bool / string | Khi có sự kiện |
| Audit RPC | rpc_log | string JSON một dòng | Mỗi RPC |
| Bất thường | anomaly_score, anomaly_flag, anomaly_top_features | float / bool / string | Mỗi 10 s |
| OTA | ota_stage, ota_reason, ota_progress | string / int | Khi ESP32 báo |

Client attributes: fw_version (ESP32), sw_version (phần mềm Pi), model_version, qr_format_version. Ảnh giữ ≤ 40 KB để nằm dưới giới hạn payload MQTT mặc định của ThingsBoard.

### 4.4 Whitelist RPC

| Phương thức | Tham số | Điều kiện | Hành động |
|---|---|---|---|
| start | {"speed": 30–100} | ESP32 ở IDLE | UART START |
| stop | {} | — | UART STOP |
| emergencyStop | {} | Luôn nhận, không tính rate limit | UART ESTOP |
| resetEstop | {} | ESP32 ở ESTOP hoặc FAULT | UART ESTOP_RESET |
| resetCounters | {} | — | Đặt bộ đếm về 0 |
| resetUsedCodes | {"confirm": "RESET"} | device.demo_mode: true | Xóa bảng used_codes |
| otaCheck | {} | ESP32 ở IDLE | UART OTA_CHECK |
| getStatus | {} | — | Trả trạng thái hiện tại |

Mọi phương thức nhận thêm requestedBy (tùy chọn, do widget điền). Trả về {"ok": true, ...} hoặc {"ok": false, "error": "METHOD_NOT_ALLOWED" | "BAD_PARAMS" | "BAD_STATE" | "RATE_LIMITED" | "ESP_TIMEOUT"}.

### 4.5 Manifest Secure OTA

```
{"created_at":"2026-11-02T09:15:00Z","device_model":"sorter-esp32","fw_file":"firmware.bin","fw_sha256":"<64 ký tự hex>","fw_size":912384,"key_id":"ota-p256-01","version_code":3,"version_name":"1.0.2"}
```
- manifest.json: một dòng, khóa sắp xếp, không khoảng trắng.
- manifest.sig: base64 của chữ ký ECDSA P-256/SHA-256 dạng DER, ký trên đúng các byte của file manifest.json (ESP32 xác minh trên byte nhận được, không cần chuẩn hóa lại).
- Báo cáo từ thiết bị: POST /ota/report với {"device":"sorter-esp32-01","version_code":3,"result":"SUCCESS|REJECTED|ROLLED_BACK","reason":"..."}.
- Mã stage: 1 CHECKING, 2 DOWNLOADING, 3 VERIFIED, 4 REJECTED, 5 REBOOTING, 6 SUCCESS, 7 ROLLED_BACK, 8 UP_TO_DATE. Mã reason: 0 NONE, 1 SIGNATURE_INVALID, 2 HASH_MISMATCH, 3 ROLLBACK_REJECTED, 4 MODEL_MISMATCH, 5 TLS_ERROR, 6 DOWNLOAD_ERROR, 7 SELFTEST_FAILED, 8 BAD_STATE.

### 4.6 File cấu hình mẫu của Pi

```
device:
  name: sorter-pi-01
  demo_mode: true
camera:
  resolution: [1280, 960]
  settle_ms: 150
  max_qr_frames: 3
  roi: [320, 160, 640, 640]      # x, y, w, h
qr:
  hmac_key_file: /etc/sorter/secrets/qr_hmac.key
  max_age_days: 30
  future_skew_s: 300
ai:
  backend: onnx                  # onnx | tflite
  model_path: /opt/sorter/models/packaging_cls.onnx
  labels_path: /opt/sorter/models/labels.json
  input_size: 224
  min_confidence: 0.6
uart:
  port: /dev/serial0
  baud: 115200
  ack_timeout_ms: 200
  retries: 3
  ping_interval_ms: 500
thingsboard:
  host: tb.local
  port: 8883
  ca_cert: /etc/sorter/certs/ca.pem
  client_cert: /etc/sorter/certs/device.pem
  client_key: /etc/sorter/certs/device.key
  health_interval_s: 10
  image_max_kb: 40
rpc:
  rate_limit: {count: 5, window_s: 10}
storage:
  db_path: /var/lib/sorter/sorter.db
  defect_dir: /var/lib/sorter/defects
  max_defect_images: 500
anomaly:
  enabled: true
  window_s: 10
  model_path: /opt/sorter/models/anomaly_if.joblib
```

## 5. Kịch bản demo và tiêu chí nghiệm thu

Buổi demo chạy 6 kịch bản bắt buộc trên mô hình lắp hoàn chỉnh, thêm 6 kịch bản bổ sung nếu còn thời gian; mỗi kịch bản chứng minh một nhóm chức năng cụ thể.

### 5.1 Kịch bản bắt buộc

| # | Kịch bản | Kết quả mong đợi | Chức năng chứng minh |
|---|---|---|---|
| 1 | Hàng tốt, QR kho A | Vào khay A, bíp ngắn, cnt_a tăng, log kiện mới | E-04, P-03 → P-08, T-06 |
| 2 | Hàng tốt, QR kho B | Vào khay B, cnt_b tăng | E-04, P-06 |
| 3 | Hàng rách, QR hợp lệ | Vào khay Loại, LED đỏ, ảnh lỗi + nhãn hiện trên dashboard | AI-06, P-05, T-06 |
| 4 | QR giả (in mã tự chế, chữ ký sai) | Vào khay Loại, đỏ nháy + còi, alarm "Security alert" loại BAD_SIGNATURE | S-03, S-09, T-05 |
| 5 | Bấm DỪNG KHẨN CẤP trên dashboard | Băng dừng, conveyor_state = ESTOP, rpc_log ghi lệnh | E-08, S-05, T-04 |
| 6a | Secure OTA bản hợp lệ | ESP32 tải, xác minh, khởi động lại; dashboard hiện fw_version mới, ota_stage SUCCESS | O-01 → O-14 |
| 6b | Secure OTA bản bị sửa 1 byte | ESP32 từ chối với HASH_MISMATCH, vẫn chạy bản cũ | O-07, O-12 |

### 5.2 Kịch bản bổ sung (khuyến nghị)

| # | Kịch bản | Kết quả mong đợi | Chức năng chứng minh |
|---|---|---|---|
| 7 | Quét lại một QR hợp lệ đã dùng | Khay Loại, DUPLICATE (chống replay) | S-03 |
| 8 | Bấm nút E-stop vật lý, kể cả khi rút mạng | Băng dừng ngay; phải nhả nút và Reset mới chạy lại | E-07 |
| 9 | Đăng nhập Viewer, rồi Admin | Viewer chỉ xem, không có nút điều khiển; Admin phải nhập mã 2FA | S-02, T-07, T-08 |
| 10 | Chạy mô phỏng tấn công | anomaly_flag bật, alarm "Anomaly" kèm đặc trưng lệch | A-03 → A-05 |
| 11 | Rút dây UART khi băng đang chạy | ESP32 dừng sau 2 s, FAULT LINK_LOST | E-09 |
| 12 | OTA bản ký hợp lệ nhưng phiên bản cũ | Từ chối ROLLBACK_REJECTED | O-11 |

### 5.3 Tiêu chí nghiệm thu (mục tiêu đề xuất, nhóm chốt lại)

| Tiêu chí | Mục tiêu | Cách đo |
|---|---|---|
| Độ chính xác AI trên tập test | Accuracy ≥ 90 %, recall lớp Lỗi ≥ 95 % | Script đánh giá (AI-05) |
| Thời gian suy luận trên Pi | ≤ 150 ms trung bình | Benchmark 100 lần |
| Từ lúc dừng băng đến lệnh SORT | ≤ 1 s | Trung bình latency_ms của 20 kiện |
| Phân loại đúng khay | 20/20 kiện ở kịch bản 1–4 | Chạy thử trước demo |
| E-stop từ dashboard | Băng dừng ≤ 0,5 s sau khi bấm | Quay video, đếm khung hình |
| QR giả và QR trùng bị chặn | 100 % | 10 mã giả + 5 lần quét lại |
| OTA tấn công bị từ chối | 100 % cả 3 kiểu (sửa byte, giả manifest, hạ phiên bản) | Chạy O-07 |
| Cảnh báo nhầm của Isolation Forest | ≤ 1 lần trong 30 phút chạy bình thường | Log anomaly_flag |

### 5.4 Chuẩn bị trước buổi demo

- ☐ Bộ hộp demo đánh số: 2 hộp tốt (A, B), 1 hộp rách, 1 hộp móp; QR in sẵn: hợp lệ A, hợp lệ B, QR giả, QR đã dùng.
- ☐ Gọi RPC resetUsedCodes và resetCounters ngay trước khi demo.
- ☐ Sẵn 3 file OTA: bản hợp lệ, bản sửa 1 byte, bản hạ phiên bản.
- ☐ Đèn chiếu sáng bật ổn định ≥ 5 phút trước khi chạy.
- ☐ Đăng nhập sẵn tài khoản Admin (đã qua 2FA) và Viewer trên 2 cửa sổ.
- ☐ Board ESP32 dự phòng đã nạp firmware giống board chính.

## 6. Prompt tổng (dán đầu mỗi phiên code)

Mỗi phiên code mới dán theo thứ tự: Prompt 0 bên dưới → phần 1–5 của tài liệu này (xuất Markdown) → đúng một prompt module ở phần 7. Nếu dùng Claude Code, lưu Prompt 0 thành CLAUDE.md ở gốc repo và phần 1–5 thành docs/SPEC.md, sau đó mỗi lần chỉ cần dán prompt module.

```
# PROMPT 0 — BỐI CẢNH CHUNG CỦA DỰ ÁN

## Vai trò
Bạn là kỹ sư senior về hệ nhúng, IoT và bảo mật, code cùng nhóm sinh viên năm 4 ngành CNTT (Trường ĐH Bách khoa – ĐH Đà Nẵng) cho đồ án "Hệ thống phân loại kiện hàng thông minh, kết hợp mã QR và AI kiểm tra bao bì, có bảo mật" (Smart Logistics Sorter). Mô hình phần cứng chạy thật sẽ được chấm trực tiếp, nên code phải chạy được trên thiết bị thật, không phải code minh họa.

## Tài liệu đặc tả
Ngay sau prompt này tôi dán tài liệu đặc tả (Phần 1–5). Đó là nguồn sự thật duy nhất:
- Phần 1: luật phân loại (bảng 1.2) — không được đổi.
- Phần 2: phần cứng, sơ đồ chân ESP32, bố trí cơ khí, luồng xử lý một kiện.
- Phần 3: 75 chức năng có mã E-xx, P-xx, AI-xx, T-xx, S-xx, O-xx, A-xx.
- Phần 4: giao thức — tên khóa telemetry, mã lệnh UART, kích thước khung, tên RPC, định dạng QR, manifest OTA, file cấu hình. Dùng đúng từng ký tự.
- Phần 5: kịch bản demo và tiêu chí nghiệm thu.
Nếu đặc tả mâu thuẫn hoặc thiếu thông tin để code đúng, DỪNG LẠI và hỏi tôi, không tự đoán rồi code tiếp.

## Công nghệ đã chốt
- Raspberry Pi 4, Raspberry Pi OS 64-bit Bookworm, Python 3.11: picamera2, pyzbar + OpenCV, onnxruntime (tùy chọn tflite-runtime), paho-mqtt 2.x, pyserial, PyYAML, sqlite3, psutil, scikit-learn, joblib, pytest.
- ESP32 DevKit (chip revision ≥ v3.0), ESP-IDF v5.x, ngôn ngữ C, build bằng idf.py; LEDC cho PWM động cơ và servo, UART2, NVS, esp_http_client + esp_ota_ops + mbedTLS cho OTA.
- ThingsBoard Community Edition bản ổn định mới nhất, Docker Compose trên laptop, MQTT SSL cổng 8883, thiết bị xác thực bằng chứng chỉ X.509.
- App Secure OTA: Python 3.11 + PySide6 + cryptography + bcrypt + pyotp + aiohttp (HTTPS).
- Huấn luyện AI: ultralytics YOLOv8n-cls trên Google Colab hoặc laptop, xuất ONNX.

## Cấu trúc repo (monorepo)
smart-sorter/
  docs/              đặc tả, hướng dẫn, số liệu cho slide
  shared/protocol/   protocol.yaml (nguồn duy nhất của giao thức UART) + gen.py sinh file .h và .py
  pi/                package Python `sorter`, tools/, tests/, systemd/, scripts/
  esp32/             firmware ESP-IDF: main/, components/, partitions.csv, sdkconfig.defaults
  ai/                chuẩn bị dữ liệu, notebook train, đánh giá, export
  thingsboard/       docker-compose, pki/, widgets/, SETUP.md
  ota-app/           app Secure OTA

## Quy tắc code
1. Bám đúng đặc tả. Mỗi file/hàm chính ghi chú mã chức năng nó hiện thực, ví dụ `# P-06`, `/* E-05 */`.
2. Không hardcode bí mật (khóa HMAC, private key, mật khẩu Wi-Fi). Bí mật đọc từ file quyền 600 hoặc NVS; .gitignore chặn chúng; chỉ commit file mẫu `*.example`.
3. Không bao giờ tắt xác minh TLS: không `verify=False`, không `tls_insecure_set(True)`, không bỏ kiểm tra chứng chỉ trên ESP32.
4. Fail-safe: mọi lỗi, timeout, ngoại lệ trong đường xử lý kiện hàng dẫn tới REJECT, không bao giờ A/B.
5. Python: type hints, dataclass, logging (không print), pytest cho mỗi module; mọi thứ chạy được ở chế độ `--sim` trên laptop không có phần cứng.
6. C (ESP-IDF): tách component theo chức năng; ISR chỉ làm việc tối thiểu; dùng FreeRTOS task + queue; kiểm tra mọi `esp_err_t`; log bằng ESP_LOGx; không vTaskDelay trong vòng xử lý chính để tạo hiệu ứng đèn.
7. Tên biến, hàm bằng tiếng Anh; comment và tài liệu bằng tiếng Việt.
8. Mỗi câu trả lời gồm: (a) tóm tắt sẽ làm gì; (b) cây file tạo/sửa; (c) toàn bộ nội dung từng file, không viết "..." bỏ dở; (d) lệnh cài đặt, chạy, test; (e) cách kiểm tra trên phần cứng thật; (f) giả định đã dùng và việc còn lại.
9. Chỉ làm đúng module trong prompt module. Cần giao diện của module chưa có thì tạo interface/stub tối thiểu và ghi rõ.
10. API nào không chắc chắn (ThingsBoard, ESP-IDF, picamera2, ultralytics) thì ghi rõ phiên bản giả định và chỗ cần đối chiếu tài liệu chính thức; không bịa tên hàm hay tham số.
11. Bài dài thì chia nhiều lượt trả lời theo nhóm file, báo trước tổng số lượt.

## An toàn phần cứng
- Không đưa lệnh ghi eFuse (Secure Boot, Flash Encryption, tắt JTAG) mà không cảnh báo rõ là không đảo ngược được và yêu cầu thử trên board dự phòng.
- Nút E-stop vật lý ngắt nguồn động cơ bằng phần cứng; phần mềm chỉ là lớp bảo vệ thứ hai.
- Script hardening không được làm mất quyền SSH của chính nhóm.

## Bắt đầu
Nếu tôi đã dán đặc tả và prompt module trong cùng tin nhắn thì bắt đầu làm luôn. Nếu chưa, tóm tắt kiến trúc trong 10 dòng rồi chờ.
```

## 7. Prompt từng module

10 prompt dưới đây dán lần lượt, mỗi phiên một prompt, sau Prompt 0 và đặc tả. Mỗi prompt ghi rõ mã chức năng phải hiện thực, file cần tạo, test và tiêu chí xong để kiểm tra AI có làm đủ không.

### P1 — Khung repo và giao thức dùng chung

```
# MODULE P1 — Khung repo + giao thức UART dùng chung (E-10, P-07, phần 4.2)

Mục tiêu: tạo monorepo theo Prompt 0 và một nguồn định nghĩa giao thức UART duy nhất để Pi và ESP32 không bao giờ lệch nhau.

Yêu cầu:
1. Tạo cây thư mục, README.md gốc (mục đích, sơ đồ thư mục, cách build từng phần), .gitignore (chặn *.key, *.pem trừ *.example.pem, secrets/, .env, build/, __pycache__/, dataset/, *.onnx lớn), Makefile với các lệnh: gen-protocol, test-pi, build-esp32.
2. shared/protocol/protocol.yaml mô tả đủ phần 4.2: SOF, VER, LEN tối đa; mọi TYPE (tên, mã, hướng, payload gồm tên trường + kiểu u8/u16/u32, trạng thái được phép); mã lỗi NACK; mã trạng thái; mã fault; mã target/reason của SORT; mã stage/reason OTA (4.5); bảng SET_PARAM (id, tên, đơn vị, min, max, default).
3. shared/protocol/gen.py đọc YAML và sinh:
   - esp32/components/protocol/include/protocol_gen.h: enum, #define, struct payload __attribute__((packed)), mảng min/max/default tham số.
   - pi/sorter/protocol_gen.py: IntEnum, dataclass payload, hàm pack/unpack bằng struct.
   Dòng đầu mỗi file sinh ra: "AUTO-GENERATED — DO NOT EDIT".
4. pi/sorter/uart_codec.py: encode_frame(seq, msg_type, payload) -> bytes; lớp FrameDecoder.feed(data: bytes) -> list[Frame] nhận dữ liệu theo từng mảnh, tự dò lại SOF, bỏ khung sai CRC hoặc LEN > 64 và đếm lỗi. CRC-16/CCITT-FALSE tự viết.
5. esp32/components/protocol/: proto_encode() và bộ giải mã từng byte proto_decoder_feed(), cùng thuật toán CRC.
6. shared/protocol/test_vectors.json: ≥ 10 khung mẫu (hex) kèm kết quả giải mã mong đợi, gồm ca sai CRC, LEN quá lớn, rác trước SOF, 2 khung dính liền, khung bị cắt đôi qua 2 lần feed.
7. Test: pytest cho Python dùng test_vectors.json; chương trình test C chạy trên máy host bằng gcc dùng cùng file. Kiểm tra chéo: CRC của chuỗi ASCII "123456789" phải bằng 0x29B1.

Tiêu chí xong: make gen-protocol sinh đủ file; pytest pi/tests/test_uart_codec.py pass; test C pass; hai bên giải mã test vectors giống nhau.
Không làm: logic điều khiển, ThingsBoard, AI.
```

### P2 — Firmware ESP32 phần chấp hành

```
# MODULE P2 — Firmware ESP32 phần chấp hành (E-01 → E-12)

Bối cảnh: đã có component protocol từ P1. ESP-IDF v5.x, C. Sơ đồ chân theo 2.3, giao thức theo 4.2, đèn/còi theo E-06.

Yêu cầu:
1. Cấu trúc: main/app_main.c; components: board (định nghĩa chân), motor (L298N, LEDC 1 kHz 10 bit, ramp 200 ms), servo (LEDC 50 Hz, góc → xung 500–2500 µs, chặn góc 0–180), sensor (TCRT5000: ngắt + chống dội 20 ms; HC-SR04: đo mỗi 30 ms, 2 lần liên tiếp dưới ngưỡng; chọn loại bằng Kconfig), indicator (pattern LED/còi không chặn, dùng esp_timer), estop (GPIO39 ngắt cả hai cạnh), link (UART2 + protocol + ACK + gửi lại + chống lặp SEQ), params (NVS + whitelist min/max từ protocol_gen.h), fsm (máy trạng thái).
2. E-01: bảng chuyển trạng thái viết trong comment đầu fsm.c; mọi sự kiện (lệnh UART, cảm biến, E-stop, timeout) vào một queue, một task fsm xử lý tuần tự; lệnh sai trạng thái → NACK BAD_STATE.
3. E-03/E-04/E-05: phát hiện hàng → dừng băng ngay, sang DETECTED, gửi EVT_PKG_DETECTED (gửi lại nếu chưa có ACK), chờ SORT tối đa t_decision_timeout; nhận SORT → đặt servo, chạy băng t_clear_x, trả servo về, gửi EVT_SORT_DONE, chờ t_rearm mới nhận cảm biến tiếp; quá hạn → tự REJECT lý do FAILSAFE + EVT_FAULT(DECISION_TIMEOUT).
4. E-07/E-08: ISR E-stop tắt PWM động cơ và kéo IN1/IN2 về 0 ngay, rồi báo task; ESTOP chỉ thoát khi GPIO39 về LOW (nút đã nhả) và nhận ESTOP_RESET → IDLE, không tự chạy lại. Tiếp điểm NC: LOW = bình thường, HIGH = dừng (đứt dây cũng dừng).
5. E-09: Pi gửi PING mỗi 500 ms; đang RUNNING/DETECTED/SORTING mà 2000 ms không có khung hợp lệ → dừng băng, FAULT(LINK_LOST); có liên kết lại thì gửi EVT_FAULT và chờ ESTOP_RESET. Bật Task Watchdog cho task fsm và link.
6. E-11: SET_PARAM chỉ ở IDLE, kiểm tra min/max, lưu NVS, áp dụng ngay; khởi động đọc NVS, thiếu thì dùng default.
7. E-12: STATUS mỗi 1 s; fw_version_code là một hằng trong version.h (P8 dùng lại); reset_reason từ esp_reset_reason().
8. Chế độ kiểm tra đấu dây: Kconfig SORTER_HW_TEST khi bật thì chạy lần lượt: quay động cơ 2 s, quét servo A rồi B, nháy từng LED, kêu còi, in trạng thái cảm biến và E-stop mỗi 200 ms.
9. partitions.csv dùng bố cục OTA cho flash 4 MB: nvs, otadata, phy_init, ota_0, ota_1 (mỗi phân vùng ≥ 1,5 MB); sdkconfig.defaults bật CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE ngay từ đầu để P8 dùng lại.

Kiểm thử:
- pi/tools/esp_bench.py (chạy trên laptop hoặc Pi, dùng codec P1): START → đưa tay qua cảm biến → nhận EVT → gửi SORT A/B/REJECT → nhận SORT_DONE; thử lệnh sai trạng thái, sai CRC, SEQ lặp, tham số ngoài miền, ngừng PING.
- Bảng kiểm tra bằng tay cho E-stop vật lý (bấm khi đang chạy, bấm khi đang gạt, rút dây nút).

Tiêu chí xong: idf.py build sạch; mọi ca trong esp_bench.py đúng bảng 4.2; ngừng PING thì băng dừng sau khoảng 2 s; bấm E-stop thì băng dừng ngay và không tự chạy lại.
Không làm: Wi-Fi, OTA (P8), Secure Boot (P6).
```

### P3 — Phần mềm điều phối trên Raspberry Pi

```
# MODULE P3 — sorter-core trên Raspberry Pi (P-01 → P-09, P-11 → P-16, S-03, S-09)

Bối cảnh: đã có codec UART từ P1. Chưa có ThingsBoard (P5) và model thật (P4): tạo interface TelemetrySink (bản tạm ghi ra log) và Classifier với bản giả DummyClassifier (trả OK, hoặc đọc nhãn từ tên file ảnh khi chạy sim).

Yêu cầu:
1. Package pi/sorter/:
   - config.py: đọc + validate YAML theo 4.6 bằng dataclass, báo lỗi rõ ràng.
   - camera.py: Picamera2Camera (mở một lần, khóa AE/AWB sau 2 s, capture() → numpy RGB) và FolderCamera cho sim.
   - qr_reader.py: pyzbar, dự phòng cv2.QRCodeDetector; trả NO_QR / MULTI_QR / chuỗi; thử tối đa max_qr_frames khung, tăng tương phản nếu thất bại.
   - qr_security.py (S-03): parse, regex, HMAC-SHA256 base64url, hmac.compare_digest, kiểm tra thời gian, chống trùng bằng bảng used_codes trong một transaction; trả dataclass QrResult{status, pkg_id, warehouse, ts} với status đúng danh sách 4.1.
   - classifier.py: interface + DummyClassifier (OnnxClassifier để P4 làm).
   - decision.py (P-06): hàm thuần, đúng bảng 1.2.
   - uart_link.py (P-07): thread đọc, thread PING, send_command(msg_type, payload, timeout) chờ ACK và gửi lại, hàng đợi sự kiện, tự ACK sự kiện của ESP32, trạng thái link, đếm lỗi CRC.
   - fake_esp32.py: giả lập đúng máy trạng thái E-01 qua cặp pty, có hàm giả "cảm biến" và "E-stop".
   - storage.py: SQLite WAL, 5 bảng ở P-09 + migration đơn giản; counters.py (P-11); health.py (P-12, nhiệt độ CPU từ /sys/class/thermal, psutil); security_events.py (S-09).
   - orchestrator.py (P-08): vòng chính + pipeline một kiện đúng 2.5, đo thời gian capture, qr, verify, ai, tổng.
   - main.py: CLI --config, --sim, --log-level.
2. Mọi exception trong pipeline → REJECT_FAILSAFE + log + system event; không để vòng chính chết.
3. Ảnh lỗi: lưu JPEG gốc vào defect_dir; tạo bản 320×240 chất lượng 70, quá image_max_kb thì giảm chất lượng dần; dọn ảnh cũ khi vượt max_defect_images.
4. Log JSON một dòng (P-13), file xoay vòng 5 × 5 MB.
5. tools/make_hmac_key.py: sinh khóa 32 byte bằng secrets, ghi file quyền 600. tools/qr_generator.py: tạo QR cho danh sách (pkg_id, kho), xuất PNG từng mã + trang A4 PDF lưới 3×4 có nhãn chữ dưới mỗi mã; cờ --forge tạo QR chữ ký sai để demo; không in khóa ra màn hình.
6. tools/capture_dataset.py (dùng ở P4): chụp tại trạm, gán nhãn bằng phím 1–4, lưu đúng cấu trúc AI-01, có chế độ chụp khi nhận EVT_PKG_DETECTED. tools/uart_monitor.py: in mọi khung đã giải mã kèm thời gian.
7. tests/fixtures/make_images.py: sinh ảnh giả (nền màu carton + QR thật / QR giả / không QR / vết rách vẽ giả) để test không cần camera.
8. systemd/sorter.service (User=sorter, Restart=on-failure, NoNewPrivileges=yes, ProtectSystem=strict, ReadWritePaths=/var/lib/sorter /var/log/sorter) và scripts/install_pi.sh (tạo user sorter thuộc nhóm video, dialout, gpio; tạo thư mục với quyền đúng; venv; apt cài python3-picamera2, libzbar0; pip requirements; hướng dẫn bật UART bằng raspi-config + dtoverlay=disable-bt).
9. pytest: decision (mọi hàng bảng 1.2); qr_security (mọi mã ở 4.1, gồm replay); uart_link với fake_esp32 (ACK, gửi lại, SEQ lặp, mất link); orchestrator end-to-end ở chế độ sim với ảnh fixtures (QR A tốt, QR B tốt, rách, QR giả, không QR) → kiểm tra lệnh SORT gửi đi đúng target và reason.

Tiêu chí xong: python -m sorter.main --sim --config config.sim.yaml chạy được trên laptop và in quyết định đúng cho ảnh mẫu; toàn bộ pytest pass; trên Pi thật với ESP32 của P2 thì băng dừng – chụp – gạt đúng khay.
Không làm: MQTT/ThingsBoard (P5), train model (P4), phát hiện bất thường (P9).
```

### P4 — AI kiểm tra bao bì

**Cập nhật triển khai 2026-11-04:** theo model nhóm cung cấp, các đường train YOLO-cls/Keras dưới đây được thay bằng `ai/3-best.ipynb` (MobileNetV4, RGB 256, normal/damage), checkpoint `best_model.pth` và ONNX FP32 xuất sẵn. Bước phát hiện hộp dùng YOLO-World ONNX độc lập. Hướng dẫn hiện hành: [ai/README.md](../ai/README.md). Khối prompt bên dưới được giữ làm lịch sử đặc tả.

```
# MODULE P4 — AI kiểm tra bao bì (AI-01 → AI-07, P-05)

Bối cảnh: dataset chụp bằng pi/tools/capture_dataset.py, cấu trúc dataset/<lớp>/<box_id>_<timestamp>.jpg, mỗi lớp 300–500 ảnh. Mặc định 2 lớp ok/defect; tùy chọn 4 lớp ok/tear/dent/wet. Model OpenCV cũ bị bỏ, thay bằng transfer learning.

Yêu cầu:
1. ai/prepare_dataset.py: loại ảnh hỏng; thống kê số ảnh theo lớp và theo hộp; chia train/val/test 70/15/15 THEO box_id (group split, seed cố định); xuất cấu trúc thư mục mà ultralytics classify cần; cảnh báo nếu các lớp lệch nhau quá 1,5 lần.
2. ai/train_yolo_cls.ipynb (chạy được trên Colab, có ô mount Google Drive) và ai/train_yolo_cls.py tương đương: YOLOv8n-cls, imgsz 224, epochs 50, patience 10, augmentation xoay ±15°, lật ngang, đổi độ sáng, scale nhẹ; lưu best.pt; export ONNX (imgsz 224) thành packaging_cls.onnx; sinh labels.json đúng thứ tự lớp.
3. Phương án 2 ai/train_mobilenetv2.py (Keras): MobileNetV2 ImageNet, đóng băng backbone train head 10 epoch, rồi mở khoảng 30 lớp cuối fine-tune với learning rate nhỏ; export TFLite float16 và int8 (representative dataset).
4. ai/evaluate.py: chạy trên tập test; in accuracy, precision/recall/F1 từng lớp; lưu confusion_matrix.png, misclassified_grid.png (≤ 24 ảnh sai), metrics.json; quét ngưỡng min_confidence trên tập val, chọn ngưỡng nhỏ nhất đạt recall lớp lỗi ≥ 95 % (AI-07); ghi model_card.json (phiên bản, ngày, imgsz, chuẩn hóa, lớp, ngưỡng, kết quả).
5. pi/sorter/classifier.py: OnnxClassifier (onnxruntime CPUExecutionProvider, intra_op_num_threads=4, tiền xử lý giống hệt lúc train theo model_card) và TfliteClassifier; cùng interface predict(image, roi) -> ClassResult{label, confidence, probs, infer_ms}; áp dụng ROI và ngưỡng; ánh xạ nhãn về OK / DEFECT (và TEAR / DENT / WET nếu 4 lớp); dưới ngưỡng trả UNCERTAIN.
6. pi/tools/benchmark_model.py: warm-up 10 lần, đo 100 lần, in mean / p50 / p95 / max (ms) và RAM (psutil), ghi benchmark.json.
7. ai/README.md: cách chụp dataset đúng (cùng đèn, cùng góc, nhiều hướng đặt hộp, nhiều mức rách/móp, nền là băng thật), cách bổ sung ảnh khó khi model sai, cách copy model + labels + model_card sang Pi, cách đổi backend trong config.

Tiêu chí xong: notebook chạy từ đầu đến cuối trên Colab; evaluate.py sinh đủ file; benchmark chạy được trên Pi 4; orchestrator P3 chuyển từ DummyClassifier sang OnnxClassifier chỉ bằng đổi config.
```

### P5 — ThingsBoard, MQTT TLS, RPC và dashboard

```
# MODULE P5 — ThingsBoard, MQTT TLS + X.509, RPC whitelist, dashboard (T-01 → T-08, S-01, S-02, S-04, S-05)

Bối cảnh: sorter-core (P3) đã có interface TelemetrySink. ThingsBoard CE chạy Docker trên laptop trong LAN, tên máy tb.local (hoặc IP tĩnh).

Yêu cầu:
1. thingsboard/pki/ (bash + openssl): make_ca.sh (CA EC P-256, 5 năm); make_server_cert.sh (cert cho tb.local, SAN gồm DNS và IP, 2 năm); make_device_cert.sh <device_name> (CN=<device_name>, 1 năm, key quyền 600); make_ota_server_cert.sh (cert cho máy chủ OTA của P7). In hướng dẫn file nào copy đi đâu. Không commit khóa.
2. thingsboard/docker-compose.yml: ThingsBoard CE image chính thức (ghi rõ tag), volume dữ liệu, mount thư mục cert vào /config; MQTT_SSL_ENABLED=true, MQTT_SSL_CREDENTIALS_TYPE=PEM, MQTT_SSL_PEM_CERT=/config/server.pem, MQTT_SSL_PEM_KEY=/config/server_key.pem, MQTT_SSL_BIND_PORT=8883; chỉ publish cổng web và 8883, KHÔNG publish 1883. Ghi chú đối chiếu tên biến với tài liệu đúng phiên bản.
3. pi/sorter/tb_client.py: paho-mqtt 2.x, TLS với CA + client cert/key, TLS ≥ 1.2, xác minh hostname; client_id = tên thiết bị; tự kết nối lại với backoff 1, 2, 4… tối đa 60 s và đếm số lần (cho A-01); hàng đợi khi mất mạng (tối đa 1000 bản tin, bỏ cũ nhất); hiện thực TelemetrySink với đúng tên khóa 4.3; gửi client attributes khi kết nối; subscribe RPC.
4. pi/sorter/rpc_handler.py (S-05): whitelist đúng 4.4; schema kiểm tra kiểu và miền từng tham số; rate limit token bucket 5 lệnh/10 s (emergencyStop luôn được nhận và xử lý trước); kiểm tra trạng thái ESP32; gọi UartLink; trả response đúng định dạng 4.4; ghi rpc_audit và gửi rpc_log; phương thức lạ → METHOD_NOT_ALLOWED + security_event RPC_REJECTED. pytest cho mọi nhánh.
5. thingsboard/SETUP.md — hướng dẫn từng bước trên giao diện (kèm file JSON import nếu chắc chắn đúng định dạng của phiên bản, nếu không chắc thì chỉ viết hướng dẫn):
   a. Device profile "Sorter": MQTT, inactivity timeout 30 s; alarm rules: "Security alert" khi security_alert = true (mức theo security_level), "Anomaly" khi anomaly_flag = true, "High CPU temperature" khi cpu_temp > 75.
   b. Device sorter-pi-01 với credentials X.509 (dán device.pem). Device sorter-view là thiết bị gương, không kết nối thật.
   c. Rule chain: sao chép telemetry của sorter-pi-01 sang sorter-view (relation + node change originator) rồi lưu timeseries.
   d. Người dùng: Admin = Tenant Administrator; customer "Viewer" + Customer User chỉ được gán sorter-view và dashboard Giám sát. Bật 2FA bằng app xác thực (Security → Two-factor authentication), cưỡng chế cho Admin nếu phiên bản hỗ trợ; password policy; khóa sau 5 lần đăng nhập sai.
   e. Dashboard "Sorter — Điều khiển" đủ widget T-06; dashboard "Sorter — Giám sát" theo T-07.
6. thingsboard/widgets/: (a) Defect image card: HTML/JS hiển thị ảnh base64 từ defect_image kèm nhãn, độ tin cậy, thời gian; (b) Sorter control panel: các nút gửi two-way RPC đúng tên 4.4 (timeout 10 s), tự thêm requestedBy = email người dùng đang đăng nhập, hiện kết quả trả về; nút DỪNG KHẨN CẤP màu đỏ lớn gửi ngay không hỏi; Reset E-stop và Reset mã đã dùng có hộp xác nhận. Ghi chú các API widget cần đối chiếu theo phiên bản.
7. thingsboard/tools/rpc_cli.py: đăng nhập REST API (JWT) và gọi RPC từ laptop để test whitelist.

Tiêu chí xong: Pi kết nối 8883 bằng cert; kết nối không cert hoặc cert lạ bị từ chối; cổng 1883 không mở; dashboard cập nhật thời gian thực khi chạy sim; RPC lạ bị chặn và ghi log; tài khoản Viewer không thấy và không gọi được điều khiển.
```

### P6 — Hardening Raspberry Pi và bảo vệ ESP32

```
# MODULE P6 — Hardening Pi, Secure Boot / Flash Encryption ESP32, tài liệu slide bảo mật (S-06, S-07, S-08)

Yêu cầu:
1. pi/scripts/harden_pi.sh — idempotent, chạy bằng sudo, hỏi xác nhận trước mỗi bước có thể tự khóa mình ra ngoài:
   - Bắt đổi mật khẩu nếu còn mặc định; khóa tài khoản không dùng.
   - SSH: kiểm tra đã có authorized_keys của user quản trị TRƯỚC khi tắt mật khẩu; tạo /etc/ssh/sshd_config.d/99-hardening.conf: PasswordAuthentication no, KbdInteractiveAuthentication no, PermitRootLogin no, MaxAuthTries 3, AllowUsers <admin>; chạy sshd -t trước khi reload.
   - ufw: default deny incoming, allow outgoing, allow 22/tcp chỉ từ subnet LAN (tham số); Pi không cần mở cổng nào khác vì chỉ kết nối ra ThingsBoard.
   - fail2ban cho sshd (maxretry 5, bantime 10 phút).
   - /etc/sorter/secrets và /etc/sorter/certs: chown sorter:sorter, thư mục 700, file 600.
   - Tắt dịch vụ không dùng (hỏi trước): bluetooth, avahi-daemon, cups nếu có.
   - unattended-upgrades cho bản vá bảo mật.
2. pi/scripts/security_audit.sh: không sửa gì, chỉ kiểm tra và in bảng PASS/FAIL từng mục ở trên kèm lệnh kiểm chứng, để chụp vào slide.
3. esp32/SECURITY.md — hướng dẫn từng bước trên ESP-IDF v5.x:
   - Cảnh báo in đậm ở đầu: ghi eFuse vĩnh viễn; làm trên board dự phòng trước; sao lưu khóa ký ở 2 nơi; mất khóa là không nạp được nữa.
   - Kiểm tra chip revision ≥ v3.0 bằng esptool trước khi bắt đầu (ghi lệnh cụ thể).
   - Sinh khóa ký Secure Boot V2 (RSA-3072) bằng espsecure.py; cấu hình menuconfig mục Security features; Flash Encryption chế độ Development cho demo, giải thích khác biệt với Release; bật NVS encryption để bảo vệ mật khẩu Wi-Fi; tắt JTAG.
   - Thứ tự build/flash lần đầu, kiểm tra trạng thái bằng espefuse.py summary, cách nạp lại về sau; nhấn mạnh firmware OTA (P8) cũng phải được ký bằng khóa secure boot.
   - Cách chứng minh khi demo: nạp firmware không ký thì bị từ chối; đọc flash bằng esptool thì dữ liệu đã bị mã hóa.
   - Đánh dấu rõ chỗ cần đối chiếu tài liệu Espressif cho đúng phiên bản IDF.
4. docs/security_slide.md: bảng S-01 → S-08 (mối đe dọa → biện pháp → cách chứng minh khi demo → file/cấu hình liên quan) và mục "Giới hạn" trung thực (CRC không chống giả mạo; requestedBy chỉ để hiển thị, nguồn tin cậy là Audit Logs của ThingsBoard; Development mode của Flash Encryption vẫn còn cho nạp lại).

Tiêu chí xong: chạy harden_pi.sh trên Pi thật không mất SSH; security_audit.sh báo PASS toàn bộ; SECURITY.md đủ để một thành viên làm theo mà không phải đoán.
```

### P7 — App Secure OTA trên máy tính

```
# MODULE P7 — App Secure OTA trên máy dev (O-01 → O-07, phần 4.5)

Công nghệ: Python 3.11, PySide6, cryptography, bcrypt, pyotp, SQLite, aiohttp (HTTPS server chạy trong thread riêng, không chặn giao diện).

Yêu cầu:
1. Cấu trúc ota-app/ota_app/: main.py, auth.py, keys.py, firmware.py, manifest.py, server.py, history.py, demo_attacks.py, ui/ (cửa sổ đăng nhập; cửa sổ chính dạng tab: Phát hành, Lịch sử, Khóa, Máy chủ, Demo).
2. O-01: lần chạy đầu bắt tạo tài khoản quản trị; mật khẩu ≥ 10 ký tự, băm bcrypt cost 12; khóa 5 phút sau 5 lần sai (lưu DB); TOTP tùy chọn (hiện QR để quét bằng app xác thực); tự khóa phiên sau 15 phút không thao tác; vai trò release_manager và viewer, chỉ release_manager được ký và phát hành.
3. O-02: tạo cặp khóa ECDSA P-256; private key lưu PEM mã hóa bằng passphrase trong thư mục người dùng, quyền 600; không bao giờ hiển thị hay ghi log private key; xuất ota_pubkey.pem và esp32/components/ota_secure/include/ota_pubkey.h (chuỗi PEM dạng C); key_id = 8 byte đầu SHA-256 của public key DER, hiển thị dạng hex.
4. O-03: chọn file .bin; kiểm tra byte đầu 0xE9; đọc esp_app_desc_t ở offset 32 (sau 24 byte image header + 8 byte segment header, magic 0xABCD5432) để lấy chuỗi version, đọc thất bại thì cho nhập tay; nhập version_code (số nguyên) + version_name; chặn nếu version_code ≤ bản phát hành lớn nhất; nếu bật tùy chọn "thiết bị đã bật Secure Boot" mà không thấy khối chữ ký Secure Boot V2 thì cảnh báo.
5. O-04: manifest.json một dòng (sort_keys, separators=(',', ':')); ký ECDSA với SHA-256 → DER → base64 → manifest.sig; tự xác minh lại bằng public key trước khi phát hành.
6. O-05: HTTPS server dùng cert từ P5 (make_ota_server_cert.sh), cổng 8443, chỉ 4 route: GET /ota/manifest.json, GET /ota/manifest.sig, GET /ota/firmware.bin, POST /ota/report (JSON ≤ 2 KB, kiểm tra trường); log mọi request (IP, route, thời gian, mã trả về); nút Start/Stop và hiện URL.
7. O-06: bảng releases và device_reports trong SQLite; tab Lịch sử lọc, xem chi tiết, xuất CSV; mỗi lần ký ghi người ký, thời gian, hash.
8. O-07 tab Demo (nền vàng, nhãn "CHỈ DÙNG ĐỂ DEMO"): (a) Sửa 1 byte firmware: lật 1 bit ở vị trí ngẫu nhiên sau header của bản đang phát hành, giữ nguyên manifest; (b) Giả manifest: sửa fw_sha256 hoặc version trong manifest nhưng giữ chữ ký cũ; (c) Hạ phiên bản: ký hợp lệ một bản có version_code thấp hơn bản đang chạy trên thiết bị. Mỗi kiểu có nút "Khôi phục bản hợp lệ".
9. ota-app/tools/verify_cli.py: xác minh một thư mục phát hành ngoài giao diện.
10. pytest: ký/xác minh; phát hiện đủ 3 kiểu tấn công; chặn version không tăng; khóa tài khoản sau 5 lần sai.

Tiêu chí xong: tạo được bản phát hành hợp lệ và verify_cli báo hợp lệ; 3 bản demo đều bị verify_cli từ chối đúng lý do; curl --cacert ca.pem tải được 3 file qua HTTPS.
```

### P8 — Secure OTA phía ESP32

```
# MODULE P8 — Secure OTA trên ESP32 (O-08 → O-15)

Bối cảnh: firmware P2 đã có bố cục phân vùng OTA và bật app rollback; P7 đã xuất ota_pubkey.h và chạy HTTPS server cổng 8443.

Yêu cầu:
1. Component esp32/components/ota_secure/ (ota_secure.c/.h), nhúng CA của máy chủ OTA bằng EMBED_TXTFILES và ota_pubkey.h.
2. Wi-Fi SSID/mật khẩu và URL máy chủ lưu trong NVS (namespace riêng), nạp bằng file CSV + nvs_partition_gen hoặc lệnh provisioning qua cổng USB; Wi-Fi chỉ bật khi OTA rồi tắt.
3. Luồng OTA (task riêng, stack đủ cho mbedTLS):
   a. Nhận OTA_CHECK: FSM chỉ cho phép ở IDLE → sang trạng thái OTA, giữ băng dừng, khóa mọi lệnh trừ ESTOP và GET_STATUS.
   b. Tải manifest.json + manifest.sig bằng esp_http_client với cert_pem = CA (không bao giờ bỏ xác minh); giới hạn manifest 1 KB, sig 256 byte.
   c. Xác minh ECDSA P-256 (mbedtls_pk_parse_public_key + SHA-256 + mbedtls_pk_verify) trên đúng các byte manifest nhận được; sai → REJECTED SIGNATURE_INVALID.
   d. Parse JSON (cJSON); kiểm tra device_model; version_code phải lớn hơn bản đang chạy và lớn hơn ota_max_version trong NVS (O-11); fw_size ≤ kích thước phân vùng.
   e. esp_ota_get_next_update_partition → esp_ota_begin → đọc firmware theo khối 4 KB, mỗi khối vừa cập nhật SHA-256 vừa esp_ota_write; báo DOWNLOADING mỗi 10 %; sai size hoặc hash → esp_ota_abort, REJECTED HASH_MISMATCH, phân vùng đang chạy không đổi.
   f. esp_ota_end (khi bật Secure Boot V2 bước này còn kiểm tra chữ ký secure boot) → esp_ota_set_boot_partition → lưu ota_max_version → báo REBOOTING → esp_restart.
4. Sau khởi động (O-13): kiểm tra trạng thái phân vùng; nếu đang chờ xác nhận thì self-test: motor/servo/LED khởi tạo OK và nhận PING hợp lệ từ Pi trong 30 s; đạt → esp_ota_mark_app_valid_cancel_rollback + báo SUCCESS; không đạt → esp_ota_mark_app_invalid_rollback_and_reboot; bản cũ khi chạy lại nhận ra vừa rollback thì báo ROLLED_BACK.
5. O-14: gửi EVT_OTA_STATUS qua UART ở mọi stage; gửi POST /ota/report (best-effort, lỗi thì bỏ qua) khi REJECTED, SUCCESS, ROLLED_BACK.
6. Phía Pi: xử lý EVT_OTA_STATUS → telemetry ota_stage / ota_reason / ota_progress và cập nhật attribute fw_version.
7. esp32/OTA_TEST.md: các bước demo 6a, 6b, 12 và cách thử rollback (build một bản cố ý không trả lời PING để self-test thất bại).

Tiêu chí xong: 4 ca đều đúng — hợp lệ (SUCCESS), sửa 1 byte (HASH_MISMATCH), giả manifest (SIGNATURE_INVALID), hạ phiên bản (ROLLBACK_REJECTED); ca self-test thất bại tự quay về bản cũ; băng chuyền không chạy trong suốt quá trình OTA.
```

### P9 — Phát hiện truy cập bất thường

```
# MODULE P9 — Phát hiện truy cập bất thường bằng Isolation Forest (A-01 → A-06)

Bối cảnh: sorter-core ghi log JSON (P-13) và SQLite (rpc_audit, security_events, packages); tb_client đếm số lần kết nối lại; log SSH đọc qua journalctl.

Yêu cầu:
1. pi/sorter/anomaly/features.py: gom đặc trưng theo cửa sổ 10 s đúng A-01 từ bộ đếm trong bộ nhớ (orchestrator, rpc_handler, uart_link, tb_client gọi hàm increment) + đọc `journalctl -u ssh --since` cho số lần SSH thất bại (subprocess có timeout, không shell=True); lưu mỗi cửa sổ vào bảng anomaly_windows.
2. anomaly/train.py: lấy các cửa sổ trong khoảng thời gian "bình thường" do người dùng chỉ định; StandardScaler + IsolationForest(n_estimators=200, contamination=0.02, random_state=42); ngưỡng = phân vị 99 của điểm trên dữ liệu bình thường; lưu joblib + metadata.json (đặc trưng, ngưỡng, số mẫu, ngày).
3. anomaly/detector.py: thread chạy mỗi 10 s; điểm = -score_samples (càng lớn càng bất thường); gửi anomaly_score; vượt ngưỡng → anomaly_flag = true, security_event ANOMALY mức CRITICAL, anomaly_top_features = 3 đặc trưng có z-score lớn nhất; cooldown 60 s để không báo dồn.
4. anomaly/rules.py (A-04): luật cứng chạy song song, sinh security_event riêng (RULE_*): ≥ 5 QR chữ ký sai/phút, ≥ 5 SSH thất bại/phút, có RPC lạ.
5. tools/attack_sim.py (A-05), bắt buộc cờ --i-own-this-system: (a) gửi RPC phương thức lạ và spam RPC hợp lệ qua REST ThingsBoard; (b) thử SSH sai mật khẩu N lần tới chính Pi của nhóm; (c) ở chế độ sim: bơm QR chữ ký sai và khung UART sai CRC. In thời điểm bắt đầu/kết thúc từng kịch bản ra file để đối chiếu.
6. anomaly/evaluate.py (A-06): từ anomaly_windows + nhật ký attack_sim tính tỉ lệ phát hiện từng kịch bản và số báo nhầm trong khoảng bình thường; xuất bảng Markdown + biểu đồ PNG điểm theo thời gian có tô vùng tấn công.
7. pytest với dữ liệu tổng hợp.

Tiêu chí xong: train chạy được trên Pi; khi chạy attack_sim thì dashboard bật alarm Anomaly trong ≤ 20 s; 30 phút chạy bình thường báo nhầm ≤ 1 lần.
```

### P10 — Tích hợp, kiểm thử demo, số liệu cho slide

```
# MODULE P10 — Tích hợp toàn hệ thống, kiểm thử demo, số liệu cho slide

Yêu cầu:
1. docs/RUNBOOK.md: thứ tự bật hệ thống (laptop ThingsBoard → Pi → ESP32 → kiểm tra dashboard); checklist trước demo theo mục 5.4; xử lý sự cố thường gặp: Pi không kết nối được 8883 (giờ hệ thống/NTP, SAN của cert, quyền file key), ESP32 không phản hồi UART (đảo TX/RX, GND chung, serial console chưa tắt), ảnh tối hoặc nhòe (đèn, settle_ms), AI sai nhiều (chụp thêm dữ liệu đúng điều kiện demo).
2. pi/tools/demo_check.py: chạy các kịch bản 5.1–5.2 ở chế độ sim (tự động) và chế độ thật (hướng dẫn người đặt hộp từng bước, chờ kết quả, so với kết quả mong đợi), in PASS/FAIL.
3. pi/tools/report_metrics.py: từ SQLite và file kết quả của P4, P9 xuất docs/metrics.md: latency trung bình/p95 từng bước, số kiện mỗi loại, tỉ lệ đúng khay, kết quả AI, benchmark Pi, kết quả OTA, kết quả phát hiện bất thường — dạng bảng dùng ngay cho slide.
4. Rà soát toàn repo theo đặc tả: lập bảng truy vết 75 mã chức năng → file hiện thực → test hoặc kịch bản chứng minh; đánh dấu mã nào chưa có.

Tiêu chí xong: bảng truy vết không còn mã nào trống; demo_check ở chế độ sim PASS toàn bộ.
```

## 8. Thứ tự code và checklist

Code P1 trước vì mọi module dùng chung giao thức; sau đó P2, P3, P4 làm song song, rồi mới đến ThingsBoard, bảo mật, OTA và phần mở rộng.

### 8.1 Thứ tự đề xuất

| Bước | Prompt | Cần có trước | Làm song song với | Bàn giao |
|---|---|---|---|---|
| 1 | P1 Giao thức + khung repo | — | Lắp cơ khí băng chuyền | protocol.yaml, codec Python + C, test vectors |
| 2 | P2 Firmware ESP32 | P1 | P3, P4 | Băng chạy, dừng, gạt đúng khi điều khiển bằng esp_bench.py |
| 3 | P3 sorter-core trên Pi | P1 | P2, P4 | Chạy sim trên laptop; chạy thật với ESP32 |
| 4 | P4 AI bao bì (Hùng đang phụ trách nhận diện) | Trạm chụp + đèn lắp xong, tool chụp của P3 | P2, P3 | Model ONNX, metrics, benchmark trên Pi |
| 5 | P5 ThingsBoard | P3 | P6 | Dashboard thời gian thực, RPC whitelist, X.509 |
| 6 | P6 Hardening + Secure Boot | P2, P3 | P5, P7 | Script hardening, audit PASS, SECURITY.md |
| 7 | P7 App OTA | P1 | P6 | App ký và phát hành, 3 bản demo tấn công |
| 8 | P8 OTA trên ESP32 | P2, P7 | P9 | 4 ca OTA đúng, rollback hoạt động |
| 9 | P9 Phát hiện bất thường | P3, P5 | P8 | Model IF, alarm Anomaly, đánh giá |
| 10 | P10 Tích hợp | Tất cả | — | RUNBOOK, demo_check PASS, metrics cho slide |

### 8.2 Cách làm việc với AI code

- Xuất tài liệu này ra Markdown; mỗi phiên dán Prompt 0 + phần 1–5 + đúng một prompt module (hoặc dùng CLAUDE.md + docs/SPEC.md như phần 6).
- Khi chuyển sang phiên mới, dán thêm cây thư mục hiện tại (lệnh tree -L 3) và các file giao diện liên quan: protocol.yaml, config.py, các interface TelemetrySink, Classifier.
- Chạy test ngay sau khi nhận code. Lỗi thì dán nguyên văn log lỗi và yêu cầu sửa đúng chỗ, không yêu cầu viết lại toàn bộ.
- Commit git sau mỗi module với thông điệp ghi mã prompt, ví dụ P3: orchestrator + qr_security.
- Đối chiếu "Tiêu chí xong" của prompt trước khi sang module sau.

### 8.3 Checklist

- ☐ Chốt loại cảm biến, loại servo, kích thước hộp và chiều dài băng (mục 1.4).
- ☐ Kiểm tra chip revision ≥ v3.0 cho cả 2 board ESP32.
- ☐ P1 xong — test vectors pass cả Python và C.
- ☐ P2 xong — băng dừng/gạt đúng, E-stop vật lý ngắt được động cơ.
- ☐ P3 xong — chế độ sim và chạy thật đều đúng bảng 1.2.
- ☐ Chụp đủ ≥ 300 ảnh mỗi lớp tại trạm chụp thật.
- ☐ P4 xong — accuracy và recall lớp lỗi đạt mục tiêu 5.3.
- ☐ P5 xong — X.509 + TLS 8883, dashboard Admin/Viewer, 2FA.
- ☐ P6 xong — audit PASS; Secure Boot + Flash Encryption đã thử trên board dự phòng.
- ☐ P7 và P8 xong — đủ 4 ca OTA.
- ☐ P9 xong — alarm Anomaly khi chạy attack_sim.
- ☐ P10 xong — bảng truy vết đủ 75 mã, metrics.md cho slide.
- ☐ Diễn tập toàn bộ kịch bản 5.1–5.2 ít nhất 2 lần trên mô hình hoàn chỉnh.