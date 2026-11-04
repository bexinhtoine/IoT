/* E-03 — Cảm biến phát hiện kiện hàng. Loại chọn bằng Kconfig:
 *   CONFIG_SORTER_SENSOR_TCRT5000 (mặc định): GPIO34 ngắt + chống dội 20 ms (esp_timer one-shot).
 *   CONFIG_SORTER_SENSOR_HCSR04: task đo mỗi 30 ms, xác nhận khi 2 lần liên tiếp < hcsr04_threshold (mm).
 * Khi phát hiện, gọi callback (ngữ cảnh task: task esp_timer hoặc task đo) — fsm đẩy EV_SENSOR vào queue.
 */
#ifndef SENSOR_H
#define SENSOR_H

#include <stdbool.h>
#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef void (*sensor_cb_t)(void);

/* Khởi tạo GPIO/ngắt/task. cb có thể NULL (chế độ kiểm tra đấu dây). Mặc định disabled. */
esp_err_t sensor_init(sensor_cb_t cb);

/* Bật/tắt phát hiện (tắt trong DETECTED/SORTING và trong t_rearm). */
void sensor_set_enabled(bool en);
bool sensor_is_enabled(void);

/* Giá trị thô để log/test: TCRT5000 → mức logic DO (0/1); HC-SR04 → khoảng cách mm gần nhất (-1 = timeout). */
int sensor_read_raw(void);

/* Tên cảm biến đang dùng. */
const char *sensor_name(void);

#ifdef __cplusplus
}
#endif
#endif /* SENSOR_H */
