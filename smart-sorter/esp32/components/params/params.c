/* E-11 — Tham số hiệu chỉnh (SET_PARAM): whitelist và miền giá trị lấy từ PROTO_PARAMS (protocol_gen.h),
 * lưu NVS namespace "params", key = PROTO_PARAMS[i].name (≤ 15 ký tự theo giới hạn key NVS —
 * "t_decision_timeout" dài 18 ký tự nên dùng key rút gọn "p<id>" để chắc chắn hợp lệ).
 */
#include "params.h"

#include <stdio.h>
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "nvs.h"
#include "nvs_flash.h"

static const char *TAG = "params";
#define PARAMS_NS "params"

static uint16_t s_values[PROTO_PARAM_COUNT];
static portMUX_TYPE s_lock = portMUX_INITIALIZER_UNLOCKED;
static bool s_inited;

/* Key NVS: "p<id>" (tối đa 15 ký tự). Tên dài trong PROTO_PARAMS chỉ dùng để log. */
static void make_key(uint8_t id, char out[8])
{
    snprintf(out, 8, "p%u", (unsigned)id);
}

static int index_of(uint8_t id)
{
    for (size_t i = 0; i < PROTO_PARAM_COUNT; i++) {
        if (PROTO_PARAMS[i].id == id) {
            return (int)i;
        }
    }
    return -1;
}

esp_err_t params_init(void)
{
    /* Mặc định trước */
    for (size_t i = 0; i < PROTO_PARAM_COUNT; i++) {
        s_values[i] = PROTO_PARAMS[i].def;
    }

    nvs_handle_t h;
    esp_err_t err = nvs_open(PARAMS_NS, NVS_READONLY, &h);
    if (err == ESP_ERR_NVS_NOT_FOUND) {
        ESP_LOGW(TAG, "namespace chua co — dung mac dinh");
        s_inited = true;
        return ESP_OK;
    }
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "nvs_open loi: %s — dung mac dinh", esp_err_to_name(err));
        s_inited = true;
        return err;
    }
    for (size_t i = 0; i < PROTO_PARAM_COUNT; i++) {
        char key[8];
        make_key(PROTO_PARAMS[i].id, key);
        uint16_t v = 0;
        err = nvs_get_u16(h, key, &v);
        if (err == ESP_OK) {
            if (v >= PROTO_PARAMS[i].min && v <= PROTO_PARAMS[i].max) {
                s_values[i] = v;
            } else {
                ESP_LOGW(TAG, "%s=%u ngoai mien [%u..%u] — dung mac dinh %u", PROTO_PARAMS[i].name, v,
                         PROTO_PARAMS[i].min, PROTO_PARAMS[i].max, PROTO_PARAMS[i].def);
            }
        } else if (err != ESP_ERR_NVS_NOT_FOUND) {
            ESP_LOGW(TAG, "doc %s loi: %s", PROTO_PARAMS[i].name, esp_err_to_name(err));
        }
        ESP_LOGI(TAG, "%-20s = %u", PROTO_PARAMS[i].name, s_values[i]);
    }
    nvs_close(h);
    s_inited = true;
    return ESP_OK;
}

uint16_t params_get(uint8_t id)
{
    int i = index_of(id);
    if (i < 0) {
        return 0;
    }
    portENTER_CRITICAL(&s_lock);
    uint16_t v = s_values[i];
    portEXIT_CRITICAL(&s_lock);
    return v;
}

esp_err_t params_set(uint8_t id, uint16_t value)
{
    const proto_param_desc_t *d = proto_param_find(id);
    if (d == NULL || value < d->min || value > d->max) {
        ESP_LOGW(TAG, "SET_PARAM id=%u value=%u bi tu choi", id, value);
        return ESP_ERR_INVALID_ARG;
    }
    int i = index_of(id);
    if (i < 0) {
        return ESP_ERR_INVALID_ARG;
    }

    nvs_handle_t h;
    esp_err_t err = nvs_open(PARAMS_NS, NVS_READWRITE, &h);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "nvs_open RW loi: %s", esp_err_to_name(err));
        return err;
    }
    char key[8];
    make_key(id, key);
    err = nvs_set_u16(h, key, value);
    if (err == ESP_OK) {
        err = nvs_commit(h);
    }
    nvs_close(h);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "ghi %s loi: %s", d->name, esp_err_to_name(err));
        return err;
    }

    portENTER_CRITICAL(&s_lock);
    s_values[i] = value;
    portEXIT_CRITICAL(&s_lock);
    ESP_LOGI(TAG, "%s = %u (da luu NVS)", d->name, value);
    return ESP_OK;
}
