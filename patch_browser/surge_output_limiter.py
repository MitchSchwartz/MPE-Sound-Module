"""Global Surge output limiter — Conditioner on a dedicated global FX slot via OSC."""

from __future__ import annotations

import os
import time

from patch_browser.ui_prefs import load_ui_preference

DEFAULT_LIMITER_THRESHOLD_DB = -1.0
DEFAULT_LIMITER_FX_SLOT = 4
CONDITIONER_TYPE = "Conditioner"

# Conditioner OSC param order matches init_ctrltypes in Surge (param1..param9).
PARAM_BASS = 1
PARAM_TREBLE = 2
PARAM_WIDTH = 3
PARAM_HPWIDTH = 4
PARAM_BALANCE = 5
PARAM_THRESHOLD = 6
PARAM_ATTACK = 7
PARAM_RELEASE = 8
PARAM_GAIN = 9

# Fast limiter: negative attack/release = faster in Surge Conditioner UI.
LIMITER_ATTACK = -100.0
LIMITER_RELEASE = -20.0


def limiter_threshold_db() -> float:
    raw = os.environ.get("MPE_LIMITER_THRESHOLD_DB", str(DEFAULT_LIMITER_THRESHOLD_DB)).strip()
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_LIMITER_THRESHOLD_DB
    return max(-48.0, min(0.0, value))


def normalize_limiter_threshold_db(value: float) -> float:
    """Surge Conditioner threshold OSC uses dB in [-48, 0] (e.g. -1 = 1 dB headroom)."""
    return max(-48.0, min(0.0, float(value)))


def limiter_fx_slot() -> int:
    raw = os.environ.get("MPE_LIMITER_FX_SLOT", str(DEFAULT_LIMITER_FX_SLOT)).strip()
    try:
        slot = int(raw)
    except ValueError:
        return DEFAULT_LIMITER_FX_SLOT
    return max(1, min(4, slot))


def limiter_enabled_by_env() -> bool:
    return os.environ.get("MPE_OUTPUT_LIMITER", "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def limiter_enabled_by_pref() -> bool:
    return load_ui_preference("output_limiter_enabled", default=False)


def limiter_active() -> bool:
    return limiter_enabled_by_env() and limiter_enabled_by_pref()


def _fx_path(slot: int, suffix: str) -> str:
    return f"/param/fx/global/{slot}/{suffix}"


def _send_param(osc_client, slot: int, param_index: int, value: float) -> None:
    osc_client.send_message(_fx_path(slot, f"param{param_index}"), float(value))


def apply_output_limiter(osc_client, *, threshold_db: float | None = None) -> bool:
    """Enable Surge global Conditioner limiter (in-process — no extra audio hop)."""
    if osc_client is None:
        return False
    slot = limiter_fx_slot()
    threshold = (
        limiter_threshold_db()
        if threshold_db is None
        else normalize_limiter_threshold_db(threshold_db)
    )
    try:
        osc_client.send_message(_fx_path(slot, "type"), CONDITIONER_TYPE)
        time.sleep(0.05)
        _send_param(osc_client, slot, PARAM_BASS, 0.0)
        _send_param(osc_client, slot, PARAM_TREBLE, 0.0)
        _send_param(osc_client, slot, PARAM_WIDTH, 0.0)
        _send_param(osc_client, slot, PARAM_HPWIDTH, -60.0)
        _send_param(osc_client, slot, PARAM_BALANCE, 0.0)
        _send_param(osc_client, slot, PARAM_THRESHOLD, threshold)
        _send_param(osc_client, slot, PARAM_ATTACK, LIMITER_ATTACK)
        _send_param(osc_client, slot, PARAM_RELEASE, LIMITER_RELEASE)
        _send_param(osc_client, slot, PARAM_GAIN, 0.0)
        osc_client.send_message(_fx_path(slot, "deactivate"), 0.0)
        return True
    except Exception as exc:
        print(f"Error applying output limiter via OSC: {exc}")
        return False


def disable_output_limiter(osc_client) -> bool:
    """Bypass the appliance limiter slot without changing its patch FX type."""
    if osc_client is None:
        return False
    slot = limiter_fx_slot()
    try:
        osc_client.send_message(_fx_path(slot, "deactivate"), 1.0)
        return True
    except Exception as exc:
        print(f"Error disabling output limiter via OSC: {exc}")
        return False


def sync_output_limiter(osc_client) -> bool:
    """Apply or bypass limiter according to env + touch settings."""
    if limiter_active():
        return apply_output_limiter(osc_client)
    return disable_output_limiter(osc_client)
