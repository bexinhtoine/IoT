/* P1 — Test codec C trên máy host bằng gcc, dùng shared/protocol/test_vectors.json.
 *
 * Build: gcc -std=c11 -Wall -Wextra -I../include ../protocol.c host_test.c -o host_test
 * Chạy : ./host_test ../../../../shared/protocol/test_vectors.json
 *
 * Bộ phân tích JSON ở đây rất tối giản (chỉ đủ cho test_vectors.json do gen_vectors.py sinh),
 * không dùng cho dữ liệu bất kỳ.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "protocol.h"

static int failures = 0;
#define CHECK(cond, ...) do { if (!(cond)) { failures++; printf("  FAIL: "); printf(__VA_ARGS__); printf("\n"); } } while (0)

static char *read_file(const char *path, size_t *n)
{
    FILE *f = fopen(path, "rb");
    if (!f) return NULL;
    fseek(f, 0, SEEK_END); long sz = ftell(f); fseek(f, 0, SEEK_SET);
    char *buf = malloc((size_t)sz + 1);
    *n = fread(buf, 1, (size_t)sz, f); buf[*n] = 0; fclose(f);
    return buf;
}

/* tìm "key": và trả con trỏ ngay sau dấu hai chấm (trong phạm vi [p, end)) */
static const char *find_key(const char *p, const char *end, const char *key)
{
    char pat[64]; snprintf(pat, sizeof pat, "\"%s\"", key);
    size_t kl = strlen(pat);
    for (; p + kl < end; p++) {
        if (memcmp(p, pat, kl) == 0) { p += kl; while (p < end && (*p == ' ' || *p == ':')) p++; return p; }
    }
    return NULL;
}

static size_t hex_to_bytes(const char *hex, uint8_t *out, size_t cap)
{
    size_t n = 0;
    while (hex[0] && hex[1] && hex[0] != '"' && n < cap) {
        unsigned v; char tmp[3] = { hex[0], hex[1], 0 };
        if (sscanf(tmp, "%2x", &v) != 1) break;
        out[n++] = (uint8_t)v; hex += 2;
    }
    return n;
}

int main(int argc, char **argv)
{
    /* 1. CRC chuẩn */
    uint16_t c = proto_crc16((const uint8_t *)"123456789", 9, PROTO_CRC_INIT);
    printf("CRC(\"123456789\") = 0x%04X (mong đợi 0x%04X)\n", c, PROTO_CRC_CHECK_VALUE);
    CHECK(c == PROTO_CRC_CHECK_VALUE, "CRC check value");

    /* 2. encode -> decode vòng tròn */
    proto_sort_t s = { TARGET_A, REASON_OK, 0x1234 };
    uint8_t buf[PROTO_MAX_FRAME];
    size_t n = proto_encode(buf, sizeof buf, 7, MSG_SORT, &s, sizeof s);
    CHECK(n == PROTO_OVERHEAD + sizeof s, "encode len");
    proto_decoder_t d; proto_decoder_init(&d); proto_frame_t fr;
    bool got = false;
    for (size_t i = 0; i < n; i++) got |= proto_decoder_feed(&d, buf[i], &fr);
    CHECK(got && fr.seq == 7 && fr.type == MSG_SORT && fr.len == 4, "roundtrip");
    CHECK(memcmp(fr.payload, &s, 4) == 0, "roundtrip payload");

    if (argc < 2) { printf("(không có test_vectors.json, chỉ chạy test nội bộ)\n"); return failures ? 1 : 0; }

    /* 3. test vectors */
    size_t len; char *js = read_file(argv[1], &len);
    if (!js) { printf("Không đọc được %s\n", argv[1]); return 1; }
    const char *p = js, *end = js + len;
    int count = 0;
    while ((p = find_key(p, end, "name")) != NULL) {
        const char *name = p + 1; const char *q = strchr(name, '"');
        char nm[128]; size_t nl = (size_t)(q - name); if (nl >= sizeof nm) nl = sizeof nm - 1;
        memcpy(nm, name, nl); nm[nl] = 0;
        /* phạm vi của mục này: đến "name" tiếp theo */
        const char *nxt = find_key(q, end, "name"); const char *lim = nxt ? nxt : end;
        const char *chunks = find_key(q, lim, "chunks");
        const char *exp_frames = find_key(q, lim, "expected_frames");
        const char *exp_crc = find_key(q, lim, "expected_crc_errors");
        const char *exp_len = find_key(q, lim, "expected_len_errors");
        if (!chunks || !exp_frames) { p = q; continue; }
        proto_decoder_init(&d);
        int frames_got = 0; proto_frame_t frs[16];
        const char *cp = chunks;
        while ((cp = strchr(cp, '"')) != NULL && cp < exp_frames) {
            uint8_t raw[512]; size_t rn = hex_to_bytes(cp + 1, raw, sizeof raw);
            for (size_t i = 0; i < rn; i++)
                if (proto_decoder_feed(&d, raw[i], &fr) && frames_got < 16) frs[frames_got++] = fr;
            cp = strchr(cp + 1, '"') + 1;
        }
        /* đếm số khung mong đợi = số lần xuất hiện "type" trong expected_frames */
        int exp_n = 0; const char *fp = exp_frames;
        const char *frames_end = exp_crc ? exp_crc : lim;
        while ((fp = find_key(fp, frames_end, "type")) != NULL) {
            int type_v = (int)strtol(fp, NULL, 0);
            const char *sp = find_key(fp, frames_end, "seq"); int seq_v = sp ? (int)strtol(sp, NULL, 10) : -1;
            const char *pp = find_key(fp, frames_end, "payload");
            uint8_t pl[64]; size_t pn = pp ? hex_to_bytes(pp + 1, pl, sizeof pl) : 0;
            if (exp_n < frames_got) {
                CHECK(frs[exp_n].type == type_v, "%s: khung %d type 0x%02X != 0x%02X", nm, exp_n, frs[exp_n].type, type_v);
                CHECK(frs[exp_n].seq == seq_v, "%s: khung %d seq %d != %d", nm, exp_n, frs[exp_n].seq, seq_v);
                CHECK(frs[exp_n].len == pn && memcmp(frs[exp_n].payload, pl, pn) == 0, "%s: khung %d payload", nm, exp_n);
            }
            exp_n++;
            fp = pp ? pp : fp + 1;
        }
        CHECK(frames_got == exp_n, "%s: số khung %d != %d", nm, frames_got, exp_n);
        if (exp_crc) CHECK((int)d.stats.crc_errors == (int)strtol(exp_crc, NULL, 10), "%s: crc_errors %u", nm, d.stats.crc_errors);
        if (exp_len) CHECK((int)d.stats.len_errors == (int)strtol(exp_len, NULL, 10), "%s: len_errors %u", nm, d.stats.len_errors);
        count++;
        p = q;
    }
    printf("%d test vector, %d lỗi\n", count, failures);
    free(js);
    return failures ? 1 : 0;
}
