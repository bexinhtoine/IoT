/* E-11 — Tham số hiệu chỉnh: RAM + NVS (namespace "params"), id/miền/mặc định lấy từ PROTO_PARAMS. */
#ifndef PARAMS_H
#define PARAMS_H

#include <stdint.h>
#include "esp_err.h"
#include "protocol_gen.h"

#ifdef __cplusplus
extern "C" {
#endif

/* Đọc toàn bộ tham số từ NVS; thiếu hoặc ngoài miền → dùng mặc định. nvs_flash_init() phải gọi trước. */
esp_err_t params_init(void);

/* Giá trị hiện tại; id không hợp lệ → 0. */
uint16_t params_get(uint8_t id);

/* Kiểm tra id + min/max (ESP_ERR_INVALID_ARG nếu sai), ghi NVS, cập nhật RAM. */
esp_err_t params_set(uint8_t id, uint16_t value);

#ifdef __cplusplus
}
#endif
#endif /* PARAMS_H */
