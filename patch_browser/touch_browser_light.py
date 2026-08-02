"""Touch patch browser — Light (MPE pressure floor) mixin."""

from __future__ import annotations

from patch_browser.patch_pressure import (
    DEFAULT_PRESSURE_FLOOR,
    PRESSURE_FLOOR_MAX,
    PRESSURE_FLOOR_MIN,
)


class TouchBrowserLightMixin:
    """Mixin — expects TouchPatchBrowser host attributes."""

    def _show_light_fader(self) -> bool:
        return bool(self.detail_patch)

    def _light_floor_for_detail(self) -> float:
        if not self.detail_patch:
            return DEFAULT_PRESSURE_FLOOR
        return self.loader.pressure.get_effective_floor(self.detail_patch["name"])

    def _sync_pressure_live(self, floor: float | None = None) -> None:
        if not self.detail_patch:
            return
        name = self.detail_patch["name"]
        eff = self.loader.pressure.get_effective_floor(name) if floor is None else float(floor)
        self.loader.pressure.write_live_state(name, eff)

    def _apply_light_floor(self, floor: float, *, persist: bool = True) -> None:
        if not self.detail_patch:
            return
        name = self.detail_patch["name"]
        store = self.loader.pressure
        clamped = max(PRESSURE_FLOOR_MIN, min(PRESSURE_FLOOR_MAX, float(floor)))
        if abs(clamped - DEFAULT_PRESSURE_FLOOR) < 0.01:
            store.clear_user_floor(name, persist=persist)
        else:
            store.set_user_floor(name, clamped, persist=persist)
        self._sync_pressure_live(clamped)

    def _reset_light_to_default(self) -> None:
        if not self.detail_patch:
            return
        name = self.detail_patch["name"]
        self.loader.pressure.clear_user_floor(name)
        self._sync_pressure_live(DEFAULT_PRESSURE_FLOOR)
        self._toast("Light reset", 1.2)
