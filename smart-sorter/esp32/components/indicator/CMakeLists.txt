# indicator — LED xanh/đỏ + còi, pattern không chặn bằng esp_timer 50 ms (E-06)
idf_component_register(SRCS "indicator.c"
                       INCLUDE_DIRS "include"
                       REQUIRES board driver esp_timer)
