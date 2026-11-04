# sensor — phát hiện kiện hàng: TCRT5000 (ngắt) hoặc HC-SR04 (task đo), chọn bằng Kconfig (E-03)
idf_component_register(SRCS "sensor.c"
                       INCLUDE_DIRS "include"
                       REQUIRES board driver esp_timer params)
