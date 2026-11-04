#ifndef INDICATOR_PATTERN_H
#define INDICATOR_PATTERN_H
#include <stdbool.h>
#include <stdint.h>
#include "indicator_modes.h"

static inline bool indicator_is_transient(indicator_mode_t m)
{
    return m == INDICATOR_OK || m == INDICATOR_DEFECT || m == INDICATOR_SECURITY || m == INDICATOR_FAILSAFE || m == INDICATOR_NOT_BOX;
}

static inline bool indicator_pattern_eval(indicator_mode_t m, uint32_t t, int *g, int *r, int *b)
{
    *g = 0; *r = 0; *b = 0;
    switch (m) {
    case INDICATOR_IDLE:
    case INDICATOR_RUNNING:
    case INDICATOR_CHECKING:
    case INDICATOR_OTA:
        return false;  /* xanh=do=coi=0 */
    case INDICATOR_ESTOP:
        *r = 1;
        *b = t < 20;
        return false;
    case INDICATOR_FAULT:
        *r = 1;
        return false;
    case INDICATOR_OK:
        *g = t < 6;
        *b = t < 2;
        return t >= 6;
    case INDICATOR_DEFECT:
    case INDICATOR_FAILSAFE:
        *r = t < 6;
        return t >= 6;
    case INDICATOR_NOT_BOX:
    case INDICATOR_SECURITY:
        *r = t < 24 && ((t / 4) % 2) == 0;
        *b = m == INDICATOR_SECURITY && t < 24 && (t % 8) < 2;
        return t >= 24;
    default:
        return true;
    }
}

#endif
