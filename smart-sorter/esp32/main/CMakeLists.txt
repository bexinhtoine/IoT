# main — app_main.c + version.h (P2/P8)
idf_component_register(SRCS "app_main.c"
                       INCLUDE_DIRS "."
                       REQUIRES board motor servo sensor indicator estop link params fsm ota_secure protocol
                                nvs_flash driver esp_timer)
