"""Load Surge patches and volume via OSC (no GPIO / OLED dependencies)."""

from __future__ import annotations

import re
import socket
import struct
import time
from pathlib import Path

from patch_browser.patch_hold import (
    PatchHoldStore,
    effective_aeg_value,
    iter_hold_osc_paths,
)
from patch_browser.patch_pressure import PatchPressureStore
from patch_browser.patch_normalization import (
    MAX_AMP_VOLUME_LINEAR,
    NORM_MAX_AMP_VOLUME_LINEAR,
    PatchNormalizationStore,
    db_to_linear,
    volume_fader_to_amp_linear,
)
from patch_browser.surge_playback import (
    effective_poly_after_load,
    ensure_reuse_single_patch,
    poly_ceiling,
    query_polylimit,
    reuse_single_enabled,
    send_polylimit,
    write_poly_state,
)
from patch_browser.touch_ui_constants import VOLUME_MAX, VOLUME_MIN

OSC_OUT_PORT = 53270
OSC_QUERY_TIMEOUT_S = 0.08
PATCH_LOAD_SETTLE_S = 0.06


class PatchLoader:
    """Loads patches into Surge XT CLI via OSC."""

    def __init__(
        self,
        osc_host="127.0.0.1",
        osc_port=53280,
        normalization_store=None,
        hold_store=None,
        pressure_store=None,
        osc_out_port: int = OSC_OUT_PORT,
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
        self.hold = hold_store or PatchHoldStore()
        self.pressure = pressure_store or PatchPressureStore()
        self.osc_host = osc_host
        self.osc_out_port = osc_out_port
        self.user_volume_trim = 1.0
        self._patch_gain_linear = 1.0
        self._norm_active = False
        self._loaded_patch_name: str | None = None
        self._native_poly_limit: int | None = None
        self._effective_poly_limit: int | None = None

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

        cap = self._volume_cap()
        combined = volume_fader_to_amp_linear(
            self.user_volume_trim,
            patch_gain_linear=self._patch_gain_linear,
            cap=cap,
            fader_min=VOLUME_MIN,
            fader_max=VOLUME_MAX,
            norm_active=self._norm_active,
        )
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
        gain_db = store.get_effective_gain_db(patch_name)
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

    @staticmethod
    def _parse_surge_param_query(data: bytes) -> float | None:
        """Parse Surge /q/param/... replies (often `fs`: float + display string like '9.64 %')."""
        if len(data) < 8 or data[0] != 0x2F:
            return None
        try:
            from pythonosc.osc_message import OscMessage

            for param in OscMessage(data).params:
                if isinstance(param, str):
                    match = re.search(r"([\d.]+)\s*%", param)
                    if match:
                        return max(0.0, min(1.0, float(match.group(1)) / 100.0))
                    try:
                        value = float(param.strip())
                    except ValueError:
                        continue
                    if value <= 1.0:
                        return max(0.0, min(1.0, value))
                    return max(0.0, min(1.0, value / 100.0))
                if isinstance(param, (int, float)):
                    value = float(param)
                    if 0.0 <= value <= 1.0:
                        return value
        except Exception:
            pass

        match = re.search(rb"([\d.]+)\s*%", data)
        if match:
            return max(0.0, min(1.0, float(match.group(1).decode()) / 100.0))

        idx = data.find(b"\x00,\x00")
        while idx != -1:
            start = idx + 4
            if start + 4 <= len(data):
                try:
                    value = struct.unpack(">f", data[start : start + 4])[0]
                except struct.error:
                    return None
                if 0.0 <= value <= 1.0:
                    return value
            idx = data.find(b"\x00,\x00", idx + 1)
        return None

    def _query_osc_float(self, osc_path: str) -> float | None:
        """Query a Surge parameter via /q/ prefix (requires --osc-out-port)."""
        if not self.osc_enabled or self.osc_client is None:
            return None

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.bind((self.osc_host, self.osc_out_port))
            sock.settimeout(OSC_QUERY_TIMEOUT_S)
            query_paths = (f"/q{osc_path}", f"/q{osc_path.rstrip('/')}")
            for query in query_paths:
                self.osc_client.send_message(query, [])
                try:
                    data, _addr = sock.recvfrom(4096)
                except socket.timeout:
                    continue
                value = self._parse_surge_param_query(data)
                if value is not None:
                    return max(0.0, min(1.0, float(value)))
        except OSError:
            return None
        finally:
            sock.close()
        return None

    def _capture_hold_baseline(self, patch_name: str) -> bool:
        """Read AEG sustain/decay/release from Surge after patch load."""
        baseline: dict[str, dict[str, float]] = {"a": {}, "b": {}}
        captured = False
        for scene, stage, osc_path in iter_hold_osc_paths():
            value = self._query_osc_float(osc_path)
            if value is None:
                continue
            baseline[scene][stage] = value
            captured = True
        if not captured:
            return False
        for scene, stage, _osc_path in iter_hold_osc_paths():
            if stage not in baseline[scene]:
                stored = self.hold.get_baseline(patch_name)
                if stored and stage in stored.get(scene, {}):
                    baseline[scene][stage] = stored[scene][stage]
                else:
                    baseline[scene][stage] = 0.0
        self.hold.set_baseline(patch_name, baseline)
        return True

    def _send_hold_osc(self, patch_name: str) -> bool:
        baseline = self.hold.get_baseline(patch_name)
        if not baseline:
            return False
        mult = self.hold.get_effective_hold_mult(patch_name)
        try:
            for scene, stage, osc_path in iter_hold_osc_paths():
                base_val = baseline[scene][stage]
                effective = effective_aeg_value(base_val, mult)
                self.osc_client.send_message(osc_path, effective)
            return True
        except Exception as e:
            print(f"Error applying Hold via OSC: {e}")
            return False

    def refresh_hold(self, patch_name: str) -> bool:
        """Re-apply Hold multiplier to the current patch baseline."""
        if not self.osc_enabled:
            return False
        return self._send_hold_osc(patch_name)

    def _apply_playback_policy(self, patch_name: str) -> None:
        """Reuse Single (patch XML) + Pi poly ceiling via Surge OSC."""
        native = query_polylimit(
            self.osc_client,
            osc_host=self.osc_host,
            osc_out_port=self.osc_out_port,
        )
        ceiling = poly_ceiling()
        effective = effective_poly_after_load(native, ceiling=ceiling)
        self._native_poly_limit = native
        self._effective_poly_limit = effective
        send_polylimit(self.osc_client, effective)
        write_poly_state(
            patch_name=patch_name,
            native_poly=native if native is not None else effective,
            ceiling_poly=ceiling,
            effective_poly=effective,
            reuse_single=reuse_single_enabled(),
        )

    def load_patch(self, patch_path, *, apply_normalization: bool = True):
        if not self.osc_enabled:
            print(f"OSC disabled, cannot load: {patch_path}")
            return False

        try:
            load_path = ensure_reuse_single_patch(Path(patch_path))
            path_no_ext = str(load_path)
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
            self._loaded_patch_name = patch_name
            time.sleep(PATCH_LOAD_SETTLE_S)
            self._apply_playback_policy(patch_name)
            if not self._capture_hold_baseline(patch_name):
                print(f"Hold baseline query failed for {patch_name}; using stored values if any")
            self._send_hold_osc(patch_name)
            return True
        except Exception as e:
            print(f"Error loading patch via OSC: {e}")
            return False
