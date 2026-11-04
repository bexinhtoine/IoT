/* E-03: HC-SR04, 70 ms/mau; 2 mau 20..300 mm de kich hoat, latch cho vat roi. */
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

/* Khoang cach mm; -1 = khong co echo, -2 = ECHO stuck HIGH/qua han. */
int sensor_read_raw(void);

/* Tên cảm biến đang dùng. */
const char *sensor_name(void);

#ifdef __cplusplus
}
#endif
#endif /* SENSOR_H */
