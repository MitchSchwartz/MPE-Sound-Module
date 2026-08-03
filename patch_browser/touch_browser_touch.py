"""Touch patch browser — Touch (MPE pressure floor) mixin."""

from __future__ import annotations

from patch_browser.patch_pressure import (
    PRESSURE_OFFSET_MAX,
    PRESSURE_OFFSET_MIN,
    clamp_touch_offset,
)


class TouchBrowserTouchMixin:
    """Mixin — expects TouchPatchBrowser host attributes."""

    def _show_touch_fader(self) -> bool:
        return bool(self.detail_patch)

    def _touch_offset_for_detail(self) -> float:
        if not self.detail_patch:
            return 0.0
        return self.loader.pressure.get_user_touch_offset(self.detail_patch["name"])

    def _sync_pressure_live(self, floor: float | None = None) -> None:
        if not self.detail_patch:
            return
        name = self.detail_patch["name"]
        eff = self.loader.pressure.get_effective_floor(name) if floor is None else float(floor)
        self.loader.pressure.write_live_state(name, eff)

    def _apply_touch_offset(self, offset: float, *, persist: bool = True) -> None:
        if not self.detail_patch:
            return
        name = self.detail_patch["name"]
        store = self.loader.pressure
        clamped = clamp_touch_offset(offset)
        store.set_user_touch_offset(name, clamped, persist=persist)
        self._sync_pressure_live()

    def _reset_touch_to_default(self) -> None:
        if not self.detail_patch:
            return
        name = self.detail_patch["name"]
        store = self.loader.pressure
        store.clear_user_touch_offset(name)
        self._sync_pressure_live()
        if store.get_calibrated_floor(name) is not None:
            self._toast("Touch reset to 0", 1.2)
        else:
            self._toast("Touch reset", 1.2)
