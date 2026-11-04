/* AUTO-GENERATED — DO NOT EDIT (sinh bởi shared/protocol/gen.py từ protocol.yaml) */
#ifndef PROTOCOL_GEN_H
#define PROTOCOL_GEN_H
#include <stdint.h>
#include <stddef.h>
#ifdef __cplusplus
extern "C" {
#endif

/* ---- Khung ---- */
#define PROTO_SOF0            0xAAu
#define PROTO_SOF1            0x55u
#define PROTO_VER             0x01u
#define PROTO_MAX_PAYLOAD     64u
#define PROTO_HEADER_LEN      5u   /* SOF0 SOF1 VER SEQ TYPE LEN -> trừ SOF = 4, cộng LEN byte... xem protocol.c */
#define PROTO_OVERHEAD        8u   /* SOF(2)+VER+SEQ+TYPE+LEN+CRC(2) */
#define PROTO_MAX_FRAME       (PROTO_OVERHEAD + PROTO_MAX_PAYLOAD)
#define PROTO_CRC_POLY        0x1021u
#define PROTO_CRC_INIT        0xFFFFu
#define PROTO_CRC_CHECK_VALUE 0x29B1u /* CRC("123456789") */
#define PROTO_ACK_TIMEOUT_MS  200u
#define PROTO_MAX_RETRIES     3u
#define PROTO_PING_INTERVAL_MS 500u
#define PROTO_LINK_TIMEOUT_MS  2000u

/* ---- Mã trạng thái (E-01) ---- */
typedef enum {
    ST_BOOT = 0,
    ST_IDLE = 1,
    ST_RUNNING = 2,
    ST_DETECTED = 3,
    ST_SORTING = 4,
    ST_ESTOP = 5,
    ST_FAULT = 6,
    ST_OTA = 7,
} proto_state_t;

/* ---- Mã lỗi NACK ---- */
typedef enum {
    NACK_CRC = 1,
    NACK_UNKNOWN_TYPE = 2,
    NACK_BAD_STATE = 3,
    NACK_BAD_PARAM = 4,
    NACK_BAD_LEN = 5,
} proto_nack_err_t;

/* ---- Mã fault ---- */
typedef enum {
    FAULT_DECISION_TIMEOUT = 1,
    FAULT_LINK_LOST = 2,
    FAULT_SENSOR = 3,
    FAULT_WDT_RESET = 4,
} proto_fault_t;

/* ---- Nguồn E-stop ---- */
typedef enum {
    ESTOP_SRC_PHYSICAL = 1,
    ESTOP_SRC_REMOTE = 2,
} proto_estop_src_t;

/* ---- SORT target / reason ---- */
typedef enum {
    TARGET_A = 1,
    TARGET_B = 2,
    TARGET_REJECT = 3,
} proto_sort_target_t;

typedef enum {
    REASON_OK = 0,
    REASON_DEFECT = 1,
    REASON_SECURITY = 2,
    REASON_FAILSAFE = 3,
} proto_sort_reason_t;

/* ---- OTA stage / reason (4.5) ---- */
typedef enum {
    OTA_STAGE_CHECKING = 1,
    OTA_STAGE_DOWNLOADING = 2,
    OTA_STAGE_VERIFIED = 3,
    OTA_STAGE_REJECTED = 4,
    OTA_STAGE_REBOOTING = 5,
    OTA_STAGE_SUCCESS = 6,
    OTA_STAGE_ROLLED_BACK = 7,
    OTA_STAGE_UP_TO_DATE = 8,
} proto_ota_stage_t;

typedef enum {
    OTA_REASON_NONE = 0,
    OTA_REASON_SIGNATURE_INVALID = 1,
    OTA_REASON_HASH_MISMATCH = 2,
    OTA_REASON_ROLLBACK_REJECTED = 3,
    OTA_REASON_MODEL_MISMATCH = 4,
    OTA_REASON_TLS_ERROR = 5,
    OTA_REASON_DOWNLOAD_ERROR = 6,
    OTA_REASON_SELFTEST_FAILED = 7,
    OTA_REASON_BAD_STATE = 8,
} proto_ota_reason_t;

/* ---- Loại khung ---- */
typedef enum {
    MSG_PING = 0x01,
    MSG_START = 0x10,
    MSG_STOP = 0x11,
    MSG_ESTOP = 0x12,
    MSG_ESTOP_RESET = 0x13,
    MSG_SORT = 0x20,
    MSG_SET_PARAM = 0x40,
    MSG_OTA_CHECK = 0x50,
    MSG_GET_STATUS = 0x60,
    MSG_ACK = 0x80,
    MSG_NACK = 0x81,
    MSG_PONG = 0x82,
    MSG_EVT_PKG_DETECTED = 0x90,
    MSG_EVT_SORT_DONE = 0x91,
    MSG_EVT_ESTOP = 0x92,
    MSG_EVT_ESTOP_CLEARED = 0x93,
    MSG_EVT_FAULT = 0x94,
    MSG_EVT_OTA_STATUS = 0xA0,
    MSG_STATUS = 0xA1,
} proto_msg_type_t;

/* ---- Payload (packed, little-endian — ESP32 là little-endian) ---- */
typedef struct __attribute__((packed)) {
    uint8_t speed;
} proto_start_t;
_Static_assert(sizeof(proto_start_t) == 1, "size START");

typedef struct __attribute__((packed)) {
    uint8_t target;
    uint8_t reason;
    uint16_t pkg_seq;
} proto_sort_t;
_Static_assert(sizeof(proto_sort_t) == 4, "size SORT");

typedef struct __attribute__((packed)) {
    uint8_t param_id;
    uint16_t value;
} proto_set_param_t;
_Static_assert(sizeof(proto_set_param_t) == 3, "size SET_PARAM");

typedef struct __attribute__((packed)) {
    uint8_t acked_seq;
} proto_ack_t;
_Static_assert(sizeof(proto_ack_t) == 1, "size ACK");

typedef struct __attribute__((packed)) {
    uint8_t acked_seq;
    uint8_t err;
} proto_nack_t;
_Static_assert(sizeof(proto_nack_t) == 2, "size NACK");

typedef struct __attribute__((packed)) {
    uint8_t state;
} proto_pong_t;
_Static_assert(sizeof(proto_pong_t) == 1, "size PONG");

typedef struct __attribute__((packed)) {
    uint16_t pkg_seq;
} proto_evt_pkg_detected_t;
_Static_assert(sizeof(proto_evt_pkg_detected_t) == 2, "size EVT_PKG_DETECTED");

typedef struct __attribute__((packed)) {
    uint16_t pkg_seq;
    uint8_t target;
} proto_evt_sort_done_t;
_Static_assert(sizeof(proto_evt_sort_done_t) == 3, "size EVT_SORT_DONE");

typedef struct __attribute__((packed)) {
    uint8_t source;
} proto_evt_estop_t;
_Static_assert(sizeof(proto_evt_estop_t) == 1, "size EVT_ESTOP");

typedef struct __attribute__((packed)) {
    uint8_t code;
} proto_evt_fault_t;
_Static_assert(sizeof(proto_evt_fault_t) == 1, "size EVT_FAULT");

typedef struct __attribute__((packed)) {
    uint8_t stage;
    uint8_t reason;
    uint8_t progress;
    uint32_t version_code;
} proto_evt_ota_status_t;
_Static_assert(sizeof(proto_evt_ota_status_t) == 7, "size EVT_OTA_STATUS");

typedef struct __attribute__((packed)) {
    uint8_t state;
    uint32_t fw_version_code;
    uint32_t uptime_s;
    uint16_t pkg_count;
    uint16_t crc_err;
    uint8_t reset_reason;
} proto_status_t;
_Static_assert(sizeof(proto_status_t) == 14, "size STATUS");

/* Kích thước payload mong đợi theo TYPE; -1 = TYPE không tồn tại */
static inline int proto_expected_len(uint8_t type) {
    switch (type) {
    case MSG_PING: return 0;
    case MSG_START: return 1;
    case MSG_STOP: return 0;
    case MSG_ESTOP: return 0;
    case MSG_ESTOP_RESET: return 0;
    case MSG_SORT: return 4;
    case MSG_SET_PARAM: return 3;
    case MSG_OTA_CHECK: return 0;
    case MSG_GET_STATUS: return 0;
    case MSG_ACK: return 1;
    case MSG_NACK: return 2;
    case MSG_PONG: return 1;
    case MSG_EVT_PKG_DETECTED: return 2;
    case MSG_EVT_SORT_DONE: return 3;
    case MSG_EVT_ESTOP: return 1;
    case MSG_EVT_ESTOP_CLEARED: return 0;
    case MSG_EVT_FAULT: return 1;
    case MSG_EVT_OTA_STATUS: return 7;
    case MSG_STATUS: return 14;
    default: return -1;
    }
}

/* Hướng khung: 1 = Pi->ESP32, 2 = ESP32->Pi, 0 = không tồn tại */
static inline int proto_direction(uint8_t type) {
    switch (type) {
    case MSG_PING: return 1;
    case MSG_START: return 1;
    case MSG_STOP: return 1;
    case MSG_ESTOP: return 1;
    case MSG_ESTOP_RESET: return 1;
    case MSG_SORT: return 1;
    case MSG_SET_PARAM: return 1;
    case MSG_OTA_CHECK: return 1;
    case MSG_GET_STATUS: return 1;
    case MSG_ACK: return 2;
    case MSG_NACK: return 2;
    case MSG_PONG: return 2;
    case MSG_EVT_PKG_DETECTED: return 2;
    case MSG_EVT_SORT_DONE: return 2;
    case MSG_EVT_ESTOP: return 2;
    case MSG_EVT_ESTOP_CLEARED: return 2;
    case MSG_EVT_FAULT: return 2;
    case MSG_EVT_OTA_STATUS: return 2;
    case MSG_STATUS: return 2;
    default: return 0;
    }
}

/* Bitmask trạng thái được phép nhận lệnh (chỉ Pi->ESP32); bit i = trạng thái i */
#define PROTO_STATE_ANY 0xFFu
static inline uint8_t proto_allowed_states(uint8_t type) {
    switch (type) {
    case MSG_PING: return PROTO_STATE_ANY;
    case MSG_START: return (uint8_t)((1u << ST_IDLE));
    case MSG_STOP: return (uint8_t)((1u << ST_IDLE) | (1u << ST_RUNNING));
    case MSG_ESTOP: return PROTO_STATE_ANY;
    case MSG_ESTOP_RESET: return (uint8_t)((1u << ST_ESTOP) | (1u << ST_FAULT));
    case MSG_SORT: return (uint8_t)((1u << ST_DETECTED));
    case MSG_SET_PARAM: return (uint8_t)((1u << ST_IDLE));
    case MSG_OTA_CHECK: return (uint8_t)((1u << ST_IDLE));
    case MSG_GET_STATUS: return PROTO_STATE_ANY;
    default: return 0;
    }
}

/* ---- Tham số SET_PARAM (E-11) ---- */
typedef enum {
    PARAM_BELT_SPEED = 1,
    PARAM_SERVO_A_HOME = 2,
    PARAM_SERVO_A_DIVERT = 3,
    PARAM_SERVO_B_HOME = 4,
    PARAM_SERVO_B_DIVERT = 5,
    PARAM_T_CLEAR_A = 6,
    PARAM_T_CLEAR_B = 7,
    PARAM_T_CLEAR_REJECT = 8,
    PARAM_T_REARM = 9,
    PARAM_T_DECISION_TIMEOUT = 10,
    PARAM_HCSR04_THRESHOLD = 11,
} proto_param_id_t;
#define PROTO_PARAM_COUNT 11u
typedef struct { uint8_t id; const char *name; uint16_t min; uint16_t max; uint16_t def; } proto_param_desc_t;
static const proto_param_desc_t PROTO_PARAMS[PROTO_PARAM_COUNT] = {
    { 1, "belt_speed", 30, 100, 60 },
    { 2, "servo_a_home", 0, 180, 0 },
    { 3, "servo_a_divert", 0, 180, 45 },
    { 4, "servo_b_home", 0, 180, 0 },
    { 5, "servo_b_divert", 0, 180, 45 },
    { 6, "t_clear_a", 500, 8000, 2500 },
    { 7, "t_clear_b", 500, 8000, 3500 },
    { 8, "t_clear_reject", 500, 10000, 5000 },
    { 9, "t_rearm", 0, 3000, 500 },
    { 10, "t_decision_timeout", 1000, 10000, 3000 },
    { 11, "hcsr04_threshold", 20, 400, 80 },
};
/* Tìm mô tả tham số theo id; NULL nếu không có */
static inline const proto_param_desc_t *proto_param_find(uint8_t id) {
    for (size_t i = 0; i < PROTO_PARAM_COUNT; i++) if (PROTO_PARAMS[i].id == id) return &PROTO_PARAMS[i];
    return NULL;
}

#ifdef __cplusplus
}
#endif
#endif /* PROTOCOL_GEN_H */
