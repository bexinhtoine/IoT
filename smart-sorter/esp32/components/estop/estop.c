/* E-07 — Dừng khẩn cấp vật lý.
 *
 * GPIO39 (chỉ đọc, không pull nội): nút NC xuống GND + pull-up 10 kΩ ngoài. Bình thường LOW.
 * Bấm nút (hở tiếp điểm) hoặc đứt dây → HIGH → dừng (fail-safe).
 * Ngắt cả hai cạnh: cạnh lên → motor_stop_from_isr() ngay trong ISR rồi báo fsm (EV_ESTOP pressed);
 * cạnh xuống → báo fsm (released) để log; thoát ESTOP vẫn cần ESTOP_RESET từ Pi.
 * Nút cơ khí dội: fsm xử lý idempotent (đã ESTOP thì bỏ qua lần bấm tiếp theo).
 * Nhắc lại: E-stop vật lý phải cắt nguồn động cơ bằng phần cứng; phần mềm chỉ là lớp thứ hai.
 */
#include "estop.h"

#include "board.h"
#include "driver/gpio.h"
#include "esp_attr.h"
#include "esp_log.h"
#include "motor.h"

static const char *TAG = "estop";
static estop_cb_t s_cb;
static bool s_inited;

static void IRAM_ATTR estop_isr(void *arg)
{
    (void)arg;
    int level = gpio_get_level(BOARD_ESTOP_GPIO);
    BaseType_t woken = pdFALSE;
    if (level) {
        motor_stop_from_isr();       /* tắt PWM + IN1/IN2 ngay, chỉ ghi thanh ghi */
    }
    if (s_cb) {
        s_cb(level != 0, &woken);
    }
    if (woken == pdTRUE) {
        portYIELD_FROM_ISR();
    }
}

esp_err_t estop_init(estop_cb_t cb)
{
    if (s_inited) {
        return ESP_OK;
    }
    s_cb = cb;
    gpio_config_t io = {
        .pin_bit_mask = 1ULL << BOARD_ESTOP_GPIO,
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_DISABLE,    /* GPIO39 không có pull nội — bắt buộc pull-up ngoài */
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_ANYEDGE,
    };
    esp_err_t err = gpio_config(&io);
    if (err != ESP_OK) {
        return err;
    }
    /* Dịch vụ ISR GPIO dùng chung (sensor có thể đã cài) — không dùng ESP_INTR_FLAG_IRAM nên handler được phép
     * gọi gpio_get_level (không nằm trong IRAM); motor_stop_from_isr vẫn thuần thanh ghi. */
    err = gpio_install_isr_service(0);
    if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) {
        return err;
    }
    err = gpio_isr_handler_add(BOARD_ESTOP_GPIO, estop_isr, NULL);
    if (err != ESP_OK) {
        return err;
    }
    s_inited = true;
    ESP_LOGI(TAG, "init OK: GPIO%d, muc hien tai=%d (%s)", BOARD_ESTOP_GPIO, gpio_get_level(BOARD_ESTOP_GPIO),
             estop_is_pressed() ? "DANG BAM/DUT DAY" : "binh thuong");
    return ESP_OK;
}

bool estop_is_pressed(void)
{
    return gpio_get_level(BOARD_ESTOP_GPIO) != 0;
}
