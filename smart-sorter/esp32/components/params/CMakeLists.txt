# params — tham số hiệu chỉnh trong NVS, whitelist/miền giá trị từ protocol_gen.h (E-11)
idf_component_register(SRCS "params.c"
                       INCLUDE_DIRS "include"
                       REQUIRES protocol nvs_flash)
