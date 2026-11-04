/* E-03 — Phát hiện kiện hàng.
 *
 * TCRT5000 (GPIO34, chân chỉ đọc — module LM393 thường có pull-up sẵn; nếu dùng cảm biến trần phải thêm
 * pull-up ngoài 10 kΩ vì GPIO34 không có pull nội):
 *   - Ngắt ở cạnh "có vật" (NEGEDGE nếu CONFIG_SORTER_TCRT_ACTIVE_LOW).
 *   - ISR tối thiểu: nếu đang enabled và chưa trong cửa sổ chống dội → khởi động esp_timer one-shot 20 ms.
 *     esp_timer_start_once() là IRAM_ATTR và dùng portENTER_CRITICAL_SAFE trong ESP-IDF v5.x nên gọi được từ ISR
 *     (đối chiếu docs esp_timer của phiên bản IDF đang dùng; nếu không hỗ trợ, thay bằng xSemaphoreGiveFromISR
 *     tới một task nhỏ rồi task khởi động timer).
 *   - Sau 20 ms (task esp_timer): đọc lại mức; vẫn "có vật" → gọi callback. Nhờ vậy xung dội < 20 ms bị bỏ.
 *
 * HC-SR04 (TRIG GPIO13, ECHO GPIO35 qua cầu chia áp): task đo chu kỳ 30 ms: TRIG 10 µs, đo độ rộng ECHO bằng
 * vòng lặp gpio_get_level + esp_timer_get_time với timeout 30 ms (cách đơn giản; chính xác hơn thì dùng RMT RX
 * hoặc ngắt cả hai cạnh — ghi chú). mm = us * 0,343 / 2 ≈ us / 5,83. Xác nhận khi 2 lần liên tiếp < ngưỡng.
 */
#include "sensor.h"

#include "board.h"
#include "driver/gpio.h"
#include "esp_attr.h"
#include "esp_log.h"
#include "esp_rom_sys.h"   /* esp_rom_delay_us */
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "params.h"
#include "sdkconfig.h"

static const char *TAG = "sensor";

static sensor_cb_t s_cb;
static volatile bool s_enabled;
static bool s_inited;

#if CONFIG_SORTER_SENSOR_HCSR04
/* ------------------------------------------------------------------ HC-SR04 */
#define HCSR04_PERIOD_MS   30
#define HCSR04_TIMEOUT_US  30000
#define HCSR04_CONFIRM     2

static volatile int s_last_mm = -1;
static int s_consecutive;

/* Đo một lần; trả về mm hoặc -1 nếu timeout. Chạy trong task đo. */
static int hcsr04_measure_mm(void)
{
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
            return -1;
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
        if (!s_enabled) {
            s_consecutive = 0;
            continue;
        }
        uint16_t thr = params_get(PARAM_HCSR04_THRESHOLD);
        if (mm >= 0 && mm < (int)thr) {
            s_consecutive++;
            if (s_consecutive >= HCSR04_CONFIRM) {
                s_consecutive = 0;
                ESP_LOGI(TAG, "HC-SR04 phat hien %d mm (< %u)", mm, thr);
                if (s_cb) {
                    s_cb();
                }
            }
        } else {
            s_consecutive = 0;
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

#else
/* ------------------------------------------------------------------ TCRT5000 */
#if CONFIG_SORTER_TCRT_ACTIVE_LOW
#define TCRT_ACTIVE_LEVEL 0
#define TCRT_INTR_TYPE    GPIO_INTR_NEGEDGE
#else
#define TCRT_ACTIVE_LEVEL 1
#define TCRT_INTR_TYPE    GPIO_INTR_POSEDGE
#endif
#define TCRT_DEBOUNCE_US  20000

static esp_timer_handle_t s_debounce_timer;
static volatile bool s_debouncing;

/* Sau 20 ms — ngữ cảnh task esp_timer */
static void debounce_cb(void *arg)
{
    (void)arg;
    s_debouncing = false;
    if (!s_enabled) {
        return;
    }
    if (gpio_get_level(BOARD_TCRT_DO_GPIO) == TCRT_ACTIVE_LEVEL) {
        ESP_LOGI(TAG, "TCRT5000 phat hien");
        if (s_cb) {
            s_cb();
        }
    }
}

/* ISR tối thiểu: chỉ khởi động timer chống dội */
static void IRAM_ATTR tcrt_isr(void *arg)
{
    (void)arg;
    if (!s_enabled || s_debouncing) {
        return;
    }
    s_debouncing = true;
    if (esp_timer_start_once(s_debounce_timer, TCRT_DEBOUNCE_US) != ESP_OK) {
        s_debouncing = false; /* timer đang chạy (hiếm) — bỏ cạnh này */
    }
}

esp_err_t sensor_init(sensor_cb_t cb)
{
    if (s_inited) {
        return ESP_OK;
    }
    s_cb = cb;
    gpio_config_t io = {
        .pin_bit_mask = 1ULL << BOARD_TCRT_DO_GPIO,
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_DISABLE,   /* GPIO34 không có pull nội — cần pull-up ngoài */
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = TCRT_INTR_TYPE,
    };
    esp_err_t err = gpio_config(&io);
    if (err != ESP_OK) {
        return err;
    }
    const esp_timer_create_args_t targs = {
        .callback = debounce_cb,
        .arg = NULL,
        .dispatch_method = ESP_TIMER_TASK,
        .name = "tcrt_db",
        .skip_unhandled_events = true,
    };
    err = esp_timer_create(&targs, &s_debounce_timer);
    if (err != ESP_OK) {
        return err;
    }
    /* Dịch vụ ISR GPIO dùng chung với estop: có thể đã cài → ESP_ERR_INVALID_STATE là bình thường */
    err = gpio_install_isr_service(0);
    if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) {
        return err;
    }
    err = gpio_isr_handler_add(BOARD_TCRT_DO_GPIO, tcrt_isr, NULL);
    if (err != ESP_OK) {
        return err;
    }
    s_inited = true;
    ESP_LOGI(TAG, "init OK: TCRT5000 DO GPIO%d, active %s, chong doi %d ms", BOARD_TCRT_DO_GPIO,
             TCRT_ACTIVE_LEVEL ? "HIGH" : "LOW", TCRT_DEBOUNCE_US / 1000);
    return ESP_OK;
}

int sensor_read_raw(void)
{
    return gpio_get_level(BOARD_TCRT_DO_GPIO);
}

const char *sensor_name(void)
{
    return "TCRT5000";
}
#endif /* CONFIG_SORTER_SENSOR_HCSR04 */

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
