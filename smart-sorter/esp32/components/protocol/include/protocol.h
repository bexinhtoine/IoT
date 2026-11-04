/* P1 / E-10 — Codec khung UART Pi <-> ESP32 (đặc tả 4.2).
 *
 * Khung: SOF(AA 55) VER SEQ TYPE LEN PAYLOAD[LEN] CRC16(LE)
 * CRC-16/CCITT-FALSE trên VER..PAYLOAD. CRC("123456789") = 0x29B1.
 * File này không phụ thuộc ESP-IDF nên test được trên máy host bằng gcc.
 */
#ifndef PROTOCOL_H
#define PROTOCOL_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "protocol_gen.h"

#ifdef __cplusplus
extern "C" {
#endif

/* CRC-16/CCITT-FALSE; gọi nối tiếp bằng cách truyền crc của đoạn trước (bắt đầu PROTO_CRC_INIT). */
uint16_t proto_crc16(const uint8_t *data, size_t len, uint16_t crc);

/* Đóng khung vào out (cần >= PROTO_MAX_FRAME byte). Trả về số byte ghi, 0 nếu payload quá dài. */
size_t proto_encode(uint8_t *out, size_t out_cap, uint8_t seq, uint8_t type,
                    const void *payload, uint8_t len);

/* Khung đã giải mã. */
typedef struct {
    uint8_t seq;
    uint8_t type;
    uint8_t len;
    uint8_t payload[PROTO_MAX_PAYLOAD];
} proto_frame_t;

typedef struct {
    uint32_t frames_ok;
    uint32_t crc_errors;
    uint32_t len_errors;
    uint32_t ver_errors;
    uint32_t resyncs;
} proto_stats_t;

/* Bộ giải mã từng byte — trạng thái nội bộ, dùng một instance cho một cổng. */
typedef struct {
    uint8_t state;
    uint8_t seq, type, len, got;
    uint8_t body[4 + PROTO_MAX_PAYLOAD]; /* VER SEQ TYPE LEN PAYLOAD */
    uint8_t crc0;
    proto_stats_t stats;
} proto_decoder_t;

void proto_decoder_init(proto_decoder_t *d);

/* Nạp một byte. Trả về true khi có khung hợp lệ đầy đủ trong *out. */
bool proto_decoder_feed(proto_decoder_t *d, uint8_t byte, proto_frame_t *out);

#ifdef __cplusplus
}
#endif
#endif /* PROTOCOL_H */
