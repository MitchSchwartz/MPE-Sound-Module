"""Load Surge patches and volume via OSC (no GPIO / OLED dependencies)."""

from __future__ import annotations

from pathlib import Path

from patch_browser.patch_normalization import (
    MAX_AMP_VOLUME_LINEAR,
    NORM_MAX_AMP_VOLUME_LINEAR,
    PatchNormalizationStore,
    db_to_linear,
)


class PatchLoader:
    """Loads patches into Surge XT CLI via OSC."""

    def __init__(
        self,
        osc_host="127.0.0.1",
        osc_port=53280,
        normalization_store=None,
    ):
        try:
            from pythonosc import udp_client

            self.osc_client = udp_client.SimpleUDPClient(osc_host, osc_port)
            self.osc_enabled = True
            print(f"OSC client initialized: {osc_host}:{osc_port}")
        except ImportError:
            print("Warning: python-osc not installed, patch loading disabled")
            self.osc_enabled = False
        except Exception as e:
            print(f"Warning: OSC client failed to initialize: {e}")
            self.osc_enabled = False

        self.normalization = normalization_store or PatchNormalizationStore()
        self.user_volume_trim = 1.0
        self._patch_gain_linear = 1.0
        self._norm_active = False

    def set_volume(self, volume=1.0):
        """Set user volume trim (stacks on per-patch normalization baseline)."""
        self.user_volume_trim = float(volume)
        return self._send_combined_volume()

    def _volume_cap(self) -> float:
        if self._norm_active:
            return NORM_MAX_AMP_VOLUME_LINEAR
        return MAX_AMP_VOLUME_LINEAR

    def _send_combined_volume(self):
        if not self.osc_enabled:
            return False

        combined = self.user_volume_trim * self._patch_gain_linear
        cap = self._volume_cap()
        if combined > cap:
            combined = cap
        try:
            self.osc_client.send_message("/param/a/amp/volume", combined)
            self.osc_client.send_message("/param/b/amp/volume", combined)
            return True
        except Exception as e:
            print(f"Error setting volume via OSC: {e}")
            return False

    def _apply_patch_normalization(self, patch_name: str) -> None:
        store = self.normalization
        if not store.is_effectively_enabled(patch_name):
            self._patch_gain_linear = 1.0
            self._norm_active = False
            return

        self._norm_active = True
        gain_db = store.get_raw_gain_db(patch_name)
        if gain_db is not None:
            self._patch_gain_linear = db_to_linear(gain_db)
        else:
            self._patch_gain_linear = 1.0

    def refresh_patch_volume(self, patch_name: str) -> bool:
        """Re-apply normalization baseline + user trim (e.g. after toggling enabled)."""
        self._apply_patch_normalization(patch_name)
        if not self._send_combined_volume():
            return False
        # Headless Surge sometimes ignores the first amp/volume OSC after re-enabling norm.
        if self._norm_active:
            return self._send_combined_volume()
        return True

    def load_patch(self, patch_path, *, apply_normalization: bool = True):
        if not self.osc_enabled:
            print(f"OSC disabled, cannot load: {patch_path}")
            return False

        try:
            path_no_ext = str(patch_path)
            if path_no_ext.endswith(".fxp") or path_no_ext.endswith(".FXP"):
                path_no_ext = path_no_ext[:-4]

            self.osc_client.send_message("/patch/load", [path_no_ext])
            patch_name = Path(patch_path).stem
            print(f"Loaded patch: {Path(patch_path).name}")

            if apply_normalization:
                self._apply_patch_normalization(patch_name)
            else:
                self._patch_gain_linear = 1.0
                self._norm_active = False
            self._send_combined_volume()
            return True
        except Exception as e:
            print(f"Error loading patch via OSC: {e}")
            return False
