/* E-03: HC-SR04 duy nhat; 2 mau 20..300 mm => phat hien mot vat.
 * Chu ky 70 ms; latch den khi 3 mau >330 mm hoac khong co echo.
 * ECHO 5 V phai qua chia ap vao GPIO35. Khong nhan dien hop bang sieu am. */
#include "sensor.h"

#include "board.h"
#include "driver/gpio.h"
#include "esp_log.h"
#include "esp_rom_sys.h"   /* esp_rom_delay_us */
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "sensor_gate.h"

static const char *TAG = "sensor";

static sensor_cb_t s_cb;
static volatile bool s_enabled;
static bool s_inited;

/* ------------------------------------------------------------------ HC-SR04 */
#define HCSR04_PERIOD_MS   70
#define HCSR04_TIMEOUT_US  30000

static volatile int s_last_mm = -1;
static sensor_gate_t s_gate;

/* Đo một lần; trả về mm hoặc -1 nếu timeout. Chạy trong task đo. */
static int hcsr04_measure_mm(void)
{
    if (gpio_get_level(BOARD_HCSR04_ECHO_GPIO) != 0) {
        return -2; /* ECHO stuck HIGH: khong xem la vung trong */
    }
    (void)gpio_set_level(BOARD_HCSR04_TRIG_GPIO, 0);
    esp_rom_delay_us(2);
    (void)gpio_set_level(BOARD_HCSR04_TRIG_GPIO, 1);
    esp_rom_delay_us(10);
    (void)gpio_set_level(BOARD_HCSR04_TRIG_GPIO, 0);

    int64_t t0 = esp_timer_get_time();
    /* chờ ECHO lên */
    while (gpio_get_level(BOARD_HCSR04_ECHO_GPIO) == 0) {
        if (esp_timer_get_time() - t0 > HCSR04_TIMEOUT_US) {
            return -1;
        }
    }
    int64_t t_rise = esp_timer_get_time();
    while (gpio_get_level(BOARD_HCSR04_ECHO_GPIO) == 1) {
        if (esp_timer_get_time() - t_rise > HCSR04_TIMEOUT_US) {
            return -2;
        }
    }
    int64_t width = esp_timer_get_time() - t_rise;
    /* 343 m/s = 0,343 mm/µs; đi-về chia 2 → mm = width * 343 / 2000 */
    return (int)((width * 343) / 2000);
}

static void hcsr04_task(void *arg)
{
    (void)arg;
    TickType_t last = xTaskGetTickCount();
    for (;;) {
        vTaskDelayUntil(&last, pdMS_TO_TICKS(HCSR04_PERIOD_MS));
        int mm = hcsr04_measure_mm();
        s_last_mm = mm;
        if (sensor_gate_update(&s_gate, mm, s_enabled)) {
            ESP_LOGI(TAG, "HC-SR04: vat %d mm (<= 300), cho Pi kiem tra hop", mm);
            if (s_cb) {
                s_cb();
            }
        }
    }
}

esp_err_t sensor_init(sensor_cb_t cb)
{
    if (s_inited) {
        return ESP_OK;
    }
    s_cb = cb;
    gpio_config_t trig = {
        .pin_bit_mask = 1ULL << BOARD_HCSR04_TRIG_GPIO,
        .mode = GPIO_MODE_OUTPUT,
        .pull_up_en = GPIO_PULLUP_DISABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    esp_err_t err = gpio_config(&trig);
    if (err != ESP_OK) {
        return err;
    }
    (void)gpio_set_level(BOARD_HCSR04_TRIG_GPIO, 0);
    gpio_config_t echo = {
        .pin_bit_mask = 1ULL << BOARD_HCSR04_ECHO_GPIO,
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_DISABLE,   /* GPIO35 không có pull nội */
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    err = gpio_config(&echo);
    if (err != ESP_OK) {
        return err;
    }
    BaseType_t ok = xTaskCreate(hcsr04_task, "hcsr04", 3072, NULL, 6, NULL);
    if (ok != pdPASS) {
        return ESP_ERR_NO_MEM;
    }
    s_inited = true;
    ESP_LOGI(TAG, "init OK: HC-SR04 TRIG GPIO%d ECHO GPIO%d, moi %d ms", BOARD_HCSR04_TRIG_GPIO,
             BOARD_HCSR04_ECHO_GPIO, HCSR04_PERIOD_MS);
    return ESP_OK;
}

int sensor_read_raw(void)
{
    return s_last_mm;
}

const char *sensor_name(void)
{
    return "HC-SR04";
}

/* ------------------------------------------------------------------ chung */
void sensor_set_enabled(bool en)
{
    s_enabled = en;
    ESP_LOGD(TAG, "enabled=%d", (int)en);
}

bool sensor_is_enabled(void)
{
    return s_enabled;
}
