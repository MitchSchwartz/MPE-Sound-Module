"""Touch patch browser — Tail (AEG sustain/decay/release) multiplier mixin."""

from __future__ import annotations

from patch_browser.patch_hold import (
    DEFAULT_HOLD_MULT,
    HOLD_MULT_MAX,
    HOLD_MULT_MIN,
)


class TouchBrowserHoldMixin:
    """Mixin — expects TouchPatchBrowser host attributes."""

    def _show_tail_fader(self) -> bool:
        return bool(self.detail_patch)

    # Back-compat alias for layout getattr fallback
    _show_hold_fader = _show_tail_fader

    def _tail_mult_for_detail(self) -> float:
        if not self.detail_patch:
            return DEFAULT_HOLD_MULT
        return self.loader.hold.get_effective_hold_mult(self.detail_patch["name"])

    def _apply_tail_mult(self, mult: float, *, persist: bool = True) -> None:
        if not self.detail_patch:
            return
        name = self.detail_patch["name"]
        store = self.loader.hold
        clamped = max(HOLD_MULT_MIN, min(HOLD_MULT_MAX, float(mult)))
        if abs(clamped - DEFAULT_HOLD_MULT) < 0.02:
            store.clear_user_hold_mult(name, persist=persist)
        else:
            store.set_user_hold_mult(name, clamped, persist=persist)
        loaded = self.loaded_patch_info
        if (
            loaded
            and self.loader.osc_enabled
            and store.patch_key(loaded["name"]) == store.patch_key(name)
        ):
            self.loader.refresh_hold(name)

    def _reset_tail_to_patch_default(self) -> None:
        if not self.detail_patch:
            return
        name = self.detail_patch["name"]
        store = self.loader.hold
        store.clear_user_hold_mult(name)
        loaded = self.loaded_patch_info
        if (
            loaded
            and self.loader.osc_enabled
            and store.patch_key(loaded["name"]) == store.patch_key(name)
        ):
            self.loader.refresh_hold(name)
        self._toast("Tail reset to 1.0×", 1.5)
