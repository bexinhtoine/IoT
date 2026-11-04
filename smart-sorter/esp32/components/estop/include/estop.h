/* E-07 — Nút dừng khẩn cấp vật lý: GPIO39, tiếp điểm NC + pull-up ngoài; LOW = bình thường, HIGH = dừng. */
#ifndef ESTOP_H
#define ESTOP_H

#include <stdbool.h>
#include "esp_err.h"
#include "freertos/FreeRTOS.h"

#ifdef __cplusplus
extern "C" {
#endif

/* Gọi TỪ ISR sau khi động cơ đã bị tắt; pressed = mức hiện tại (true = HIGH = dừng). */
typedef void (*estop_cb_t)(bool pressed, BaseType_t *hp_task_woken);

esp_err_t estop_init(estop_cb_t cb);

/* true khi nút đang bấm hoặc dây đứt (GPIO39 = HIGH). */
bool estop_is_pressed(void);

#ifdef __cplusplus
}
#endif
#endif /* ESTOP_H */
