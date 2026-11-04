# motor — L298N qua LEDC (E-02)
idf_component_register(SRCS "motor.c"
                       INCLUDE_DIRS "include"
                       REQUIRES board driver esp_timer)
