# Smart Logistics Sorter — firmware ESP32 (MODULE P2 + P8)
# Build: idf.py set-target esp32 && idf.py build && idf.py -p PORT flash monitor
cmake_minimum_required(VERSION 3.16)

# PROJECT_VER ghi vào esp_app_desc_t.version (app OTA P7 đọc từ đây, O-03).
# Giữ đồng bộ với FW_VERSION_NAME / FW_VERSION_CODE trong main/version.h.
set(PROJECT_VER "1.0.0")

include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(smart_sorter_esp32)
