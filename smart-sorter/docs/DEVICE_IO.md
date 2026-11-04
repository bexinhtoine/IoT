# Vận hành thiết bị: HC-SR04 → nhận diện hộp → QR → bao bì

Cập nhật theo yêu cầu: dùng HC-SR04, không dùng hồng ngoại TCRT5000. Raspberry Pi phân tích ảnh; servo chỉ gạt vật theo quyết định của Pi, không nhận ảnh hoặc chạy AI.

## Chu trình một vật

1. Máy được START, ESP32 chạy động cơ băng chuyền. Hai LED tắt.
2. HC-SR04 đo mỗi 70 ms. Hai mẫu liên tiếp trong **20–300 mm**, bao gồm đúng 30 cm, kích hoạt một sự kiện khi máy đang RUNNING.
3. ESP32 dừng động cơ, khóa phát hiện lặp, tăng pkg_seq và gửi EVT_PKG_DETECTED qua UART. Pi chờ camera.settle_ms cho vật ổn định rồi chụp. Hai LED vẫn tắt.
4. YOLO-World tìm hộp trong camera.roi:
   - Không thấy hộp: KHÔNG quét QR, KHÔNG chạy classifier bao bì; gửi SORT(REJECT, NOT_BOX). Đỏ nháy 3 nhịp rồi tắt.
   - Thấy nhiều hộp: không xác nhận được đúng một hộp, xử lý tương tự; ghi MULTIPLE_BOXES.
   - Đúng một hộp: lưu crop, tiến hành đọc/xác thực QR trên cùng khung ảnh.
5. QR không hợp lệ/không đọc được: loại, đỏ nháy 3 nhịp + 3 bíp rồi tắt, không chạy classifier bao bì. Khi không đọc được QR, Pi có thể chụp lại tới max_qr_frames; mỗi khung mới đều phải xác nhận hộp trước.
6. QR hợp lệ: classifier MobileNetV4 kiểm tra **crop của chính khung đã quét QR**, không detect lại:
   - Nguyên vẹn và QR kho A → servo A gạt; xanh sáng 300 ms rồi tắt.
   - Nguyên vẹn và QR kho B → servo B gạt; xanh sáng 300 ms rồi tắt.
   - Hộp lỗi hoặc không đủ tin cậy → giữ cả hai servo ở vị trí home, vật đi đường loại; đỏ sáng 300 ms rồi tắt.
7. ESP32 đặt servo phù hợp rồi chạy lại động cơ theo thời gian t_clear tương ứng; hết thời gian trả servo về home, gửi EVT_SORT_DONE. **Không phát lại nhịp đèn ở bước kết thúc.**
8. Cảm biến chỉ nhận vật tiếp theo khi hết t_rearm và latch đã được giải phóng: ba mẫu liên tiếp >330 mm hoặc không có echo. Dải 300–330 mm tránh chập chờn quanh ngưỡng. Việc tắt/bật phát hiện không tự xóa latch nên một vật đứng yên không bị xử lý lặp.

Không nhận diện được hộp không chứng minh vật chắc chắn không phải hộp: detector có thể bỏ sót. Chính sách là không cho qua khi chưa xác nhận được đúng một hộp. Các vật bị từ chối vẫn được đưa theo đường loại, không giữ băng dừng vô hạn.

## Bảng input, điều kiện và output

| Thiết bị | Input | Điều kiện hoạt động | Output / tác dụng |
|---|---|---|---|
| HC-SR04 | Xung TRIG 10 µs từ GPIO13; sóng phản xạ từ vật | Đo định kỳ 70 ms; 2 mẫu 2–30 cm và đang RUNNING, chưa latch | ECHO đưa qua chia áp vào GPIO35; ESP32 tính khoảng cách rồi phát sự kiện có vật. Không xác định đó có phải hộp hoặc bị hỏng không |
| Camera CSI | Yêu cầu chụp từ Pi khi nhận sự kiện và hết settle_ms | Băng đã dừng; có thể chụp lại khi QR chưa đọc được | Ảnh RGB chứa vật và QR |
| Raspberry Pi | Sự kiện UART, ảnh camera, model ONNX, khóa xác thực QR | Detector xác nhận đúng 1 hộp mới quét QR; QR hợp lệ mới phân loại bao bì | Lệnh SORT(target, reason, pkg_seq) gửi ESP32; log, ảnh bị loại và dữ liệu trạng thái |
| YOLO-World ONNX | Vùng camera.roi từ ảnh RGB | Chạy trước QR trên từng khung cần xử lý | Không hộp / nhiều hộp / một hộp và tọa độ crop; mặc định score >=0.35, lọc NMS, padding 10% |
| MobileNetV4 ONNX của nhóm | Crop hộp RGB, resize 256×256, /255; mean/std trong model | Chỉ sau khi đã xác nhận hộp và QR hợp lệ | p_normal, p_damage. p_damage >=0.5 → DEFECT; nếu chưa đạt mà p_normal >=0.6 → OK; còn lại UNCERTAIN và loại |
| ESP32 | Khoảng cách HC-SR04, lệnh Pi, E-stop, trạng thái liên kết | Điều phối BOOT/IDLE/RUNNING/DETECTED/SORTING/ESTOP/FAULT/OTA | GPIO điều khiển L298N, servo, LED/còi; UART phản hồi sự kiện/ACK/trạng thái |
| L298N | PWM GPIO25 vào ENA; GPIO26/27 vào IN1/IN2; nguồn động cơ | ESP32 cho chạy: IN1=1, IN2=0 và PWM; dừng: motor_stop | Công suất cấp động cơ. Không dùng GPIO cấp điện trực tiếp cho động cơ |
| Động cơ DC + băng chuyền | Điện từ đầu ra L298N | START hoặc sau quyết định SORT: chạy; phát hiện vật/STOP hợp lệ/E-stop/mất liên kết: dừng | Vận chuyển vật đến camera và tới cơ cấu gạt/khay loại |
| Servo A | Xung điều khiển GPIO18, nguồn servo 5 V riêng | SORT target A và reason OK, đúng pkg_seq, đang DETECTED | Chuyển từ góc home sang góc gạt A; hết t_clear_a hoặc khi dừng khẩn/lỗi → home |
| Servo B | Xung điều khiển GPIO19, nguồn servo 5 V riêng | SORT target B và reason OK, đúng pkg_seq, đang DETECTED | Chuyển từ góc home sang góc gạt B; hết t_clear_b hoặc khi dừng khẩn/lỗi → home |
| LED xanh | GPIO32 qua điện trở 220 Ω | Đã xác nhận hộp + QR hợp lệ + bao bì OK, chọn A/B | Sáng 300 ms một lần rồi tắt; không sáng trong lúc chờ hoặc đang kiểm tra |
| LED đỏ | GPIO33 qua điện trở 220 Ω | Không xác nhận hộp, QR lỗi, bao bì lỗi/không chắc, hoặc lỗi hệ thống | Pattern theo bảng bên dưới; báo theo từng vật một lần |
| Còi | GPIO23 qua transistor NPN và điện trở base | OK: bíp 100 ms; QR lỗi: 3 bíp; E-stop: kêu 1 s | Cảnh báo âm thanh hữu hạn. Hộp lỗi/không hộp chỉ báo đèn, không kêu mặc định |
| Nút E-stop | Tiếp điểm NC xuống GND; GPIO39 có pull-up 10 kΩ lên 3.3 V | Bình thường LOW; nhấn hoặc đứt dây → HIGH | Dừng động cơ, vô hiệu phát hiện, đưa servo home, trạng thái ESTOP; cần nhả nút rồi ESTOP_RESET mới về IDLE |
| UART Pi–ESP32 | TX Pi GPIO14→RX ESP32 GPIO16; TX ESP32 GPIO17→RX Pi GPIO15; GND chung | 115200 8N1, frame CRC, ACK/retry, pkg_seq | Sự kiện có vật → Pi; quyết định → ESP32; xác nhận và trạng thái → Pi |
| Nguồn và GND | Nguồn phù hợp từng thiết bị, chung GND tín hiệu | Pi/servo/động cơ được cấp dòng đủ; GPIO dùng logic 3.3 V | Cấp năng lượng và mốc tín hiệu. Không nối tùy ý các đường nguồn dương với nhau |
| ThingsBoard (nếu bật) | Telemetry/trạng thái từ Pi qua MQTT TLS | Kết nối và cấu hình được bật | Hiển thị số lượng A/B/loại, trạng thái, cảnh báo; không chạy AI thay cho Pi |

## LED theo yêu cầu mới

| Tình huống | Xanh | Đỏ | Thời điểm |
|---|---|---|---|
| IDLE, băng đang chờ vật, đang chụp/phân tích, OTA | Tắt | Tắt | Không dùng đèn nháy liên tục để báo chờ |
| Hộp hợp lệ, nguyên vẹn, nhận vào A/B | Sáng 300 ms rồi tắt | Tắt | Khi ESP32 chấp nhận lệnh SORT, chỉ một lần |
| Không xác nhận được hộp hoặc có nhiều hộp | Tắt | 200 ms sáng / 200 ms tắt, lặp đúng 3 nhịp rồi tắt | Khi nhận SORT với reason NOT_BOX=4 |
| Hộp lỗi hoặc classifier không đủ tin cậy | Tắt | Sáng 300 ms rồi tắt | Khi nhận SORT với reason DEFECT |
| QR không hợp lệ/không đọc được | Tắt | 3 nhịp 200 ms sáng / 200 ms tắt rồi tắt | Kèm 3 bíp ngắn; reason SECURITY |
| Lỗi suy luận hoặc quá hạn quyết định dẫn tới loại | Tắt | Sáng 300 ms rồi tắt | reason FAILSAFE |
| E-stop hoặc FAULT mất liên kết | Tắt | Sáng cố định đến khi reset lỗi | Đây là báo lỗi máy còn tồn tại, không phải nhịp báo phân loại; không nhấp nháy vô hạn |

Timer LED có tick 50 ms; thời điểm bật có thể trễ tối đa xấp xỉ một tick khi task không bị trì hoãn. Khi một trạng thái ưu tiên khác (ví dụ E-stop) tới, pattern trước được thay thế.

## Đấu HC-SR04 thay TCRT5000

- VCC → 5 V; GND → GND chung.
- TRIG → ESP32 GPIO13.
- ECHO → điện trở 1 kΩ → nút giữa nối GPIO35; từ nút giữa qua điện trở 2 kΩ xuống GND. Không nối ECHO 5 V trực tiếp vào GPIO35.
- GPIO34 bỏ trống; firmware đã bỏ lựa chọn TCRT5000 trong Kconfig.
- Hướng cảm biến vào vùng camera để khi phát hiện trong 30 cm, vật đang nằm trong khung hình sau khi băng dừng. Khi không có vật, nền/thành máy không được nằm trong vùng kích hoạt; nên khoảng cách nền >33 cm.
- Vùng dưới khoảng 2 cm không phải vùng đo tin cậy, không dùng đặt vật sát đầu cảm biến. Không có echo không chứng minh cảm biến còn kết nối; kiểm tra dây và đo thực tế khi nghiệm thu.
- Hai vật phải có khoảng trống để cảm biến thấy vùng trống và rearm; không đưa sát nhau thành một khối liên tục.

Chu kỳ 70 ms được chọn theo [datasheet HC-SR04](https://cdn.sparkfun.com/datasheets/Sensors/Proximity/HCSR04.pdf) khuyến nghị chu kỳ trên 60 ms và xung TRIG 10 µs.

## Cấu hình, tương thích và kiểm chứng

- Bản thật dùng `ai.detection.backend: yolo_world_onnx`. Orchestrator từ chối chạy UART thật nếu classifier không có bước xác nhận hộp. Chế độ dummy/ROI không detector chỉ dùng simulator hoặc công cụ thử ảnh riêng.
- Tham số UART `hcsr04_threshold` id 11 nay cố định min=max=default=300 mm. NVS cũ 80 mm bị kiểm tra miền và dùng mặc định 300; không cần xóa toàn bộ NVS.
- Mã reason **NOT_BOX=4** mới: phải cập nhật cả firmware ESP32 và phần Pi. Firmware cũ chưa hiểu mã này sẽ NACK; không kết hợp hai phiên bản để nghiệm thu đèn.
- Để tương thích bộ đếm/database, vật không được xác nhận hộp vẫn thuộc nhóm REJECT_DEFECT, nhưng `reason=NOT_BOX`, `ai_label=NO_BOX/MULTIPLE_BOXES`, `qr_status=SKIPPED_NO_BOX/SKIPPED_MULTIPLE_BOXES` cho biết rõ nguyên nhân. Không tiêu thụ mã QR trên vật chưa được xác nhận hộp.
- Logic C cảm biến và LED có test host trực tiếp bằng cùng header mà firmware sử dụng: `components/sensor/test/test_sensor_gate.c`, `components/indicator/test/test_indicator_pattern.c`. Đã compile và chạy bằng MSVC C11 trên máy phát triển.
- Test Pi kiểm tra thứ tự detect→QR→classify, không gọi QR/classifier khi không hộp/nhiều hộp, detector lỗi→fail-safe, mã UART mới và các luồng cũ.
- Chưa build toàn bộ ESP-IDF hoặc nạp board thật trong phiên này. Phải đo khoảng cách, vị trí dừng, hướng/góc servo và thời gian t_clear trên phần cứng. Các `.docx/.pdf` báo cáo đã xuất trước đó là bản cũ, chưa được tái xuất; tài liệu hiện hành là file này và SPEC.md đã cập nhật.

Kết quả kiểm thử phiên cập nhật: **230 passed, 4 skipped** cho bộ test Pi; hai chương trình kiểm thử C (sensor gate và LED pattern) đã biên dịch và chạy thành công. Các test PostgreSQL cần môi trường tích hợp riêng nên bị skip. Chưa push các thay đổi này lên Git.
