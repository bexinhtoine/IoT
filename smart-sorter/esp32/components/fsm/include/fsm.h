/* E-01 — Máy trạng thái ESP32. Một queue sự kiện, một task xử lý tuần tự. Bảng chuyển trạng thái ở đầu fsm.c. */
#ifndef FSM_H
#define FSM_H

#include <stdint.h>
#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

/* Khởi tạo: đăng ký callback với sensor/estop/link (gọi sensor_init, estop_init, link_init bên trong),
 * đặt servo về góc nghỉ, tạo task fsm. motor_init/servo_init/indicator_init/params_init phải gọi trước. */
esp_err_t fsm_init(uint32_t fw_version_code);

/* Trạng thái hiện tại (proto_state_t) — cho PONG/STATUS. */
uint8_t fsm_get_state(void);

/* Số kiện đã phát hiện kể từ khi boot (pkg_seq hiện tại). */
uint16_t fsm_get_pkg_count(void);

/* Callback cho ota_secure (O-14): đẩy EV_OTA vào queue → gửi EVT_OTA_STATUS, cập nhật trạng thái. */
void fsm_on_ota_status(uint8_t stage, uint8_t reason, uint8_t progress, uint32_t version_code);

#ifdef __cplusplus
}
#endif
#endif /* FSM_H */
