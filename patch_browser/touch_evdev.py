"""Linux evdev touch bridge — SYN_REPORT-safe input for DSI panels.

Pygame/SDL touch on Pi DSI is unreliable; this reads the panel directly.
Critical: emit press/move/release only on EV_SYN/SYN_REPORT so X/Y are synced
(see Linux input docs + lvgl/lvgl#5211).
"""

from __future__ import annotations

import os
import threading
from typing import Callable

try:
    import evdev
    from evdev import InputDevice, ecodes
except ImportError:
    evdev = None  # type: ignore[assignment]
    InputDevice = None  # type: ignore[assignment,misc]
    ecodes = None  # type: ignore[assignment]


def _touch_name_match(name: str) -> bool:
    lowered = name.lower()
    keys = ("touch", "touchscreen", "goodix", "ft5", "edt", "ili", "lcd", "ts")
    return any(key in lowered for key in keys)


def find_touch_device_path() -> str | None:
    if evdev is None:
        return None
    candidates: list[tuple[int, str, str]] = []
    for path in evdev.list_devices():
        try:
            dev = InputDevice(path)
            caps = dev.capabilities()
        except OSError:
            continue
        if ecodes.EV_ABS not in caps:
            continue
        abs_codes = {code for code, _ in caps[ecodes.EV_ABS]}
        if ecodes.ABS_X not in abs_codes and ecodes.ABS_MT_POSITION_X not in abs_codes:
            continue
        if ecodes.ABS_Y not in abs_codes and ecodes.ABS_MT_POSITION_Y not in abs_codes:
            continue
        name = dev.name or path
        if not _touch_name_match(name):
            continue
        score = 0
        if "touch" in name.lower():
            score += 10
        candidates.append((score, path, name))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][1]


class TouchEvdevBridge:
    """Read capacitive panel via evdev; callbacks fire on SYN_REPORT frames."""

    def __init__(
        self,
        screen_width: int,
        screen_height: int,
        on_down: Callable[[tuple[int, int]], None],
        on_up: Callable[[tuple[int, int]], None],
        on_motion: Callable[[tuple[int, int]], None],
        device_path: str | None = None,
    ) -> None:
        self.screen_width = screen_width
        self.screen_height = screen_height
        self.on_down = on_down
        self.on_up = on_up
        self.on_motion = on_motion
        self.device_path = device_path or find_touch_device_path()
        self._device: InputDevice | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._touch_active = False
        self._down_sent = False
        self._x = 0
        self._y = 0
        self._x_code = ecodes.ABS_X if ecodes else 0
        self._y_code = ecodes.ABS_Y if ecodes else 0
        self._x_info = None
        self._y_info = None
        self._invert_y = os.environ.get("MPE_TOUCH_INVERT_Y", "0").strip() in (
            "1",
            "true",
            "yes",
        )

    @property
    def active(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> bool:
        if evdev is None or not self.device_path:
            return False
        try:
            self._device = InputDevice(self.device_path)
            caps = self._device.capabilities().get(ecodes.EV_ABS, [])
            abs_codes = {code for code, _ in caps}
            if ecodes.ABS_MT_POSITION_X in abs_codes and ecodes.ABS_MT_POSITION_Y in abs_codes:
                self._x_code = ecodes.ABS_MT_POSITION_X
                self._y_code = ecodes.ABS_MT_POSITION_Y
            else:
                self._x_code = ecodes.ABS_X
                self._y_code = ecodes.ABS_Y
            self._x_info = self._device.absinfo(self._x_code)
            self._y_info = self._device.absinfo(self._y_code)
        except OSError as exc:
            print(f"Touch evdev bridge: could not open {self.device_path} ({exc})")
            return False

        self._thread = threading.Thread(target=self._run, name="TouchEvdevBridge", daemon=True)
        self._thread.start()
        x_rng = f"{self._x_info.min}-{self._x_info.max}" if self._x_info else "?"
        y_rng = f"{self._y_info.min}-{self._y_info.max}" if self._y_info else "?"
        print(
            f"Touch evdev bridge: {self._device.name} ({self.device_path}) "
            f"axes {self._x_code}/{self._y_code} range {x_rng}x{y_rng} "
            f"-> {self.screen_width}x{self.screen_height}"
        )
        return True

    def stop(self) -> None:
        self._stop.set()
        if self._device is not None:
            try:
                self._device.close()
            except OSError:
                pass

    def _scale(self) -> tuple[int, int]:
        sx = self._scale_axis(self._x, self._x_info, self.screen_width, invert=False)
        sy = self._scale_axis(self._y, self._y_info, self.screen_height, invert=self._invert_y)
        return sx, sy

    @staticmethod
    def _scale_axis(raw: int, info, screen_dim: int, *, invert: bool) -> int:
        if info is None:
            return 0
        min_v = info.min
        max_v = info.max
        if max_v <= min_v:
            return 0
        ratio = (raw - min_v) / float(max_v - min_v)
        ratio = max(0.0, min(1.0, ratio))
        if invert:
            ratio = 1.0 - ratio
        return max(0, min(screen_dim - 1, int(round(ratio * (screen_dim - 1)))))

    def _handle_syn_report(self, frame_touch: int | None) -> None:
        pos = self._scale()

        if frame_touch == 1:
            self._touch_active = True
            self._down_sent = False

        if frame_touch == 0 and self._touch_active:
            self.on_up(pos)
            self._touch_active = False
            self._down_sent = False
            return

        if not self._touch_active:
            return

        if not self._down_sent:
            self.on_down(pos)
            self._down_sent = True
            return

        self.on_motion(pos)

    def _run(self) -> None:
        assert self._device is not None
        frame_touch: int | None = None
        try:
            for event in self._device.read_loop():
                if self._stop.is_set():
                    break
                if event.type == ecodes.EV_ABS:
                    if event.code in (self._x_code, ecodes.ABS_X, ecodes.ABS_MT_POSITION_X):
                        self._x = event.value
                    elif event.code in (self._y_code, ecodes.ABS_Y, ecodes.ABS_MT_POSITION_Y):
                        self._y = event.value
                elif event.type == ecodes.EV_KEY and event.code in (
                    ecodes.BTN_TOUCH,
                    ecodes.BTN_LEFT,
                ):
                    frame_touch = event.value
                elif event.type == ecodes.EV_SYN and event.code == ecodes.SYN_REPORT:
                    self._handle_syn_report(frame_touch)
                    frame_touch = None
        except OSError as exc:
            if not self._stop.is_set():
                print(f"Touch evdev bridge stopped ({exc})")


def evdev_bridge_enabled() -> bool:
    mode = os.environ.get("MPE_TOUCH_EVDEV", "auto").strip().lower()
    if mode in ("0", "false", "no", "off"):
        return False
    return True
