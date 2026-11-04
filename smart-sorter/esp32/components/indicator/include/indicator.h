/* E-06 — Đèn LED xanh/đỏ và còi: pattern chạy nền bằng esp_timer chu kỳ 50 ms, không vTaskDelay. */
#ifndef INDICATOR_H
#define INDICATOR_H

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

#include "indicator_modes.h"

esp_err_t indicator_init(void);
void indicator_set(indicator_mode_t mode);
indicator_mode_t indicator_get(void);

/* Điều khiển thô (chỉ cho chế độ kiểm tra đấu dây). */
void indicator_raw(int green, int red, int buzzer);

#ifdef __cplusplus
}
#endif
#endif /* INDICATOR_H */
