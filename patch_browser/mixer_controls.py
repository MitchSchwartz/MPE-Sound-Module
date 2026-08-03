"""Reusable mixer fader bindings — one control per column (Vol / Tail / Touch / Norm).

Fader semantics: docs/TOUCH_PATCH_BROWSER.md §Mixer faders (Tail ≠ Touch ≠ Norm).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from patch_browser.patch_hold import (
    DEFAULT_HOLD_MULT,
    HOLD_OFFSET_MAX,
    HOLD_OFFSET_MIN,
    clamp_hold_offset,
    hold_mult_to_offset,
    hold_offset_to_mult,
)
from patch_browser.patch_normalization import NORM_GAIN_DB_MAX, NORM_GAIN_DB_MIN, volume_fader_display_pct
from patch_browser.patch_pressure import (
    TOUCH_DISPLAY_CLEAR_EPSILON,
    TOUCH_DISPLAY_MAX,
    TOUCH_DISPLAY_MIN,
    TOUCH_OFFSET_CLEAR_EPSILON,
    cal_floor_to_touch_anchor,
    touch_fader_to_offset,
    touch_fader_value,
)
from patch_browser.touch_ui_constants import DEFAULT_VOLUME, VOLUME_MAX, VOLUME_MIN


@dataclass(frozen=True)
class MixerControlSpec:
    channel_id: str
    label: str
    min_value: float
    max_value: float


@runtime_checkable
class MixerControl(Protocol):
    spec: MixerControlSpec

    def visible(self, browser) -> bool: ...

    def read(self, browser) -> float: ...

    def default(self, browser) -> float: ...

    def write(self, browser, value: float, *, persist: bool) -> None: ...

    def reset(self, browser) -> None: ...

    def format(self, value: float) -> str: ...

    def persist(self, browser) -> None: ...


def sync_pressure_live(browser, floor: float | None = None) -> None:
    if not browser.detail_patch:
        return
    name = browser.detail_patch["name"]
    store = browser.loader.pressure
    eff = store.get_effective_floor(name) if floor is None else float(floor)
    store.write_live_state(name, eff)


def _format_hold_offset(value: float) -> str:
    pts = round(clamp_hold_offset(value) * 100)
    if pts > 0:
        return f"+{pts}"
    return str(pts)


def _format_touch_display(value: float) -> str:
    from patch_browser.patch_pressure import clamp_touch_display

    pts = round(clamp_touch_display(value))
    if pts > 0:
        return f"+{pts}"
    return str(pts)


class VolumeControl:
    spec = MixerControlSpec("volume", "Vol", VOLUME_MIN, VOLUME_MAX)

    def visible(self, browser) -> bool:
        return True

    def read(self, browser) -> float:
        return float(browser.volume_level)

    def default(self, browser) -> float:
        return DEFAULT_VOLUME

    def write(self, browser, value: float, *, persist: bool) -> None:
        browser._apply_volume(max(self.spec.min_value, min(self.spec.max_value, value)))

    def reset(self, browser) -> None:
        browser._apply_volume(self.default(browser))
        browser._toast("Volume reset", 1.2)

    def format(self, value: float) -> str:
        return f"{volume_fader_display_pct(value, fader_min=VOLUME_MIN, fader_max=VOLUME_MAX)}"

    def persist(self, browser) -> None:
        pass


class TailControl:
    spec = MixerControlSpec("tail", "Tail", HOLD_OFFSET_MIN, HOLD_OFFSET_MAX)

    def visible(self, browser) -> bool:
        return bool(browser.detail_patch)

    def read(self, browser) -> float:
        if not browser.detail_patch:
            return 0.0
        mult = browser.loader.hold.get_effective_hold_mult(browser.detail_patch["name"])
        return hold_mult_to_offset(mult)

    def default(self, browser) -> float:
        return 0.0

    def write(self, browser, value: float, *, persist: bool) -> None:
        if not browser.detail_patch:
            return
        name = browser.detail_patch["name"]
        store = browser.loader.hold
        clamped = hold_offset_to_mult(value)
        if abs(clamped - DEFAULT_HOLD_MULT) < 0.02:
            store.clear_user_hold_mult(name, persist=persist)
        else:
            store.set_user_hold_mult(name, clamped, persist=persist)
        loaded = browser.loaded_patch_info
        if (
            loaded
            and browser.loader.osc_enabled
            and store.patch_key(loaded["name"]) == store.patch_key(name)
        ):
            browser.loader.refresh_hold(name)

    def reset(self, browser) -> None:
        if not browser.detail_patch:
            return
        name = browser.detail_patch["name"]
        store = browser.loader.hold
        store.clear_user_hold_mult(name)
        loaded = browser.loaded_patch_info
        if (
            loaded
            and browser.loader.osc_enabled
            and store.patch_key(loaded["name"]) == store.patch_key(name)
        ):
            browser.loader.refresh_hold(name)
        browser._toast("Tail reset to 0", 1.5)

    def format(self, value: float) -> str:
        return _format_hold_offset(value)

    def persist(self, browser) -> None:
        browser.loader.hold.save()


class NormControl:
    """Calibrated absolute value on a bipolar scale."""

    spec = MixerControlSpec("norm", "Norm", NORM_GAIN_DB_MIN, NORM_GAIN_DB_MAX)

    def visible(self, browser) -> bool:
        if not browser.detail_patch:
            return False
        store = browser.loader.normalization
        if not store.is_globally_enabled():
            return False
        return store.is_enabled(browser.detail_patch["name"])

    def read(self, browser) -> float:
        if not browser.detail_patch:
            return 0.0
        store = browser.loader.normalization
        name = browser.detail_patch["name"]
        effective = store.get_effective_gain_db(name)
        if effective is not None:
            return max(self.spec.min_value, min(self.spec.max_value, effective))
        default = store.get_slider_default_gain_db(name)
        return max(self.spec.min_value, min(self.spec.max_value, default))

    def default(self, browser) -> float:
        if not browser.detail_patch:
            return 0.0
        gain = browser.loader.normalization.get_slider_default_gain_db(browser.detail_patch["name"])
        return max(self.spec.min_value, min(self.spec.max_value, gain))

    def write(self, browser, value: float, *, persist: bool) -> None:
        if not browser.detail_patch:
            return
        name = browser.detail_patch["name"]
        store = browser.loader.normalization
        default = store.get_slider_default_gain_db(name)
        clamped = max(self.spec.min_value, min(self.spec.max_value, float(value)))
        if abs(clamped - default) < 0.05:
            store.clear_user_gain_db(name, persist=persist)
        else:
            store.set_user_gain_db(name, clamped, persist=persist)
        loaded = browser.loaded_patch_info
        if (
            loaded
            and browser.loader.osc_enabled
            and store.patch_key(loaded["name"]) == store.patch_key(name)
        ):
            browser.loader.refresh_patch_volume(name)

    def reset(self, browser) -> None:
        if not browser.detail_patch:
            return
        name = browser.detail_patch["name"]
        store = browser.loader.normalization
        store.clear_user_gain_db(name)
        default = store.get_slider_default_gain_db(name)
        loaded = browser.loaded_patch_info
        if (
            loaded
            and browser.loader.osc_enabled
            and store.patch_key(loaded["name"]) == store.patch_key(name)
        ):
            browser.loader.refresh_patch_volume(name)
        if store.get_calibrated_gain_db(name) is not None:
            browser._toast(f"Level reset to {default:+.1f} dB", 1.5)
        else:
            browser._toast("Level reset to 0 dB", 1.5)

    def format(self, value: float) -> str:
        return f"{value:+.1f}"

    def persist(self, browser) -> None:
        browser.loader.normalization.save()


class TouchControl:
    """±50 fader: cal anchor + trim; double-tap restores cal position."""

    spec = MixerControlSpec("touch", "Touch", TOUCH_DISPLAY_MIN, TOUCH_DISPLAY_MAX)

    def visible(self, browser) -> bool:
        return bool(browser.detail_patch)

    def read(self, browser) -> float:
        if not browser.detail_patch:
            return 0.0
        store = browser.loader.pressure
        name = browser.detail_patch["name"]
        baseline = store.get_slider_default_floor(name)
        offset = store.get_user_touch_offset(name)
        return touch_fader_value(baseline, offset)

    def default(self, browser) -> float:
        if not browser.detail_patch:
            return 0.0
        store = browser.loader.pressure
        return cal_floor_to_touch_anchor(store.get_slider_default_floor(browser.detail_patch["name"]))

    def write(self, browser, value: float, *, persist: bool) -> None:
        if not browser.detail_patch:
            return
        name = browser.detail_patch["name"]
        store = browser.loader.pressure
        baseline = store.get_slider_default_floor(name)
        offset = touch_fader_to_offset(value, baseline)
        if abs(offset) < TOUCH_OFFSET_CLEAR_EPSILON:
            store.clear_user_touch_offset(name, persist=persist)
        else:
            store.set_user_touch_offset(name, offset, persist=persist)
        sync_pressure_live(browser)

    def reset(self, browser) -> None:
        if not browser.detail_patch:
            return
        name = browser.detail_patch["name"]
        store = browser.loader.pressure
        store.clear_user_touch_offset(name)
        cal_floor = store.get_slider_default_floor(name)
        sync_pressure_live(browser, cal_floor)
        cal_display = cal_floor_to_touch_anchor(cal_floor)
        if store.get_calibrated_floor(name) is not None:
            browser._toast(f"Touch reset to {_format_touch_display(cal_display)}", 1.2)
        else:
            browser._toast("Touch reset", 1.2)

    def format(self, value: float) -> str:
        return _format_touch_display(value)

    def persist(self, browser) -> None:
        browser.loader.pressure.save()


_MIXER_CONTROLS: list[MixerControl] = [
    VolumeControl(),
    TailControl(),
    TouchControl(),
    NormControl(),
]


def mixer_controls_for_browser(browser) -> list[MixerControl]:
    del browser
    return _MIXER_CONTROLS


def mixer_control_by_id(browser, channel_id: str) -> MixerControl | None:
    del browser
    for control in _MIXER_CONTROLS:
        if control.spec.channel_id == channel_id:
            return control
    return None
