/* E-02 — Động cơ băng chuyền: L298N, LEDC timer 0 (1 kHz, 10 bit) trên GPIO25 (ENA),
 * IN1 = 1 / IN2 = 0 khi chạy (một chiều). Tăng tốc mềm 200 ms bằng esp_timer chu kỳ 10 ms.
 *
 * Dừng từ ISR (E-07): KHÔNG gọi ledc_stop()/ledc_set_duty() trong ISR (các hàm này không nằm trong IRAM
 * trừ khi bật CONFIG_LEDC_CTRL_FUNC_IN_IRAM và dùng spinlock) — thay vào đó:
 *   1. ghi mức 0 vào thanh ghi GPIO out của GPIO25/26/27 (gpio_ll_set_level — inline, IRAM-safe);
 *   2. chuyển GPIO25 từ tín hiệu LEDC sang "GPIO thường" qua GPIO matrix (esp_rom_gpio_connect_out_signal,
 *      hàm ROM) → chân xuống 0 ngay dù LEDC vẫn phát xung.
 * motor_start() tiếp theo gắn lại LEDC bằng ledc_set_pin().
 * Đối chiếu docs ESP-IDF v5.x: hal/gpio_ll.h (gpio_ll_set_level), esp_rom_gpio.h, soc/gpio_sig_map.h (SIG_GPIO_OUT_IDX).
 */
#include "motor.h"

#include "board.h"
#include "driver/gpio.h"
#include "driver/ledc.h"
#include "esp_attr.h"
#include "esp_log.h"
#include "esp_rom_gpio.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "hal/gpio_ll.h"
#include "soc/gpio_sig_map.h"
#include "soc/gpio_struct.h"

static const char *TAG = "motor";

#define MOTOR_LEDC_MODE     LEDC_LOW_SPEED_MODE
#define MOTOR_LEDC_TIMER    LEDC_TIMER_0
#define MOTOR_LEDC_CHANNEL  LEDC_CHANNEL_0
#define MOTOR_LEDC_RES      LEDC_TIMER_10_BIT
#define MOTOR_LEDC_FREQ_HZ  1000
#define MOTOR_DUTY_MAX      1023u

#define MOTOR_RAMP_MS       200u
#define MOTOR_RAMP_STEP_MS  10u
#define MOTOR_RAMP_STEPS    (MOTOR_RAMP_MS / MOTOR_RAMP_STEP_MS)

static esp_timer_handle_t s_ramp_timer;
static portMUX_TYPE s_lock = portMUX_INITIALIZER_UNLOCKED;
static volatile uint32_t s_duty_cur;      /* duty đang áp */
static volatile uint32_t s_duty_target;   /* duty đích của ramp */
static volatile uint32_t s_duty_step;     /* bước mỗi 10 ms */
static volatile bool s_running;
static volatile bool s_pin_detached;      /* ISR đã tách LEDC khỏi GPIO25 */
static bool s_inited;

/* Áp duty lên LEDC (ngữ cảnh task). */
static void apply_duty(uint32_t duty)
{
    esp_err_t err = ledc_set_duty(MOTOR_LEDC_MODE, MOTOR_LEDC_CHANNEL, duty);
    if (err == ESP_OK) {
        err = ledc_update_duty(MOTOR_LEDC_MODE, MOTOR_LEDC_CHANNEL);
    }
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "ledc duty %lu loi: %s", (unsigned long)duty, esp_err_to_name(err));
    }
}

/* Callback ramp — chạy trong task esp_timer, mỗi 10 ms. */
static void ramp_cb(void *arg)
{
    (void)arg;
    uint32_t cur, tgt, step;
    portENTER_CRITICAL(&s_lock);
    cur = s_duty_cur;
    tgt = s_duty_target;
    step = s_duty_step;
    if (cur < tgt) {
        cur = (tgt - cur > step) ? cur + step : tgt;
    } else if (cur > tgt) {
        cur = (cur - tgt > step) ? cur - step : tgt;
    }
    s_duty_cur = cur;
    portEXIT_CRITICAL(&s_lock);

    apply_duty(cur);
    if (cur == tgt) {
        /* Dừng timer trong chính callback của nó là hợp lệ với esp_timer. */
        (void)esp_timer_stop(s_ramp_timer);
    }
}

esp_err_t motor_init(void)
{
    if (s_inited) {
        return ESP_OK;
    }

    gpio_config_t io = {
        .pin_bit_mask = (1ULL << BOARD_MOTOR_IN1_GPIO) | (1ULL << BOARD_MOTOR_IN2_GPIO),
        .mode = GPIO_MODE_OUTPUT,
        .pull_up_en = GPIO_PULLUP_DISABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    esp_err_t err = gpio_config(&io);
    if (err != ESP_OK) {
        return err;
    }
    (void)gpio_set_level(BOARD_MOTOR_IN1_GPIO, 0);
    (void)gpio_set_level(BOARD_MOTOR_IN2_GPIO, 0);

    ledc_timer_config_t tcfg = {
        .speed_mode = MOTOR_LEDC_MODE,
        .duty_resolution = MOTOR_LEDC_RES,
        .timer_num = MOTOR_LEDC_TIMER,
        .freq_hz = MOTOR_LEDC_FREQ_HZ,
        .clk_cfg = LEDC_AUTO_CLK,
    };
    err = ledc_timer_config(&tcfg);
    if (err != ESP_OK) {
        return err;
    }

    ledc_channel_config_t ccfg = {
        .gpio_num = BOARD_MOTOR_PWM_GPIO,
        .speed_mode = MOTOR_LEDC_MODE,
        .channel = MOTOR_LEDC_CHANNEL,
        .intr_type = LEDC_INTR_DISABLE,
        .timer_sel = MOTOR_LEDC_TIMER,
        .duty = 0,
        .hpoint = 0,
    };
    err = ledc_channel_config(&ccfg);
    if (err != ESP_OK) {
        return err;
    }

    const esp_timer_create_args_t targs = {
        .callback = ramp_cb,
        .arg = NULL,
        .dispatch_method = ESP_TIMER_TASK,
        .name = "motor_ramp",
        .skip_unhandled_events = true,
    };
    err = esp_timer_create(&targs, &s_ramp_timer);
    if (err != ESP_OK) {
        return err;
    }

    s_duty_cur = 0;
    s_duty_target = 0;
    s_running = false;
    s_pin_detached = false;
    s_inited = true;
    ESP_LOGI(TAG, "init OK: PWM GPIO%d 1 kHz/10 bit, IN1 GPIO%d, IN2 GPIO%d",
             BOARD_MOTOR_PWM_GPIO, BOARD_MOTOR_IN1_GPIO, BOARD_MOTOR_IN2_GPIO);
    return ESP_OK;
}

esp_err_t motor_start(uint8_t speed_pct)
{
    if (!s_inited) {
        return ESP_ERR_INVALID_STATE;
    }
    if (speed_pct == 0) {
        motor_stop();
        return ESP_OK;
    }
    if (speed_pct > 100) {
        speed_pct = 100;
    }

    /* Nếu ISR đã tách LEDC khỏi chân (E-stop), gắn lại trước khi chạy. */
    if (s_pin_detached) {
        apply_duty(0);
        esp_err_t err = ledc_set_pin(BOARD_MOTOR_PWM_GPIO, MOTOR_LEDC_MODE, MOTOR_LEDC_CHANNEL);
        if (err != ESP_OK) {
            ESP_LOGE(TAG, "ledc_set_pin loi: %s", esp_err_to_name(err));
            return err;
        }
        s_pin_detached = false;
    }

    uint32_t target = ((uint32_t)speed_pct * MOTOR_DUTY_MAX) / 100u;

    (void)esp_timer_stop(s_ramp_timer); /* ESP_ERR_INVALID_STATE nếu chưa chạy — bỏ qua */

    portENTER_CRITICAL(&s_lock);
    uint32_t cur = s_duty_cur;
    uint32_t diff = (target > cur) ? (target - cur) : (cur - target);
    uint32_t step = diff / MOTOR_RAMP_STEPS;
    if (step == 0) {
        step = 1;
    }
    s_duty_target = target;
    s_duty_step = step;
    s_running = true;
    portEXIT_CRITICAL(&s_lock);

    /* Chiều quay cố định: IN1 = 1, IN2 = 0 */
    (void)gpio_set_level(BOARD_MOTOR_IN2_GPIO, 0);
    (void)gpio_set_level(BOARD_MOTOR_IN1_GPIO, 1);

    esp_err_t err = esp_timer_start_periodic(s_ramp_timer, (uint64_t)MOTOR_RAMP_STEP_MS * 1000ULL);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "ramp timer loi: %s — ap duty truc tiep", esp_err_to_name(err));
        s_duty_cur = target;
        apply_duty(target);
    }
    ESP_LOGI(TAG, "start %u%% (duty %lu, ramp %u ms)", speed_pct, (unsigned long)target, MOTOR_RAMP_MS);
    return ESP_OK;
}

void motor_stop(void)
{
    if (!s_inited) {
        return;
    }
    (void)esp_timer_stop(s_ramp_timer);
    portENTER_CRITICAL(&s_lock);
    s_duty_cur = 0;
    s_duty_target = 0;
    s_running = false;
    portEXIT_CRITICAL(&s_lock);
    apply_duty(0);
    (void)gpio_set_level(BOARD_MOTOR_IN1_GPIO, 0);
    (void)gpio_set_level(BOARD_MOTOR_IN2_GPIO, 0);
    ESP_LOGI(TAG, "stop");
}

void IRAM_ATTR motor_stop_from_isr(void)
{
    /* 1. Kéo mức GPIO về 0 (thanh ghi GPIO_OUT) */
    gpio_ll_set_level(&GPIO, BOARD_MOTOR_PWM_GPIO, 0);
    gpio_ll_set_level(&GPIO, BOARD_MOTOR_IN1_GPIO, 0);
    gpio_ll_set_level(&GPIO, BOARD_MOTOR_IN2_GPIO, 0);
    /* 2. Tách tín hiệu LEDC khỏi GPIO25: nối GPIO matrix sang "GPIO out thường" → chân = GPIO_OUT bit = 0 */
    esp_rom_gpio_connect_out_signal(BOARD_MOTOR_PWM_GPIO, SIG_GPIO_OUT_IDX, false, false);
    s_pin_detached = true;
    s_running = false;
    s_duty_cur = 0;
    s_duty_target = 0;
}

bool motor_is_running(void)
{
    return s_running;
}

uint32_t motor_get_duty(void)
{
    return s_duty_cur;
}
