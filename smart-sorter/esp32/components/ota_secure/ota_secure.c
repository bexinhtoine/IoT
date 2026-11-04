/* O-08 → O-15 — Secure OTA trên ESP32 (ESP-IDF v5.2/v5.3).
 *
 * Nguyên tắc an toàn:
 *   - URL máy chủ chỉ lấy từ NVS (namespace "ota_cfg", key "url"), bắt buộc https://; không nhận URL qua UART (O-08).
 *   - esp_http_client luôn có .cert_pem = CA nhúng; KHÔNG dùng crt_bundle, KHÔNG skip_cert_common_name_check (O-09).
 *   - Chữ ký ECDSA P-256/SHA-256 xác minh trên ĐÚNG các byte manifest.json nhận được (O-10).
 *   - Chống rollback: version_code phải > bản đang chạy VÀ > max_ver trong NVS (O-11); sai model → MODEL_MISMATCH.
 *   - Firmware ghi vào phân vùng OTA kế tiếp, vừa ghi vừa SHA-256; sai size/hash → esp_ota_abort (O-12).
 *   - Rollback: CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE; self-test = driver init OK (nếu init lỗi, ESP_ERROR_CHECK
 *     trong app_main abort → bootloader tự quay lui) + nhận PING từ Pi trong 30 s (O-13).
 *   - Wi-Fi chỉ bật trong OTA / gửi báo cáo rồi tắt (esp_wifi_stop + esp_wifi_deinit).
 *   - Không có lệnh ghi eFuse nào trong file này.
 *
 * API cần đối chiếu docs theo phiên bản IDF: esp_http_client (HTTP_EVENT_DISCONNECTED mang esp_tls_error_handle_t),
 * esp_tls_get_and_clear_last_error, esp_ota_get_last_invalid_partition, esp_netif_create_default_wifi_sta dùng lại
 * sau esp_wifi_deinit (pattern init/deinit nhiều lần).
 */
#include "ota_secure.h"

#include <ctype.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "cJSON.h"
#include "esp_bit_defs.h"
#include "esp_event.h"
#include "esp_http_client.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_ota_ops.h"
#include "esp_partition.h"
#include "esp_system.h"
#include "esp_tls.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "freertos/task.h"
#include "link.h"
#include "mbedtls/base64.h"
#include "mbedtls/md.h"
#include "mbedtls/pk.h"
#include "mbedtls/sha256.h"
#include "nvs.h"
#include "nvs_flash.h"
#include "protocol_gen.h"
#include "sdkconfig.h"

/* Public key: file thật do app P7 xuất; thiếu thì dùng file mẫu (chỉ để build được). */
#if __has_include("ota_pubkey.h")
#include "ota_pubkey.h"
#else
#warning "ota_pubkey.h chua co - dung ota_pubkey.h.example (khoa mau, KHONG phat hanh ban nay)"
#include "ota_pubkey.h.example"
#endif

static const char *TAG = "ota";

/* CA nhúng (symbol đặt qua target_compile_definitions trong CMakeLists) */
extern const char OTA_CA_PEM_START[];
extern const char OTA_CA_PEM_END[];

#define OTA_NS              "ota_cfg"
#define OTA_DEVICE_MODEL    "sorter-esp32"
#define OTA_MANIFEST_MAX    1024
#define OTA_SIG_MAX         256
#define OTA_CHUNK           4096
#define OTA_HTTP_TIMEOUT_MS 10000
#define OTA_REPORT_TIMEOUT  5000
#define OTA_TASK_STACK      10240
#define OTA_WIFI_RETRY_MAX  5
#define WIFI_CONNECTED_BIT  BIT0
#define WIFI_FAIL_BIT       BIT1

typedef struct {
    char ssid[33];
    char pass[65];
    char url[160];      /* base, ví dụ https://192.168.1.10:8443/ota */
    uint32_t max_ver;
} ota_cfg_t;

typedef struct {
    uint32_t version_code;
    uint32_t fw_size;
    char version_name[32];
    char fw_sha256[65];
    char device_model[32];
} manifest_t;

static ota_secure_config_t s_cfg;
static volatile bool s_busy;
static volatile bool s_abort_req;

/* Wi-Fi */
static EventGroupHandle_t s_wifi_eg;
static esp_netif_t *s_sta_netif;
static bool s_netif_ready;
static esp_event_handler_instance_t s_h_wifi, s_h_ip;
static int s_wifi_retry;

/* Ngữ cảnh HTTP: phát hiện lỗi TLS */
typedef struct {
    bool tls_err;
} http_ctx_t;

/* ------------------------------------------------------------------ tiện ích */
static void notify(uint8_t stage, uint8_t reason, uint8_t progress, uint32_t version_code)
{
    ESP_LOGI(TAG, "stage=%u reason=%u progress=%u version=%lu", stage, reason, progress, (unsigned long)version_code);
    if (s_cfg.status_cb) {
        s_cfg.status_cb(stage, reason, progress, version_code);
    }
}

static const char *reason_str(uint8_t r)
{
    switch (r) {
    case OTA_REASON_NONE: return "NONE";
    case OTA_REASON_SIGNATURE_INVALID: return "SIGNATURE_INVALID";
    case OTA_REASON_HASH_MISMATCH: return "HASH_MISMATCH";
    case OTA_REASON_ROLLBACK_REJECTED: return "ROLLBACK_REJECTED";
    case OTA_REASON_MODEL_MISMATCH: return "MODEL_MISMATCH";
    case OTA_REASON_TLS_ERROR: return "TLS_ERROR";
    case OTA_REASON_DOWNLOAD_ERROR: return "DOWNLOAD_ERROR";
    case OTA_REASON_SELFTEST_FAILED: return "SELFTEST_FAILED";
    case OTA_REASON_BAD_STATE: return "BAD_STATE";
    default: return "UNKNOWN";
    }
}

static void to_hex(const uint8_t *in, size_t n, char *out)
{
    static const char hex[] = "0123456789abcdef";
    for (size_t i = 0; i < n; i++) {
        out[2 * i] = hex[in[i] >> 4];
        out[2 * i + 1] = hex[in[i] & 0x0F];
    }
    out[2 * n] = '\0';
}

static bool hex_equal_ci(const char *a, const char *b, size_t n)
{
    for (size_t i = 0; i < n; i++) {
        if (tolower((unsigned char)a[i]) != tolower((unsigned char)b[i])) {
            return false;
        }
    }
    return true;
}

/* ------------------------------------------------------------------ NVS ota_cfg */
static esp_err_t cfg_load(ota_cfg_t *c)
{
    memset(c, 0, sizeof(*c));
    nvs_handle_t h;
    esp_err_t err = nvs_open(OTA_NS, NVS_READONLY, &h);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "nvs_open %s loi: %s (chua nap wifi_ota.csv?)", OTA_NS, esp_err_to_name(err));
        return err;
    }
    size_t len = sizeof(c->ssid);
    err = nvs_get_str(h, "ssid", c->ssid, &len);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "thieu ssid: %s", esp_err_to_name(err));
        nvs_close(h);
        return err;
    }
    len = sizeof(c->pass);
    err = nvs_get_str(h, "pass", c->pass, &len);
    if (err != ESP_OK && err != ESP_ERR_NVS_NOT_FOUND) {
        ESP_LOGE(TAG, "doc pass loi: %s", esp_err_to_name(err));
        nvs_close(h);
        return err;
    }
    len = sizeof(c->url);
    err = nvs_get_str(h, "url", c->url, &len);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "thieu url: %s", esp_err_to_name(err));
        nvs_close(h);
        return err;
    }
    err = nvs_get_u32(h, "max_ver", &c->max_ver);
    if (err == ESP_ERR_NVS_NOT_FOUND) {
        c->max_ver = 0;
        err = ESP_OK;
    }
    nvs_close(h);

    /* bỏ dấu '/' cuối cho dễ ghép đường dẫn */
    size_t ul = strlen(c->url);
    while (ul > 0 && c->url[ul - 1] == '/') {
        c->url[--ul] = '\0';
    }
    if (strncmp(c->url, "https://", 8) != 0) {
        ESP_LOGE(TAG, "url phai bat dau bang https:// (O-08)");
        return ESP_ERR_INVALID_ARG;
    }
    return err;
}

static esp_err_t cfg_set_u32(const char *key, uint32_t v)
{
    nvs_handle_t h;
    esp_err_t err = nvs_open(OTA_NS, NVS_READWRITE, &h);
    if (err != ESP_OK) {
        return err;
    }
    err = nvs_set_u32(h, key, v);
    if (err == ESP_OK) {
        err = nvs_commit(h);
    }
    nvs_close(h);
    return err;
}

static uint32_t cfg_get_u32(const char *key, uint32_t def)
{
    nvs_handle_t h;
    uint32_t v = def;
    if (nvs_open(OTA_NS, NVS_READONLY, &h) == ESP_OK) {
        if (nvs_get_u32(h, key, &v) != ESP_OK) {
            v = def;
        }
        nvs_close(h);
    }
    return v;
}

/* ------------------------------------------------------------------ Wi-Fi (chỉ bật trong OTA) */
static void wifi_event_handler(void *arg, esp_event_base_t base, int32_t id, void *data)
{
    (void)arg;
    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_START) {
        esp_err_t e = esp_wifi_connect();
        if (e != ESP_OK) {
            ESP_LOGW(TAG, "esp_wifi_connect: %s", esp_err_to_name(e));
        }
    } else if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) {
        if (s_wifi_retry < OTA_WIFI_RETRY_MAX) {
            s_wifi_retry++;
            ESP_LOGW(TAG, "Wi-Fi mat ket noi, thu lai %d/%d", s_wifi_retry, OTA_WIFI_RETRY_MAX);
            (void)esp_wifi_connect();
        } else {
            xEventGroupSetBits(s_wifi_eg, WIFI_FAIL_BIT);
        }
    } else if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
        const ip_event_got_ip_t *ev = (const ip_event_got_ip_t *)data;
        ESP_LOGI(TAG, "Wi-Fi IP: " IPSTR, IP2STR(&ev->ip_info.ip));
        xEventGroupSetBits(s_wifi_eg, WIFI_CONNECTED_BIT);
    }
}

static void wifi_down(void)
{
    (void)esp_event_handler_instance_unregister(WIFI_EVENT, ESP_EVENT_ANY_ID, s_h_wifi);
    (void)esp_event_handler_instance_unregister(IP_EVENT, IP_EVENT_STA_GOT_IP, s_h_ip);
    (void)esp_wifi_disconnect();
    (void)esp_wifi_stop();
    esp_err_t err = esp_wifi_deinit();
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "esp_wifi_deinit: %s", esp_err_to_name(err));
    }
    ESP_LOGI(TAG, "Wi-Fi da tat");
}

static esp_err_t wifi_up(const ota_cfg_t *c, uint32_t timeout_ms)
{
    esp_err_t err;
    if (!s_netif_ready) {
        err = esp_netif_init();
        if (err != ESP_OK) {
            return err;
        }
        err = esp_event_loop_create_default();
        if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) {
            return err;
        }
        s_sta_netif = esp_netif_create_default_wifi_sta();
        if (s_sta_netif == NULL) {
            return ESP_FAIL;
        }
        s_netif_ready = true;
    }

    wifi_init_config_t icfg = WIFI_INIT_CONFIG_DEFAULT();
    err = esp_wifi_init(&icfg);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "esp_wifi_init: %s", esp_err_to_name(err));
        return err;
    }
    (void)esp_wifi_set_storage(WIFI_STORAGE_RAM); /* không ghi SSID/pass vào NVS của driver */

    err = esp_event_handler_instance_register(WIFI_EVENT, ESP_EVENT_ANY_ID, wifi_event_handler, NULL, &s_h_wifi);
    if (err == ESP_OK) {
        err = esp_event_handler_instance_register(IP_EVENT, IP_EVENT_STA_GOT_IP, wifi_event_handler, NULL, &s_h_ip);
    }
    if (err != ESP_OK) {
        wifi_down();
        return err;
    }

    wifi_config_t wc = { 0 };
    strlcpy((char *)wc.sta.ssid, c->ssid, sizeof(wc.sta.ssid));
    strlcpy((char *)wc.sta.password, c->pass, sizeof(wc.sta.password));
    wc.sta.threshold.authmode = (c->pass[0] != '\0') ? WIFI_AUTH_WPA2_PSK : WIFI_AUTH_OPEN;
    wc.sta.pmf_cfg.capable = true;
    wc.sta.pmf_cfg.required = false;

    xEventGroupClearBits(s_wifi_eg, WIFI_CONNECTED_BIT | WIFI_FAIL_BIT);
    s_wifi_retry = 0;
    err = esp_wifi_set_mode(WIFI_MODE_STA);
    if (err == ESP_OK) {
        err = esp_wifi_set_config(WIFI_IF_STA, &wc);
    }
    if (err == ESP_OK) {
        err = esp_wifi_start();
    }
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "cau hinh/khoi dong Wi-Fi loi: %s", esp_err_to_name(err));
        wifi_down();
        return err;
    }
    ESP_LOGI(TAG, "ket noi Wi-Fi '%s' (toi da %lu ms)...", c->ssid, (unsigned long)timeout_ms);
    EventBits_t bits = xEventGroupWaitBits(s_wifi_eg, WIFI_CONNECTED_BIT | WIFI_FAIL_BIT, pdTRUE, pdFALSE,
                                           pdMS_TO_TICKS(timeout_ms));
    if (bits & WIFI_CONNECTED_BIT) {
        return ESP_OK;
    }
    ESP_LOGE(TAG, "Wi-Fi khong lay duoc IP (%s)", (bits & WIFI_FAIL_BIT) ? "that bai" : "timeout");
    wifi_down();
    return ESP_ERR_TIMEOUT;
}

/* ------------------------------------------------------------------ HTTP(S) */
static esp_err_t http_evt(esp_http_client_event_t *evt)
{
    http_ctx_t *ctx = (http_ctx_t *)evt->user_data;
    if ((evt->event_id == HTTP_EVENT_DISCONNECTED || evt->event_id == HTTP_EVENT_ERROR) && evt->data != NULL && ctx) {
        int tls_code = 0, tls_flags = 0;
        esp_err_t e = esp_tls_get_and_clear_last_error((esp_tls_error_handle_t)evt->data, &tls_code, &tls_flags);
        if (tls_flags != 0 || e == ESP_ERR_MBEDTLS_SSL_HANDSHAKE_FAILED || e == ESP_ERR_MBEDTLS_X509_CRT_PARSE_FAILED) {
            ESP_LOGE(TAG, "TLS loi: %s (mbedtls=-0x%x, verify_flags=0x%x)", esp_err_to_name(e), -tls_code, tls_flags);
            ctx->tls_err = true;
        }
    }
    return ESP_OK;
}

static esp_http_client_handle_t http_client_new(const char *url, http_ctx_t *ctx, int timeout_ms)
{
    esp_http_client_config_t cfg = {
        .url = url,
        .cert_pem = OTA_CA_PEM_START,               /* O-09: xác minh máy chủ bằng CA nhúng */
        .cert_len = 0,                              /* 0 → strlen (EMBED_TXTFILES đã thêm NUL) */
        .timeout_ms = timeout_ms,
        .event_handler = http_evt,
        .user_data = ctx,
        .buffer_size = 2048,
        .buffer_size_tx = 1024,
        .keep_alive_enable = false,
        /* KHÔNG đặt skip_cert_common_name_check, KHÔNG đặt crt_bundle_attach, KHÔNG dùng http:// */
    };
    return esp_http_client_init(&cfg);
}

/* GET file nhỏ (manifest/sig) với giới hạn kích thước. buf có cap+1 byte (NUL cuối). */
static esp_err_t http_get_small(const char *url, uint8_t *buf, size_t cap, size_t *out_len, bool *tls_err)
{
    http_ctx_t ctx = { 0 };
    esp_http_client_handle_t cl = http_client_new(url, &ctx, OTA_HTTP_TIMEOUT_MS);
    if (cl == NULL) {
        return ESP_ERR_NO_MEM;
    }
    esp_err_t err = esp_http_client_open(cl, 0);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "GET %s: open loi %s", url, esp_err_to_name(err));
        goto out;
    }
    int64_t clen = esp_http_client_fetch_headers(cl);
    int status = esp_http_client_get_status_code(cl);
    if (clen < 0 || status != 200) {
        ESP_LOGE(TAG, "GET %s: status=%d len=%lld", url, status, (long long)clen);
        err = ESP_FAIL;
        goto out;
    }
    if (clen > (int64_t)cap) {
        ESP_LOGE(TAG, "GET %s: qua lon (%lld > %u)", url, (long long)clen, (unsigned)cap);
        err = ESP_ERR_INVALID_SIZE;
        goto out;
    }
    size_t total = 0;
    for (;;) {
        if (total >= cap) {
            /* còn dữ liệu nữa? đọc thử 1 byte */
            uint8_t extra;
            int r = esp_http_client_read(cl, (char *)&extra, 1);
            if (r > 0) {
                ESP_LOGE(TAG, "GET %s: vuot gioi han %u byte", url, (unsigned)cap);
                err = ESP_ERR_INVALID_SIZE;
            }
            break;
        }
        int r = esp_http_client_read(cl, (char *)buf + total, (int)(cap - total));
        if (r < 0) {
            ESP_LOGE(TAG, "GET %s: read loi", url);
            err = ESP_FAIL;
            break;
        }
        if (r == 0) {
            break;
        }
        total += (size_t)r;
    }
    if (err == ESP_OK && total == 0) {
        err = ESP_FAIL;
    }
    buf[total < cap ? total : cap] = '\0';
    *out_len = total;
out:
    (void)esp_http_client_close(cl);
    (void)esp_http_client_cleanup(cl);
    if (tls_err) {
        *tls_err = ctx.tls_err;
    }
    return err;
}

/* ------------------------------------------------------------------ xác minh chữ ký (O-10) */
static esp_err_t verify_manifest_sig(const uint8_t *manifest, size_t mlen, const char *sig_b64_in, size_t slen_in)
{
    /* bỏ khoảng trắng/xuống dòng trong sig */
    char sig_b64[OTA_SIG_MAX + 1];
    size_t slen = 0;
    for (size_t i = 0; i < slen_in && slen < OTA_SIG_MAX; i++) {
        if (!isspace((unsigned char)sig_b64_in[i])) {
            sig_b64[slen++] = sig_b64_in[i];
        }
    }
    sig_b64[slen] = '\0';

    uint8_t der[128];
    size_t dlen = 0;
    int rc = mbedtls_base64_decode(der, sizeof(der), &dlen, (const unsigned char *)sig_b64, slen);
    if (rc != 0 || dlen == 0) {
        ESP_LOGE(TAG, "base64 sig loi (-0x%x)", -rc);
        return ESP_ERR_INVALID_ARG;
    }

    uint8_t hash[32];
    rc = mbedtls_sha256(manifest, mlen, hash, 0);
    if (rc != 0) {
        ESP_LOGE(TAG, "sha256 manifest loi (-0x%x)", -rc);
        return ESP_FAIL;
    }

    mbedtls_pk_context pk;
    mbedtls_pk_init(&pk);
    /* PEM phải tính cả byte NUL cuối */
    rc = mbedtls_pk_parse_public_key(&pk, (const unsigned char *)OTA_PUBKEY_PEM, sizeof(OTA_PUBKEY_PEM));
    if (rc != 0) {
        ESP_LOGE(TAG, "parse public key loi (-0x%x) — ota_pubkey.h sai/placeholder?", -rc);
        mbedtls_pk_free(&pk);
        return ESP_ERR_INVALID_STATE;
    }
    if (!mbedtls_pk_can_do(&pk, MBEDTLS_PK_ECDSA)) {
        ESP_LOGE(TAG, "public key khong phai ECDSA");
        mbedtls_pk_free(&pk);
        return ESP_ERR_INVALID_STATE;
    }
    rc = mbedtls_pk_verify(&pk, MBEDTLS_MD_SHA256, hash, sizeof(hash), der, dlen);
    mbedtls_pk_free(&pk);
    if (rc != 0) {
        ESP_LOGE(TAG, "CHU KY MANIFEST SAI (-0x%x)", -rc);
        return ESP_ERR_INVALID_RESPONSE;
    }
    ESP_LOGI(TAG, "chu ky manifest hop le");
    return ESP_OK;
}

/* ------------------------------------------------------------------ parse manifest (4.5) */
static esp_err_t parse_manifest(const char *json, manifest_t *m)
{
    memset(m, 0, sizeof(*m));
    cJSON *root = cJSON_Parse(json);
    if (root == NULL) {
        ESP_LOGE(TAG, "manifest JSON loi");
        return ESP_ERR_INVALID_ARG;
    }
    esp_err_t err = ESP_ERR_INVALID_ARG;
    const cJSON *model = cJSON_GetObjectItemCaseSensitive(root, "device_model");
    const cJSON *vc = cJSON_GetObjectItemCaseSensitive(root, "version_code");
    const cJSON *vn = cJSON_GetObjectItemCaseSensitive(root, "version_name");
    const cJSON *sha = cJSON_GetObjectItemCaseSensitive(root, "fw_sha256");
    const cJSON *sz = cJSON_GetObjectItemCaseSensitive(root, "fw_size");
    if (!cJSON_IsString(model) || !cJSON_IsNumber(vc) || !cJSON_IsString(sha) || !cJSON_IsNumber(sz)) {
        ESP_LOGE(TAG, "manifest thieu truong bat buoc");
        goto out;
    }
    if (strlen(sha->valuestring) != 64 || vc->valuedouble < 0 || sz->valuedouble <= 0) {
        ESP_LOGE(TAG, "manifest gia tri khong hop le");
        goto out;
    }
    strlcpy(m->device_model, model->valuestring, sizeof(m->device_model));
    strlcpy(m->fw_sha256, sha->valuestring, sizeof(m->fw_sha256));
    if (cJSON_IsString(vn)) {
        strlcpy(m->version_name, vn->valuestring, sizeof(m->version_name));
    }
    m->version_code = (uint32_t)vc->valuedouble;
    m->fw_size = (uint32_t)sz->valuedouble;
    err = ESP_OK;
out:
    cJSON_Delete(root);
    return err;
}

/* ------------------------------------------------------------------ báo cáo (O-14, best-effort) */
static void send_report(const ota_cfg_t *c, uint32_t version_code, const char *result, const char *reason)
{
    cJSON *o = cJSON_CreateObject();
    if (o == NULL) {
        return;
    }
    cJSON_AddStringToObject(o, "device", CONFIG_SORTER_OTA_DEVICE_ID);
    cJSON_AddNumberToObject(o, "version_code", (double)version_code);
    cJSON_AddStringToObject(o, "result", result);
    cJSON_AddStringToObject(o, "reason", reason);
    char *body = cJSON_PrintUnformatted(o);
    cJSON_Delete(o);
    if (body == NULL) {
        return;
    }

    char url[192];
    snprintf(url, sizeof(url), "%s/report", c->url);
    http_ctx_t ctx = { 0 };
    esp_http_client_handle_t cl = http_client_new(url, &ctx, OTA_REPORT_TIMEOUT);
    if (cl != NULL) {
        esp_err_t err = esp_http_client_set_method(cl, HTTP_METHOD_POST);
        if (err == ESP_OK) {
            err = esp_http_client_set_header(cl, "Content-Type", "application/json");
        }
        if (err == ESP_OK) {
            err = esp_http_client_set_post_field(cl, body, (int)strlen(body));
        }
        if (err == ESP_OK) {
            err = esp_http_client_perform(cl);
        }
        if (err == ESP_OK) {
            ESP_LOGI(TAG, "report %s → HTTP %d", result, esp_http_client_get_status_code(cl));
        } else {
            ESP_LOGW(TAG, "report that bai (bo qua): %s", esp_err_to_name(err));
        }
        (void)esp_http_client_cleanup(cl);
    }
    cJSON_free(body);
}

/* ------------------------------------------------------------------ tải firmware (O-12) */
static esp_err_t download_firmware(const char *url, const esp_partition_t *part, const manifest_t *m, uint8_t *reason)
{
    *reason = OTA_REASON_DOWNLOAD_ERROR;
    uint8_t *chunk = malloc(OTA_CHUNK);
    if (chunk == NULL) {
        return ESP_ERR_NO_MEM;
    }
    http_ctx_t ctx = { 0 };
    esp_http_client_handle_t cl = http_client_new(url, &ctx, OTA_HTTP_TIMEOUT_MS);
    if (cl == NULL) {
        free(chunk);
        return ESP_ERR_NO_MEM;
    }

    esp_ota_handle_t oh = 0;
    bool ota_begun = false;
    mbedtls_sha256_context sha;
    mbedtls_sha256_init(&sha);
    esp_err_t err = esp_http_client_open(cl, 0);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "GET firmware: open loi %s", esp_err_to_name(err));
        if (ctx.tls_err) {
            *reason = OTA_REASON_TLS_ERROR;
        }
        goto fail;
    }
    int64_t clen = esp_http_client_fetch_headers(cl);
    int status = esp_http_client_get_status_code(cl);
    if (status != 200) {
        ESP_LOGE(TAG, "GET firmware: status=%d", status);
        err = ESP_FAIL;
        goto fail;
    }
    /* clen == 0 khi máy chủ dùng chunked encoding → chỉ kiểm tra bằng tổng byte đọc được */
    if (clen > 0 && (uint32_t)clen != m->fw_size) {
        ESP_LOGE(TAG, "Content-Length %lld != fw_size %lu", (long long)clen, (unsigned long)m->fw_size);
        err = ESP_FAIL;
        *reason = OTA_REASON_HASH_MISMATCH;
        goto fail;
    }

    err = esp_ota_begin(part, OTA_WITH_SEQUENTIAL_WRITES, &oh);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "esp_ota_begin: %s", esp_err_to_name(err));
        goto fail;
    }
    ota_begun = true;
    if (mbedtls_sha256_starts(&sha, 0) != 0) {
        err = ESP_FAIL;
        goto fail;
    }

    uint32_t total = 0;
    int last_pct = -1;
    for (;;) {
        if (s_abort_req) {
            ESP_LOGW(TAG, "OTA bi huy theo yeu cau (ESTOP)");
            err = ESP_ERR_INVALID_STATE;
            *reason = OTA_REASON_BAD_STATE;
            goto fail;
        }
        int r = esp_http_client_read(cl, (char *)chunk, OTA_CHUNK);
        if (r < 0) {
            ESP_LOGE(TAG, "read firmware loi");
            err = ESP_FAIL;
            goto fail;
        }
        if (r == 0) {
            break;
        }
        total += (uint32_t)r;
        if (total > m->fw_size) {
            ESP_LOGE(TAG, "du lieu vuot fw_size");
            err = ESP_FAIL;
            *reason = OTA_REASON_HASH_MISMATCH;
            goto fail;
        }
        err = esp_ota_write(oh, chunk, (size_t)r);
        if (err != ESP_OK) {
            ESP_LOGE(TAG, "esp_ota_write: %s", esp_err_to_name(err));
            goto fail;
        }
        if (mbedtls_sha256_update(&sha, chunk, (size_t)r) != 0) {
            err = ESP_FAIL;
            goto fail;
        }
        int pct = (int)((uint64_t)total * 100u / m->fw_size);
        if (pct / 10 != last_pct / 10 || last_pct < 0) {
            last_pct = pct;
            notify(OTA_STAGE_DOWNLOADING, OTA_REASON_NONE, (uint8_t)pct, m->version_code);
        }
    }

    if (total != m->fw_size) {
        ESP_LOGE(TAG, "kich thuoc %lu != fw_size %lu", (unsigned long)total, (unsigned long)m->fw_size);
        err = ESP_FAIL;
        *reason = OTA_REASON_HASH_MISMATCH;
        goto fail;
    }
    uint8_t digest[32];
    if (mbedtls_sha256_finish(&sha, digest) != 0) {
        err = ESP_FAIL;
        goto fail;
    }
    char hex[65];
    to_hex(digest, sizeof(digest), hex);
    if (!hex_equal_ci(hex, m->fw_sha256, 64)) {
        ESP_LOGE(TAG, "SHA-256 SAI: tinh=%s manifest=%s", hex, m->fw_sha256);
        err = ESP_FAIL;
        *reason = OTA_REASON_HASH_MISMATCH;
        goto fail;
    }
    ESP_LOGI(TAG, "SHA-256 khop: %s", hex);

    /* esp_ota_end: kiểm tra header ảnh + (khi bật Secure Boot V2) chữ ký secure boot (O-15) */
    err = esp_ota_end(oh);
    ota_begun = false;
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "esp_ota_end: %s", esp_err_to_name(err));
        *reason = OTA_REASON_HASH_MISMATCH;
        goto fail;
    }
    *reason = OTA_REASON_NONE;
    err = ESP_OK;
    goto done;

fail:
    if (ota_begun) {
        (void)esp_ota_abort(oh);  /* phân vùng đang chạy không đổi */
    }
    if (err == ESP_OK) {
        err = ESP_FAIL;
    }
done:
    mbedtls_sha256_free(&sha);
    (void)esp_http_client_close(cl);
    (void)esp_http_client_cleanup(cl);
    free(chunk);
    return err;
}

/* ------------------------------------------------------------------ task OTA */
static void ota_task(void *arg)
{
    (void)arg;
    ota_cfg_t cfg;
    uint8_t reason = OTA_REASON_DOWNLOAD_ERROR;
    uint32_t ver = 0;
    bool wifi_on = false;
    bool rejected = false;

    notify(OTA_STAGE_CHECKING, OTA_REASON_NONE, 0, s_cfg.running_version_code);

    /* a) cấu hình + Wi-Fi */
    if (cfg_load(&cfg) != ESP_OK) {
        reason = OTA_REASON_DOWNLOAD_ERROR;
        goto reject;
    }
    if (wifi_up(&cfg, (uint32_t)CONFIG_SORTER_OTA_WIFI_TIMEOUT_S * 1000u) != ESP_OK) {
        reason = OTA_REASON_DOWNLOAD_ERROR;
        goto reject;
    }
    wifi_on = true;

    /* b) manifest.json + manifest.sig */
    static uint8_t manifest[OTA_MANIFEST_MAX + 1];
    static uint8_t sig[OTA_SIG_MAX + 1];
    size_t mlen = 0, slen = 0;
    bool tls_err = false;
    char url[224];

    snprintf(url, sizeof(url), "%s/manifest.json", cfg.url);
    if (http_get_small(url, manifest, OTA_MANIFEST_MAX, &mlen, &tls_err) != ESP_OK) {
        reason = tls_err ? OTA_REASON_TLS_ERROR : OTA_REASON_DOWNLOAD_ERROR;
        goto reject;
    }
    snprintf(url, sizeof(url), "%s/manifest.sig", cfg.url);
    if (http_get_small(url, sig, OTA_SIG_MAX, &slen, &tls_err) != ESP_OK) {
        reason = tls_err ? OTA_REASON_TLS_ERROR : OTA_REASON_DOWNLOAD_ERROR;
        goto reject;
    }

    /* c) chữ ký trên đúng các byte manifest */
    if (verify_manifest_sig(manifest, mlen, (const char *)sig, slen) != ESP_OK) {
        reason = OTA_REASON_SIGNATURE_INVALID;
        goto reject;
    }

    /* d) nội dung manifest */
    manifest_t m;
    if (parse_manifest((const char *)manifest, &m) != ESP_OK) {
        reason = OTA_REASON_DOWNLOAD_ERROR;
        goto reject;
    }
    ver = m.version_code;
    ESP_LOGI(TAG, "manifest: model=%s version=%lu (%s) size=%lu", m.device_model, (unsigned long)m.version_code,
             m.version_name, (unsigned long)m.fw_size);
    if (strcmp(m.device_model, OTA_DEVICE_MODEL) != 0) {
        reason = OTA_REASON_MODEL_MISMATCH;
        goto reject;
    }
    if (m.version_code == s_cfg.running_version_code) {
        ESP_LOGI(TAG, "da la ban moi nhat");
        notify(OTA_STAGE_UP_TO_DATE, OTA_REASON_NONE, 100, ver);
        goto finish;
    }
    if (m.version_code < s_cfg.running_version_code || m.version_code <= cfg.max_ver) {
        ESP_LOGE(TAG, "CHONG ROLLBACK: version %lu <= dang chay %lu hoac <= max_ver %lu", (unsigned long)m.version_code,
                 (unsigned long)s_cfg.running_version_code, (unsigned long)cfg.max_ver);
        reason = OTA_REASON_ROLLBACK_REJECTED;
        goto reject;
    }
    const esp_partition_t *next = esp_ota_get_next_update_partition(NULL);
    if (next == NULL) {
        ESP_LOGE(TAG, "khong co phan vung OTA ke tiep");
        reason = OTA_REASON_DOWNLOAD_ERROR;
        goto reject;
    }
    if (m.fw_size > next->size) {
        ESP_LOGE(TAG, "fw_size %lu > phan vung %s %lu", (unsigned long)m.fw_size, next->label, (unsigned long)next->size);
        reason = OTA_REASON_HASH_MISMATCH;
        goto reject;
    }
    ESP_LOGI(TAG, "ghi vao phan vung %s @0x%lx", next->label, (unsigned long)next->address);

    /* e) tải + ghi + SHA-256 */
    notify(OTA_STAGE_DOWNLOADING, OTA_REASON_NONE, 0, ver);
    snprintf(url, sizeof(url), "%s/firmware.bin", cfg.url);
    if (download_firmware(url, next, &m, &reason) != ESP_OK) {
        goto reject;
    }

    /* f) đã xác minh */
    notify(OTA_STAGE_VERIFIED, OTA_REASON_NONE, 100, ver);
    if (esp_ota_set_boot_partition(next) != ESP_OK) {
        ESP_LOGE(TAG, "esp_ota_set_boot_partition loi");
        reason = OTA_REASON_DOWNLOAD_ERROR;
        goto reject;
    }
    if (cfg_set_u32("max_ver", m.version_code) != ESP_OK) {
        ESP_LOGW(TAG, "khong luu duoc max_ver (van tiep tuc)");
    }
    send_report(&cfg, ver, "SUCCESS", "VERIFIED_REBOOTING");
    notify(OTA_STAGE_REBOOTING, OTA_REASON_NONE, 100, ver);
    wifi_down();
    vTaskDelay(pdMS_TO_TICKS(500)); /* cho link_tx kịp gửi EVT_OTA_STATUS */
    esp_restart();

reject:
    rejected = true;
    ESP_LOGE(TAG, "OTA TU CHOI: %s", reason_str(reason));
    if (wifi_on) {
        send_report(&cfg, ver ? ver : s_cfg.running_version_code, "REJECTED", reason_str(reason));
    }
    notify(OTA_STAGE_REJECTED, reason, 0, ver ? ver : s_cfg.running_version_code);
finish:
    (void)rejected;
    if (wifi_on) {
        wifi_down();
    }
    s_busy = false;
    vTaskDelete(NULL);
}

/* ------------------------------------------------------------------ O-13: tự kiểm tra sau khởi động */
static void boot_task(void *arg)
{
    (void)arg;
    const esp_partition_t *running = esp_ota_get_running_partition();
    esp_ota_img_states_t st = ESP_OTA_IMG_UNDEFINED;
    uint32_t ver = s_cfg.running_version_code;

    if (running != NULL && esp_ota_get_state_partition(running, &st) == ESP_OK && st == ESP_OTA_IMG_PENDING_VERIFY) {
        ESP_LOGW(TAG, "anh moi dang cho xac nhan (%s) — self-test: cho PING tu Pi toi da %d s", running->label,
                 CONFIG_SORTER_OTA_SELFTEST_TIMEOUT_S);
        bool ok = link_wait_first_ping((uint32_t)CONFIG_SORTER_OTA_SELFTEST_TIMEOUT_S * 1000u);
#if CONFIG_SORTER_OTA_SELFTEST_FAIL_DEMO
        ok = false;
#endif
        if (ok) {
            esp_err_t err = esp_ota_mark_app_valid_cancel_rollback();
            ESP_LOGI(TAG, "self-test DAT → mark valid: %s", esp_err_to_name(err));
            notify(OTA_STAGE_SUCCESS, OTA_REASON_NONE, 100, ver);
            ota_cfg_t cfg;
            if (cfg_load(&cfg) == ESP_OK && wifi_up(&cfg, (uint32_t)CONFIG_SORTER_OTA_WIFI_TIMEOUT_S * 1000u) == ESP_OK) {
                send_report(&cfg, ver, "SUCCESS", "SELFTEST_OK");
                wifi_down();
            }
        } else {
            ESP_LOGE(TAG, "self-test THAT BAI → quay ve ban cu");
            notify(OTA_STAGE_REJECTED, OTA_REASON_SELFTEST_FAILED, 0, ver);
            vTaskDelay(pdMS_TO_TICKS(500));
            esp_err_t err = esp_ota_mark_app_invalid_rollback_and_reboot();
            /* chỉ tới đây nếu không quay lui được */
            ESP_LOGE(TAG, "rollback loi: %s", esp_err_to_name(err));
        }
    } else {
        const esp_partition_t *inv = esp_ota_get_last_invalid_partition();
        if (inv != NULL) {
            uint32_t reported = cfg_get_u32("rb_addr", 0xFFFFFFFFu);
            if (reported != inv->address) {
                ESP_LOGW(TAG, "phat hien vua quay lui tu %s → bao ROLLED_BACK", inv->label);
                (void)link_wait_first_ping(60000); /* chờ Pi lên để sự kiện tới nơi */
                notify(OTA_STAGE_ROLLED_BACK, OTA_REASON_SELFTEST_FAILED, 0, ver);
                ota_cfg_t cfg;
                if (cfg_load(&cfg) == ESP_OK && wifi_up(&cfg, (uint32_t)CONFIG_SORTER_OTA_WIFI_TIMEOUT_S * 1000u) == ESP_OK) {
                    send_report(&cfg, ver, "ROLLED_BACK", "SELFTEST_FAILED");
                    wifi_down();
                }
                (void)cfg_set_u32("rb_addr", inv->address);
            }
        }
    }
    vTaskDelete(NULL);
}

/* ------------------------------------------------------------------ API */
esp_err_t ota_secure_init(const ota_secure_config_t *cfg)
{
    if (cfg == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    s_cfg = *cfg;
    if (s_wifi_eg == NULL) {
        s_wifi_eg = xEventGroupCreate();
        if (s_wifi_eg == NULL) {
            return ESP_ERR_NO_MEM;
        }
    }
    size_t ca_len = (size_t)(OTA_CA_PEM_END - OTA_CA_PEM_START);
    ESP_LOGI(TAG, "init OK: running version_code=%lu, CA nhung %u byte, key id %s", (unsigned long)s_cfg.running_version_code,
             (unsigned)ca_len, OTA_PUBKEY_ID);
    return ESP_OK;
}

void ota_secure_set_status_cb(ota_secure_status_cb_t cb)
{
    s_cfg.status_cb = cb;
}

esp_err_t ota_secure_start(void)
{
    if (s_busy) {
        return ESP_ERR_INVALID_STATE;
    }
    s_busy = true;
    s_abort_req = false;
    if (xTaskCreate(ota_task, "ota", OTA_TASK_STACK, NULL, 5, NULL) != pdPASS) {
        s_busy = false;
        return ESP_ERR_NO_MEM;
    }
    return ESP_OK;
}

void ota_secure_abort(void)
{
    s_abort_req = true;
}

esp_err_t ota_secure_on_boot(void)
{
    if (xTaskCreate(boot_task, "ota_boot", OTA_TASK_STACK, NULL, 4, NULL) != pdPASS) {
        return ESP_ERR_NO_MEM;
    }
    return ESP_OK;
}

bool ota_secure_is_busy(void)
{
    return s_busy;
}
