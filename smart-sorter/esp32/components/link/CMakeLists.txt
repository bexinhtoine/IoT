# link — UART2 + protocol: ACK/NACK, gửi lại, chống lặp SEQ, STATUS 1 s, watchdog liên kết (E-09, E-10, E-12)
idf_component_register(SRCS "link.c"
                       INCLUDE_DIRS "include"
                       REQUIRES board protocol driver esp_timer)
