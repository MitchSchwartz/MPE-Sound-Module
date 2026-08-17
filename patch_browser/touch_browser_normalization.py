"""Touch patch browser — normalization mixin."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pygame

from patch_browser.calibration_constants import (
    CALIBRATION_LOADER_SCRIPT,
    MPE_CALIB_FROM_BROWSER,
    MPE_CALIB_FROM_BROWSER_ACTIVE,
    format_calibration_duration_hint,
)
from patch_browser.dsi_splash import SplashMode, draw_splash_frame
from patch_browser.geometry import Rect
from patch_browser.patch_normalization import NORM_GAIN_DB_MAX, NORM_GAIN_DB_MIN
from patch_browser.patch_sidecar_key import sidecar_kwargs_from_patch
from patch_browser.draw_primitives import draw_toggle_switch
from patch_browser.touch_ui_constants import (
    COMPACT_TOGGLE_H,
    COMPACT_TOGGLE_W,
    SETTINGS_TOGGLE_H,
    SETTINGS_TOGGLE_W,
)
from patch_browser.patch_scanner import favorites_display_name
from patch_browser.touch_ui_enums import CalibrateMode
from patch_browser.ui_text import blit_text_block, text_block_height, wrap_text_lines

CALIBRATION_EXECV_FAILURE_REPORT = Path("/tmp/touch-browser-calibration-execv-failure.json")


def _write_calibration_execv_failure_report(message: str, *, script: Path) -> None:
    payload = {
        "error": message,
        "script": str(script),
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    try:
        CALIBRATION_EXECV_FAILURE_REPORT.write_text(
            json.dumps(payload, indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError:
        pass


class TouchBrowserNormalizationMixin:
    """Mixin — expects TouchPatchBrowser host attributes."""

    def _detail_sidecar_kw(self) -> dict[str, str | None]:
        return sidecar_kwargs_from_patch(self.detail_patch)

    def _normalization_enabled_for_detail(self) -> bool:
        if not self.detail_patch:
            return True
        return self.loader.normalization.is_enabled(
            self.detail_patch["name"], **self._detail_sidecar_kw()
        )

    def _normalization_has_gain(self) -> bool:
        if not self.detail_patch:
            return False
        entry = self.loader.normalization.get_entry(
            self.detail_patch["name"], **self._detail_sidecar_kw()
        )
        return bool(
            entry
            and (
                entry.get("gain_db") is not None or entry.get("user_gain_db") is not None
            )
        )

    def _show_norm_level_fader(self) -> bool:
        """Second mixer column — only when Norm. is checked for this patch."""
        if not self.detail_patch:
            return False
        store = self.loader.normalization
        if not store.is_globally_enabled():
            return False
        return store.is_enabled(self.detail_patch["name"], **self._detail_sidecar_kw())

    def _norm_gain_db_for_detail(self) -> float:
        if not self.detail_patch:
            return 0.0
        store = self.loader.normalization
        kw = self._detail_sidecar_kw()
        name = self.detail_patch["name"]
        effective = store.get_effective_gain_db(name, **kw)
        if effective is not None:
            return max(NORM_GAIN_DB_MIN, min(NORM_GAIN_DB_MAX, effective))
        default = store.get_slider_default_gain_db(name, **kw)
        return max(NORM_GAIN_DB_MIN, min(NORM_GAIN_DB_MAX, default))

    def _apply_norm_gain_db(self, gain_db: float, *, persist: bool = True) -> None:
        if not self.detail_patch:
            return
        patch = self.detail_patch
        kw = self._detail_sidecar_kw()
        name = patch["name"]
        store = self.loader.normalization
        default = store.get_slider_default_gain_db(name, **kw)
        clamped = max(NORM_GAIN_DB_MIN, min(NORM_GAIN_DB_MAX, float(gain_db)))
        if abs(clamped - default) < 0.05:
            store.clear_user_gain_db(name, persist=persist, **kw)
        else:
            store.set_user_gain_db(name, clamped, persist=persist, **kw)
        loaded = self.loaded_patch_info
        if loaded and self.loader.osc_enabled and store.refs_match(loaded, patch):
            self.loader.refresh_patch_volume(name)

    def _reset_norm_gain_to_calibrated(self) -> None:
        if not self.detail_patch:
            return
        patch = self.detail_patch
        kw = self._detail_sidecar_kw()
        name = patch["name"]
        store = self.loader.normalization
        store.clear_user_gain_db(name, **kw)
        default = store.get_slider_default_gain_db(name, **kw)
        loaded = self.loaded_patch_info
        if loaded and self.loader.osc_enabled and store.refs_match(loaded, patch):
            self.loader.refresh_patch_volume(name)
        if store.get_calibrated_gain_db(name, **kw) is not None:
            self._toast(f"Level reset to {default:+.1f} dB", 1.5)
        else:
            self._toast("Level reset to 0 dB", 1.5)

    def _normalization_patch_name(self) -> str | None:
        if not self.detail_patch:
            return None
        if self.loaded_patch_info and self.loaded_patch_info.get("name") == self.detail_patch.get("name"):
            return self.loaded_patch_info["name"]
        return self.detail_patch["name"]
    def _toggle_normalization(self) -> None:
        if not self.loader.normalization.is_globally_enabled():
            return
        name = self._normalization_patch_name()
        if not name:
            return
        store = self.loader.normalization
        kw = sidecar_kwargs_from_patch(self.detail_patch)
        new_state = not store.is_enabled(name, **kw)
        store.set_enabled(name, new_state, **kw)
        self._layout()
        loaded = self.loaded_patch_info
        if loaded and self.loader.osc_enabled and store.refs_match(loaded, self.detail_patch):
            self.loader.refresh_patch_volume(loaded["name"])
        if new_state:
            if store.get_raw_gain_db(name, **kw) is not None:
                self._toast("Normalize on", 1.5)
            else:
                self._toast("Normalize on (no calibration)", 2.0)
        else:
            self._toast("Normalize off", 1.5)
    def _toggle_global_normalization(self) -> None:
        store = self.loader.normalization
        new_state = not store.is_globally_enabled()
        store.set_globally_enabled(new_state)
        self._layout()
        if self.loaded_patch_info:
            loaded_name = self.loaded_patch_info.get("name")
            if self.loader.osc_enabled and loaded_name:
                self.loader.refresh_patch_volume(loaded_name)
        if new_state:
            self._toast("Patch normalization on", 2.0)
        else:
            self._toast("Patch normalization off", 2.0)
    def _toggle_switch_rect(self, row: Rect, *, compact: bool = False) -> Rect:
        tw = COMPACT_TOGGLE_W if compact else SETTINGS_TOGGLE_W
        th = COMPACT_TOGGLE_H if compact else SETTINGS_TOGGLE_H
        pad = (row.h - th) // 2
        return Rect(row.right - pad - tw, row.y + pad, tw, th)

    def _draw_normalize_toggle(
        self,
        rect: Rect,
        enabled: bool,
        *,
        has_gain: bool,
        disabled: bool = False,
        label: str = "Norm.",
    ) -> None:
        compact = rect.w < 180
        toggle = self._toggle_switch_rect(rect, compact=compact)
        row_bg = self.theme.surface if disabled else self.theme.surface
        pygame.draw.rect(self.screen, row_bg, rect.pygame_rect, border_radius=10)

        text_color = self.theme.muted if disabled else self.theme.text
        label_font = self.font_sm if compact else self.font_md
        toggle_w = toggle.w
        label_max_w = max(1, rect.w - toggle_w - 28)
        lines = wrap_text_lines(label_font, label, label_max_w, max_lines=2)
        block_h = text_block_height(label_font, len(lines), line_spacing=2)
        start_y = rect.y + max(0, (rect.h - block_h) // 2)
        blit_text_block(
            self.screen,
            label_font,
            lines,
            rect.x + 16 if not compact else rect.x + 12,
            start_y,
            text_color,
            line_spacing=2,
        )

        if disabled:
            track_on = self.theme.muted
            track_off = self.theme.surface_alt
            knob_color = self.theme.surface
            border_color = self.theme.muted
        elif enabled and has_gain:
            track_on = self.theme.accent
            track_off = self.theme.surface_alt
            knob_color = self.theme.bg
            border_color = None
        elif enabled:
            track_on = self.theme.muted
            track_off = self.theme.surface_alt
            knob_color = self.theme.bg
            border_color = None
        else:
            track_on = self.theme.accent
            track_off = self.theme.surface_alt
            knob_color = self.theme.text
            border_color = self.theme.muted
        draw_toggle_switch(
            self.screen,
            toggle,
            on=enabled,
            track_on=track_on,
            track_off=track_off,
            knob_color=knob_color,
            border_color=border_color,
        )
    def _favorites_scope_patches(self) -> list[dict]:
        """Quick Select patches (root + nested subfolders) for cal scope hints."""
        label = favorites_display_name()
        with self._scan_lock:
            return list(self.scanner.patches.get(label, []))

    def _calibration_scope_stats(self, mode: CalibrateMode) -> tuple[int, int]:
        """Return (target_count, total_in_scope) for confirm modal duration hints."""
        fav_patches = self._favorites_scope_patches()
        store = self.loader.normalization
        total = len(
            {
                store._storage_key(
                    p["name"],
                    patch_path=p.get("path"),
                    stable_key=p.get("stable_key"),
                )
                for p in fav_patches
            }
        )
        if mode == CalibrateMode.FORCE_FULL:
            return total, total
        missing = store.list_missing(fav_patches)
        return len(missing), total
    def _calibration_duration_hint(self, targets: int) -> str:
        return format_calibration_duration_hint(targets)
    def _calibration_mode_label(self, mode: CalibrateMode) -> str:
        if mode == CalibrateMode.FORCE_FULL:
            return "Force full normalization"
        return "Normalize missing only"
    def _calibration_mode_description(self, mode: CalibrateMode, targets: int, total: int) -> str:
        if mode == CalibrateMode.FORCE_FULL:
            return (
                f"Re-measure loudness for all {total} Quick Select patches "
                "(including subfolders; overwrites existing gain_db entries)."
            )
        if targets == 0:
            return "Every Quick Select patch already has a gain_db entry."
        return (
            f"Calibrate {targets} Quick Select patch(es) missing gain_db entries "
            f"({total - targets} already done)."
        )
    def _launch_calibration_loader(self) -> None:
        argv = [sys.executable, "-u", str(CALIBRATION_LOADER_SCRIPT), "--favorites-only"]
        if self._pending_calibrate_mode == CalibrateMode.FORCE_FULL:
            argv.append("--force")
        if self._evdev_bridge is not None:
            self._evdev_bridge.stop()
        draw_splash_frame(
            self.screen,
            mode=SplashMode.CAL_ENTER,
            theme=self.theme,
            progress=0.0,
        )
        pygame.display.flip()
        os.environ[MPE_CALIB_FROM_BROWSER] = MPE_CALIB_FROM_BROWSER_ACTIVE
        try:
            os.execv(sys.executable, argv)
        except OSError as exc:
            message = f"Failed to launch calibration loader: {exc}"
            print(message, file=sys.stderr)
            _write_calibration_execv_failure_report(
                message,
                script=CALIBRATION_LOADER_SCRIPT,
            )
            sys.exit(1)
