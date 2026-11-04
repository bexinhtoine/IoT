# Nạp cấu hình Wi-Fi / URL OTA vào NVS (O-08)

Firmware **không** nhận URL/SSID qua UART; chúng nằm trong NVS namespace `ota_cfg` và được nạp một lần qua USB.

## 1. Tạo file CSV

```
cp wifi_ota.csv.example wifi_ota.csv
# sửa ssid, pass, url (bắt buộc https://, ví dụ https://192.168.1.10:8443/ota)
```

`wifi_ota.csv` chứa mật khẩu Wi-Fi → không commit (chỉ commit `*.example`, quy tắc CLAUDE.md #2).

## 2. Sinh ảnh phân vùng NVS và ghi vào flash

Phân vùng `nvs` trong `esp32/partitions.csv`: offset `0x9000`, kích thước `0x6000`.

```
# trong môi trường ESP-IDF (đã . export.sh / export.bat)
python $IDF_PATH/components/nvs_flash/nvs_partition_generator/nvs_partition_gen.py \
       generate esp32/nvs/wifi_ota.csv build/nvs_wifi_ota.bin 0x6000

esptool.py --chip esp32 -p /dev/ttyUSB0 write_flash 0x9000 build/nvs_wifi_ota.bin
```

Hoặc dùng script: `bash esp32/tools/provision_nvs.sh esp32/nvs/wifi_ota.csv /dev/ttyUSB0`.

Lưu ý:
- Ghi ảnh này **thay toàn bộ** phân vùng NVS: các tham số E-11 (`params`) sẽ về mặc định (firmware tự tạo lại),
  `max_ver` về giá trị trong CSV. Muốn giữ `max_ver` chống rollback thì sửa giá trị trong CSV trước khi ghi.
- Firmware tự ghi thêm `max_ver` (khi OTA thành công) và `rb_addr` (đã báo ROLLED_BACK) vào cùng namespace.
- Nếu sau này bật NVS encryption (P6) cần thêm phân vùng `nvs_keys` và dùng `--encrypt` của nvs_partition_gen —
  đối chiếu docs "NVS Partition Generator Utility" của phiên bản IDF.
