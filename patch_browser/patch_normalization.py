"""Per-patch volume normalization — static calibration store and helpers.

The one place the level of the trunk is decided (`stage_db`). Surge plays
every patch at its authored volume; normalization, the Norm trim, device
mode's headroom and the trim-mode Vol fader are one linear gain, applied by
mpe-fx-host before its effects (`native/mpe-fx-host/fx_dsp.h`, "Stage gain")
and sent by `PatchLoader`. Nothing sets Surge's `/param/*/amp/volume`: that
parameter is read as a cube law (dB = 60·log10 v) and clamped to 0..1, so
gains converted with 10^(dB/20) were cut three times too deep and boosts were
lost (2026-10-19). `tests/test_stage_gain_guard.py` refuses a second site.

When mpe-fx-host is not running, patches play as authored: the boosts and cuts
are missing, the trim-mode Vol fader has no effect, and the level never exceeds
what Surge itself produces (fx_host_protocol, "Without mpe-fx-host").
"""

from __future__ import annotations

import math
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from patch_browser.json_store import atomic_write_json, read_json_dict
from patch_browser.patch_sidecar_store import SidecarKeyMixin

# Integrated LUFS target for relative loudness matching across patches.
TARGET_LUFS = -18.0

# After applying gain, the gesture's true peak should land ~3 dB below clip (0 dBFS).
# Sound-engineer spec: normalize close to 0 with headroom, not open-ended LUFS boost.
SAFE_PEAK_DBTP = -3.0

# The calibration gesture may be trimmed up to this true peak and no further: the
# Norm trim's ceiling (`norm_ceiling_db`). No limiter follows the stage.
PEAK_CEILING_DBTP = 0.0

# Norm fader: trim offset from calibrated gain_db, symmetric so 0 (the calibrated
# baseline) sits at the fader's centre. Double-tap clears it.
NORM_TRIM_DB_MIN = -30.0
NORM_TRIM_DB_MAX = 30.0

# Entries measured for the stage: Surge alone at its authored volume, the gain applied
# arithmetically. An entry without it is uncalibrated: gain 0, trim 0, the patch plays
# as authored until it is re-measured (its numbers were taken through amp/volume's
# cube law and are in the wrong units).
CAL_PATH = "fx-stage-v1"

# A user trim written on or after the stage's deploy is plain dB on the stage
# (`set_user_trim_db` marks it). An uncalibrated row's trim without the mark was
# heard through amp/volume's cube law; it is bridged (`legacy_norm_db`) and
# re-expressed for the fader (`norm_fader_db`), never read as plain dB.
TRIM_LAW_KEY = "trim_law"
TRIM_LAW_STAGE = CAL_PATH

# Calibration captures whose sample peak reaches this are clipped: re-measured with
# CAPTURE_PAD_DB off Surge's master volume (`padded_master_volume`).
CLIP_SAMPLE_PEAK_DBFS = -0.1
CAPTURE_PAD_DB = 12.0

# A patch whose amp/volume (either scene) now reads differently from the
# calibration's was edited since: its entry is stale and plays no louder than
# authored (`stage_norm`). Surge reports the value as text with a few digits, so
# the tolerance is a display step, not float equality.
AUTHORED_VOLUME_TOLERANCE = 0.002

# An authored amp/volume below this is not believed: 0.1 is -60 dB through Surge's
# cube law, quieter than any patch anyone plays, and the reading most likely to be
# wrong is a near-zero one (a reply for another parameter, a display string). It
# reads as unknown, which errs quiet (`authored_level_db`).
MIN_PLAUSIBLE_AUTHORED_VOLUME = 0.1

# The most a pre-stage row's bridge (`legacy_norm_db`) may raise the stage. The
# bridge raises a patch only to undo a quiet authored volume, so it is
# 60·log10(1/authored): +30 dB covers any patch authored at 0.32 (-30 dB) or
# louder, the Norm trim's own range. A patch authored quieter than that plays
# quieter than it did, never louder, until it is re-measured; and a wrong
# authored read can never put more than +30 dB on the trunk through this path.
LEGACY_NORM_MAX_DB = 30.0

# Legacy aliases -- NormControl uses trim range; keep names for import stability.
NORM_GAIN_DB_MIN = NORM_TRIM_DB_MIN
NORM_GAIN_DB_MAX = NORM_TRIM_DB_MAX

# Closed-loop cal verify: post-gain peak must land at or below SAFE_PEAK + tolerance.
POST_GAIN_VERIFY_PEAK_MAX_DBTP = SAFE_PEAK_DBTP + 0.2

VOL_FADER_LAW_CONSOLE = "console"
VOL_FADER_LAW_LINEAR = "linear"


def post_gain_verify_passes(peak_dbtp: float) -> bool:
    """True when closed-loop post-gain peak is finite and within v2 save gate."""
    return math.isfinite(peak_dbtp) and peak_dbtp <= POST_GAIN_VERIFY_PEAK_MAX_DBTP



def default_normalization_path() -> Path:
    """Resolve normalization JSON path (env override, then user state file)."""
    env = os.environ.get("MPE_NORMALIZATION_FILE", "").strip()
    if env:
        return Path(env).expanduser()
    return Path.home() / ".patch_browser_normalization.json"


def repo_starter_path() -> Path:
    """Shipped starter file in the repo (empty `{}` until calibrated)."""
    return Path(__file__).resolve().parent.parent / "config" / "patch_normalization.json"


def db_to_linear(gain_db: float) -> float:
    """dB to a linear gain (1.0 = unity, -inf dB = 0)."""
    return 10.0 ** (gain_db / 20.0)


def linear_to_db(linear: float) -> float:
    if linear <= 0:
        return -120.0
    return 20.0 * math.log10(linear)


# Vol fader attenuation display/range: bottom = mute, top = 0 dB (unity vs patch baseline).
VOLUME_FADER_FLOOR_DB = -60.0

# IEC 60268-17 console fader law, as position(%) -> dB breakpoints. A fader that is
# linear in dB over a 60 dB span puts everything musically useful (0 to -12 dB) in the
# top 20% of travel and wastes the bottom half below -30 dB, which reads as a cliff on
# a short touchscreen column. The console law spends half the travel on the top 20 dB.
# Segments are (position_fraction, dB_at_that_position), ascending.
_IEC_FADER_POINTS: tuple[tuple[float, float], ...] = (
    (0.000, -70.0),
    (0.025, -60.0),
    (0.075, -50.0),
    (0.150, -40.0),
    (0.300, -30.0),
    (0.500, -20.0),
    (1.000, 0.0),
)
_IEC_FADER_BOTTOM_DB = _IEC_FADER_POINTS[0][1]


def _iec_fader_db(t: float) -> float:
    """Console-law attenuation in dB for fader travel *t* (0..1), bottoming at -70 dB."""
    if t <= 0.0:
        return _IEC_FADER_BOTTOM_DB
    if t >= 1.0:
        return 0.0
    for (t_lo, db_lo), (t_hi, db_hi) in zip(_IEC_FADER_POINTS, _IEC_FADER_POINTS[1:]):
        if t <= t_hi:
            span = t_hi - t_lo
            if span <= 0:
                return db_hi
            return db_lo + (db_hi - db_lo) * ((t - t_lo) / span)
    return 0.0


def _volume_fader_t(
    trim: float,
    *,
    fader_min: float,
    fader_max: float,
) -> float:
    span = fader_max - fader_min
    if span <= 0:
        return 1.0
    return max(0.0, min(1.0, (trim - fader_min) / span))


def volume_fader_law() -> str:
    """Vol fader taper — env ``MPE_VOL_FADER_LAW`` (``console`` default, or ``linear``)."""
    raw = os.environ.get("MPE_VOL_FADER_LAW", VOL_FADER_LAW_CONSOLE).strip().lower()
    if raw == VOL_FADER_LAW_LINEAR:
        return VOL_FADER_LAW_LINEAR
    return VOL_FADER_LAW_CONSOLE


def volume_fader_trim_to_db(
    trim: float,
    *,
    fader_min: float,
    fader_max: float,
    floor_db: float = VOLUME_FADER_FLOOR_DB,
    law: str | None = None,
) -> float | None:
    """Attenuation in dB relative to full patch level. None when fader is at mute.

    Console (IEC 60268-17) taper by default; ``linear`` spreads dB evenly over travel
    (``MPE_VOL_FADER_LAW=linear``).
    """
    if trim <= fader_min:
        return None
    t = _volume_fader_t(trim, fader_min=fader_min, fader_max=fader_max)
    if t <= 0.0:
        return None
    if (law or volume_fader_law()) == VOL_FADER_LAW_LINEAR:
        return floor_db * (1.0 - t)
    return _iec_fader_db(t) * (floor_db / _IEC_FADER_BOTTOM_DB)


def volume_fader_display_db(
    trim: float,
    *,
    fader_min: float,
    fader_max: float,
    floor_db: float = VOLUME_FADER_FLOOR_DB,
) -> str:
    """Touch Vol fader label: -∞ at bottom, 0 at top."""
    db = volume_fader_trim_to_db(
        trim, fader_min=fader_min, fader_max=fader_max, floor_db=floor_db
    )
    if db is None:
        return "-∞"
    if abs(db) < 0.05:
        return "0"
    return f"{db:.0f}"


def volume_fader_display_pct(
    trim: float,
    *,
    fader_min: float,
    fader_max: float,
) -> int:
    """Legacy 0–100 display — prefer volume_fader_display_db for the Vol fader."""
    span = fader_max - fader_min
    if span <= 0:
        return 100
    ratio = (trim - fader_min) / span
    return round(max(0.0, min(1.0, ratio)) * 100)


def volume_fader_db_to_trim(
    db: float | None,
    *,
    fader_min: float,
    fader_max: float,
    floor_db: float = VOLUME_FADER_FLOOR_DB,
    law: str | None = None,
) -> float:
    """The fader position whose `volume_fader_trim_to_db` is `db` (None: mute).

    Below the floor is mute: the fader has no position quieter than its floor
    other than the bottom.
    """
    if db is None or not math.isfinite(db) or db < floor_db:
        return fader_min
    if db >= 0.0:
        return fader_max
    if (law or volume_fader_law()) == VOL_FADER_LAW_LINEAR:
        t = 1.0 - db / floor_db
    else:
        target = db * (_IEC_FADER_BOTTOM_DB / floor_db)
        t = 1.0
        for (t_lo, db_lo), (t_hi, db_hi) in zip(_IEC_FADER_POINTS, _IEC_FADER_POINTS[1:]):
            if db_lo <= target <= db_hi:
                t = t_lo + (t_hi - t_lo) * ((target - db_lo) / (db_hi - db_lo))
                break
    return fader_min + t * (fader_max - fader_min)


def legacy_vol_fader_to_stage(trim: float, *, fader_min: float, fader_max: float) -> float:
    """A trim-mode Vol fader position saved before the stage, moved so it sounds the same.

    Until 2026-10-19 the fader's dB went to Surge's amp/volume through 10^(dB/20),
    which Surge reads as a cube law: a fader labelled -10 dB cut 30. The stage
    applies the label, so the saved position moves to the label that cuts what
    was heard. Anything below the floor goes to mute: never louder than before.
    """
    db = volume_fader_trim_to_db(trim, fader_min=fader_min, fader_max=fader_max)
    if db is None:
        return fader_min
    return volume_fader_db_to_trim(3.0 * db, fader_min=fader_min, fader_max=fader_max)


def is_calibrated(entry: dict[str, Any] | None) -> bool:
    """An entry measured for the stage (CAL_PATH) with a gain. Anything else plays as authored."""
    return (
        isinstance(entry, dict)
        and entry.get("cal_path") == CAL_PATH
        and isinstance(entry.get("gain_db"), (int, float))
        and math.isfinite(float(entry["gain_db"]))
    )


def norm_ceiling_db(entry: dict[str, Any] | None) -> float | None:
    """The most gain_db + user_trim_db may be: the gesture's peak at PEAK_CEILING_DBTP.

    None when the entry has no measured unity peak (no ceiling known). The
    peak is the louder of the two anchors.
    """
    if not is_calibrated(entry):
        return None
    peaks = entry.get("true_peak_unity_dbtp")
    if isinstance(peaks, dict):
        values = [float(v) for v in peaks.values() if isinstance(v, (int, float))]
    elif isinstance(peaks, (int, float)):
        values = [float(peaks)]
    else:
        values = []
    values = [v for v in values if math.isfinite(v)]
    if not values:
        return None
    return PEAK_CEILING_DBTP - max(values)


def stage_db(
    *,
    norm_db: float,
    ceiling_db: float | None,
    headroom_db: float,
    vol_db: float | None,
) -> float:
    """The stage gain, in dB: the one formula for the level of the trunk.

    `norm_db` is gain_db + user_trim_db (0 when normalization is off or the patch is
    uncalibrated), held to `ceiling_db`. `headroom_db` is device mode's fixed cut (0 in
    trim mode); `vol_db` is the trim-mode Vol fader (0 in device mode, None at mute).
    Headroom and Vol only attenuate, so the ceiling applies to the norm part alone.
    """
    if vol_db is None:
        return -math.inf
    norm = float(norm_db)
    if ceiling_db is not None:
        norm = min(norm, float(ceiling_db))
    return norm + min(0.0, float(headroom_db)) + min(0.0, float(vol_db))


def norm_at_ceiling(norm_db: float, ceiling_db: float | None) -> bool:
    """True when the ceiling is holding the norm part down (the fader has stopped acting)."""
    return ceiling_db is not None and float(norm_db) > float(ceiling_db) + 1e-9


# Surge's master volume (/param/global/volume) spans -48..0 dB, linear in its 0..1
# value, and is applied after the patch's effects, so a pad there moves the output
# by exactly the pad. The scene amp/volume sits before the effects: on a patch
# that saturates it moved the output less than the pad (Cloud Horn, 12 dB in,
# 7.8 dB out, 2026-10-22), and adding the full pad back read it 4 dB too loud.
MASTER_VOLUME_RANGE_DB = 48.0


def padded_master_volume(authored: float, pad_db: float) -> float | None:
    """Master volume `pad_db` below `authored`, or None when the range ends first."""
    value = float(authored) - float(pad_db) / MASTER_VOLUME_RANGE_DB
    return value if value >= 0.0 else None


def plausible_authored_volume(value: float | None) -> float | None:
    """An authored amp/volume as read from Surge, or None when it cannot be
    believed (not a finite 0..1 number, or under MIN_PLAUSIBLE_AUTHORED_VOLUME)."""
    if value is None or isinstance(value, bool):
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value) or not MIN_PLAUSIBLE_AUTHORED_VOLUME <= value <= 1.0:
        return None
    return value


def louder_authored(a: float | None, b: float | None = None) -> float | None:
    """The louder of the two scenes' authored volumes, for a gain worked out
    against the authored level (the louder errs quiet). Unknown when scene A is:
    B alone is not enough to know the patch."""
    a, b = plausible_authored_volume(a), plausible_authored_volume(b)
    if a is None:
        return None
    return a if b is None else max(a, b)


def legacy_level_db(gain_db_old: float, trim_db_old: float) -> float:
    """The level a pre-stage entry played at, in dB re amp/volume 1.0.

    It sent amp/volume = g·t (gain and trim as 10^(dB/20)); Surge clamps that to
    1.0 and reads it as 60·log10.
    """
    return 60.0 * math.log10(min(1.0, db_to_linear(float(gain_db_old) + float(trim_db_old))))


def authored_level_db(authored_volume: float | None) -> float:
    """60·log10 of the authored amp/volume. Unknown or implausible
    (`plausible_authored_volume`) reads as 1.0: 0 dB, the loudest it can be, so a
    gain worked out against it errs quiet."""
    authored_volume = plausible_authored_volume(authored_volume)
    if authored_volume is None:
        return 0.0
    return 60.0 * math.log10(authored_volume)


def migrate_trim_to_stage(
    *,
    gain_db_old: float,
    trim_db_old: float,
    gain_db_new: float,
    authored_volume: float | None,
) -> tuple[float, bool]:
    """A pre-stage Norm trim, re-chosen so the new stage gives the level heard before.

    `legacy_level_db` less the authored level is what the stage must apply; the new
    trim is that less gain_db_new. An unknown authored volume errs quiet
    (`authored_level_db`). Returns (trim, clamped).

    A cut is kept exactly, even past the Norm fader's bottom (the fader then
    reads "min"): clamping it to -30 would play the patch louder than the owner
    left it, by as much as the clamp took. Only a raise past the fader's top is
    clamped, which plays quieter; `clamped` reports that.
    """
    wanted = (
        legacy_level_db(gain_db_old, trim_db_old)
        - authored_level_db(authored_volume)
        - float(gain_db_new)
    )
    trim = min(wanted, NORM_TRIM_DB_MAX)
    return trim, trim < wanted


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def legacy_norm_db(
    entry: dict[str, Any] | None,
    authored_volume: float | None,
    authored_volume_b: float | None = None,
) -> float | None:
    """For an entry from before the stage: the stage gain that plays it at the
    level it played at then. None when the entry carries neither a gain nor a trim.

    Bridges the deploy and the re-measurement (`CAL_PATH` entries replace these):
    until then a cut patch stays cut. A trim with no gain is the owner's cut on an
    uncalibrated patch: the old code took the missing gain as 0, and so does this.
    A trim marked stage-law (`trim_is_stage_law`) is plain dB on the row's base
    instead: its pre-stage gain bridged, or as authored with none.
    Worked out against the louder scene's authored volume, an unknown one as 0 dB
    (`louder_authored`, `authored_level_db`): both err quiet. Held to
    LEGACY_NORM_MAX_DB.
    """
    if not isinstance(entry, dict) or is_calibrated(entry):
        return None
    gain = _finite_number(entry.get("gain_db"))
    trim = _finite_number(entry.get("user_trim_db"))
    if gain is None and trim is None:
        return None
    authored = louder_authored(authored_volume, authored_volume_b)
    if trim_is_stage_law(entry):
        # Plain dB on the row's base: the fader moves the stage by what it says.
        return min(LEGACY_NORM_MAX_DB, _bridge_base_db(gain, authored) + (trim or 0.0))
    level = legacy_level_db(gain or 0.0, trim or 0.0)
    return min(LEGACY_NORM_MAX_DB, level - authored_level_db(authored))


def trim_is_stage_law(entry: dict[str, Any] | None) -> bool:
    """True when the row's user trim is plain dB on the stage: a calibrated row
    (its trim was migrated, `migrate_trim_to_stage`) or one marked by
    `set_user_trim_db`."""
    return isinstance(entry, dict) and (
        is_calibrated(entry) or entry.get(TRIM_LAW_KEY) == TRIM_LAW_STAGE
    )


def _bridge_base_db(gain: float | None, authored: float | None) -> float:
    """What an uncalibrated row plays at before its stage-law trim: its pre-stage
    gain bridged (`legacy_level_db` against the authored level, held to
    LEGACY_NORM_MAX_DB), or 0 (as authored) with no gain."""
    if gain is None:
        return 0.0
    return min(LEGACY_NORM_MAX_DB, legacy_level_db(gain, 0.0) - authored_level_db(authored))


def norm_fader_db(
    entry: dict[str, Any] | None,
    authored_volume: float | None = None,
    authored_volume_b: float | None = None,
) -> float:
    """Where the Norm fader sits for this row, in stage dB: a move of the fader by
    x dB moves the stage by x dB from what plays now.

    A stage-law trim is itself. An uncalibrated row's cube-law trim is re-expressed
    as the stage-law trim that plays what it plays now (`legacy_norm_db` less the
    row's base), so the first write does not jump. An unknown authored volume reads
    as 0 dB (`authored_level_db`), which puts the fader no higher than the level
    really is: a move from it lands quiet. Unclamped: below the fader's bottom it
    reads "min" (mixer_controls.NormControl).
    """
    if not isinstance(entry, dict):
        return 0.0
    trim = _finite_number(entry.get("user_trim_db"))
    if trim is None:
        return 0.0
    if trim_is_stage_law(entry):
        return trim
    playing = legacy_norm_db(entry, authored_volume, authored_volume_b)
    authored = louder_authored(authored_volume, authored_volume_b)
    base = _bridge_base_db(_finite_number(entry.get("gain_db")), authored)
    return (playing if playing is not None else 0.0) - base


def is_stale(
    entry: dict[str, Any] | None,
    authored_volume: float | None,
    authored_volume_b: float | None = None,
) -> bool:
    """A calibrated entry whose patch now has a different authored volume in either
    scene (edited since). A scene unknown now, or not recorded then, is not
    compared."""
    if not is_calibrated(entry):
        return False
    for key, now in (("authored_volume", authored_volume), ("authored_volume_b", authored_volume_b)):
        was = _finite_number(entry.get(key))
        now = plausible_authored_volume(now)
        if was is not None and now is not None and abs(was - now) > AUTHORED_VOLUME_TOLERANCE:
            return True
    return False


def compute_gain_db(
    lufs_integrated: float,
    true_peak_dbtp: float,
    *,
    target_lufs: float = TARGET_LUFS,
    safe_peak_dbtp: float = SAFE_PEAK_DBTP,
) -> float:
    """
    Compute static per-patch gain from measured gesture render.

    Uses integrated LUFS for relative matching across patches, but caps gain so
    the boosted peak lands at safe_peak_dbtp (~-3 dBFS headroom before clip).
    Attenuation-only patches use the LUFS delta; loud patches are limited by peak cap.
    """
    lufs_gain = target_lufs - lufs_integrated
    peak_gain = safe_peak_dbtp - true_peak_dbtp
    return min(lufs_gain, peak_gain)


def compute_gain_db_dual_anchor(
    strike_lufs: float,
    strike_peak_dbtp: float,
    sustain_lufs: float,
    sustain_peak_dbtp: float,
    *,
    target_lufs: float = TARGET_LUFS,
    safe_peak_dbtp: float = SAFE_PEAK_DBTP,
) -> float:
    """Gain from strike + sustain anchors — min so neither anchor clips (#31 Stage 2).

    max() was wrong when strike is already hot and sustain is quiet (e.g. A Robotic Mind):
    boosting for sustain clips the strike. min() is peak-safe for both with one gain_db.
    """
    strike_gain = compute_gain_db(
        strike_lufs, strike_peak_dbtp, target_lufs=target_lufs, safe_peak_dbtp=safe_peak_dbtp
    )
    sustain_gain = compute_gain_db(
        sustain_lufs, sustain_peak_dbtp, target_lufs=target_lufs, safe_peak_dbtp=safe_peak_dbtp
    )
    return min(strike_gain, sustain_gain)


def _merge_patch_entry(
    base: dict[str, Any] | None,
    overlay: dict[str, Any],
) -> dict[str, Any]:
    """Field-wise merge so user toggles do not drop calibration fields."""
    if not isinstance(base, dict):
        return dict(overlay)
    merged = dict(base)
    merged.update(overlay)
    return merged


def clamp_user_trim_db(trim_db: float) -> float:
    """Clamp Norm trim offset to the v2 fader range."""
    return max(NORM_TRIM_DB_MIN, min(NORM_TRIM_DB_MAX, float(trim_db)))


def _migrate_legacy_user_gain(entry: dict[str, Any]) -> dict[str, Any]:
    """v1 ``user_gain_db`` (absolute) -> v2 ``user_trim_db`` (offset from gain_db)."""
    if "user_gain_db" not in entry:
        return entry
    updated = dict(entry)
    if "user_trim_db" not in updated:
        user_gain = float(updated.pop("user_gain_db"))
        gain_db = updated.get("gain_db")
        if gain_db is not None:
            trim = clamp_user_trim_db(user_gain - float(gain_db))
        else:
            trim = clamp_user_trim_db(user_gain)
        updated["user_trim_db"] = round(trim, 3)
    else:
        updated.pop("user_gain_db", None)
    return updated


# Reserved key in user normalization JSON — master switch, not a patch stem.
_GLOBAL_SETTINGS_KEY = "_global"


class PatchNormalizationStore(SidecarKeyMixin):
    """Load/save patch_normalization.json keyed by stable_key (path-based) with stem fallback."""

    _reserved_keys = frozenset({"_global"})

    def __init__(self, path: Path | None = None):
        self.path = path or default_normalization_path()
        self._data: dict[str, dict[str, Any]] = {}
        self._global_enabled = True
        self.load()

    def load(self) -> None:
        """Load repo starter, then overlay user state so toggles keep calibration."""
        merged: dict[str, dict[str, Any]] = {}

        starter = repo_starter_path()
        if starter.exists():
            for key, entry in read_json_dict(starter, label="normalization file").items():
                if key == _GLOBAL_SETTINGS_KEY:
                    continue
                if isinstance(entry, dict):
                    merged[key] = dict(entry)

        if self.path.exists():
            for key, entry in read_json_dict(self.path, label="normalization file").items():
                if key == _GLOBAL_SETTINGS_KEY:
                    if isinstance(entry, dict) and "enabled" in entry:
                        self._global_enabled = bool(entry["enabled"])
                    continue
                if isinstance(entry, dict):
                    merged[key] = _merge_patch_entry(merged.get(key), entry)

        migrated = False
        for key in list(merged.keys()):
            new_entry = _migrate_legacy_user_gain(merged[key])
            if new_entry != merged[key]:
                merged[key] = new_entry
                migrated = True
        self._data = merged
        if migrated and self.path.exists():
            self.save()

    def save(self) -> None:
        """Persist store atomically so cancel/interrupt mid-write cannot truncate JSON."""
        payload: dict[str, Any] = dict(self._data)
        payload[_GLOBAL_SETTINGS_KEY] = {"enabled": self._global_enabled}
        atomic_write_json(self.path, payload)

    def get_entry(
        self,
        patch_name: str,
        *,
        patch_path: str | None = None,
        stable_key: str | None = None,
    ) -> dict[str, Any] | None:
        entry, _key = self._lookup(
            patch_name, patch_path=patch_path, stable_key=stable_key
        )
        return entry

    def is_globally_enabled(self) -> bool:
        """Master switch — when False, no patch normalization is applied."""
        return self._global_enabled

    def set_globally_enabled(self, enabled: bool) -> None:
        """Persist global normalization on/off without changing per-patch flags."""
        self._global_enabled = bool(enabled)
        self.save()

    def is_enabled(
        self,
        patch_name: str,
        *,
        patch_path: str | None = None,
        stable_key: str | None = None,
    ) -> bool:
        """Per-patch enabled flag (default True when no entry). Ignores global switch."""
        entry = self.get_entry(
            patch_name, patch_path=patch_path, stable_key=stable_key
        )
        if entry is None:
            return True
        if "enabled" not in entry:
            return True
        return bool(entry["enabled"])

    def is_effectively_enabled(
        self,
        patch_name: str,
        *,
        patch_path: str | None = None,
        stable_key: str | None = None,
    ) -> bool:
        """Whether normalization applies at runtime (global + per-patch)."""
        return self.is_globally_enabled() and self.is_enabled(
            patch_name, patch_path=patch_path, stable_key=stable_key
        )

    def _ensure_calibration_fields(self, key: str, entry: dict[str, Any]) -> dict[str, Any]:
        """Keep gain_db from repo starter when persisting enable toggles."""
        if entry.get("gain_db") is not None:
            return entry
        starter = repo_starter_path()
        if not starter.exists():
            return entry
        starter_entry = read_json_dict(starter, label="normalization file").get(key)
        if isinstance(starter_entry, dict):
            return _merge_patch_entry(starter_entry, entry)
        return entry

    def set_enabled(
        self,
        patch_name: str,
        enabled: bool,
        *,
        patch_path: str | None = None,
        stable_key: str | None = None,
    ) -> None:
        """Persist per-patch normalization on/off (issue #5 UI toggle)."""
        key = self._storage_key(
            patch_name, patch_path=patch_path, stable_key=stable_key
        )
        entry, matched = self._lookup(
            patch_name, patch_path=patch_path, stable_key=stable_key
        )
        if not isinstance(entry, dict):
            entry = {}
        entry = self._ensure_calibration_fields(matched or key, entry)
        entry["enabled"] = enabled
        if matched and matched != key:
            self._data.pop(matched, None)
        self._data[key] = entry
        self.save()

    def get_raw_gain_db(
        self,
        patch_name: str,
        *,
        patch_path: str | None = None,
        stable_key: str | None = None,
    ) -> float | None:
        """Calibration gain for a patch, ignoring the enabled flag."""
        return self.get_calibrated_gain_db(
            patch_name, patch_path=patch_path, stable_key=stable_key
        )

    def get_calibrated_gain_db(
        self,
        patch_name: str,
        *,
        patch_path: str | None = None,
        stable_key: str | None = None,
    ) -> float | None:
        """Stage-calibrated gain_db only; None for an uncalibrated entry."""
        entry = self.get_entry(
            patch_name, patch_path=patch_path, stable_key=stable_key
        )
        if not is_calibrated(entry):
            return None
        return float(entry["gain_db"])

    def get_user_trim_db(
        self,
        patch_name: str,
        *,
        patch_path: str | None = None,
        stable_key: str | None = None,
    ) -> float:
        entry = self.get_entry(
            patch_name, patch_path=patch_path, stable_key=stable_key
        )
        if not entry or "user_trim_db" not in entry:
            return 0.0
        return float(entry["user_trim_db"])

    def get_norm_fader_db(
        self,
        patch_name: str,
        *,
        patch_path: str | None = None,
        stable_key: str | None = None,
        authored_volume: float | None = None,
        authored_volume_b: float | None = None,
    ) -> float:
        """The Norm fader's position for this patch, in stage dB (`norm_fader_db`)."""
        entry = self.get_entry(
            patch_name, patch_path=patch_path, stable_key=stable_key
        )
        return norm_fader_db(entry, authored_volume, authored_volume_b)

    def get_effective_gain_db(
        self,
        patch_name: str,
        *,
        patch_path: str | None = None,
        stable_key: str | None = None,
    ) -> float | None:
        """Calibrated gain_db + user_trim_db, or None for an uncalibrated entry."""
        entry = self.get_entry(
            patch_name, patch_path=patch_path, stable_key=stable_key
        )
        if not is_calibrated(entry):
            return None
        trim = entry.get("user_trim_db")
        offset = float(trim) if trim is not None else 0.0
        return float(entry["gain_db"]) + offset

    def stage_norm(
        self,
        patch_name: str,
        *,
        patch_path: str | None = None,
        stable_key: str | None = None,
        authored_volume: float | None = None,
        authored_volume_b: float | None = None,
    ) -> tuple[float, float | None]:
        """(norm_db, ceiling_db) for `stage_db`, given the patch's amp/volume in each
        scene as Surge reports it after the load (None if it did not answer).

        (0, None) when normalization is off, globally or for this patch, or when the
        patch has no entry. A stale entry (`is_stale`: the patch was edited since it
        was measured) keeps its cut but loses its boost, min(norm, 0): the edit may
        have made it louder, and a boost measured on the old patch could then clip,
        while dropping the cut would play a patch the owner turned down at full
        level. An entry from before the stage plays at its old level
        (`legacy_norm_db`).
        """
        kw = {"patch_path": patch_path, "stable_key": stable_key}
        if not self.is_effectively_enabled(patch_name, **kw):
            return 0.0, None
        entry = self.get_entry(patch_name, **kw)
        if is_calibrated(entry):
            trim = entry.get("user_trim_db")
            norm = float(entry["gain_db"]) + (float(trim) if trim is not None else 0.0)
            if is_stale(entry, authored_volume, authored_volume_b):
                return min(norm, 0.0), None
            return norm, norm_ceiling_db(entry)
        legacy = legacy_norm_db(entry, authored_volume, authored_volume_b)
        if legacy is None:
            return 0.0, None
        return legacy, None

    def get_slider_default_gain_db(
        self,
        patch_name: str,
        *,
        patch_path: str | None = None,
        stable_key: str | None = None,
    ) -> float:
        """Norm fader double-tap reset — 0 dB trim (calibrated baseline)."""
        return 0.0

    def has_user_trim_override(
        self,
        patch_name: str,
        *,
        patch_path: str | None = None,
        stable_key: str | None = None,
    ) -> bool:
        entry = self.get_entry(
            patch_name, patch_path=patch_path, stable_key=stable_key
        )
        return bool(entry and "user_trim_db" in entry)

    def has_user_gain_override(
        self,
        patch_name: str,
        *,
        patch_path: str | None = None,
        stable_key: str | None = None,
    ) -> bool:
        return self.has_user_trim_override(
            patch_name, patch_path=patch_path, stable_key=stable_key
        )

    def set_user_trim_db(
        self,
        patch_name: str,
        trim_db: float,
        *,
        persist: bool = True,
        patch_path: str | None = None,
        stable_key: str | None = None,
    ) -> None:
        """Set the Norm trim, plain dB on the stage (defer persist=False while dragging).

        Marks the row stage-law (TRIM_LAW_KEY): the value is the fader's, which
        `norm_fader_db` already put in stage dB."""
        key = self._storage_key(
            patch_name, patch_path=patch_path, stable_key=stable_key
        )
        entry, matched = self._lookup(
            patch_name, patch_path=patch_path, stable_key=stable_key
        )
        if not isinstance(entry, dict):
            entry = {}
        entry = self._ensure_calibration_fields(matched or key, entry)
        entry["user_trim_db"] = round(clamp_user_trim_db(trim_db), 3)
        entry[TRIM_LAW_KEY] = TRIM_LAW_STAGE
        entry.pop("user_gain_db", None)
        if matched and matched != key:
            self._data.pop(matched, None)
        self._data[key] = entry
        if persist:
            self.save()

    def set_user_gain_db(
        self,
        patch_name: str,
        gain_db: float,
        *,
        persist: bool = True,
        patch_path: str | None = None,
        stable_key: str | None = None,
    ) -> None:
        """Legacy v1 API — absolute gain_db converted to trim offset when calibrated."""
        entry = self.get_entry(
            patch_name, patch_path=patch_path, stable_key=stable_key
        )
        calibrated = entry.get("gain_db") if entry else None
        if calibrated is not None:
            trim = float(gain_db) - float(calibrated)
        else:
            trim = float(gain_db)
        self.set_user_trim_db(
            patch_name,
            trim,
            persist=persist,
            patch_path=patch_path,
            stable_key=stable_key,
        )

    def clear_user_trim_db(
        self,
        patch_name: str,
        *,
        persist: bool = True,
        patch_path: str | None = None,
        stable_key: str | None = None,
    ) -> None:
        """Remove trim override; runtime reverts to calibrated gain_db only."""
        entry, key = self._lookup(
            patch_name, patch_path=patch_path, stable_key=stable_key
        )
        if not entry or not key:
            return
        if "user_trim_db" not in entry and "user_gain_db" not in entry:
            return
        updated = dict(entry)
        updated.pop("user_trim_db", None)
        updated.pop("user_gain_db", None)
        updated.pop(TRIM_LAW_KEY, None)
        storage = self._storage_key(
            patch_name, patch_path=patch_path, stable_key=stable_key
        )
        if key != storage:
            self._data.pop(key, None)
        self._data[storage] = updated
        if persist:
            self.save()

    def clear_user_gain_db(
        self,
        patch_name: str,
        *,
        persist: bool = True,
        patch_path: str | None = None,
        stable_key: str | None = None,
    ) -> None:
        self.clear_user_trim_db(
            patch_name,
            persist=persist,
            patch_path=patch_path,
            stable_key=stable_key,
        )

    def get_gain_db(
        self,
        patch_name: str,
        *,
        patch_path: str | None = None,
        stable_key: str | None = None,
    ) -> float | None:
        if not self.is_effectively_enabled(
            patch_name, patch_path=patch_path, stable_key=stable_key
        ):
            return None
        return self.get_effective_gain_db(
            patch_name, patch_path=patch_path, stable_key=stable_key
        )

    def set_calibration(
        self,
        patch_name: str,
        gain_db: float,
        lufs_measured: float,
        *,
        enabled: bool | None = None,
        calibrated_at: str | None = None,
        true_peak_dbtp: float | None = None,
        post_gain_peak_dbtp: float | None = None,
        cal_route: str | None = None,
        strike_lufs: float | None = None,
        sustain_lufs: float | None = None,
        cal_path: str | None = CAL_PATH,
        lufs_unity: dict[str, float] | None = None,
        true_peak_unity_dbtp: dict[str, float] | None = None,
        authored_volume: float | None = None,
        authored_volume_b: float | None = None,
        capture_padded_db: float | None = None,
        patch_path: str | None = None,
        stable_key: str | None = None,
    ) -> dict[str, Any] | None:
        """Write a calibration. Returns the trim migration's record when this replaced a
        pre-stage entry that had a user trim (`migrate_trim_to_stage`), else None."""
        key = self._storage_key(
            patch_name, patch_path=patch_path, stable_key=stable_key
        )
        existing, matched = self._lookup(
            patch_name, patch_path=patch_path, stable_key=stable_key
        )
        if enabled is None:
            if existing is None or "enabled" not in existing:
                enabled_value: bool = True
            else:
                enabled_value = bool(existing["enabled"])
        else:
            enabled_value = enabled

        entry: dict[str, Any] = {
            "gain_db": round(float(gain_db), 3),
            "enabled": enabled_value,
            "lufs_measured": round(float(lufs_measured), 2),
            "calibrated_at": calibrated_at or datetime.now(timezone.utc).isoformat(),
        }
        if true_peak_dbtp is not None:
            entry["true_peak_dbtp"] = round(float(true_peak_dbtp), 2)
        if post_gain_peak_dbtp is not None:
            entry["post_gain_peak_dbtp"] = round(float(post_gain_peak_dbtp), 2)
        if cal_route is not None:
            entry["cal_route"] = cal_route
        if strike_lufs is not None:
            entry["strike_lufs"] = round(float(strike_lufs), 2)
        if sustain_lufs is not None:
            entry["sustain_lufs"] = round(float(sustain_lufs), 2)
        if cal_path is not None:
            entry["cal_path"] = cal_path
        if lufs_unity is not None:
            entry["lufs_unity"] = {k: round(float(v), 2) for k, v in lufs_unity.items()}
        if true_peak_unity_dbtp is not None:
            entry["true_peak_unity_dbtp"] = {
                k: round(float(v), 2) for k, v in true_peak_unity_dbtp.items()
            }
        if authored_volume is not None:
            entry["authored_volume"] = round(float(authored_volume), 6)
        if authored_volume_b is not None:
            entry["authored_volume_b"] = round(float(authored_volume_b), 6)
        if capture_padded_db is not None:
            entry["capture_padded_db"] = round(float(capture_padded_db), 2)
        migration: dict[str, Any] | None = None
        if existing:
            if "user_gain_db" in existing and "user_trim_db" not in existing:
                existing = _migrate_legacy_user_gain(existing)
            if "user_trim_db" in existing:
                entry["user_trim_db"] = existing["user_trim_db"]
                for carried in ("user_trim_db_legacy",):
                    if carried in existing:
                        entry[carried] = existing[carried]
                # A pre-stage row: its trim was heard through amp/volume's cube law,
                # on its gain or, with none, on 0 (`legacy_norm_db`).
                if (entry.get("cal_path") == CAL_PATH and not is_calibrated(existing)
                        and trim_is_stage_law(existing)):
                    # A stage-law trim on an uncalibrated row: keep what it plays.
                    old_trim = float(existing["user_trim_db"])
                    heard = legacy_norm_db(existing, authored_volume, authored_volume_b)
                    wanted = (heard if heard is not None else 0.0) - float(gain_db)
                    trim = min(wanted, NORM_TRIM_DB_MAX)
                    clamped = trim < wanted
                    entry["user_trim_db"] = round(trim, 3)
                    migration = {"key": key, "old": old_trim, "new": round(trim, 3),
                                 "clamped": clamped}
                elif entry.get("cal_path") == CAL_PATH and not is_calibrated(existing):
                    old_trim = float(existing["user_trim_db"])
                    old_gain = _finite_number(existing.get("gain_db")) or 0.0
                    trim, clamped = migrate_trim_to_stage(
                        gain_db_old=old_gain,
                        trim_db_old=old_trim,
                        gain_db_new=float(gain_db),
                        authored_volume=louder_authored(authored_volume, authored_volume_b),
                    )
                    entry["user_trim_db"] = round(trim, 3)
                    entry["user_trim_db_legacy"] = old_trim
                    migration = {"key": key, "old": old_trim, "new": round(trim, 3),
                                 "clamped": clamped}
        if matched and matched != key:
            self._data.pop(matched, None)
        self._data[key] = entry
        return migration

    def list_missing(self, patches: list[dict]) -> list[str]:
        missing: list[str] = []
        seen: set[str] = set()
        for patch in patches:
            key = self._storage_key(
                patch["name"],
                patch_path=patch.get("path"),
                stable_key=patch.get("stable_key"),
            )
            if key in seen:
                continue
            seen.add(key)
            entry = self.get_entry(
                patch["name"],
                patch_path=patch.get("path"),
                stable_key=patch.get("stable_key"),
            )
            if not is_calibrated(entry):
                missing.append(key)
        return sorted(missing)

    def count_missing(self, patches: list[dict]) -> tuple[int, int]:
        """Return (missing_count, unique_patch_count)."""
        missing = self.list_missing(patches)
        seen: set[str] = set()
        for patch in patches:
            key = self._storage_key(
                patch["name"],
                patch_path=patch.get("path"),
                stable_key=patch.get("stable_key"),
            )
            seen.add(key)
        return len(missing), len(seen)


def log_missing_normalization_summary(
    patches: list[dict],
    store: PatchNormalizationStore | None = None,
) -> None:
    """Lightweight scan-complete log — no rendering."""
    store = store or PatchNormalizationStore()
    missing_count, total = store.count_missing(patches)
    if total == 0:
        return
    if missing_count == 0:
        print(f"Patch normalization: all {total} patches have calibration entries")
    else:
        print(
            f"Patch normalization: {missing_count} of {total} patches missing calibration "
            f"(run scripts/calibrate-patch-normalization.py or System → Calibrate missing patches)"
        )
