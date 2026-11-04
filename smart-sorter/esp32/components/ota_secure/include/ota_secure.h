/* O-08 → O-15 — Secure OTA phía ESP32.
 *
 * - ota_secure_start(): task riêng (stack 10 KB) chạy toàn bộ luồng: Wi-Fi → manifest+sig (HTTPS, CA nhúng) →
 *   xác minh ECDSA P-256 → kiểm tra model/version/size → tải firmware.bin vừa ghi vừa SHA-256 → esp_ota_end →
 *   set boot → lưu max_ver → báo cáo → reboot. Mọi stage báo qua callback (fsm → EVT_OTA_STATUS, O-14).
 * - ota_secure_on_boot(): O-13 — nếu ảnh đang PENDING_VERIFY thì self-test (chờ PING từ Pi) rồi xác nhận
 *   hoặc quay lui; nếu vừa quay lui thì báo ROLLED_BACK một lần.
 */
#ifndef OTA_SECURE_H
#define OTA_SECURE_H

#include <stdbool.h>
#include <stdint.h>
#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

/* stage/reason theo proto_ota_stage_t / proto_ota_reason_t (protocol_gen.h); progress 0–100. */
typedef void (*ota_secure_status_cb_t)(uint8_t stage, uint8_t reason, uint8_t progress, uint32_t version_code);

typedef struct {
    uint32_t running_version_code;      /* FW_VERSION_CODE của bản đang chạy (main/version.h) */
    ota_secure_status_cb_t status_cb;   /* có thể NULL rồi đặt sau bằng ota_secure_set_status_cb */
} ota_secure_config_t;

esp_err_t ota_secure_init(const ota_secure_config_t *cfg);
void ota_secure_set_status_cb(ota_secure_status_cb_t cb);

/* Bắt đầu OTA (fsm gọi khi nhận OTA_CHECK ở IDLE). ESP_ERR_INVALID_STATE nếu đang chạy. */
esp_err_t ota_secure_start(void);

/* Yêu cầu hủy (ví dụ ESTOP trong lúc OTA): vòng tải kiểm tra cờ, esp_ota_abort, báo REJECTED BAD_STATE. */
void ota_secure_abort(void);

/* O-13 — gọi một lần từ app_main sau khi mọi driver + link đã init. Tạo task nền, không chặn. */
esp_err_t ota_secure_on_boot(void);

bool ota_secure_is_busy(void);

#ifdef __cplusplus
}
#endif
#endif /* OTA_SECURE_H */
