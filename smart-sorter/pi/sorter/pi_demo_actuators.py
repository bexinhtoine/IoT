"""Direct Raspberry Pi GPIO actuators for the camera-only demonstration.

This module is deliberately separate from the production ESP32/UART path.  It
contains no sensor or button logic and can be tested with a recording driver on
a laptop.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol


class ActuatorDriver(Protocol):
    """Minimal output surface used by the demo sequence."""

    def set_led(self, enabled: bool) -> None: ...

    def set_buzzer(self, enabled: bool) -> None: ...

    def set_servo_angle(self, angle: float) -> None: ...

    def set_motor(self, enabled: bool, forward: bool = True) -> None: ...

    def close(self) -> None: ...


@dataclass(frozen=True)
class DemoActuatorTiming:
    buzzer_s: float = 0.12
    servo_hold_s: float = 0.8
    servo_return_s: float = 0.25

    def __post_init__(self) -> None:
        if self.buzzer_s < 0 or self.servo_hold_s < 0 or self.servo_return_s < 0:
            raise ValueError("actuator durations must be non-negative")


class DemoActuatorSequence:
    """Turn on LED/buzzer and sweep one servo once, then return to safe state."""

    def __init__(
        self,
        driver: ActuatorDriver,
        *,
        home_angle: float = 0.0,
        active_angle: float = 45.0,
        timing: DemoActuatorTiming | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not 0 <= home_angle <= 180 or not 0 <= active_angle <= 180:
            raise ValueError("servo angles must be in [0, 180]")
        self.driver = driver
        self.home_angle = home_angle
        self.active_angle = active_angle
        self.timing = timing or DemoActuatorTiming()
        self._sleep = sleep
        self._closed = False
        self.safe_state()

    def safe_state(self) -> None:
        """Force finite outputs: buzzer/LED off, motor stopped, and servo at home."""
        self.driver.set_buzzer(False)
        self.driver.set_led(False)
        self.driver.set_servo_angle(self.home_angle)
        if hasattr(self.driver, "set_motor"):
            self.driver.set_motor(False)

    def activate(self) -> None:
        """Run one bounded indication, servo and motor cycle."""
        if self._closed:
            raise RuntimeError("actuator sequence is closed")
        try:
            self.driver.set_led(True)
            self.driver.set_buzzer(True)
            self.driver.set_servo_angle(self.active_angle)
            if hasattr(self.driver, "set_motor"):
                self.driver.set_motor(True)
            self._sleep(self.timing.buzzer_s)
            self.driver.set_buzzer(False)
            self._sleep(max(0.0, self.timing.servo_hold_s - self.timing.buzzer_s))
            self.driver.set_servo_angle(self.home_angle)
            self._sleep(self.timing.servo_return_s)
            self.driver.set_led(False)
            if hasattr(self.driver, "set_motor"):
                self.driver.set_motor(False)
        except BaseException:
            self.safe_state()
            raise

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self.safe_state()
        finally:
            self.driver.close()


class BoxTriggerGate:
    """Debounce one detected box and require it to leave before rearming."""

    def __init__(
        self,
        *,
        stable_frames: int = 3,
        clear_frames: int = 5,
        cooldown_s: float = 2.0,
    ) -> None:
        if stable_frames < 1 or clear_frames < 1:
            raise ValueError("stable_frames and clear_frames must be positive")
        if cooldown_s < 0:
            raise ValueError("cooldown_s must be non-negative")
        self.stable_frames = stable_frames
        self.clear_frames = clear_frames
        self.cooldown_s = cooldown_s
        self._present_count = 0
        self._clear_count = 0
        self._armed = True
        self._last_trigger_at = float("-inf")

    @property
    def armed(self) -> bool:
        return self._armed

    @property
    def present_count(self) -> int:
        return self._present_count

    def update(self, box_count: int, *, now: float) -> bool:
        """Return True once for one stable single-box appearance."""
        if box_count < 0:
            raise ValueError("box_count must be non-negative")
        if box_count == 1:
            self._present_count += 1
            self._clear_count = 0
            if (
                self._armed
                and self._present_count >= self.stable_frames
                and now - self._last_trigger_at >= self.cooldown_s
            ):
                self._armed = False
                self._last_trigger_at = now
                return True
            return False

        self._present_count = 0
        if box_count == 0:
            self._clear_count += 1
            if (
                not self._armed
                and self._clear_count >= self.clear_frames
                and now - self._last_trigger_at >= self.cooldown_s
            ):
                self._armed = True
        else:
            self._clear_count = 0
        return False


class LoggingActuatorDriver:
    """No-GPIO driver for laptop and Pi dry runs."""

    def __init__(self, emit: Callable[[str], None] = print) -> None:
        self._emit = emit

    def set_led(self, enabled: bool) -> None:
        self._emit(f"LED={'ON' if enabled else 'OFF'}")

    def set_buzzer(self, enabled: bool) -> None:
        self._emit(f"BUZZER={'ON' if enabled else 'OFF'}")

    def set_servo_angle(self, angle: float) -> None:
        self._emit(f"SERVO={angle:.1f}deg")

    def set_motor(self, enabled: bool, forward: bool = True) -> None:
        self._emit(f"MOTOR={'RUN' if enabled else 'STOP'}")

    def close(self) -> None:
        self._emit("GPIO=CLOSED")


class GpioZeroActuatorDriver:
    """gpiozero implementation using BCM numbering and finite safe defaults."""

    def __init__(
        self,
        *,
        led_pin: int = 17,
        buzzer_pin: int = 27,
        servo_pin: int = 18,
        motor_in1: int | None = None,
        motor_in2: int | None = None,
        motor_en: int | None = None,
        home_angle: float = 0.0,
        use_pigpio: bool = False,
    ) -> None:
        try:
            from gpiozero import LED, AngularServo, OutputDevice, Motor
        except ImportError as error:  # pragma: no cover - Raspberry Pi dependency
            raise RuntimeError("gpiozero is required on Raspberry Pi") from error

        pin_factory = None
        if use_pigpio:
            try:
                from gpiozero.pins.pigpio import PiGPIOFactory
            except ImportError as error:  # pragma: no cover - Raspberry Pi dependency
                raise RuntimeError(
                    "pigpio is required when --pigpio is enabled"
                ) from error
            pin_factory = PiGPIOFactory()
        kwargs = {"pin_factory": pin_factory} if pin_factory is not None else {}
        self._led = LED(led_pin, initial_value=False, **kwargs)
        self._buzzer = OutputDevice(
            buzzer_pin,
            active_high=True,
            initial_value=False,
            **kwargs,
        )
        self._servo = AngularServo(
            servo_pin,
            min_angle=0,
            max_angle=180,
            initial_angle=home_angle,
            min_pulse_width=0.0005,
            max_pulse_width=0.0025,
            **kwargs,
        )
        self._motor = None
        if motor_in1 is not None:
            if motor_in2 is not None:
                self._motor = Motor(forward=motor_in1, backward=motor_in2, enable=motor_en, **kwargs)
            else:
                self._motor = OutputDevice(motor_in1, initial_value=False, **kwargs)

    def set_led(self, enabled: bool) -> None:
        self._led.on() if enabled else self._led.off()

    def set_buzzer(self, enabled: bool) -> None:
        self._buzzer.on() if enabled else self._buzzer.off()

    def set_servo_angle(self, angle: float) -> None:
        self._servo.angle = angle

    def set_motor(self, enabled: bool, forward: bool = True) -> None:
        if self._motor is not None:
            if hasattr(self._motor, "forward"):
                if enabled:
                    self._motor.forward() if forward else self._motor.backward()
                else:
                    self._motor.stop()
            else:
                self._motor.on() if enabled else self._motor.off()

    def close(self) -> None:
        self._buzzer.off()
        self._led.off()
        self.set_motor(False)
        self._servo.close()
        self._buzzer.close()
        self._led.close()
        if self._motor is not None:
            self._motor.close()
