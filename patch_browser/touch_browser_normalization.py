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
from patch_browser.touch_ui_constants import NORM_CHECKBOX_SIZE
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

    def _normalization_enabled_for_detail(self) -> bool:
        if not self.detail_patch:
            return True
        return self.loader.normalization.is_enabled(self.detail_patch["name"])
    def _normalization_has_gain(self) -> bool:
        if not self.detail_patch:
            return False
        entry = self.loader.normalization.get_entry(self.detail_patch["name"])
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
        return store.is_enabled(self.detail_patch["name"])

    def _norm_gain_db_for_detail(self) -> float:
        if not self.detail_patch:
            return 0.0
        store = self.loader.normalization
        name = self.detail_patch["name"]
        effective = store.get_effective_gain_db(name)
        if effective is not None:
            return max(NORM_GAIN_DB_MIN, min(NORM_GAIN_DB_MAX, effective))
        default = store.get_slider_default_gain_db(name)
        return max(NORM_GAIN_DB_MIN, min(NORM_GAIN_DB_MAX, default))

    def _apply_norm_gain_db(self, gain_db: float, *, persist: bool = True) -> None:
        if not self.detail_patch:
            return
        name = self.detail_patch["name"]
        store = self.loader.normalization
        default = store.get_slider_default_gain_db(name)
        clamped = max(NORM_GAIN_DB_MIN, min(NORM_GAIN_DB_MAX, float(gain_db)))
        if abs(clamped - default) < 0.05:
            store.clear_user_gain_db(name, persist=persist)
        else:
            store.set_user_gain_db(name, clamped, persist=persist)
        loaded = self.loaded_patch_info
        if (
            loaded
            and self.loader.osc_enabled
            and store.patch_key(loaded["name"]) == store.patch_key(name)
        ):
            self.loader.refresh_patch_volume(name)

    def _reset_norm_gain_to_calibrated(self) -> None:
        if not self.detail_patch:
            return
        name = self.detail_patch["name"]
        store = self.loader.normalization
        store.clear_user_gain_db(name)
        default = store.get_slider_default_gain_db(name)
        loaded = self.loaded_patch_info
        if (
            loaded
            and self.loader.osc_enabled
            and store.patch_key(loaded["name"]) == store.patch_key(name)
        ):
            self.loader.refresh_patch_volume(name)
        if store.get_calibrated_gain_db(name) is not None:
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
        new_state = not store.is_enabled(name)
        store.set_enabled(name, new_state)
        self._layout()
        loaded = self.loaded_patch_info
        if (
            loaded
            and self.loader.osc_enabled
            and store.patch_key(loaded["name"]) == store.patch_key(name)
        ):
            self.loader.refresh_patch_volume(loaded["name"])
        if new_state:
            if store.get_raw_gain_db(name) is not None:
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
    def _normalize_checkbox_rect(self, row: Rect) -> Rect:
        pad = (row.h - NORM_CHECKBOX_SIZE) // 2
        return Rect(
            row.right - pad - NORM_CHECKBOX_SIZE,
            row.y + pad,
            NORM_CHECKBOX_SIZE,
            NORM_CHECKBOX_SIZE,
        )
    def _draw_normalize_toggle(
        self,
        rect: Rect,
        enabled: bool,
        *,
        has_gain: bool,
        disabled: bool = False,
        label: str = "Norm.",
    ) -> None:
        row_bg = self.theme.surface if disabled else self.theme.surface_alt
        pygame.draw.rect(self.screen, row_bg, rect.pygame_rect, border_radius=8)

        text_color = self.theme.muted if disabled else self.theme.text
        label_max_w = max(1, rect.w - NORM_CHECKBOX_SIZE - 28)
        lines = wrap_text_lines(self.font_sm, label, label_max_w, max_lines=2)
        block_h = text_block_height(self.font_sm, len(lines), line_spacing=2)
        start_y = rect.y + max(0, (rect.h - block_h) // 2)
        blit_text_block(
            self.screen,
            self.font_sm,
            lines,
            rect.x + 12,
            start_y,
            text_color,
            line_spacing=2,
        )

        box = self._normalize_checkbox_rect(rect)
        if disabled:
            box_bg = self.theme.surface
            border_color = self.theme.muted
            check_color = self.theme.muted if enabled else None
        elif enabled and has_gain:
            box_bg = self.theme.accent
            border_color = self.theme.accent
            check_color = self.theme.bg
        elif enabled:
            box_bg = self.theme.surface
            border_color = self.theme.muted
            check_color = self.theme.muted
        else:
            box_bg = self.theme.surface
            border_color = self.theme.muted
            check_color = None
        pygame.draw.rect(self.screen, box_bg, box.pygame_rect, border_radius=5)
        pygame.draw.rect(self.screen, border_color, box.pygame_rect, width=2, border_radius=5)
        if enabled and check_color is not None:
            check = self.font_sm.render("✓", True, check_color)
            cx = box.x + (box.w - check.get_width()) // 2
            cy = box.y + (box.h - check.get_height()) // 2 - 1
            self.screen.blit(check, (cx, cy))
    def _calibration_scope_stats(self, mode: CalibrateMode) -> tuple[int, int]:
        """Return (target_count, total_in_scope) for confirm modal duration hints."""
        with self._scan_lock:
            names: list[str] = []
            seen: set[str] = set()
            for patches in self.scanner.patches.values():
                for patch in patches:
                    stem = Path(patch["path"]).stem
                    if stem not in seen:
                        seen.add(stem)
                        names.append(stem)
        store = self.loader.normalization
        total = len(names)
        if mode == CalibrateMode.FORCE_FULL:
            return total, total
        missing = store.list_missing(names)
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
                f"Re-measure loudness for all {total} patches in the library "
                "(overwrites existing gain_db entries)."
            )
        if targets == 0:
            return "Every scanned patch already has a gain_db entry."
        return (
            f"Calibrate {targets} patch(es) missing gain_db entries "
            f"({total - targets} already done)."
        )
    def _launch_calibration_loader(self) -> None:
        argv = [sys.executable, "-u", str(CALIBRATION_LOADER_SCRIPT)]
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
