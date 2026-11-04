/* E-09 / E-10 / E-12 — Liên kết UART2 với Raspberry Pi.
 *
 * - Task rx: giải mã từng byte (proto_decoder_feed); PING → PONG, GET_STATUS → STATUS tại chỗ;
 *   lệnh khác → cfg->on_cmd() (fsm quyết định ACK/NACK bằng link_send_ack / link_send_nack).
 * - Chống lặp: nhớ lệnh vừa xử lý (SEQ/TYPE/PAYLOAD) + bản sao ACK/NACK đã trả; khung trùng → chỉ gửi lại ACK.
 * - Task tx: hàng đợi sự kiện ESP32→Pi, mỗi sự kiện chờ ACK 200 ms, gửi lại tối đa 3 lần cùng SEQ.
 * - Timer 100 ms: STATUS mỗi 1 s; mất liên kết khi > PROTO_LINK_TIMEOUT_MS không có khung hợp lệ.
 * - link_wait_first_ping(): cho self-test OTA (O-13).
 */
#ifndef LINK_H
#define LINK_H

#include <stdbool.h>
#include <stdint.h>
#include "esp_err.h"
#include "protocol.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    /* Lệnh Pi→ESP32 cần ACK (không gồm PING/GET_STATUS). Ngữ cảnh task link rx — chỉ nên đẩy vào queue. */
    void (*on_cmd)(const proto_frame_t *frame);
    /* Trạng thái hiện tại (cho PONG/STATUS). */
    uint8_t (*get_state)(void);
    /* Số kiện đã phát hiện (cho STATUS). */
    uint16_t (*get_pkg_count)(void);
    /* Liên kết lên (khung hợp lệ đầu tiên / sau khi mất) và mất (quá PROTO_LINK_TIMEOUT_MS). Ngữ cảnh task/timer. */
    void (*on_link_up)(void);
    void (*on_link_lost)(void);
    /* Phiên bản firmware cho STATUS (từ main/version.h). */
    uint32_t fw_version_code;
} link_config_t;

esp_err_t link_init(const link_config_t *cfg);

/* Gửi sự kiện ESP32→Pi (EVT_*): xếp vào hàng đợi tx, task tx gửi + chờ ACK + gửi lại. */
esp_err_t link_send_event(uint8_t type, const void *payload, uint8_t len);

/* Trả lời lệnh (gọi từ fsm). cmd = khung lệnh đã nhận; bản sao trả lời được lưu để chống lặp. */
esp_err_t link_send_ack(const proto_frame_t *cmd);
esp_err_t link_send_nack(const proto_frame_t *cmd, uint8_t err);

/* Số khung sai CRC từ decoder (cho STATUS.crc_err). */
uint16_t link_get_crc_errors(void);

/* Liên kết đang lên? */
bool link_is_up(void);

/* Chờ PING hợp lệ đầu tiên kể từ khi boot (O-13). true nếu đã nhận trước khi hết timeout. */
bool link_wait_first_ping(uint32_t timeout_ms);

#ifdef __cplusplus
}
#endif
#endif /* LINK_H */
