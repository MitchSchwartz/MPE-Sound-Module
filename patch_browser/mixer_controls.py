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
    PatchHoldStore,
    clamp_hold_offset,
    hold_mult_to_offset,
    hold_offset_to_mult,
)
from patch_browser.patch_normalization import NORM_GAIN_DB_MAX, NORM_GAIN_DB_MIN, volume_fader_display_db
from patch_browser.patch_pressure import (
    TOUCH_DISPLAY_CLEAR_EPSILON,
    TOUCH_DISPLAY_MAX,
    TOUCH_DISPLAY_MIN,
    TOUCH_OFFSET_CLEAR_EPSILON,
    cal_floor_to_touch_anchor,
    touch_fader_to_offset,
    touch_fader_value,
)
from patch_browser.patch_sidecar_key import sidecar_kwargs_from_patch
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
    patch = browser.detail_patch
    kw = sidecar_kwargs_from_patch(patch)
    store = browser.loader.pressure
    eff = (
        store.get_effective_floor(patch["name"], **kw)
        if floor is None
        else float(floor)
    )
    store.write_live_state(patch["name"], eff, **kw)


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
        return volume_fader_display_db(
            value, fader_min=VOLUME_MIN, fader_max=VOLUME_MAX
        )

    def persist(self, browser) -> None:
        pass


class TailControl:
    spec = MixerControlSpec("tail", "Tail", HOLD_OFFSET_MIN, HOLD_OFFSET_MAX)

    def visible(self, browser) -> bool:
        return bool(browser.detail_patch)

    def read(self, browser) -> float:
        if not browser.detail_patch:
            return 0.0
        kw = sidecar_kwargs_from_patch(browser.detail_patch)
        mult = browser.loader.hold.get_effective_hold_mult(
            browser.detail_patch["name"], **kw
        )
        return hold_mult_to_offset(mult)

    def default(self, browser) -> float:
        return 0.0

    def write(self, browser, value: float, *, persist: bool) -> None:
        if not browser.detail_patch:
            return
        patch = browser.detail_patch
        kw = sidecar_kwargs_from_patch(patch)
        name = patch["name"]
        store = browser.loader.hold
        clamped = hold_offset_to_mult(value)
        if abs(clamped - DEFAULT_HOLD_MULT) < 0.02:
            store.clear_user_hold_mult(name, persist=persist, **kw)
        else:
            store.set_user_hold_mult(name, clamped, persist=persist, **kw)
        loaded = browser.loaded_patch_info
        if loaded and browser.loader.osc_enabled and PatchHoldStore.refs_match(loaded, patch):
            browser.loader.refresh_hold(name)

    def reset(self, browser) -> None:
        if not browser.detail_patch:
            return
        patch = browser.detail_patch
        kw = sidecar_kwargs_from_patch(patch)
        name = patch["name"]
        store = browser.loader.hold
        store.clear_user_hold_mult(name, **kw)
        loaded = browser.loaded_patch_info
        if loaded and browser.loader.osc_enabled and PatchHoldStore.refs_match(loaded, patch):
            browser.loader.refresh_hold(name)
        browser._toast("Tail reset to 0", 1.5)

    def format(self, value: float) -> str:
        return _format_hold_offset(value)

    def persist(self, browser) -> None:
        browser.loader.hold.save()


class NormControl:
    """Per-patch trim offset from calibrated gain (v2 Norm fader)."""

    spec = MixerControlSpec("norm", "Norm", NORM_GAIN_DB_MIN, NORM_GAIN_DB_MAX)

    def visible(self, browser) -> bool:
        if not browser.detail_patch:
            return False
        store = browser.loader.normalization
        if not store.is_globally_enabled():
            return False
        kw = sidecar_kwargs_from_patch(browser.detail_patch)
        return store.is_enabled(browser.detail_patch["name"], **kw)

    def read(self, browser) -> float:
        if not browser.detail_patch:
            return 0.0
        store = browser.loader.normalization
        patch = browser.detail_patch
        kw = sidecar_kwargs_from_patch(patch)
        name = patch["name"]
        trim = store.get_user_trim_db(name, **kw)
        return max(self.spec.min_value, min(self.spec.max_value, trim))

    def default(self, browser) -> float:
        if not browser.detail_patch:
            return 0.0
        kw = sidecar_kwargs_from_patch(browser.detail_patch)
        gain = browser.loader.normalization.get_slider_default_gain_db(
            browser.detail_patch["name"], **kw
        )
        return max(self.spec.min_value, min(self.spec.max_value, gain))

    def write(self, browser, value: float, *, persist: bool) -> None:
        if not browser.detail_patch:
            return
        patch = browser.detail_patch
        kw = sidecar_kwargs_from_patch(patch)
        name = patch["name"]
        store = browser.loader.normalization
        clamped = max(self.spec.min_value, min(self.spec.max_value, float(value)))
        if abs(clamped) < 0.05:
            store.clear_user_trim_db(name, persist=persist, **kw)
        else:
            store.set_user_trim_db(name, clamped, persist=persist, **kw)
        loaded = browser.loaded_patch_info
        if (
            loaded
            and browser.loader.osc_enabled
            and store.refs_match(loaded, patch)
        ):
            browser.loader.refresh_patch_volume(name)

    def reset(self, browser) -> None:
        if not browser.detail_patch:
            return
        patch = browser.detail_patch
        kw = sidecar_kwargs_from_patch(patch)
        name = patch["name"]
        store = browser.loader.normalization
        store.clear_user_trim_db(name, **kw)
        loaded = browser.loaded_patch_info
        if (
            loaded
            and browser.loader.osc_enabled
            and store.refs_match(loaded, patch)
        ):
            browser.loader.refresh_patch_volume(name)
        browser._toast("Norm trim reset to calibrated baseline", 1.5)

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
        patch = browser.detail_patch
        kw = sidecar_kwargs_from_patch(patch)
        name = patch["name"]
        baseline = store.get_slider_default_floor(name, **kw)
        offset = store.get_user_touch_offset(name, **kw)
        return touch_fader_value(baseline, offset)

    def default(self, browser) -> float:
        if not browser.detail_patch:
            return 0.0
        store = browser.loader.pressure
        kw = sidecar_kwargs_from_patch(browser.detail_patch)
        return cal_floor_to_touch_anchor(
            store.get_slider_default_floor(browser.detail_patch["name"], **kw)
        )

    def write(self, browser, value: float, *, persist: bool) -> None:
        if not browser.detail_patch:
            return
        patch = browser.detail_patch
        kw = sidecar_kwargs_from_patch(patch)
        name = patch["name"]
        store = browser.loader.pressure
        baseline = store.get_slider_default_floor(name, **kw)
        offset = touch_fader_to_offset(value, baseline)
        if abs(offset) < TOUCH_OFFSET_CLEAR_EPSILON:
            store.clear_user_touch_offset(name, persist=persist, **kw)
        else:
            store.set_user_touch_offset(name, offset, persist=persist, **kw)
        sync_pressure_live(browser)

    def reset(self, browser) -> None:
        if not browser.detail_patch:
            return
        patch = browser.detail_patch
        kw = sidecar_kwargs_from_patch(patch)
        name = patch["name"]
        store = browser.loader.pressure
        store.clear_user_touch_offset(name, **kw)
        cal_floor = store.get_slider_default_floor(name, **kw)
        sync_pressure_live(browser, cal_floor)
        cal_display = cal_floor_to_touch_anchor(cal_floor)
        if store.get_calibrated_floor(name, **kw) is not None:
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
