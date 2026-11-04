/* E-12 / O-11 — Phiên bản firmware.
 *
 * FW_VERSION_CODE: số nguyên tăng dần, dùng trong STATUS.fw_version_code (E-12) và chống rollback OTA (O-11).
 * FW_VERSION_NAME: chuỗi hiển thị; phải trùng PROJECT_VER trong esp32/CMakeLists.txt (esp_app_desc.version)
 * để app OTA (P7, O-03) đọc đúng phiên bản từ file .bin.
 *
 * Quy ước: mỗi lần phát hành OTA tăng FW_VERSION_CODE lên ít nhất 1 (manifest.version_code phải lớn hơn
 * bản đang chạy, xem ota_secure.c).
 */
#ifndef VERSION_H
#define VERSION_H

#define FW_VERSION_CODE 1u
#define FW_VERSION_NAME "1.0.0"

#endif /* VERSION_H */
