"""Touch patch browser — Touch (MPE pressure floor) mixin."""

from __future__ import annotations

from patch_browser.patch_pressure import (
    DEFAULT_PRESSURE_FLOOR,
    PRESSURE_FLOOR_MAX,
    PRESSURE_FLOOR_MIN,
)


class TouchBrowserTouchMixin:
    """Mixin — expects TouchPatchBrowser host attributes."""

    def _show_touch_fader(self) -> bool:
        return bool(self.detail_patch)

    def _touch_floor_for_detail(self) -> float:
        if not self.detail_patch:
            return DEFAULT_PRESSURE_FLOOR
        return self.loader.pressure.get_effective_floor(self.detail_patch["name"])

    def _sync_pressure_live(self, floor: float | None = None) -> None:
        if not self.detail_patch:
            return
        name = self.detail_patch["name"]
        eff = self.loader.pressure.get_effective_floor(name) if floor is None else float(floor)
        self.loader.pressure.write_live_state(name, eff)

    def _apply_touch_floor(self, floor: float, *, persist: bool = True) -> None:
        if not self.detail_patch:
            return
        name = self.detail_patch["name"]
        store = self.loader.pressure
        clamped = max(PRESSURE_FLOOR_MIN, min(PRESSURE_FLOOR_MAX, float(floor)))
        default = store.get_slider_default_floor(name)
        if abs(clamped - default) < 0.01:
            store.clear_user_touch_offset(name, persist=persist)
        else:
            store.set_user_floor(name, clamped, persist=persist)
        self._sync_pressure_live()

    def _reset_touch_to_default(self) -> None:
        if not self.detail_patch:
            return
        name = self.detail_patch["name"]
        store = self.loader.pressure
        store.clear_user_touch_offset(name)
        default = store.get_slider_default_floor(name)
        self._sync_pressure_live(default)
        if store.get_calibrated_floor(name) is not None:
            self._toast(f"Touch reset to {store.format_floor(default)}", 1.2)
        else:
            self._toast("Touch reset", 1.2)
