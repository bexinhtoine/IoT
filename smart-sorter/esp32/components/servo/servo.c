/* E-04 — Servo A (GPIO18) và B (GPIO19): LEDC timer 1, 50 Hz, 14 bit (chu kỳ 20 000 µs ↔ 16384 bước).
 * Góc 0..180° ánh xạ tuyến tính sang xung 500..2500 µs; góc được chặn ở 180.
 * Dùng timer riêng với motor (timer 0, 1 kHz) vì khác tần số.
 */
#include "servo.h"

#include "board.h"
#include "driver/ledc.h"
#include "esp_log.h"

static const char *TAG = "servo";

#define SERVO_LEDC_MODE    LEDC_LOW_SPEED_MODE
#define SERVO_LEDC_TIMER   LEDC_TIMER_1
#define SERVO_LEDC_RES     LEDC_TIMER_14_BIT
#define SERVO_DUTY_FULL    16384u      /* 2^14 */
#define SERVO_FREQ_HZ      50
#define SERVO_PERIOD_US    20000u
#define SERVO_MIN_US       500u
#define SERVO_MAX_US       2500u
#define SERVO_MAX_DEG      180u

static const int s_gpio[SERVO_COUNT] = { BOARD_SERVO_A_GPIO, BOARD_SERVO_B_GPIO };
static const ledc_channel_t s_chan[SERVO_COUNT] = { LEDC_CHANNEL_1, LEDC_CHANNEL_2 };
static uint8_t s_angle[SERVO_COUNT];
static bool s_inited;

static uint32_t angle_to_duty(uint8_t deg)
{
    uint32_t us = SERVO_MIN_US + ((uint32_t)deg * (SERVO_MAX_US - SERVO_MIN_US)) / SERVO_MAX_DEG;
    /* duty = us / 20000 * 16384; nhân trước chia sau, không tràn 32 bit (2500*16384 = 40,96 M) */
    return (us * SERVO_DUTY_FULL) / SERVO_PERIOD_US;
}

esp_err_t servo_init(void)
{
    if (s_inited) {
        return ESP_OK;
    }
    ledc_timer_config_t tcfg = {
        .speed_mode = SERVO_LEDC_MODE,
        .duty_resolution = SERVO_LEDC_RES,
        .timer_num = SERVO_LEDC_TIMER,
        .freq_hz = SERVO_FREQ_HZ,
        .clk_cfg = LEDC_AUTO_CLK,
    };
    esp_err_t err = ledc_timer_config(&tcfg);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "timer config loi: %s", esp_err_to_name(err));
        return err;
    }
    for (int i = 0; i < SERVO_COUNT; i++) {
        ledc_channel_config_t ccfg = {
            .gpio_num = s_gpio[i],
            .speed_mode = SERVO_LEDC_MODE,
            .channel = s_chan[i],
            .intr_type = LEDC_INTR_DISABLE,
            .timer_sel = SERVO_LEDC_TIMER,
            .duty = angle_to_duty(0),
            .hpoint = 0,
        };
        err = ledc_channel_config(&ccfg);
        if (err != ESP_OK) {
            ESP_LOGE(TAG, "channel %d config loi: %s", i, esp_err_to_name(err));
            return err;
        }
        s_angle[i] = 0;
    }
    s_inited = true;
    ESP_LOGI(TAG, "init OK: A GPIO%d, B GPIO%d, 50 Hz/14 bit", BOARD_SERVO_A_GPIO, BOARD_SERVO_B_GPIO);
    return ESP_OK;
}

esp_err_t servo_set_angle(servo_id_t id, uint8_t deg)
{
    if (!s_inited) {
        return ESP_ERR_INVALID_STATE;
    }
    if (id >= SERVO_COUNT) {
        return ESP_ERR_INVALID_ARG;
    }
    if (deg > SERVO_MAX_DEG) {
        deg = SERVO_MAX_DEG;
    }
    esp_err_t err = ledc_set_duty(SERVO_LEDC_MODE, s_chan[id], angle_to_duty(deg));
    if (err == ESP_OK) {
        err = ledc_update_duty(SERVO_LEDC_MODE, s_chan[id]);
    }
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "servo %d goc %u loi: %s", (int)id, deg, esp_err_to_name(err));
        return err;
    }
    s_angle[id] = deg;
    ESP_LOGD(TAG, "servo %c -> %u deg", id == SERVO_A ? 'A' : 'B', deg);
    return ESP_OK;
}

uint8_t servo_get_angle(servo_id_t id)
{
    return (id < SERVO_COUNT) ? s_angle[id] : 0;
}
