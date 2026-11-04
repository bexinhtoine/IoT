/* E-01 → E-05, E-08, E-09 — Máy trạng thái băng chuyền.
 *
 * ===================================== BẢNG CHUYỂN TRẠNG THÁI =====================================
 * Trạng thái | Sự kiện                         | Hành động                                              | Sang
 * -----------+---------------------------------+--------------------------------------------------------+----------
 * BOOT       | fsm task bắt đầu                | servo home, indicator IDLE                             | IDLE
 * IDLE       | START(speed 30–100)             | ACK; motor_start(speed) ramp 200 ms; sensor on; den tat | RUNNING
 * IDLE       | STOP                            | ACK (không làm gì)                                     | IDLE
 * IDLE       | SET_PARAM(id, value)            | hợp lệ → params_set + áp dụng, ACK; sai → NACK BAD_PARAM | IDLE
 * IDLE       | OTA_CHECK                       | ota_secure_start() OK → ACK; sensor off; den tat | OTA
 * RUNNING    | sensor                          | motor_stop NGAY; sensor off; pkg_seq++;                | DETECTED
 *            |                                 | EVT_PKG_DETECTED(pkg_seq); timer t_decision_timeout    |
 * RUNNING    | STOP                            | ACK; motor_stop; sensor off; indicator IDLE            | IDLE
 * RUNNING    | timeout t_rearm                 | sensor on                                              | RUNNING
 * DETECTED   | SORT(target, reason, pkg_seq)   | pkg_seq ≠ hiện tại hoặc target/reason sai → NACK BAD_PARAM; | DETECTED
 *            |                                 | hợp lệ → ACK; hủy timer decision; servo A/B gạt (REJECT: không); | SORTING
 *            |                                 | motor_start; timer t_clear_{a,b,reject}                |
 * DETECTED   | timeout t_decision_timeout      | như SORT(REJECT, FAILSAFE) + EVT_FAULT(DECISION_TIMEOUT) | SORTING
 * SORTING    | timeout t_clear                 | servo home; khong lap lai pattern LED da bao luc SORT; | RUNNING
 *            |                                 | EVT_SORT_DONE(pkg_seq, target); |
 *            |                                 | motor vẫn chạy; sensor khóa t_rearm (0 → mở ngay)      |
 * RUNNING/   | LINK_LOST (>2000 ms không khung)| motor_stop; servo home; sensor off; indicator FAULT;   | FAULT
 * DETECTED/  |                                 | ghi nhớ EVT_FAULT(LINK_LOST) — gửi khi liên kết trở lại |
 * SORTING    |                                 |                                                        |
 * mọi        | ESTOP (lệnh) / nút E-stop (ISR) | ISR đã tắt PWM; motor_stop; servo home; sensor off;    | ESTOP
 *   (≠ESTOP) |                                 | hủy timer; do sang tinh + coi; EVT_ESTOP(REMOTE/PHYSICAL);  |
 *            |                                 | đang OTA → ota_secure_abort()                          |
 * ESTOP      | ESTOP (lệnh)                    | ACK (idempotent)                                       | ESTOP
 * ESTOP/     | ESTOP_RESET                     | nút còn bấm/đứt dây → NACK BAD_STATE;                  | IDLE
 * FAULT      |                                 | nhả rồi → ACK; indicator IDLE; EVT_ESTOP_CLEARED       |
 * OTA        | OTA stage REJECTED/UP_TO_DATE   | indicator IDLE                                         | IDLE
 * OTA        | OTA stage REBOOTING             | (esp_restart trong task OTA)                           | —
 * mọi        | PING / GET_STATUS               | link trả PONG / STATUS, không qua fsm                   | giữ nguyên
 * mọi        | lệnh ngoài proto_allowed_states | NACK BAD_STATE                                         | giữ nguyên
 * mọi        | link lên                        | gửi EVT_FAULT(WDT_RESET) nếu boot do WDT (một lần);     | giữ nguyên
 *            |                                 | gửi EVT_FAULT(LINK_LOST) nếu đang nợ                   |
 * ==================================================================================================
 *
 * Timer (esp_timer one-shot) chỉ đẩy EV_TIMEOUT vào queue; task fsm kiểm tra trạng thái hiện tại trước khi
 * xử lý nên timeout "cũ" (đã bị hủy nhưng kịp bắn) bị bỏ qua an toàn nhờ xử lý tuần tự theo thứ tự queue.
 */
#include "fsm.h"

#include <string.h>
#include "esp_log.h"
#include "esp_system.h"
#include "esp_task_wdt.h"
#include "esp_timer.h"
#include "estop.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include "indicator.h"
#include "link.h"
#include "motor.h"
#include "ota_secure.h"
#include "params.h"
#include "protocol.h"
#include "sensor.h"
#include "servo.h"

static const char *TAG = "fsm";
#define FSM_QUEUE_LEN 16

typedef enum { EV_CMD, EV_SENSOR, EV_ESTOP, EV_TIMEOUT, EV_LINK, EV_OTA } fsm_ev_type_t;
typedef enum { TMR_DECISION, TMR_CLEAR, TMR_REARM, TMR_COUNT } fsm_timer_t;

typedef struct {
    fsm_ev_type_t type;
    union {
        proto_frame_t cmd;
        struct { bool pressed; } estop;
        struct { fsm_timer_t id; } timeout;
        struct { bool up; } link;
        struct { uint8_t stage, reason, progress; uint32_t version_code; } ota;
    };
} fsm_event_t;

static QueueHandle_t s_queue;
static esp_timer_handle_t s_timers[TMR_COUNT];
static volatile uint8_t s_state = ST_BOOT;
static uint16_t s_pkg_seq;          /* số thứ tự kiện (tăng mỗi lần phát hiện) */
static uint8_t s_run_speed;         /* tốc độ START gần nhất */
static uint8_t s_sort_target;
static bool s_pending_link_fault;   /* nợ EVT_FAULT(LINK_LOST) cho tới khi liên kết lên */
static bool s_pending_wdt_fault;    /* nợ EVT_FAULT(WDT_RESET) */

static const char *state_name(uint8_t s)
{
    switch (s) {
    case ST_BOOT: return "BOOT";
    case ST_IDLE: return "IDLE";
    case ST_RUNNING: return "RUNNING";
    case ST_DETECTED: return "DETECTED";
    case ST_SORTING: return "SORTING";
    case ST_ESTOP: return "ESTOP";
    case ST_FAULT: return "FAULT";
    case ST_OTA: return "OTA";
    default: return "?";
    }
}

/* ------------------------------------------------------------------ đẩy sự kiện */
static void post_event(const fsm_event_t *ev)
{
    if (s_queue && xQueueSend(s_queue, ev, pdMS_TO_TICKS(50)) != pdTRUE) {
        ESP_LOGE(TAG, "queue day — bo su kien type=%d", (int)ev->type);
    }
}

static void cb_cmd(const proto_frame_t *f)
{
    fsm_event_t ev = { .type = EV_CMD };
    ev.cmd = *f;
    post_event(&ev);
}

static void cb_sensor(void)
{
    fsm_event_t ev = { .type = EV_SENSOR };
    post_event(&ev);
}

/* Từ ISR E-stop */
static void cb_estop_isr(bool pressed, BaseType_t *woken)
{
    fsm_event_t ev = { .type = EV_ESTOP };
    ev.estop.pressed = pressed;
    if (s_queue) {
        (void)xQueueSendFromISR(s_queue, &ev, woken);
    }
}

static void cb_link_up(void)
{
    fsm_event_t ev = { .type = EV_LINK };
    ev.link.up = true;
    post_event(&ev);
}

static void cb_link_lost(void)
{
    fsm_event_t ev = { .type = EV_LINK };
    ev.link.up = false;
    post_event(&ev);
}

static void timer_cb(void *arg)
{
    fsm_event_t ev = { .type = EV_TIMEOUT };
    ev.timeout.id = (fsm_timer_t)(intptr_t)arg;
    post_event(&ev);
}

void fsm_on_ota_status(uint8_t stage, uint8_t reason, uint8_t progress, uint32_t version_code)
{
    fsm_event_t ev = { .type = EV_OTA };
    ev.ota.stage = stage;
    ev.ota.reason = reason;
    ev.ota.progress = progress;
    ev.ota.version_code = version_code;
    post_event(&ev);
}

/* ------------------------------------------------------------------ hành động */
static void set_state(uint8_t ns)
{
    if (ns != s_state) {
        ESP_LOGI(TAG, "%s -> %s", state_name(s_state), state_name(ns));
        s_state = ns;
    }
}

static void timer_start(fsm_timer_t id, uint32_t ms)
{
    (void)esp_timer_stop(s_timers[id]);
    esp_err_t err = esp_timer_start_once(s_timers[id], (uint64_t)ms * 1000ULL);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "timer %d start loi: %s", (int)id, esp_err_to_name(err));
    }
}

static void timers_stop_all(void)
{
    for (int i = 0; i < TMR_COUNT; i++) {
        (void)esp_timer_stop(s_timers[i]); /* ESP_ERR_INVALID_STATE nếu không chạy — bỏ qua */
    }
}

static void servos_home(void)
{
    (void)servo_set_angle(SERVO_A, (uint8_t)params_get(PARAM_SERVO_A_HOME));
    (void)servo_set_angle(SERVO_B, (uint8_t)params_get(PARAM_SERVO_B_HOME));
}

static void send_evt_fault(uint8_t code)
{
    proto_evt_fault_t p = { .code = code };
    (void)link_send_event(MSG_EVT_FAULT, &p, sizeof(p));
}

/* Dừng mọi thứ về trạng thái an toàn (dùng cho ESTOP/FAULT/STOP) */
static void all_stop(void)
{
    motor_stop();
    timers_stop_all();
    sensor_set_enabled(false);
    servos_home();
}

static void enter_estop(uint8_t source)
{
    bool was_ota = (s_state == ST_OTA);
    all_stop();
    if (was_ota) {
        ota_secure_abort();
    }
    set_state(ST_ESTOP);
    indicator_set(INDICATOR_ESTOP);
    proto_evt_estop_t p = { .source = source };
    (void)link_send_event(MSG_EVT_ESTOP, &p, sizeof(p));
}

static void enter_fault_link_lost(void)
{
    all_stop();
    set_state(ST_FAULT);
    indicator_set(INDICATOR_FAULT);
    s_pending_link_fault = true; /* gửi EVT_FAULT khi liên kết trở lại */
}

/* DETECTED → SORTING */
static void start_sorting(uint8_t target, uint8_t reason)
{
    s_sort_target = target;
    uint32_t t_clear;
    switch (target) {
    case TARGET_A:
        (void)servo_set_angle(SERVO_A, (uint8_t)params_get(PARAM_SERVO_A_DIVERT));
        t_clear = params_get(PARAM_T_CLEAR_A);
        break;
    case TARGET_B:
        (void)servo_set_angle(SERVO_B, (uint8_t)params_get(PARAM_SERVO_B_DIVERT));
        t_clear = params_get(PARAM_T_CLEAR_B);
        break;
    default: /* REJECT: không gạt, hàng chạy tới cuối băng */
        t_clear = params_get(PARAM_T_CLEAR_REJECT);
        break;
    }
    set_state(ST_SORTING);
    /* Bao ket qua NGAY khi nhan SORT, khong cho vat ra khoi bang moi bao do. */
    indicator_set(INDICATOR_RUNNING); /* nen tat khi pattern ket thuc */
    switch (reason) {
    case REASON_OK: indicator_set(INDICATOR_OK); break;
    case REASON_DEFECT: indicator_set(INDICATOR_DEFECT); break;
    case REASON_NOT_BOX: indicator_set(INDICATOR_NOT_BOX); break;
    case REASON_SECURITY: indicator_set(INDICATOR_SECURITY); break;
    default: indicator_set(INDICATOR_FAILSAFE); break;
    }
    (void)motor_start(s_run_speed);
    timer_start(TMR_CLEAR, t_clear);
    ESP_LOGI(TAG, "SORT pkg=%u target=%u reason=%u t_clear=%lu ms", s_pkg_seq, target, reason, (unsigned long)t_clear);
}

/* SORTING → RUNNING sau t_clear */
static void finish_sorting(void)
{
    servos_home();
    proto_evt_sort_done_t p = { .pkg_seq = s_pkg_seq, .target = s_sort_target };
    (void)link_send_event(MSG_EVT_SORT_DONE, &p, sizeof(p));
    set_state(ST_RUNNING);
    uint32_t t_rearm = params_get(PARAM_T_REARM);
    if (t_rearm == 0) {
        sensor_set_enabled(true);
    } else {
        timer_start(TMR_REARM, t_rearm);
    }
}

/* Áp dụng ngay tham số vừa đổi (E-11) */
static void apply_param(uint8_t id)
{
    switch (id) {
    case PARAM_SERVO_A_HOME:
        (void)servo_set_angle(SERVO_A, (uint8_t)params_get(PARAM_SERVO_A_HOME));
        break;
    case PARAM_SERVO_B_HOME:
        (void)servo_set_angle(SERVO_B, (uint8_t)params_get(PARAM_SERVO_B_HOME));
        break;
    case PARAM_BELT_SPEED:
        s_run_speed = (uint8_t)params_get(PARAM_BELT_SPEED);
        break;
    default:
        /* t_clear/t_rearm/t_decision/hcsr04_threshold/góc gạt: đọc params_get() tại thời điểm dùng */
        break;
    }
}

/* ------------------------------------------------------------------ xử lý lệnh (E-01, E-10) */
static void handle_cmd(const proto_frame_t *f)
{
    uint8_t allowed = proto_allowed_states(f->type);
    if (!(allowed & (uint8_t)(1u << s_state))) {
        ESP_LOGW(TAG, "lenh 0x%02X sai trang thai %s", f->type, state_name(s_state));
        (void)link_send_nack(f, NACK_BAD_STATE);
        return;
    }

    switch (f->type) {
    case MSG_START: {
        const proto_start_t *p = (const proto_start_t *)f->payload;
        const proto_param_desc_t *d = proto_param_find(PARAM_BELT_SPEED);
        if (p->speed < d->min || p->speed > d->max) {
            (void)link_send_nack(f, NACK_BAD_PARAM);
            return;
        }
        (void)link_send_ack(f);
        s_run_speed = p->speed;
        set_state(ST_RUNNING);
        indicator_set(INDICATOR_RUNNING);
        (void)motor_start(s_run_speed);
        sensor_set_enabled(true);
        break;
    }
    case MSG_STOP:
        (void)link_send_ack(f);
        if (s_state == ST_RUNNING) {
            all_stop();
            set_state(ST_IDLE);
            indicator_set(INDICATOR_IDLE);
        }
        break;
    case MSG_ESTOP:
        (void)link_send_ack(f);
        if (s_state != ST_ESTOP) {
            enter_estop(ESTOP_SRC_REMOTE);
        }
        break;
    case MSG_ESTOP_RESET:
        if (estop_is_pressed()) {
            ESP_LOGW(TAG, "ESTOP_RESET bi tu choi: nut E-stop con bam / dut day");
            (void)link_send_nack(f, NACK_BAD_STATE);
            return;
        }
        (void)link_send_ack(f);
        all_stop();
        s_pending_link_fault = false;
        set_state(ST_IDLE);
        indicator_set(INDICATOR_IDLE);
        (void)link_send_event(MSG_EVT_ESTOP_CLEARED, NULL, 0);
        break;
    case MSG_SORT: {
        proto_sort_t p;
        memcpy(&p, f->payload, sizeof(p));
        bool target_ok = (p.target == TARGET_A || p.target == TARGET_B || p.target == TARGET_REJECT);
        bool reason_ok = (p.reason <= REASON_NOT_BOX);
        if (!target_ok || !reason_ok || p.pkg_seq != s_pkg_seq ||
            (p.target != TARGET_REJECT && p.reason != REASON_OK) ||
            (p.target == TARGET_REJECT && p.reason == REASON_OK)) {
            ESP_LOGW(TAG, "SORT sai: target=%u reason=%u pkg_seq=%u (hien tai %u)", p.target, p.reason, p.pkg_seq,
                     s_pkg_seq);
            (void)link_send_nack(f, NACK_BAD_PARAM);
            return;
        }
        (void)link_send_ack(f);
        (void)esp_timer_stop(s_timers[TMR_DECISION]);
        start_sorting(p.target, p.reason);
        break;
    }
    case MSG_SET_PARAM: {
        proto_set_param_t p;
        memcpy(&p, f->payload, sizeof(p));
        if (params_set(p.param_id, p.value) != ESP_OK) {
            (void)link_send_nack(f, NACK_BAD_PARAM);
            return;
        }
        (void)link_send_ack(f);
        apply_param(p.param_id);
        break;
    }
    case MSG_OTA_CHECK: {
        esp_err_t err = ota_secure_start();
        if (err != ESP_OK) {
            ESP_LOGW(TAG, "ota_secure_start: %s", esp_err_to_name(err));
            (void)link_send_nack(f, NACK_BAD_STATE);
            return;
        }
        (void)link_send_ack(f);
        sensor_set_enabled(false);
        set_state(ST_OTA);
        indicator_set(INDICATOR_OTA);
        break;
    }
    default:
        (void)link_send_nack(f, NACK_UNKNOWN_TYPE);
        break;
    }
}

/* ------------------------------------------------------------------ các sự kiện khác */
static void handle_sensor(void)
{
    if (s_state != ST_RUNNING || !sensor_is_enabled()) {
        return;
    }
    motor_stop();                    /* E-02: dừng tức thì */
    sensor_set_enabled(false);
    s_pkg_seq++;
    set_state(ST_DETECTED);
    indicator_set(INDICATOR_CHECKING);
    proto_evt_pkg_detected_t p = { .pkg_seq = s_pkg_seq };
    (void)link_send_event(MSG_EVT_PKG_DETECTED, &p, sizeof(p));
    timer_start(TMR_DECISION, params_get(PARAM_T_DECISION_TIMEOUT));
    ESP_LOGI(TAG, "phat hien kien #%u, cho SORT toi da %u ms", s_pkg_seq, params_get(PARAM_T_DECISION_TIMEOUT));
}

static void handle_timeout(fsm_timer_t id)
{
    switch (id) {
    case TMR_DECISION:
        if (s_state == ST_DETECTED) {
            ESP_LOGW(TAG, "qua han quyet dinh kien #%u → REJECT/FAILSAFE", s_pkg_seq);
            start_sorting(TARGET_REJECT, REASON_FAILSAFE);
            send_evt_fault(FAULT_DECISION_TIMEOUT);
        }
        break;
    case TMR_CLEAR:
        if (s_state == ST_SORTING) {
            finish_sorting();
        }
        break;
    case TMR_REARM:
        if (s_state == ST_RUNNING) {
            sensor_set_enabled(true);
        }
        break;
    default:
        break;
    }
}

static void handle_estop_event(bool pressed)
{
    if (pressed) {
        if (s_state != ST_ESTOP) {
            ESP_LOGW(TAG, "E-STOP VAT LY");
            enter_estop(ESTOP_SRC_PHYSICAL);
        } else {
            motor_stop(); /* dội nút: bảo đảm trạng thái driver sạch */
        }
    } else {
        ESP_LOGI(TAG, "nut E-stop da nha (cho ESTOP_RESET)");
    }
}

static void handle_link(bool up)
{
    if (up) {
        if (s_pending_wdt_fault) {
            s_pending_wdt_fault = false;
            send_evt_fault(FAULT_WDT_RESET);
        }
        if (s_pending_link_fault) {
            s_pending_link_fault = false;
            send_evt_fault(FAULT_LINK_LOST);
        }
        return;
    }
    if (s_state == ST_RUNNING || s_state == ST_DETECTED || s_state == ST_SORTING) {
        ESP_LOGE(TAG, "MAT LIEN KET khi %s → FAULT", state_name(s_state));
        enter_fault_link_lost();
    }
}

static void handle_ota(const fsm_event_t *ev)
{
    proto_evt_ota_status_t p = {
        .stage = ev->ota.stage,
        .reason = ev->ota.reason,
        .progress = ev->ota.progress,
        .version_code = ev->ota.version_code,
    };
    (void)link_send_event(MSG_EVT_OTA_STATUS, &p, sizeof(p));
    if (s_state == ST_OTA && (ev->ota.stage == OTA_STAGE_REJECTED || ev->ota.stage == OTA_STAGE_UP_TO_DATE)) {
        set_state(ST_IDLE);
        indicator_set(INDICATOR_IDLE);
    }
}

/* ------------------------------------------------------------------ task */
static void fsm_task(void *arg)
{
    (void)arg;
    esp_err_t werr = esp_task_wdt_add(NULL);
    if (werr != ESP_OK) {
        ESP_LOGW(TAG, "esp_task_wdt_add loi: %s", esp_err_to_name(werr));
    }

    servos_home();
    indicator_set(INDICATOR_IDLE);
    set_state(ST_IDLE);
    if (estop_is_pressed()) {
        ESP_LOGW(TAG, "nut E-stop dang bam/dut day luc khoi dong → ESTOP");
        enter_estop(ESTOP_SRC_PHYSICAL);
    }

    fsm_event_t ev;
    for (;;) {
        BaseType_t got = xQueueReceive(s_queue, &ev, pdMS_TO_TICKS(1000));
        (void)esp_task_wdt_reset();
        if (got != pdTRUE) {
            continue;
        }
        switch (ev.type) {
        case EV_CMD: handle_cmd(&ev.cmd); break;
        case EV_SENSOR: handle_sensor(); break;
        case EV_ESTOP: handle_estop_event(ev.estop.pressed); break;
        case EV_TIMEOUT: handle_timeout(ev.timeout.id); break;
        case EV_LINK: handle_link(ev.link.up); break;
        case EV_OTA: handle_ota(&ev); break;
        default: break;
        }
    }
}

/* ------------------------------------------------------------------ API */
esp_err_t fsm_init(uint32_t fw_version_code)
{
    s_queue = xQueueCreate(FSM_QUEUE_LEN, sizeof(fsm_event_t));
    if (s_queue == NULL) {
        return ESP_ERR_NO_MEM;
    }
    static const char *names[TMR_COUNT] = { "t_decision", "t_clear", "t_rearm" };
    for (int i = 0; i < TMR_COUNT; i++) {
        const esp_timer_create_args_t a = {
            .callback = timer_cb,
            .arg = (void *)(intptr_t)i,
            .dispatch_method = ESP_TIMER_TASK,
            .name = names[i],
            .skip_unhandled_events = true,
        };
        esp_err_t err = esp_timer_create(&a, &s_timers[i]);
        if (err != ESP_OK) {
            return err;
        }
    }

    s_run_speed = (uint8_t)params_get(PARAM_BELT_SPEED);

    /* Boot do watchdog → nợ EVT_FAULT(WDT_RESET) tới khi liên kết lên (E-09) */
    esp_reset_reason_t rr = esp_reset_reason();
    if (rr == ESP_RST_TASK_WDT || rr == ESP_RST_INT_WDT || rr == ESP_RST_WDT) {
        ESP_LOGW(TAG, "khoi dong do watchdog (reason=%d)", (int)rr);
        s_pending_wdt_fault = true;
    }

    esp_err_t err = sensor_init(cb_sensor);
    if (err != ESP_OK) {
        return err;
    }
    err = estop_init(cb_estop_isr);
    if (err != ESP_OK) {
        return err;
    }
    link_config_t lc = {
        .on_cmd = cb_cmd,
        .get_state = fsm_get_state,
        .get_pkg_count = fsm_get_pkg_count,
        .on_link_up = cb_link_up,
        .on_link_lost = cb_link_lost,
        .fw_version_code = fw_version_code,
    };
    err = link_init(&lc);
    if (err != ESP_OK) {
        return err;
    }
    ota_secure_set_status_cb(fsm_on_ota_status);

    if (xTaskCreate(fsm_task, "fsm", 4096, NULL, 8, NULL) != pdPASS) {
        return ESP_ERR_NO_MEM;
    }
    ESP_LOGI(TAG, "init OK (cam bien %s)", sensor_name());
    return ESP_OK;
}

uint8_t fsm_get_state(void)
{
    return s_state;
}

uint16_t fsm_get_pkg_count(void)
{
    return s_pkg_seq;
}
