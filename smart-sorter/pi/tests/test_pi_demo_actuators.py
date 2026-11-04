from __future__ import annotations

import pytest
from sorter.pi_demo_actuators import (
    BoxTriggerGate,
    DemoActuatorSequence,
    DemoActuatorTiming,
    LoggingActuatorDriver,
)


class RecordingDriver:
    def __init__(self, *, fail_on_active_servo: bool = False) -> None:
        self.events: list[tuple[str, object]] = []
        self.fail_on_active_servo = fail_on_active_servo

    def set_led(self, enabled: bool) -> None:
        self.events.append(("led", enabled))

    def set_buzzer(self, enabled: bool) -> None:
        self.events.append(("buzzer", enabled))

    def set_servo_angle(self, angle: float) -> None:
        self.events.append(("servo", angle))
        if self.fail_on_active_servo and angle == 45:
            raise RuntimeError("servo failure")

    def close(self) -> None:
        self.events.append(("close", True))


def test_actuator_sequence_is_bounded_and_returns_to_safe_state() -> None:
    driver = RecordingDriver()
    sleeps: list[float] = []
    sequence = DemoActuatorSequence(
        driver,
        home_angle=0,
        active_angle=45,
        timing=DemoActuatorTiming(buzzer_s=0.1, servo_hold_s=0.8, servo_return_s=0.2),
        sleep=sleeps.append,
    )
    driver.events.clear()

    sequence.activate()

    assert driver.events == [
        ("led", True),
        ("buzzer", True),
        ("servo", 45),
        ("buzzer", False),
        ("servo", 0),
        ("led", False),
    ]
    assert sleeps == pytest.approx([0.1, 0.7, 0.2])
    sequence.close()
    assert driver.events[-4:] == [
        ("buzzer", False),
        ("led", False),
        ("servo", 0),
        ("close", True),
    ]
    sequence.close()
    assert driver.events.count(("close", True)) == 1


def test_actuator_sequence_fails_safe_on_driver_error() -> None:
    driver = RecordingDriver(fail_on_active_servo=True)
    sequence = DemoActuatorSequence(driver, sleep=lambda _: None)
    driver.events.clear()

    with pytest.raises(RuntimeError, match="servo failure"):
        sequence.activate()

    assert driver.events[-3:] == [("buzzer", False), ("led", False), ("servo", 0)]


def test_actuator_contract_rejects_invalid_values_and_closed_use() -> None:
    with pytest.raises(ValueError, match="durations"):
        DemoActuatorTiming(buzzer_s=-1)
    with pytest.raises(ValueError, match="angles"):
        DemoActuatorSequence(RecordingDriver(), active_angle=181)
    sequence = DemoActuatorSequence(RecordingDriver())
    sequence.close()
    with pytest.raises(RuntimeError, match="closed"):
        sequence.activate()


def test_box_gate_triggers_once_and_rearms_only_after_clear_and_cooldown() -> None:
    gate = BoxTriggerGate(stable_frames=3, clear_frames=2, cooldown_s=2)

    assert not gate.update(1, now=0.0)
    assert not gate.update(1, now=0.1)
    assert gate.update(1, now=0.2)
    assert not gate.armed
    assert not gate.update(1, now=10.0)
    assert not gate.update(2, now=10.1)
    assert not gate.update(0, now=10.2)
    assert not gate.update(0, now=10.3)
    assert gate.armed
    assert not gate.update(1, now=10.4)
    assert not gate.update(1, now=10.5)
    assert gate.update(1, now=10.6)


def test_box_gate_validates_inputs() -> None:
    with pytest.raises(ValueError, match="positive"):
        BoxTriggerGate(stable_frames=0)
    with pytest.raises(ValueError, match="non-negative"):
        BoxTriggerGate(cooldown_s=-1)
    gate = BoxTriggerGate()
    with pytest.raises(ValueError, match="box_count"):
        gate.update(-1, now=0)


def test_logging_driver_emits_all_actions() -> None:
    events: list[str] = []
    driver = LoggingActuatorDriver(events.append)
    driver.set_led(True)
    driver.set_buzzer(False)
    driver.set_servo_angle(45)
    driver.close()
    assert events == ["LED=ON", "BUZZER=OFF", "SERVO=45.0deg", "GPIO=CLOSED"]
