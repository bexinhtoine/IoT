/* E-02 — Điều khiển động cơ băng chuyền qua L298N (PWM LEDC 1 kHz, 10 bit; chỉ một chiều). */
#ifndef MOTOR_H
#define MOTOR_H

#include <stdbool.h>
#include <stdint.h>
#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

/* Khởi tạo LEDC timer/kênh và chân IN1/IN2. Gọi một lần ở app_main. */
esp_err_t motor_init(void);

/* Chạy băng với tốc độ speed_pct (0–100 %), tăng tốc mềm 200 ms bằng esp_timer (không chặn).
 * speed_pct = 0 tương đương motor_stop(). Gọi lại khi đang chạy → ramp từ duty hiện tại sang duty mới. */
esp_err_t motor_start(uint8_t speed_pct);

/* Dừng tức thì (ngữ cảnh task): duty 0, IN1 = IN2 = 0, hủy ramp. */
void motor_stop(void);

/* Dừng tức thì từ ISR (E-07): tách LEDC khỏi chân PWM và kéo PWM/IN1/IN2 về 0 chỉ bằng ghi thanh ghi
 * (IRAM-safe, không gọi API có khóa). motor_start() sau đó sẽ tự gắn lại LEDC. */
void motor_stop_from_isr(void);

/* true khi đã lệnh chạy (kể cả đang ramp). */
bool motor_is_running(void);

/* Duty hiện tại 0–1023 (để log/test). */
uint32_t motor_get_duty(void);

#ifdef __cplusplus
}
#endif
#endif /* MOTOR_H */
