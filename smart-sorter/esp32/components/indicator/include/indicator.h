/* E-06 — Đèn LED xanh/đỏ và còi: pattern chạy nền bằng esp_timer chu kỳ 50 ms, không vTaskDelay. */
#ifndef INDICATOR_H
#define INDICATOR_H

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    /* Pattern nền (giữ cho tới khi đổi) */
    INDICATOR_IDLE = 0,     /* tắt hết */
    INDICATOR_RUNNING,      /* xanh sáng */
    INDICATOR_ESTOP,        /* đỏ nháy liên tục + còi 1 s */
    INDICATOR_FAULT,        /* xanh–đỏ luân phiên */
    INDICATOR_OTA,          /* xanh nháy nhanh */
    /* Pattern tạm (chạy xong tự về pattern nền trước đó) */
    INDICATOR_OK,           /* bíp 100 ms */
    INDICATOR_DEFECT,       /* đỏ sáng 1 s */
    INDICATOR_SECURITY,     /* đỏ nháy 2 s + 3 bíp */
    INDICATOR_FAILSAFE,     /* đỏ sáng 1 s (tự loại khi quá hạn / REJECT lý do FAILSAFE) */
    INDICATOR_MODE_COUNT
} indicator_mode_t;

esp_err_t indicator_init(void);
void indicator_set(indicator_mode_t mode);
indicator_mode_t indicator_get(void);

/* Điều khiển thô (chỉ cho chế độ kiểm tra đấu dây). */
void indicator_raw(int green, int red, int buzzer);

#ifdef __cplusplus
}
#endif
#endif /* INDICATOR_H */
