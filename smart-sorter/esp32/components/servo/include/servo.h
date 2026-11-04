/* E-04 — Servo gạt hàng A/B: LEDC 50 Hz, 14 bit; góc 0–180° → xung 500–2500 µs. */
#ifndef SERVO_H
#define SERVO_H

#include <stdint.h>
#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    SERVO_A = 0,
    SERVO_B = 1,
    SERVO_COUNT
} servo_id_t;

/* Khởi tạo LEDC timer 1 + 2 kênh; cả hai servo về 0°. */
esp_err_t servo_init(void);

/* Đặt góc 0–180° (giá trị lớn hơn bị chặn về 180). */
esp_err_t servo_set_angle(servo_id_t id, uint8_t deg);

/* Góc đã đặt gần nhất. */
uint8_t servo_get_angle(servo_id_t id);

#ifdef __cplusplus
}
#endif
#endif /* SERVO_H */
