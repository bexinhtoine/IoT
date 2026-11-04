/* P1 / E-10 — Codec khung UART (xem protocol.h). Cùng thuật toán với pi/sorter/uart_codec.py. */
#include "protocol.h"

#include <string.h>

enum { S_SOF0 = 0, S_SOF1, S_VER, S_SEQ, S_TYPE, S_LEN, S_PAYLOAD, S_CRC0, S_CRC1 };

uint16_t proto_crc16(const uint8_t *data, size_t len, uint16_t crc)
{
    for (size_t i = 0; i < len; i++) {
        crc ^= (uint16_t)data[i] << 8;
        for (int b = 0; b < 8; b++) {
            crc = (crc & 0x8000u) ? (uint16_t)((crc << 1) ^ PROTO_CRC_POLY) : (uint16_t)(crc << 1);
        }
    }
    return crc;
}

size_t proto_encode(uint8_t *out, size_t out_cap, uint8_t seq, uint8_t type,
                    const void *payload, uint8_t len)
{
    if (len > PROTO_MAX_PAYLOAD || out_cap < (size_t)(PROTO_OVERHEAD + len)) {
        return 0;
    }
    size_t n = 0;
    out[n++] = PROTO_SOF0;
    out[n++] = PROTO_SOF1;
    out[n++] = PROTO_VER;
    out[n++] = seq;
    out[n++] = type;
    out[n++] = len;
    if (len) {
        memcpy(&out[n], payload, len);
        n += len;
    }
    uint16_t crc = proto_crc16(&out[2], 4u + len, PROTO_CRC_INIT);
    out[n++] = (uint8_t)(crc & 0xFF);
    out[n++] = (uint8_t)(crc >> 8);
    return n;
}

void proto_decoder_init(proto_decoder_t *d)
{
    memset(d, 0, sizeof(*d));
    d->state = S_SOF0;
}

bool proto_decoder_feed(proto_decoder_t *d, uint8_t b, proto_frame_t *out)
{
    switch (d->state) {
    case S_SOF0:
        if (b == PROTO_SOF0) d->state = S_SOF1;
        else d->stats.resyncs++;
        return false;
    case S_SOF1:
        if (b == PROTO_SOF1) { d->state = S_VER; }
        else if (b == PROTO_SOF0) { d->stats.resyncs++; }
        else { d->stats.resyncs += 2; d->state = S_SOF0; }
        return false;
    case S_VER:
        if (b != PROTO_VER) { d->stats.ver_errors++; d->state = S_SOF0; return false; }
        d->body[0] = b; d->state = S_SEQ; return false;
    case S_SEQ:
        d->seq = b; d->body[1] = b; d->state = S_TYPE; return false;
    case S_TYPE:
        d->type = b; d->body[2] = b; d->state = S_LEN; return false;
    case S_LEN:
        if (b > PROTO_MAX_PAYLOAD) { d->stats.len_errors++; d->state = S_SOF0; return false; }
        d->len = b; d->body[3] = b; d->got = 0;
        d->state = b ? S_PAYLOAD : S_CRC0; return false;
    case S_PAYLOAD:
        d->body[4 + d->got++] = b;
        if (d->got == d->len) d->state = S_CRC0;
        return false;
    case S_CRC0:
        d->crc0 = b; d->state = S_CRC1; return false;
    case S_CRC1: {
        uint16_t rx = (uint16_t)(d->crc0 | ((uint16_t)b << 8));
        d->state = S_SOF0;
        uint16_t calc = proto_crc16(d->body, 4u + d->len, PROTO_CRC_INIT);
        if (rx != calc) { d->stats.crc_errors++; return false; }
        d->stats.frames_ok++;
        out->seq = d->seq; out->type = d->type; out->len = d->len;
        memcpy(out->payload, &d->body[4], d->len);
        return true;
    }
    default:
        d->state = S_SOF0;
        return false;
    }
}
