# ESP32 — Secure Boot V2, Flash Encryption, tắt JTAG (S-08, E-14, O-15)

> **CẢNH BÁO — ĐỌC HẾT TRƯỚC KHI GÕ BẤT KỲ LỆNH NÀO**
>
> 1. **Secure Boot và Flash Encryption ghi eFuse. eFuse là VĨNH VIỄN, không có lệnh "tắt" hay "reset".** Board đã bật thì mãi mãi chỉ chạy firmware ký bằng đúng khóa đó.
> 2. **Làm trên BOARD DỰ PHÒNG trước.** Chỉ khi board dự phòng chạy ổn, nạp lại được, OTA được, mới làm trên board chính. (Checklist 8.3 của SPEC: "đã thử trên board dự phòng".)
> 3. **Sao lưu khóa ký `secure_boot_signing_key.pem` ở ÍT NHẤT 2 NƠI độc lập** (ví dụ: USB mã hóa của trưởng nhóm + kho mật khẩu/ổ cloud riêng tư có mã hóa). **Không commit vào git** (đã có trong `.gitignore`: `*.pem`). **Mất khóa = không bao giờ nạp được firmware mới lên board, kể cả OTA.** ESP32 (chip gốc) chỉ có **một** khe digest Secure Boot, không thu hồi hay đổi khóa được.
> 4. **Lần khởi động đầu tiên sau khi nạp bootloader có Secure Boot chính là lúc eFuse bị ghi** — cắm đúng board, đúng cổng, kiểm tra lại `sdkconfig` trước khi bấm.
> 5. **Không bật `CONFIG_SECURE_DISABLE_ROM_DL_MODE`** và **không chọn Flash Encryption chế độ Release** cho đồ án: cả hai làm mất khả năng nạp qua UART, board coi như "khóa chết" với mục đích học tập.
> 6. Mọi lệnh ghi eFuse (`espefuse.py burn_*`) trong tài liệu này đều là **thủ công, có hỏi xác nhận**; không có script nào trong repo tự chạy chúng.

Tài liệu áp dụng cho **ESP32 (chip gốc, ESP32-D0WD/ESP32-WROOM-32), ESP-IDF v5.x**. Các chỗ đánh dấu **[ĐỐI CHIẾU DOCS]** là tên tùy chọn/lệnh có thể khác giữa các bản IDF 5.0 → 5.3; mở đúng trang docs của bản đang dùng (`idf.py --version`) để đối chiếu: *Security → Secure Boot V2*, *Security → Flash Encryption*, *API Reference → NVS Encryption*.

---

## 0. Khái niệm trong 5 dòng

| Tính năng | Bảo vệ gì | Cơ chế | eFuse liên quan (ESP32) |
|---|---|---|---|
| **Secure Boot V2** | Chỉ chạy bootloader + app **được ký** bằng khóa của nhóm (chống nạp firmware lạ, chống sửa firmware) | RSA-PSS 3072; ROM kiểm bootloader, bootloader kiểm app; public key digest lưu eFuse BLOCK2 | `ABS_DONE_1`, `BLOCK2` (digest) |
| **Flash Encryption** | Đọc flash ra không thấy mã/khóa/bí mật (chống dump firmware, trộm Wi-Fi password, CA, pubkey) | AES-256 (XTS-like) với khóa tạo ngẫu nhiên trên chip, lưu eFuse BLOCK1, đọc-cấm với phần mềm | `FLASH_CRYPT_CNT`, `FLASH_CRYPT_CONFIG`, `BLOCK1` (khóa) |
| **NVS Encryption** | Mã hóa phân vùng NVS (tham số E-11, thông tin Wi-Fi/OTA) vì Flash Encryption **không** áp dụng cho NVS | AES-XTS; khóa NVS lưu trong phân vùng `nvs_keys` được Flash Encryption bảo vệ | — |
| **Tắt JTAG** | Chặn gắn debugger đọc RAM/khóa lúc chạy | eFuse | `JTAG_DISABLE` |
| **OTA (P8)** | Bản OTA cũng phải được ký bằng khóa Secure Boot, ngoài chữ ký ECDSA của manifest (O-15) | `esp_ota_end()` xác minh chữ ký RSA của ảnh mới trước khi đặt boot | — |

Hai lớp chữ ký trên OTA là **khác nhau và bổ sung nhau**: khóa Secure Boot (RSA-3072, trong eFuse) chứng minh "ảnh này do nhóm build"; khóa ECDSA P-256 của app OTA (O-02, nhúng `ota_pubkey.h`) chứng minh "manifest này do release manager phát hành, đúng hash, đúng version". Mất một trong hai thì OTA thất bại.

---

## 1. Chuẩn bị

### 1.1 Môi trường

```bash
. $IDF_PATH/export.sh          # Linux/macOS  (Windows: export.bat / Terminal ESP-IDF)
idf.py --version               # ghi lại bản IDF, ví dụ v5.2.2 — dùng đúng trang docs của bản này
which esptool.py espsecure.py espefuse.py   # cả 3 đều đi kèm IDF (gói esptool)
```

Nếu `espsecure.py`/`espefuse.py` không có trên PATH: dùng `python -m espsecure` / `python -m espefuse` **[ĐỐI CHIẾU DOCS]**.

Đặt `PORT` một lần: `export PORT=/dev/ttyUSB0` (Windows: `COM5`).

### 1.2 Kiểm tra chip revision ≥ v3.0 (BẮT BUỘC)

Secure Boot **V2** chỉ có trên ESP32 **ECO3 trở lên (chip revision v3.0 / "rev 300")**. Board cũ hơn chỉ có Secure Boot V1 (không khuyến nghị) — không dùng board đó cho S-08.

```bash
esptool.py --port $PORT chip_id
# Mong đợi dòng:  Chip is ESP32-D0WD-V3 (revision v3.0)   hoặc  (revision v3.1)
# Board cũ:       Chip is ESP32-D0WDQ6 (revision v1.0)    → KHÔNG dùng

espefuse.py --port $PORT summary | grep -iE 'chip_ver|chip_rev|wafer|revision'
# [ĐỐI CHIẾU DOCS] tùy bản esptool, in dạng "CHIP_VER_REV1/REV2" hoặc "Chip revision: v3.0"
```

Làm cho **cả 2 board** (chính + dự phòng) và ghi kết quả vào checklist 8.3 của SPEC.

Khi chưa bật gì, `espefuse.py summary` phải cho: `ABS_DONE_0 = 0`, `ABS_DONE_1 = 0`, `FLASH_CRYPT_CNT = 0`, `JTAG_DISABLE = 0`, `BLOCK1`/`BLOCK2` toàn 0. Chụp màn hình lại làm ảnh "TRƯỚC" cho slide.

### 1.3 Sinh khóa ký Secure Boot V2 (RSA-3072) — làm MỘT lần cho cả dự án

```bash
cd esp32/
espsecure.py generate_signing_key --version 2 --scheme rsa3072 secure_boot_signing_key.pem
# [ĐỐI CHIẾU DOCS] trên IDF 5.x, --scheme rsa3072 là mặc định của --version 2; ESP32 gốc CHỈ hỗ trợ RSA-3072
chmod 600 secure_boot_signing_key.pem

# Xem digest public key (fingerprint) — ghi vào biên bản nhóm
espsecure.py extract_public_key --version 2 --keyfile secure_boot_signing_key.pem secure_boot_pubkey.bin
espsecure.py digest_sbv2_public_key --keyfile secure_boot_signing_key.pem --output pubkey_digest.bin
xxd pubkey_digest.bin
```

**Sao lưu ngay** `secure_boot_signing_key.pem` ra 2 nơi (xem cảnh báo đầu file). Thử mở lại bản sao lưu trước khi tiếp tục. Kiểm tra `git status` không thấy file `.pem`.

---

## 2. Cấu hình menuconfig (`idf.py menuconfig`)

Chỉ cấu hình trên nhánh/bản build riêng cho board bảo mật; giữ bản build thường để phát triển hằng ngày (ví dụ `sdkconfig.defaults` thường và `sdkconfig.secure.defaults`, build bằng `idf.py -DSDKCONFIG_DEFAULTS="sdkconfig.defaults;sdkconfig.secure.defaults" build`).

### 2.1 Security features → Secure Boot

```
Security features  --->
  [*] Enable hardware Secure Boot in bootloader            CONFIG_SECURE_BOOT=y
      Select secure boot version (Enable Secure Boot version 2)  CONFIG_SECURE_BOOT_V2_ENABLED=y
      App Signing Scheme (RSA)                              CONFIG_SECURE_SIGNED_APPS_RSA_SCHEME=y   [ĐỐI CHIẾU DOCS]
  [*] Sign binaries during build                            CONFIG_SECURE_BOOT_BUILD_SIGNED_BINARIES=y
      (secure_boot_signing_key.pem) Secure boot private signing key   CONFIG_SECURE_BOOT_SIGNING_KEY="secure_boot_signing_key.pem"
  [ ] Allow JTAG Debugging                                  CONFIG_SECURE_BOOT_ALLOW_JTAG  → ĐỂ TRỐNG (tức là JTAG sẽ bị tắt)  [ĐỐI CHIẾU DOCS]
  [ ] Allow app partition length and SHA-256 to be unchecked → ĐỂ TRỐNG
  [ ] Leave UART bootloader encryption enabled ... → ĐỂ TRỐNG (giữ mặc định bảo mật)
  [ ] Permanently disable ROM Download Mode (CONFIG_SECURE_DISABLE_ROM_DL_MODE) → TUYỆT ĐỐI ĐỂ TRỐNG
```

Đường dẫn khóa tương đối với thư mục dự án `esp32/`. Có thể dùng `CONFIG_SECURE_BOOT_SIGNING_KEY` trỏ ra ngoài repo (ví dụ `/home/<user>/keys/...`) để chắc chắn không commit nhầm.

Vì Secure Boot làm bootloader lớn hơn, build có thể báo "bootloader binary size exceeds 0x7000": chuyển `Partition Table → Offset of partition table` từ `0x8000` lên `0x9000` hoặc `0xA000` (`CONFIG_PARTITION_TABLE_OFFSET`) và đảm bảo `partitions.csv` có app partition căn chỉnh 0x10000 **[ĐỐI CHIẾU DOCS]**.

### 2.2 Security features → Flash Encryption

```
Security features  --->
  [*] Enable flash encryption on boot (READ DOCS FIRST)     CONFIG_SECURE_FLASH_ENC_ENABLED=y
      Enable usage mode (Development (NOT SECURE))          CONFIG_SECURE_FLASH_ENCRYPTION_MODE_DEVELOPMENT=y
  [ ] Check Flash Encryption enabled on app startup         (tùy chọn, bật cũng được)
```

**Development vs Release:**

| | Development (chọn cho đồ án) | Release |
|---|---|---|
| Flash đã mã hóa, đọc ra là rác | Có | Có |
| Nạp lại qua UART bằng `esptool.py write_flash --encrypt` / `idf.py encrypted-flash` | **Được** (bootloader ROM còn cho phép mã hóa khi nạp) | **Không**: eFuse `DISABLE_DL_ENCRYPT`, `DISABLE_DL_DECRYPT`, `DISABLE_DL_CACHE` bị ghi; chỉ còn OTA |
| Nạp lại **bản plaintext** (không `--encrypt`) rồi để bootloader mã hóa lại | Được thêm **tối đa 3 lần** (`FLASH_CRYPT_CNT` 7 bit, mỗi lần lật 1 bit; lẻ = mã hóa, chẵn = tắt) | Không |
| Tắt flash encryption tạm (`espefuse.py burn_efuse FLASH_CRYPT_CNT`) | Được, giới hạn như trên | Không |
| Dùng cho | Phát triển/demo — vẫn chứng minh được "đọc flash ra rác" | Sản phẩm bán ra |

Nhóm chọn **Development** vì: còn nạp lại được khi code OTA (P8) còn sửa; vẫn chứng minh được S-08 khi demo. Slide phải ghi trung thực giới hạn này (xem `docs/security_slide.md`).

### 2.3 NVS Encryption (bảo vệ tham số và mật khẩu Wi-Fi trong NVS)

Flash Encryption **không mã hóa** phân vùng kiểu `nvs` (định dạng NVS không tương thích), nên bật thêm:

```
Component config → NVS  --->
  [*] Enable NVS encryption                                 CONFIG_NVS_ENCRYPTION=y
      NVS encryption: Key protection scheme (Flash encryption-based)   CONFIG_NVS_SEC_KEY_PROTECT_USING_FLASH_ENC=y  [ĐỐI CHIẾU DOCS: tùy chọn này chỉ có từ IDF 5.1+, bản cũ mặc định là scheme này]
```

`partitions.csv` phải có phân vùng khóa NVS (được flash-encryption mã hóa nhờ cờ `encrypted`):

```
# Name,     Type, SubType,  Offset,  Size,    Flags
nvs,        data, nvs,      ,        0x6000,
nvs_key,    data, nvs_keys, ,        0x1000,  encrypted
otadata,    data, ota,      ,        0x2000,  encrypted
phy_init,   data, phy,      ,        0x1000,  encrypted
ota_0,      app,  ota_0,    0x20000, 0x1A0000,
ota_1,      app,  ota_1,    ,        0x1A0000,
```

(App partition luôn được mã hóa, không cần cờ. Offset/size cụ thể theo `esp32/partitions.csv` của P2/P8; bảng trên chỉ minh họa cờ và phân vùng `nvs_keys`.) Khi `nvs_flash_init()` chạy lần đầu với NVS encryption, nó tự sinh khóa vào `nvs_key` nếu trống. Mật khẩu Wi-Fi do `esp_wifi_set_config()` lưu vào NVS (`nvs.net80211`) cũng nằm trong vùng mã hóa này.

### 2.4 Tắt JTAG

Có hai đường, chọn **một**, ghi rõ trong biên bản:

- **(Khuyến nghị) Để Secure Boot tắt JTAG tự động:** giữ `CONFIG_SECURE_BOOT_ALLOW_JTAG` **không chọn** (mục 2.1). Bootloader sẽ ghi eFuse `JTAG_DISABLE` trong lần khởi động đầu tiên cùng lúc với bật Secure Boot. **[ĐỐI CHIẾU DOCS]**: xác nhận trong trang Secure Boot V2 của bản IDF đang dùng, mục "Secure Boot Process" / "Restrictions after Secure Boot is enabled" rằng JTAG bị tắt khi không có ALLOW_JTAG.
- **(Thủ công) Ghi eFuse bằng tay** — chỉ khi kiểm tra `summary` thấy `JTAG_DISABLE = 0` sau khi đã bật Secure Boot:

  ```bash
  # VĨNH VIỄN. Lệnh sẽ hỏi gõ BURN để xác nhận.
  espefuse.py --port $PORT burn_efuse JTAG_DISABLE
  ```

Lưu ý: tắt JTAG không ảnh hưởng nạp/monitor qua UART0 (cổng USB của DevKit) — đó là ROM download mode, không phải JTAG.

---

## 3. Build và nạp LẦN ĐẦU (trên board dự phòng trước)

Trình tự quan trọng vì ESP32 gốc **không** để `idf.py flash` nạp bootloader khi Secure Boot bật — phải nạp bootloader thủ công đúng một lần.

```bash
cd esp32/
idf.py set-target esp32
idf.py menuconfig                      # cấu hình như mục 2, lưu
idf.py build                           # app được ký tự động (Sign binaries during build)

# 1) Build và nạp BOOTLOADER thủ công (chỉ lần đầu)
idf.py bootloader
# IDF in ra đúng lệnh cần dùng; dạng chuẩn trên ESP32:
esptool.py --port $PORT --baud 460800 --chip esp32 --before default_reset --after no_reset \
  write_flash --flash_mode dio --flash_freq 40m --flash_size keep 0x1000 build/bootloader/bootloader.bin
# [ĐỐI CHIẾU DOCS] trên IDF 5.x có tùy chọn CONFIG_SECURE_BOOT_FLASH_BOOTLOADER_DEFAULT=y để idf.py flash
# tự nạp bootloader; nhóm KHÔNG bật để tránh nạp nhầm bootloader bảo mật lên board thường.

# 2) Nạp partition table + app (vẫn plaintext — bootloader sẽ tự mã hóa ở lần boot đầu)
idf.py -p $PORT flash

# 3) Theo dõi lần boot đầu: bootloader ghi eFuse, bật Secure Boot, rồi mã hóa flash (vài chục giây, ĐỪNG rút điện)
idf.py -p $PORT monitor
```

Log mong đợi ở lần boot đầu (rút gọn, thứ tự có thể khác theo bản IDF):

```
secure_boot_v2: Secure boot V2 is not enabled yet and eFuse digest keys are not set
secure_boot_v2: Verifying with RSA-PSS...
secure_boot_v2: Signature verified successfully!
secure_boot_v2: Secure boot permanently enabled
flash_encrypt: Generating new flash encryption key...
flash_encrypt: bootloader encrypted successfully
flash_encrypt: partition table encrypted and loaded successfully
flash_encrypt: Flash encryption completed
...
I (xxx) boot: Loaded app from partition at offset 0x20000
```

Sau đó board tự reset và chạy app bình thường. Nếu log hiện `secure boot verification failed` ngay lần đầu: bootloader và app ký bằng hai khóa khác nhau (build cũ còn sót) → `idf.py fullclean && idf.py build` rồi làm lại **nếu eFuse chưa ghi** (xem bước 4); nếu eFuse đã ghi với khóa sai thì board đó chỉ còn chạy với khóa sai — đây là lý do phải thử trên board dự phòng.

```bash
# 4) Kiểm tra eFuse "SAU" — chụp màn hình cho slide
espefuse.py --port $PORT summary
```

Mong đợi (ESP32): `ABS_DONE_1 = 1` (Secure Boot V2 bật), `BLOCK2` có digest (không còn toàn 0), `FLASH_CRYPT_CNT` lẻ (ví dụ `= 1` hoặc hiển thị "Flash encryption: enabled"), `FLASH_CRYPT_CONFIG = 0xF`, `BLOCK1` hiển thị `??` (khóa bị đọc-cấm), `JTAG_DISABLE = 1`, `DISABLE_DL_ENCRYPT = 0` (vì Development). **[ĐỐI CHIẾU DOCS]** tên trường có thể hiển thị khác chút giữa các bản esptool (ví dụ `FLASH_CRYPT_CNT` vs mục "Flash encryption").

Thử nạp lại một lần (mục 4) và thử OTA (mục 5) **trên board dự phòng** trước khi làm board chính.

---

## 4. Nạp lại về sau (flash đã mã hóa, Development mode)

Mỗi lần sửa code:

```bash
idf.py build
idf.py -p $PORT encrypted-flash           # nạp app + partition table, mã hóa khi nạp
# hoặc chỉ app:
idf.py -p $PORT encrypted-app-flash
# tương đương esptool:
esptool.py --port $PORT write_flash --encrypt 0x20000 build/sorter.bin
idf.py -p $PORT monitor
```

**Không** dùng `idf.py flash` thường nữa: nó nạp plaintext, bootloader thấy dữ liệu không giải mã được → không boot, và nếu làm nhiều lần sẽ tiêu `FLASH_CRYPT_CNT`. Nếu lỡ nạp plaintext: nạp lại bằng `encrypted-flash` là xong (chưa mất gì), hoặc để bootloader mã hóa lại (tốn 1 lần của 3 lần còn lại).

Bootloader **không** cần nạp lại trừ khi đổi cấu hình bootloader; khi đó dùng `esptool.py write_flash --encrypt 0x1000 build/bootloader/bootloader.bin` (bootloader mới vẫn phải ký bằng đúng khóa).

Firmware `.bin` giao cho app OTA (P7) chính là `build/sorter.bin` sau `idf.py build` — đã chứa khối chữ ký RSA ở cuối; **không** nén, không chỉnh sửa byte nào (sửa 1 byte là vô hiệu cả chữ ký Secure Boot lẫn hash manifest — chính là kịch bản 6b).

---

## 5. OTA (P8) với Secure Boot và Flash Encryption

- Ảnh OTA phải được ký bằng **khóa Secure Boot** (O-15). `idf.py build` với *Sign binaries during build* đã làm việc này. Nếu build trên máy không có khóa (CI, máy khác), ký sau:

  ```bash
  espsecure.py sign_data --version 2 --keyfile secure_boot_signing_key.pem --output sorter_signed.bin build/sorter.bin
  espsecure.py verify_signature --version 2 --keyfile secure_boot_signing_key.pem sorter_signed.bin   # kiểm tra lại
  ```

- Trên thiết bị, khi Secure Boot đã bật, `esp_ota_end()` / `esp_ota_set_boot_partition()` **tự động** xác minh chữ ký Secure Boot V2 của ảnh mới — ảnh không ký hoặc ký sai → `ESP_ERR_OTA_VALIDATE_FAILED` → firmware P8 báo `REJECTED` với reason `SIGNATURE_INVALID` (mã 1 ở 4.5) **[ĐỐI CHIẾU DOCS]** cho tên mã lỗi. (Tùy chọn `CONFIG_SECURE_SIGNED_ON_UPDATE_NO_SECURE_BOOT` chỉ dành cho board **không** bật Secure Boot mà vẫn muốn kiểm chữ ký lúc OTA — có thể dùng cho board phát triển thường để test P8 trước khi ghi eFuse.)
- `esp_ota_write()` tự mã hóa khi ghi vào phân vùng app (flash encryption trong suốt với `esp_partition_write`), nên P8 không cần làm gì thêm.
- App rollback (O-13, `CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE=y`) hoạt động bình thường cùng Secure Boot; anti-rollback bằng eFuse (`CONFIG_BOOTLOADER_APP_ANTI_ROLLBACK`) là tính năng **khác**, ghi eFuse secure version mỗi lần nâng — nhóm **không bật** (O-11 chống rollback bằng `version_code` trong NVS là đủ và đảo ngược được).
- Public key ECDSA của app OTA (`ota_pubkey.h`) và CA của máy chủ OTA nằm trong app → đã được Flash Encryption bảo vệ khỏi dump, và Secure Boot chống sửa.

---

## 6. Chứng minh khi demo (chụp log cho slide S-08)

**Chứng minh 1 — Secure Boot từ chối firmware không ký:**

```bash
# Trên máy dev: build một bản KHÔNG ký (tắt Sign binaries, hoặc build ở thư mục khác không có khóa)
idf.py -B build_unsigned -DSDKCONFIG_DEFAULTS="sdkconfig.defaults" build     # bản thường, không secure boot
# Nạp bản không ký lên board đã bật Secure Boot (phải --encrypt để bootloader đọc được và đi tới bước kiểm chữ ký)
esptool.py --port $PORT write_flash --encrypt 0x20000 build_unsigned/sorter.bin
idf.py -p $PORT monitor
```

Log mong đợi: `secure_boot_v2: No signature block found` hoặc `Signature verification failed` / `image verification failed` → `boot: No bootable app partitions in the partition table` (hoặc bootloader quay về app cũ nếu còn ở phân vùng kia). Chụp dòng này. Sau demo nạp lại bản ký: `idf.py encrypted-app-flash`.

**Chứng minh 2 — Flash Encryption: đọc flash ra chỉ là dữ liệu mã hóa:**

```bash
esptool.py --port $PORT read_flash 0x20000 0x100 dump_app.bin
xxd dump_app.bin | head -8
# Board thường: byte đầu E9 (magic ảnh ESP) và chuỗi đọc được. Board đã mã hóa: toàn byte ngẫu nhiên, không có E9.
# Để đối chiếu, đọc cùng vùng trên board thường rồi đặt 2 ảnh cạnh nhau trên slide.
strings dump_app.bin | head     # board mã hóa: không thấy chuỗi "sorter", "tb.local", v.v.
```

**Chứng minh 3 — eFuse:** `espefuse.py --port $PORT summary` ảnh "TRƯỚC" (mục 1.2) và "SAU" (mục 3) cạnh nhau: `ABS_DONE_1 0→1`, `FLASH_CRYPT_CNT 0→1`, `JTAG_DISABLE 0→1`, `BLOCK1 00..00 → ??`.

**Chứng minh 4 — OTA không ký / sửa 1 byte** dùng app OTA (O-07): ESP32 báo `SIGNATURE_INVALID` hoặc `HASH_MISMATCH` trên dashboard (`ota_stage = REJECTED`).

---

## 7. Sự cố thường gặp

| Hiện tượng | Nguyên nhân | Xử lý |
|---|---|---|
| `bootloader binary size exceeds` | Bootloader to hơn 28 KB vì SB + FE | Tăng `CONFIG_PARTITION_TABLE_OFFSET` (0x9000/0xA000), build lại |
| Boot loop `flash read err, 1000` / `invalid header: 0x...` | Nạp plaintext lên flash đã mã hóa | `idf.py encrypted-flash` |
| `secure boot verification failed` sau khi sửa code | Build không ký (mất đường dẫn khóa, sai sdkconfig) | Kiểm tra `CONFIG_SECURE_BOOT_SIGNING_KEY`, `idf.py fullclean`, build lại |
| `idf.py flash` nạp xong board im lặng, không log | `--after no_reset` hoặc board đang ở download mode | Nhấn RST |
| Không nạp được gì nữa, `esptool` báo lỗi mã hóa | Đã lỡ chọn Release hoặc `DISABLE_DL_ENCRYPT = 1` | Không khôi phục được; dùng OTA hoặc đổi board |
| OTA luôn `SIGNATURE_INVALID` | `.bin` giao cho app OTA là bản chưa ký / đã bị nén | Kiểm `espsecure.py verify_signature --version 2 --keyfile ... file.bin` |
| Monitor rác sau khi tắt JTAG | Không liên quan (JTAG ≠ UART0); kiểm tra baud 115200 | — |

---

## 8. Checklist (dán vào biên bản nhóm)

- [ ] `idf.py --version` ghi lại: ______; đã mở đúng trang docs Secure Boot V2 / Flash Encryption của bản này
- [ ] Cả 2 board: `esptool.py chip_id` → revision ≥ v3.0 (ghi: board A ____, board B ____)
- [ ] `espefuse.py summary` "TRƯỚC" đã chụp cho cả 2 board
- [ ] Sinh `secure_boot_signing_key.pem` (RSA-3072, `--version 2`), `chmod 600`, **sao lưu 2 nơi**, đã thử mở bản sao lưu
- [ ] `git status` không có `.pem`; `.gitignore` có `*.pem`
- [ ] menuconfig: Secure Boot V2 + Sign binaries + đường dẫn khóa; Flash Encryption **Development**; NVS encryption + phân vùng `nvs_keys` `encrypted`; **không** chọn Allow JTAG; **không** chọn Disable ROM DL mode; **không** chọn Release
- [ ] Board DỰ PHÒNG: `idf.py bootloader` → `esptool.py write_flash 0x1000 bootloader.bin` → `idf.py flash` → `monitor` thấy "Secure boot permanently enabled" + "Flash encryption completed"
- [ ] Board DỰ PHÒNG: `espefuse.py summary` "SAU": `ABS_DONE_1=1`, `FLASH_CRYPT_CNT` lẻ, `JTAG_DISABLE=1`, `DISABLE_DL_ENCRYPT=0`
- [ ] Board DỰ PHÒNG: `idf.py encrypted-flash` nạp lại được; app chạy; UART với Pi (P2) hoạt động
- [ ] Board DỰ PHÒNG: OTA bản ký hợp lệ SUCCESS; bản không ký / sửa 1 byte REJECTED (P7 + P8)
- [ ] Board DỰ PHÒNG: Chứng minh 1 và 2 (mục 6) chụp log xong
- [ ] Chỉ sau khi tất cả ở trên đạt: lặp lại cho board CHÍNH
- [ ] `docs/security_slide.md` mục S-08 ghi đúng "Flash Encryption chế độ Development" và giới hạn của nó
