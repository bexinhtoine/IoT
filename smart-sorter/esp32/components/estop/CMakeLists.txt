# estop — nút dừng khẩn cấp vật lý GPIO39, ngắt cả hai cạnh (E-07)
idf_component_register(SRCS "estop.c"
                       INCLUDE_DIRS "include"
                       REQUIRES board driver motor)
