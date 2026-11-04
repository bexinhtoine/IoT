/* MODULE P2 + P8 — điểm vào firmware Smart Logistics Sorter (ESP32, ESP-IDF v5.2/v5.3).
 *
 * Thứ tự khởi tạo: NVS → params (E-11) → motor (E-02) → servo (E-04) → indicator (E-06)
 *                  → fsm (E-01; bên trong: sensor E-03, estop E-07, link E-09/E-10/E-12)
 *                  → ota_secure (O-08..O-15) → ota_secure_on_boot (O-13 self-test / ROLLED_BACK).
 * Mọi init dùng ESP_ERROR_CHECK: nếu bản OTA mới lỗi init → abort → bootloader (APP_ROLLBACK_ENABLE)
 * tự quay về bản cũ ở lần boot sau — đó chính là phần "driver init OK" của self-test O-13.
 *
 * CONFIG_SORTER_HW_TEST: chế độ kiểm tra đấu dây, không chạy fsm/UART.
 */
#include <stdio.h>
#include "esp_log.h"
#include "esp_system.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "nvs_flash.h"
#include "sdkconfig.h"

#include "board.h"
#include "estop.h"
#include "fsm.h"
#include "indicator.h"
#include "motor.h"
#include "ota_secure.h"
#include "params.h"
#include "sensor.h"
#include "servo.h"
#include "version.h"

static const char *TAG = "main";

static void nvs_init(void)
{
    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_LOGW(TAG, "NVS can xoa (%s) — erase & init lai", esp_err_to_name(err));
        ESP_ERROR_CHECK(nvs_flash_erase());
        err = nvs_flash_init();
    }
    ESP_ERROR_CHECK(err);
}

#if CONFIG_SORTER_HW_TEST
/* Kiểm tra đấu dây (P2 yêu cầu 9). vTaskDelay ở đây chỉ để tuần tự hóa bài test, không phải hiệu ứng đèn. */
static void hw_test(void)
{
    ESP_LOGW(TAG, "=== CHE DO KIEM TRA DAU DAY (CONFIG_SORTER_HW_TEST) ===");
    ESP_ERROR_CHECK(motor_init());
    ESP_ERROR_CHECK(servo_init());
    ESP_ERROR_CHECK(indicator_init());
    ESP_ERROR_CHECK(params_init());
    ESP_ERROR_CHECK(sensor_init(NULL));
    ESP_ERROR_CHECK(estop_init(NULL));

    ESP_LOGI(TAG, "[1] dong co 60%% trong 2 s (GPIO%d PWM, IN1 GPIO%d, IN2 GPIO%d)", BOARD_MOTOR_PWM_GPIO,
             BOARD_MOTOR_IN1_GPIO, BOARD_MOTOR_IN2_GPIO);
    ESP_ERROR_CHECK(motor_start(60));
    vTaskDelay(pdMS_TO_TICKS(2000));
    motor_stop();

    uint8_t a_div = (uint8_t)params_get(PARAM_SERVO_A_DIVERT);
    uint8_t b_div = (uint8_t)params_get(PARAM_SERVO_B_DIVERT);
    ESP_LOGI(TAG, "[2] servo A (GPIO%d): 0 -> %u -> 0", BOARD_SERVO_A_GPIO, a_div);
    ESP_ERROR_CHECK(servo_set_angle(SERVO_A, 0));
    vTaskDelay(pdMS_TO_TICKS(700));
    ESP_ERROR_CHECK(servo_set_angle(SERVO_A, a_div));
    vTaskDelay(pdMS_TO_TICKS(700));
    ESP_ERROR_CHECK(servo_set_angle(SERVO_A, 0));
    vTaskDelay(pdMS_TO_TICKS(700));
    ESP_LOGI(TAG, "[3] servo B (GPIO%d): 0 -> %u -> 0", BOARD_SERVO_B_GPIO, b_div);
    ESP_ERROR_CHECK(servo_set_angle(SERVO_B, 0));
    vTaskDelay(pdMS_TO_TICKS(700));
    ESP_ERROR_CHECK(servo_set_angle(SERVO_B, b_div));
    vTaskDelay(pdMS_TO_TICKS(700));
    ESP_ERROR_CHECK(servo_set_angle(SERVO_B, 0));
    vTaskDelay(pdMS_TO_TICKS(700));

    ESP_LOGI(TAG, "[4] LED xanh GPIO%d", BOARD_LED_GREEN_GPIO);
    indicator_raw(1, 0, 0);
    vTaskDelay(pdMS_TO_TICKS(500));
    indicator_raw(0, 0, 0);
    vTaskDelay(pdMS_TO_TICKS(300));
    ESP_LOGI(TAG, "[5] LED do GPIO%d", BOARD_LED_RED_GPIO);
    indicator_raw(0, 1, 0);
    vTaskDelay(pdMS_TO_TICKS(500));
    indicator_raw(0, 0, 0);
    vTaskDelay(pdMS_TO_TICKS(300));
    ESP_LOGI(TAG, "[6] coi GPIO%d", BOARD_BUZZER_GPIO);
    indicator_raw(0, 0, 1);
    vTaskDelay(pdMS_TO_TICKS(300));
    indicator_raw(0, 0, 0);

    ESP_LOGI(TAG, "[7] in cam bien (%s) va E-stop (GPIO%d) moi 200 ms — Ctrl+] de thoat monitor", sensor_name(),
             BOARD_ESTOP_GPIO);
    for (;;) {
        printf("sensor_raw=%d  estop=%s\n", sensor_read_raw(), estop_is_pressed() ? "PRESSED/OPEN (HIGH)" : "ok (LOW)");
        vTaskDelay(pdMS_TO_TICKS(200));
    }
}
#endif

void app_main(void)
{
    ESP_LOGI(TAG, "Smart Sorter ESP32 fw %s (code %lu), reset_reason=%d", FW_VERSION_NAME,
             (unsigned long)FW_VERSION_CODE, (int)esp_reset_reason());
    nvs_init();

#if CONFIG_SORTER_HW_TEST
    hw_test();   /* không trở về */
#else
    ESP_ERROR_CHECK(params_init());
    ESP_ERROR_CHECK(motor_init());
    ESP_ERROR_CHECK(servo_init());
    ESP_ERROR_CHECK(indicator_init());
    ESP_ERROR_CHECK(fsm_init(FW_VERSION_CODE));

    const ota_secure_config_t ocfg = {
        .running_version_code = FW_VERSION_CODE,
        .status_cb = fsm_on_ota_status,
    };
    ESP_ERROR_CHECK(ota_secure_init(&ocfg));
    ESP_ERROR_CHECK(ota_secure_on_boot());
    ESP_LOGI(TAG, "khoi dong xong");
#endif
}
