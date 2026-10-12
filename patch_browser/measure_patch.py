"""Patch loading for measurement harnesses, at the level the touch UI would set.

A harness that builds its own PatchLoader starts at unity with no headroom.
In device mode the UI holds Surge at DEVICE_MODE_SURGE_TRIM_DB, so a bare
`loader.load_patch` was a hidden +6 dB (2026-10-12). This module decides the
mode with the same read-only resolver `DeviceVolume.rebind` uses and sets the
level with the same `apply_surge_volume` the UI's Vol fader uses, before the
load, so no message ever carries the unity level.
"""

from __future__ import annotations

from pathlib import Path

from patch_browser.device_volume import ASOUND_ROOT, apply_surge_volume, resolve_binding


def device_mode_active(jack_state: dict[str, str] | None = None, *, runner=None,
                       asound_root: Path = ASOUND_ROOT) -> bool:
    if jack_state is None:
        from patch_browser.audio_engine import read_jack_state

        jack_state = read_jack_state()
    return resolve_binding(jack_state, runner=runner, asound_root=asound_root)[2] is not None


def load_patch_like_ui(loader, patch_path, *, device_active: bool | None = None,
                       level: float | None = None) -> bool:
    """Set Surge's level as the UI would, then load. False on any failure."""
    if device_active is None:
        device_active = device_mode_active()
    if level is None:
        from patch_browser.ui_prefs import load_volume_level

        level = load_volume_level()
    if not loader.osc_enabled:
        return False
    if apply_surge_volume(loader, device_active=device_active, level=level) is False:
        return False
    return bool(loader.load_patch(Path(patch_path)))
