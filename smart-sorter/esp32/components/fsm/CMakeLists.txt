# fsm — máy trạng thái E-01..E-05, E-08, E-09 (phần xử lý), E-11 (áp dụng), O-08/O-14 (chuyển OTA)
idf_component_register(SRCS "fsm.c"
                       INCLUDE_DIRS "include"
                       REQUIRES protocol motor servo sensor indicator estop link params ota_secure esp_timer)
