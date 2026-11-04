/* E-09 / E-10 / E-12 — Liên kết UART2 (GPIO16 RX, GPIO17 TX, 115200 8N1) với Pi.
 *
 * Luồng:
 *   task link_rx  : uart_read_bytes → proto_decoder_feed → handle_frame(); đăng ký Task WDT.
 *   task link_tx  : lấy sự kiện từ s_tx_queue → gán SEQ → gửi → chờ ACK/NACK (semaphore, 200 ms)
 *                   → gửi lại cùng SEQ tối đa PROTO_MAX_RETRIES lần.
 *   timer 100 ms  : kiểm tra mất liên kết (E-09); mỗi 10 tick gửi STATUS (E-12).
 *
 * Quy tắc trả lời (4.2): PING→PONG, GET_STATUS→STATUS (không ACK); ACK/NACK/PONG/STATUS không cần ACK.
 * Sai LEN → NACK BAD_LEN; TYPE lạ hoặc TYPE hướng ESP32→Pi (trừ ACK/NACK của Pi) → NACK UNKNOWN_TYPE.
 * Chống lặp: lệnh vừa xử lý được nhớ (seq/type/len/payload); khung trùng → gửi lại đúng ACK/NACK đã trả;
 * nếu fsm chưa kịp trả lời thì bỏ khung trùng (không đẩy lệnh lần hai).
 */
#include "link.h"

#include <string.h>
#include "board.h"
#include "driver/uart.h"
#include "esp_bit_defs.h"
#include "esp_log.h"
#include "esp_system.h"
#include "esp_task_wdt.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "freertos/queue.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "sdkconfig.h"

static const char *TAG = "link";

#define LINK_UART        ((uart_port_t)BOARD_UART_PORT_NUM)
#define LINK_RX_BUF      1024
#define LINK_TX_BUF      1024
#define LINK_TX_QUEUE    16
#define LINK_TICK_MS     100
#define LINK_STATUS_TICK (1000 / LINK_TICK_MS)
#define EV_FIRST_PING    BIT0

typedef struct {
    uint8_t type;
    uint8_t len;
    uint8_t payload[PROTO_MAX_PAYLOAD];
} tx_item_t;

static link_config_t s_cfg;
static proto_decoder_t s_dec;
static QueueHandle_t s_tx_queue;
static SemaphoreHandle_t s_ack_sem;     /* nhị phân: ACK/NACK cho SEQ đang chờ */
static SemaphoreHandle_t s_dedupe_mux;  /* bảo vệ s_last */
static EventGroupHandle_t s_events;
static esp_timer_handle_t s_tick_timer;
static portMUX_TYPE s_lock = portMUX_INITIALIZER_UNLOCKED;

static uint8_t s_tx_seq;                /* bộ đếm SEQ của ESP32 (tăng vòng) */
static volatile bool s_waiting;         /* task tx đang chờ ACK */
static volatile uint8_t s_wait_seq;
static volatile bool s_got_nack;
static int64_t s_last_rx_us;
static bool s_link_up;
static uint32_t s_tick;
static bool s_inited;

/* Chống lặp */
static struct {
    bool valid;
    bool has_reply;
    proto_frame_t cmd;
    uint8_t reply[PROTO_MAX_FRAME];
    size_t reply_len;
} s_last;

/* ------------------------------------------------------------------ tiện ích */
static uint8_t next_seq(void)
{
    portENTER_CRITICAL(&s_lock);
    uint8_t s = s_tx_seq++;
    portEXIT_CRITICAL(&s_lock);
    return s;
}

/* Ghi một khung ra UART. uart_write_bytes có mutex tx nên gọi được từ nhiều task. */
static esp_err_t write_frame(const uint8_t *buf, size_t n)
{
    int w = uart_write_bytes(LINK_UART, buf, n);
    if (w < 0 || (size_t)w != n) {
        ESP_LOGE(TAG, "uart_write_bytes loi (%d/%u)", w, (unsigned)n);
        return ESP_FAIL;
    }
    return ESP_OK;
}

/* Đóng khung với SEQ mới và gửi ngay (PONG/STATUS/ACK/NACK). out/out_len tùy chọn để lưu bản sao. */
static esp_err_t send_now(uint8_t type, const void *payload, uint8_t len, uint8_t *out, size_t *out_len)
{
    uint8_t buf[PROTO_MAX_FRAME];
    size_t n = proto_encode(buf, sizeof(buf), next_seq(), type, payload, len);
    if (n == 0) {
        return ESP_ERR_INVALID_SIZE;
    }
    if (out && out_len) {
        memcpy(out, buf, n);
        *out_len = n;
    }
    return write_frame(buf, n);
}

static void send_status(void)
{
    proto_status_t st = {
        .state = s_cfg.get_state ? s_cfg.get_state() : 0,
        .fw_version_code = s_cfg.fw_version_code,
        .uptime_s = (uint32_t)(esp_timer_get_time() / 1000000LL),
        .pkg_count = s_cfg.get_pkg_count ? s_cfg.get_pkg_count() : 0,
        .crc_err = link_get_crc_errors(),
        .reset_reason = (uint8_t)esp_reset_reason(),
    };
    (void)send_now(MSG_STATUS, &st, sizeof(st), NULL, NULL);
}

static void send_pong(void)
{
    proto_pong_t p = { .state = s_cfg.get_state ? s_cfg.get_state() : 0 };
    (void)send_now(MSG_PONG, &p, sizeof(p), NULL, NULL);
}

/* NACK cho khung không đi qua fsm (BAD_LEN / UNKNOWN_TYPE) */
static void send_nack_raw(uint8_t acked_seq, uint8_t err)
{
    proto_nack_t n = { .acked_seq = acked_seq, .err = err };
    (void)send_now(MSG_NACK, &n, sizeof(n), NULL, NULL);
}

static bool frame_equal(const proto_frame_t *a, const proto_frame_t *b)
{
    return a->seq == b->seq && a->type == b->type && a->len == b->len &&
           memcmp(a->payload, b->payload, a->len) == 0;
}

/* Lưu bản sao trả lời cho chống lặp */
static esp_err_t reply_cmd(const proto_frame_t *cmd, uint8_t type, const void *payload, uint8_t len)
{
    uint8_t buf[PROTO_MAX_FRAME];
    size_t n = 0;
    esp_err_t err = send_now(type, payload, len, buf, &n);
    if (xSemaphoreTake(s_dedupe_mux, pdMS_TO_TICKS(50)) == pdTRUE) {
        s_last.valid = true;
        s_last.cmd = *cmd;
        s_last.has_reply = (err == ESP_OK);
        if (err == ESP_OK) {
            memcpy(s_last.reply, buf, n);
            s_last.reply_len = n;
        }
        xSemaphoreGive(s_dedupe_mux);
    }
    return err;
}

/* ------------------------------------------------------------------ nhận khung */
static void handle_ack(uint8_t acked_seq, bool is_nack, uint8_t nack_err)
{
    bool match = false;
    portENTER_CRITICAL(&s_lock);
    if (s_waiting && s_wait_seq == acked_seq) {
        s_got_nack = is_nack;
        match = true;
    }
    portEXIT_CRITICAL(&s_lock);
    if (match) {
        xSemaphoreGive(s_ack_sem);
    } else {
        ESP_LOGD(TAG, "%s seq=%u khong khop (khong cho)", is_nack ? "NACK" : "ACK", acked_seq);
    }
    if (is_nack) {
        ESP_LOGW(TAG, "Pi NACK seq=%u err=%u", acked_seq, nack_err);
    }
}

static void handle_frame(const proto_frame_t *f)
{
    /* Mọi khung hợp lệ đều nuôi watchdog liên kết (E-09) */
    bool became_up = false;
    portENTER_CRITICAL(&s_lock);
    s_last_rx_us = esp_timer_get_time();
    if (!s_link_up) {
        s_link_up = true;
        became_up = true;
    }
    portEXIT_CRITICAL(&s_lock);
    if (became_up) {
        ESP_LOGI(TAG, "lien ket len");
        if (s_cfg.on_link_up) {
            s_cfg.on_link_up();
        }
    }

    int dir = proto_direction(f->type);
    int exp = proto_expected_len(f->type);

    if (dir == 2) {
        /* Khung hướng ESP32→Pi: chỉ ACK/NACK của Pi là hợp lệ */
        if (f->type == MSG_ACK && f->len == exp) {
            const proto_ack_t *a = (const proto_ack_t *)f->payload;
            handle_ack(a->acked_seq, false, 0);
        } else if (f->type == MSG_NACK && f->len == exp) {
            const proto_nack_t *n = (const proto_nack_t *)f->payload;
            handle_ack(n->acked_seq, true, n->err);
        } else if (f->type == MSG_ACK || f->type == MSG_NACK) {
            send_nack_raw(f->seq, NACK_BAD_LEN);
        } else {
            send_nack_raw(f->seq, NACK_UNKNOWN_TYPE);
        }
        return;
    }
    if (dir != 1 || exp < 0) {
        send_nack_raw(f->seq, NACK_UNKNOWN_TYPE);
        return;
    }
    if (f->len != (uint8_t)exp) {
        send_nack_raw(f->seq, NACK_BAD_LEN);
        return;
    }

    if (f->type == MSG_PING) {
#if CONFIG_SORTER_OTA_SELFTEST_FAIL_DEMO
        ESP_LOGW(TAG, "DEMO self-test fail: bo qua PING, khong tra PONG");
        return;
#else
        xEventGroupSetBits(s_events, EV_FIRST_PING);
        send_pong();
        return;
#endif
    }
    if (f->type == MSG_GET_STATUS) {
        send_status();
        return;
    }

    /* Lệnh cần ACK → chống lặp theo SEQ */
    bool duplicate = false;
    if (xSemaphoreTake(s_dedupe_mux, pdMS_TO_TICKS(50)) == pdTRUE) {
        if (s_last.valid && frame_equal(&s_last.cmd, f)) {
            duplicate = true;
            if (s_last.has_reply) {
                ESP_LOGI(TAG, "khung trung seq=%u type=0x%02X → gui lai tra loi", f->seq, f->type);
                (void)write_frame(s_last.reply, s_last.reply_len);
            } else {
                ESP_LOGI(TAG, "khung trung seq=%u dang xu ly → bo", f->seq);
            }
        } else {
            s_last.valid = true;
            s_last.has_reply = false;
            s_last.cmd = *f;
        }
        xSemaphoreGive(s_dedupe_mux);
    }
    if (duplicate) {
        return;
    }
    if (s_cfg.on_cmd) {
        s_cfg.on_cmd(f);
    } else {
        send_nack_raw(f->seq, NACK_BAD_STATE);
    }
}

static void rx_task(void *arg)
{
    (void)arg;
    esp_err_t werr = esp_task_wdt_add(NULL);
    if (werr != ESP_OK) {
        ESP_LOGW(TAG, "esp_task_wdt_add (rx) loi: %s", esp_err_to_name(werr));
    }
    uint8_t buf[64];
    proto_frame_t frame;
    for (;;) {
        int n = uart_read_bytes(LINK_UART, buf, sizeof(buf), pdMS_TO_TICKS(20));
        (void)esp_task_wdt_reset();
        for (int i = 0; i < n; i++) {
            if (proto_decoder_feed(&s_dec, buf[i], &frame)) {
                handle_frame(&frame);
            }
        }
    }
}

/* ------------------------------------------------------------------ gửi sự kiện có ACK */
static void tx_task(void *arg)
{
    (void)arg;
    tx_item_t it;
    uint8_t buf[PROTO_MAX_FRAME];
    for (;;) {
        if (xQueueReceive(s_tx_queue, &it, portMAX_DELAY) != pdTRUE) {
            continue;
        }
        uint8_t seq = next_seq();
        size_t n = proto_encode(buf, sizeof(buf), seq, it.type, it.payload, it.len);
        if (n == 0) {
            ESP_LOGE(TAG, "encode type=0x%02X len=%u loi", it.type, it.len);
            continue;
        }
        bool acked = false;
        for (unsigned attempt = 0; attempt <= PROTO_MAX_RETRIES && !acked; attempt++) {
            /* xả semaphore cũ rồi đặt SEQ chờ */
            (void)xSemaphoreTake(s_ack_sem, 0);
            portENTER_CRITICAL(&s_lock);
            s_wait_seq = seq;
            s_got_nack = false;
            s_waiting = true;
            portEXIT_CRITICAL(&s_lock);

            if (write_frame(buf, n) != ESP_OK) {
                break;
            }
            if (xSemaphoreTake(s_ack_sem, pdMS_TO_TICKS(PROTO_ACK_TIMEOUT_MS)) == pdTRUE) {
                acked = true;
                if (s_got_nack) {
                    ESP_LOGW(TAG, "su kien type=0x%02X seq=%u bi Pi NACK", it.type, seq);
                }
            } else if (attempt < PROTO_MAX_RETRIES) {
                ESP_LOGW(TAG, "khong co ACK type=0x%02X seq=%u → gui lai (%u/%u)", it.type, seq, attempt + 1,
                         (unsigned)PROTO_MAX_RETRIES);
            }
        }
        portENTER_CRITICAL(&s_lock);
        s_waiting = false;
        portEXIT_CRITICAL(&s_lock);
        if (!acked) {
            ESP_LOGE(TAG, "su kien type=0x%02X seq=%u bo sau %u lan", it.type, seq, (unsigned)PROTO_MAX_RETRIES + 1);
        }
    }
}

/* ------------------------------------------------------------------ timer 100 ms */
static void tick_cb(void *arg)
{
    (void)arg;
    s_tick++;

    bool lost = false;
    portENTER_CRITICAL(&s_lock);
    if (s_link_up && (esp_timer_get_time() - s_last_rx_us) > (int64_t)PROTO_LINK_TIMEOUT_MS * 1000LL) {
        s_link_up = false;
        lost = true;
    }
    portEXIT_CRITICAL(&s_lock);
    if (lost) {
        ESP_LOGW(TAG, "mat lien ket (> %u ms khong co khung hop le)", (unsigned)PROTO_LINK_TIMEOUT_MS);
        if (s_cfg.on_link_lost) {
            s_cfg.on_link_lost();
        }
    }
    if ((s_tick % LINK_STATUS_TICK) == 0) {
        send_status();
    }
}

/* ------------------------------------------------------------------ API */
esp_err_t link_init(const link_config_t *cfg)
{
    if (s_inited) {
        return ESP_OK;
    }
    if (cfg == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    s_cfg = *cfg;
    proto_decoder_init(&s_dec);

    uart_config_t ucfg = {
        .baud_rate = 115200,
        .data_bits = UART_DATA_8_BITS,
        .parity = UART_PARITY_DISABLE,
        .stop_bits = UART_STOP_BITS_1,
        .flow_ctrl = UART_HW_FLOWCTRL_DISABLE,
        .source_clk = UART_SCLK_DEFAULT,
    };
    esp_err_t err = uart_driver_install(LINK_UART, LINK_RX_BUF, LINK_TX_BUF, 0, NULL, 0);
    if (err != ESP_OK) {
        return err;
    }
    err = uart_param_config(LINK_UART, &ucfg);
    if (err != ESP_OK) {
        return err;
    }
    err = uart_set_pin(LINK_UART, BOARD_UART_TX_GPIO, BOARD_UART_RX_GPIO, UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE);
    if (err != ESP_OK) {
        return err;
    }

    s_tx_queue = xQueueCreate(LINK_TX_QUEUE, sizeof(tx_item_t));
    s_ack_sem = xSemaphoreCreateBinary();
    s_dedupe_mux = xSemaphoreCreateMutex();
    s_events = xEventGroupCreate();
    if (!s_tx_queue || !s_ack_sem || !s_dedupe_mux || !s_events) {
        return ESP_ERR_NO_MEM;
    }

    const esp_timer_create_args_t targs = {
        .callback = tick_cb,
        .arg = NULL,
        .dispatch_method = ESP_TIMER_TASK,
        .name = "link_tick",
        .skip_unhandled_events = true,
    };
    err = esp_timer_create(&targs, &s_tick_timer);
    if (err != ESP_OK) {
        return err;
    }

    if (xTaskCreate(rx_task, "link_rx", 4096, NULL, 10, NULL) != pdPASS) {
        return ESP_ERR_NO_MEM;
    }
    if (xTaskCreate(tx_task, "link_tx", 3072, NULL, 9, NULL) != pdPASS) {
        return ESP_ERR_NO_MEM;
    }
    err = esp_timer_start_periodic(s_tick_timer, (uint64_t)LINK_TICK_MS * 1000ULL);
    if (err != ESP_OK) {
        return err;
    }
    s_inited = true;
    ESP_LOGI(TAG, "init OK: UART%d RX GPIO%d TX GPIO%d 115200 8N1", BOARD_UART_PORT_NUM, BOARD_UART_RX_GPIO,
             BOARD_UART_TX_GPIO);
    return ESP_OK;
}

esp_err_t link_send_event(uint8_t type, const void *payload, uint8_t len)
{
    if (!s_inited) {
        return ESP_ERR_INVALID_STATE;
    }
    if (len > PROTO_MAX_PAYLOAD || (len && payload == NULL)) {
        return ESP_ERR_INVALID_ARG;
    }
    tx_item_t it = { .type = type, .len = len };
    if (len) {
        memcpy(it.payload, payload, len);
    }
    if (xQueueSend(s_tx_queue, &it, pdMS_TO_TICKS(100)) != pdTRUE) {
        ESP_LOGE(TAG, "hang doi tx day — bo type=0x%02X", type);
        return ESP_ERR_TIMEOUT;
    }
    return ESP_OK;
}

esp_err_t link_send_ack(const proto_frame_t *cmd)
{
    if (!s_inited || cmd == NULL) {
        return ESP_ERR_INVALID_STATE;
    }
    proto_ack_t a = { .acked_seq = cmd->seq };
    return reply_cmd(cmd, MSG_ACK, &a, sizeof(a));
}

esp_err_t link_send_nack(const proto_frame_t *cmd, uint8_t err)
{
    if (!s_inited || cmd == NULL) {
        return ESP_ERR_INVALID_STATE;
    }
    proto_nack_t n = { .acked_seq = cmd->seq, .err = err };
    ESP_LOGW(TAG, "NACK seq=%u type=0x%02X err=%u", cmd->seq, cmd->type, err);
    return reply_cmd(cmd, MSG_NACK, &n, sizeof(n));
}

uint16_t link_get_crc_errors(void)
{
    uint32_t c = s_dec.stats.crc_errors;
    return (c > 0xFFFFu) ? 0xFFFFu : (uint16_t)c;
}

bool link_is_up(void)
{
    return s_link_up;
}

bool link_wait_first_ping(uint32_t timeout_ms)
{
    if (!s_inited) {
        return false;
    }
    EventBits_t bits = xEventGroupWaitBits(s_events, EV_FIRST_PING, pdFALSE, pdTRUE, pdMS_TO_TICKS(timeout_ms));
    return (bits & EV_FIRST_PING) != 0;
}
