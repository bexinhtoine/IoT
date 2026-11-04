/* Logic loc khoang cach doc lap ESP-IDF, dung duoc trong host test. */
#ifndef SENSOR_GATE_H
#define SENSOR_GATE_H
#include <stdbool.h>

typedef struct {
    bool latched;
    unsigned near_count;
    unsigned clear_count;
} sensor_gate_t;

static inline bool sensor_gate_update(sensor_gate_t *s, int mm, bool enabled)
{
    /* Khong co echo (-1) khac ECHO stuck/qua dai (-2). */
    bool clear = mm == -1 || (mm > 330 && mm <= 4000);
    if (s->latched) {
        s->near_count = 0;
        s->clear_count = clear ? s->clear_count + 1 : 0;
        if (s->clear_count >= 3) {
            s->latched = false;
            s->clear_count = 0;
        }
        return false;
    }
    if (!enabled || mm < 20 || mm > 300) {
        s->near_count = 0;
        return false;
    }
    if (++s->near_count < 2) {
        return false;
    }
    s->near_count = 0;
    s->latched = true;
    return true;
}
#endif
