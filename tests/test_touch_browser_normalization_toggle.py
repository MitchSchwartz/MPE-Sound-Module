"""Tests for per-patch normalization toggle applying via patch reload."""

from __future__ import annotations

import sys
import types
import unittest
from unittest import mock

if "pygame" not in sys.modules:
    sys.modules["pygame"] = mock.MagicMock()

from patch_browser.touch_browser_normalization import TouchBrowserNormalizationMixin
from patch_browser.touch_browser_patches import TouchBrowserPatchesMixin


class _NormHost(TouchBrowserPatchesMixin, TouchBrowserNormalizationMixin):
    def __init__(self) -> None:
        self.loader = mock.Mock()
        self.loader.osc_enabled = True
        self.loader.normalization = mock.Mock()
        self.loader.normalization.is_globally_enabled.return_value = True
        self.loader.normalization.is_enabled.return_value = False
        self.loader.normalization.patch_key.side_effect = lambda name: name
        self.loader.normalization.get_raw_gain_db.return_value = 6.0
        self.surge_monitor = mock.Mock()
        self.volume_level = 1.0
        self.detail_patch = {
            "name": "Acid",
            "category": "Bass",
            "path": "/patches/Bass/Acid.fxp",
        }
        self.loaded_patch_info = dict(self.detail_patch)
        self.toast_message = ""

    def _toast(self, message: str, seconds: float = 2.0) -> None:
        self.toast_message = message

    def _apply_volume(self, level: float, *, persist: bool = True) -> None:
        self.volume_level = level

    def _note_surge_patch_load_success(self) -> None:
        return None

    def _surge_ready_for_patch_load(self) -> bool:
        return True


class NormToggleReloadTests(unittest.TestCase):
    def test_per_patch_toggle_reloads_loaded_patch(self) -> None:
        host = _NormHost()
        host.loader.load_patch.return_value = True

        host._toggle_normalization()

        host.loader.normalization.set_enabled.assert_called_once_with("Acid", True)
        host.loader.load_patch.assert_called_once_with("/patches/Bass/Acid.fxp")
        host.loader.refresh_patch_volume.assert_not_called()

    def test_per_patch_toggle_falls_back_to_volume_refresh_when_reload_fails(self) -> None:
        host = _NormHost()
        host.loader.load_patch.return_value = False

        host._toggle_normalization()

        host.loader.refresh_patch_volume.assert_called_once_with("Acid")


if __name__ == "__main__":
    unittest.main()
