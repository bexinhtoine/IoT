# E-03: HC-SR04, TRIG GPIO13 / ECHO GPIO35
idf_component_register(SRCS "sensor.c"
                       INCLUDE_DIRS "include"
                       REQUIRES board driver esp_timer)
