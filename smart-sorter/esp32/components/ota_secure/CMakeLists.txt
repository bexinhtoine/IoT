# ota_secure — Secure OTA phía ESP32 (O-08 → O-15)
#
# CA của máy chủ OTA được nhúng bằng EMBED_TXTFILES (thêm byte NUL cuối → dùng được làm cert_pem).
# File thật: certs/ota_ca.pem (bị .gitignore chặn: *.pem và certs/). Nếu chưa có, dùng placeholder
# ota_ca.pem.example ở thư mục component để vẫn build được (OTA sẽ thất bại TLS_ERROR cho tới khi copy CA thật).
#
# Lưu ý: khối if/set phải đứng TRƯỚC idf_component_register (ESP-IDF chạy file này ở chế độ script
# để lấy REQUIRES và return() ngay sau idf_component_register).
if(EXISTS "${CMAKE_CURRENT_LIST_DIR}/certs/ota_ca.pem")
    set(OTA_CA_FILE "certs/ota_ca.pem")
    set(OTA_CA_SYM  "ota_ca_pem")
else()
    message(WARNING "ota_secure: chua co certs/ota_ca.pem — dung ota_ca.pem.example (placeholder). "
                    "Copy thingsboard/pki/out/ca.pem -> esp32/components/ota_secure/certs/ota_ca.pem")
    set(OTA_CA_FILE "ota_ca.pem.example")
    set(OTA_CA_SYM  "ota_ca_pem_example")
endif()

idf_component_register(SRCS "ota_secure.c"
                       INCLUDE_DIRS "include"
                       REQUIRES esp_wifi esp_netif esp_event esp_http_client esp-tls mbedtls json
                                app_update nvs_flash esp_timer link protocol
                       EMBED_TXTFILES "${OTA_CA_FILE}")

# Tên symbol do build system sinh theo tên file: _binary_<ten_file_thay_dau_cham_bang_gach_duoi>_start/_end
target_compile_definitions(${COMPONENT_LIB} PRIVATE
    OTA_CA_PEM_START=_binary_${OTA_CA_SYM}_start
    OTA_CA_PEM_END=_binary_${OTA_CA_SYM}_end)
