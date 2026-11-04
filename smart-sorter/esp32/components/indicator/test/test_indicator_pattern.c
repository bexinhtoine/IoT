#include <assert.h>
#include "indicator_pattern.h"

int main(void)
{
    int g, r, b;
    for (unsigned t = 0; t < 200; ++t) {
        indicator_pattern_eval(INDICATOR_RUNNING, t, &g, &r, &b);
        assert(!g && !r && !b);
        indicator_pattern_eval(INDICATOR_CHECKING, t, &g, &r, &b);
        assert(!g && !r && !b);
        bool done = indicator_pattern_eval(INDICATOR_OK, t, &g, &r, &b);
        assert(g == (t < 6) && !r && b == (t < 2));
        assert(done == (t >= 6));
        done = indicator_pattern_eval(INDICATOR_DEFECT, t, &g, &r, &b);
        assert(!g && r == (t < 6) && !b && done == (t >= 6));
    }
    unsigned pulses = 0;
    int last_red = 0;
    for (unsigned t = 0; t < 200; ++t) {
        bool done = indicator_pattern_eval(INDICATOR_NOT_BOX, t, &g, &r, &b);
        assert(!g && !b);
        assert(done == (t >= 24));
        if (t >= 24) assert(!r);
        if (r && !last_red) ++pulses;
        last_red = r;
    }
    assert(pulses == 3);
    assert(indicator_is_transient(INDICATOR_NOT_BOX));
    assert(indicator_is_transient(INDICATOR_OK));
    assert(indicator_is_transient(INDICATOR_DEFECT));
    assert(!indicator_is_transient(INDICATOR_RUNNING));
    return 0;
}
