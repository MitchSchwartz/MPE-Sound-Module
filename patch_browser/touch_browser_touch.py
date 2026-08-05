"""Touch patch browser — Touch (MPE pressure floor) live-sync helper."""

from __future__ import annotations

from patch_browser.mixer_controls import sync_pressure_live


class TouchBrowserTouchMixin:
    """Mixin — expects TouchPatchBrowser host attributes."""

    def _show_touch_fader(self) -> bool:
        return bool(self.detail_patch)

    def _sync_pressure_live(self, floor: float | None = None) -> None:
        sync_pressure_live(self, floor)
