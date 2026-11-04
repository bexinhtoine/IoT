/* E-06 — LED/còi theo pattern, không chặn.
 *
 * Một esp_timer chu kỳ 50 ms ("tick") gọi pattern_eval(mode, tick) → (xanh, đỏ, còi, xong?).
 * Pattern nền (IDLE/RUNNING/ESTOP/FAULT/OTA) chạy vô hạn; pattern tạm (OK/DEFECT/SECURITY/FAILSAFE)
 * chạy một lượt rồi tự quay về pattern nền gần nhất (ví dụ sau "OK bíp" khi đang RUNNING → xanh sáng lại).
 *
 *   RUNNING  : xanh sáng
 *   OK       : còi 100 ms (2 tick), xanh giữ sáng
 *   DEFECT   : đỏ 1 s (20 tick)
 *   SECURITY : đỏ nháy 2 s (200 ms on/off, 40 tick) + 3 bíp 100 ms (tick 0-1, 4-5, 8-9)
 *   ESTOP    : đỏ nháy liên tục (250 ms on/off) + còi 1 s đầu
 *   FAULT    : xanh–đỏ luân phiên 500 ms
 *   OTA      : xanh nháy nhanh 100 ms
 *   IDLE     : tắt
 */
#include "indicator.h"

#include "board.h"
#include "driver/gpio.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"

static const char *TAG = "indicator";
#define TICK_MS 50

static esp_timer_handle_t s_timer;
static portMUX_TYPE s_lock = portMUX_INITIALIZER_UNLOCKED;
static indicator_mode_t s_base = INDICATOR_IDLE;     /* pattern nền */
static indicator_mode_t s_cur = INDICATOR_IDLE;      /* pattern đang chạy */
static uint32_t s_tick;
static bool s_inited;

static bool is_transient(indicator_mode_t m)
{
    return m == INDICATOR_OK || m == INDICATOR_DEFECT || m == INDICATOR_SECURITY || m == INDICATOR_FAILSAFE;
}

static void outputs(int g, int r, int b)
{
    (void)gpio_set_level(BOARD_LED_GREEN_GPIO, g ? 1 : 0);
    (void)gpio_set_level(BOARD_LED_RED_GPIO, r ? 1 : 0);
    (void)gpio_set_level(BOARD_BUZZER_GPIO, b ? 1 : 0);
}

/* Tính đầu ra tại tick t. Trả về true nếu pattern tạm đã chạy xong. */
static bool pattern_eval(indicator_mode_t m, uint32_t t, int *g, int *r, int *b)
{
    *g = 0; *r = 0; *b = 0;
    switch (m) {
    case INDICATOR_IDLE:
        return false;
    case INDICATOR_RUNNING:
        *g = 1;
        return false;
    case INDICATOR_ESTOP:                       /* đỏ 250 ms on/off; còi 20 tick đầu */
        *r = ((t / 5) % 2) == 0;
        *b = t < 20;
        return false;
    case INDICATOR_FAULT:                       /* 500 ms xanh, 500 ms đỏ */
        if (((t / 10) % 2) == 0) { *g = 1; } else { *r = 1; }
        return false;
    case INDICATOR_OTA:                         /* xanh 100 ms on/off */
        *g = ((t / 2) % 2) == 0;
        return false;
    case INDICATOR_OK:                          /* bíp 100 ms, xanh giữ */
        *g = 1;
        *b = t < 2;
        return t >= 2;
    case INDICATOR_DEFECT:                      /* đỏ 1 s */
    case INDICATOR_FAILSAFE:
        *r = t < 20;
        return t >= 20;
    case INDICATOR_SECURITY:                    /* đỏ nháy 200 ms trong 2 s + 3 bíp */
        *r = (t < 40) && (((t / 4) % 2) == 0);
        *b = (t < 12) && (((t / 2) % 2) == 0);  /* tick 0-1, 4-5, 8-9 */
        return t >= 40;
    default:
        return true;
    }
}

static void tick_cb(void *arg)
{
    (void)arg;
    indicator_mode_t m;
    uint32_t t;
    portENTER_CRITICAL(&s_lock);
    m = s_cur;
    t = s_tick++;
    portEXIT_CRITICAL(&s_lock);

    int g, r, b;
    bool done = pattern_eval(m, t, &g, &r, &b);
    if (done) {
        /* Về pattern nền và đánh giá ngay tick 0 để không có khoảng tối */
        portENTER_CRITICAL(&s_lock);
        s_cur = s_base;
        s_tick = 1;
        m = s_cur;
        portEXIT_CRITICAL(&s_lock);
        (void)pattern_eval(m, 0, &g, &r, &b);
    }
    outputs(g, r, b);
}

esp_err_t indicator_init(void)
{
    if (s_inited) {
        return ESP_OK;
    }
    gpio_config_t io = {
        .pin_bit_mask = (1ULL << BOARD_LED_GREEN_GPIO) | (1ULL << BOARD_LED_RED_GPIO) | (1ULL << BOARD_BUZZER_GPIO),
        .mode = GPIO_MODE_OUTPUT,
        .pull_up_en = GPIO_PULLUP_DISABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    esp_err_t err = gpio_config(&io);
    if (err != ESP_OK) {
        return err;
    }
    outputs(0, 0, 0);

    const esp_timer_create_args_t targs = {
        .callback = tick_cb,
        .arg = NULL,
        .dispatch_method = ESP_TIMER_TASK,
        .name = "indicator",
        .skip_unhandled_events = true,
    };
    err = esp_timer_create(&targs, &s_timer);
    if (err != ESP_OK) {
        return err;
    }
    err = esp_timer_start_periodic(s_timer, (uint64_t)TICK_MS * 1000ULL);
    if (err != ESP_OK) {
        return err;
    }
    s_inited = true;
    ESP_LOGI(TAG, "init OK: LED xanh GPIO%d, do GPIO%d, coi GPIO%d, tick %d ms", BOARD_LED_GREEN_GPIO,
             BOARD_LED_RED_GPIO, BOARD_BUZZER_GPIO, TICK_MS);
    return ESP_OK;
}

void indicator_set(indicator_mode_t mode)
{
    if (mode >= INDICATOR_MODE_COUNT) {
        return;
    }
    portENTER_CRITICAL(&s_lock);
    if (!is_transient(mode)) {
        s_base = mode;
    }
    s_cur = mode;
    s_tick = 0;
    portEXIT_CRITICAL(&s_lock);
    ESP_LOGD(TAG, "mode %d", (int)mode);
}

indicator_mode_t indicator_get(void)
{
    return s_cur;
}

void indicator_raw(int green, int red, int buzzer)
{
    if (s_inited) {
        (void)esp_timer_stop(s_timer); /* dừng pattern khi test tay */
    }
    outputs(green, red, buzzer);
}
