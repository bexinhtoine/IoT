#include <assert.h>
#include "sensor_gate.h"

int main(void)
{
    sensor_gate_t s = {0};
    assert(!sensor_gate_update(&s, 301, true));
    assert(!sensor_gate_update(&s, 300, true));
    assert(sensor_gate_update(&s, 300, true)); /* bao gom dung 30 cm */
    for (int i = 0; i < 100; ++i) assert(!sensor_gate_update(&s, 100, true));
    assert(!sensor_gate_update(&s, 100, false));
    assert(!sensor_gate_update(&s, 100, true)); /* tat/bat khong dem lai vat cu */
    for (int i = 0; i < 10; ++i) assert(!sensor_gate_update(&s, 320, true));
    assert(s.latched); /* hysteresis */
    for (int i = 0; i < 3; ++i) assert(!sensor_gate_update(&s, 400, false));
    assert(!s.latched);
    assert(!sensor_gate_update(&s, 100, false));
    assert(!sensor_gate_update(&s, 100, true));
    assert(!sensor_gate_update(&s, -1, true)); /* xung nhieu xen giua */
    assert(!sensor_gate_update(&s, 100, true));
    assert(sensor_gate_update(&s, 100, true));
    for (int i = 0; i < 3; ++i) assert(!sensor_gate_update(&s, -2, true));
    assert(s.latched); /* stuck HIGH khong duoc giai phong latch */
    for (int i = 0; i < 3; ++i) assert(!sensor_gate_update(&s, -1, true));
    assert(!s.latched);
    for (int i = 0; i < 3; ++i) assert(!sensor_gate_update(&s, 0, true));
    assert(!sensor_gate_update(&s, 19, true));
    assert(!sensor_gate_update(&s, 20, true));
    assert(sensor_gate_update(&s, 20, true));
    return 0;
}
