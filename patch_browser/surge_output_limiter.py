"""Global Surge output limiter — Conditioner on a dedicated global FX slot via OSC."""

from __future__ import annotations

import os
import time

from patch_browser.ui_prefs import load_ui_preference

DEFAULT_LIMITER_THRESHOLD_DB = -1.0
DEFAULT_LIMITER_FX_SLOT = 4
CONDITIONER_TYPE = "Conditioner"

# Conditioner OSC param1..param9 map to cond_params enum index + 1 in Surge source.
PARAM_BASS = 1
PARAM_TREBLE = 2
PARAM_WIDTH = 3
PARAM_BALANCE = 4
PARAM_THRESHOLD = 5
PARAM_ATTACK = 6
PARAM_RELEASE = 7
PARAM_GAIN = 8
PARAM_HPWIDTH = 9

# Factory-style drive into the envelope limiter; output ceiling via PARAM_GAIN.
LIMITER_INPUT_THRESHOLD_DB = -6.0
LIMITER_WIDTH = 1.0
LIMITER_HPWIDTH_HZ = -60.0

# Fast limiter: negative attack/release on ct_percent_bipolar = faster envelope.
LIMITER_ATTACK = -1.0
LIMITER_RELEASE = -1.0
LIM_LABEL = "LIM"

# Surge fx_bypass enum — global FX (incl. our slot) only run when this is fxb_all_fx.
FX_BYPASS_ALL_FX = 0
FX_BYPASS_OSC = "/param/global/fx_bypass"

# Peak within this band of MPE_LIMITER_THRESHOLD_DB counts as "pinned at ceiling".
CEILING_MATCH_DB = 0.75
# Ignore noise floor — must be loud enough to be musically "at limit".
CEILING_MIN_SIGNAL_DB = -18.0


def _fx_enable_path(slot: int, param_index: int) -> str:
    """Surge extended params use a trailing '+' (e.g. param1/enable+)."""
    return _fx_path(slot, f"param{param_index}/enable+")


def limiter_threshold_db() -> float:
    raw = os.environ.get("MPE_LIMITER_THRESHOLD_DB", str(DEFAULT_LIMITER_THRESHOLD_DB)).strip()
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_LIMITER_THRESHOLD_DB
    return max(-48.0, min(0.0, value))


def normalize_limiter_threshold_db(value: float) -> float:
    """Output ceiling dB in [-48, 0] — applied as Conditioner Gain (param8) after limiting."""
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


def at_limiter_ceiling(peak_dbtp: float) -> bool:
    """True when measured output peak is at the configured limiter ceiling."""
    ceiling = limiter_threshold_db()
    if peak_dbtp < CEILING_MIN_SIGNAL_DB:
        return False
    return abs(float(peak_dbtp) - ceiling) <= CEILING_MATCH_DB


def limiter_header_badge_label() -> str | None:
    """Static label helper — prefer SurgeLimiterMonitor.snapshot() for live UI."""
    return LIM_LABEL if limiter_active() else None


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
        # Patches/presets often default to "No Send and Global FX" — global slot 4 never runs.
        osc_client.send_message(FX_BYPASS_OSC, float(FX_BYPASS_ALL_FX))
        osc_client.send_message(_fx_path(slot, "type"), CONDITIONER_TYPE)
        time.sleep(0.05)
        _send_param(osc_client, slot, PARAM_BASS, 0.0)
        _send_param(osc_client, slot, PARAM_TREBLE, 0.0)
        _send_param(osc_client, slot, PARAM_WIDTH, LIMITER_WIDTH)
        _send_param(osc_client, slot, PARAM_BALANCE, 0.0)
        _send_param(osc_client, slot, PARAM_THRESHOLD, LIMITER_INPUT_THRESHOLD_DB)
        _send_param(osc_client, slot, PARAM_ATTACK, LIMITER_ATTACK)
        _send_param(osc_client, slot, PARAM_RELEASE, LIMITER_RELEASE)
        _send_param(osc_client, slot, PARAM_GAIN, threshold)
        _send_param(osc_client, slot, PARAM_HPWIDTH, LIMITER_HPWIDTH_HZ)
        # Disable bass/treble EQ bands — limiter uses threshold/gain only.
        osc_client.send_message(_fx_enable_path(slot, PARAM_BASS), 0.0)
        osc_client.send_message(_fx_enable_path(slot, PARAM_TREBLE), 0.0)
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
