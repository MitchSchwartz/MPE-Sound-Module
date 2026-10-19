"""The wire format to `native/mpe-fx-host`, and nothing else.

`mpe-fx-host` is the effects insert on the Surge trunk. It holds no policy: it
is told which effects are on, one depth and a level per effect, and it
applies them. Whatever decides those (the FX policy module) sends them through
`encode_chain_levels` here,
so the datagram has one definition on the Python side. The C side's parser is
`fx_parse_control` in `native/mpe-fx-host/fx_dsp.c`; `tests/test_fx_host.py`
runs every encoding through it.

One ASCII line per UDP datagram to 127.0.0.1, in the live monitor's style
(`gain 0.500000`):

    chainl <mask> <depth> <l0> ... <l6>
                     e.g.  chainl 5 0.500000 1.000000 1.000000 0.400000 1.000000 1.000000 1.000000 1.000000
    chain <mask> <depth>            e.g.  chain 5 0.500000

**Per-effect levels** (`chainl`, what the remapper sends, `encode_chain_levels`).
After the mask and the shared depth comes one level per effect, 0.0-1.0,
indexed by effect id (l0 is the chorus, l1 the drive, ...), not in run order.
Effect i plays at depth `depth * l_i`: one knob moves every enabled effect and
they keep their proportions. The levels are append-only, like the ids: the
client reads a level it is not sent as 1.0 and ignores levels for ids it does
not ship, so a sender and a client that disagree on how many effects exist
still agree on the ones both know. `chain <mask> <depth>` is the same with
every level 1.0, and sets them so.

The mask is a decimal integer with bit `1 << effect_id` set for each enabled
effect (5 = chorus + phaser). The effects run in series in the fixed order
`CHAIN_ORDER`, whatever order the bits are in; the mask only says which. One
depth, 0.0-1.0, applies to every enabled effect. Depth 0 or an empty mask is
bypass: the output is the input, bit for bit. The client smooths depth
(~40 ms) and crossfades each effect in or out when its bit changes (~30 ms),
so a sender may send raw controller steps and toggles. Malformed datagrams and
masks naming an effect this build does not ship are ignored by the client:
they change nothing.

The single-effect datagram of the first protocol, `fx <effect id> <depth>`, is
still accepted and means exactly `chain <1 << id> <depth>`. `encode` writes it.

**Bass bypass** (`bass <hz>`, `encode_bass`, #173). Below this crossover the
signal skips the chain: the lows stay dry (per channel), and only the highs go
through the effects (`native/mpe-fx-host/fx_dsp.h`, "Bass bypass"). 0 is Off.
A datagram of its own rather than a field on `chainl`, whose level count is
open-ended: every older message keeps its meaning, a client that predates it
reads it as malformed and changes nothing, and a client that never receives
one stays Off, which is how it played before. The client glides the crossover
and fades the split in and out, so a sender may send raw slider steps. The UI
offers Off and `BASS_STEPS_HZ` (80-300 Hz); the client accepts 20-1000.

    bass 120.000000

**Stage gain** (`gain <linear>`, `encode_gain`). One linear gain the client
applies to its input before the chain, for per-patch normalization. 1.0 is
unity, which is where a fresh client starts, and a client that never
receives one plays exactly as before. `PatchLoader` is its only sender
(`tests/test_stage_gain_guard.py`). A datagram of its
own, for the reason `bass` is one. The client ramps it (~25 ms), clamps it to
`GAIN_MAX_DB` and ignores NaN, inf and negative values; the value is a plain
decimal with eight places, so -90 dB still has three significant digits.

    gain 3.98107171

**Without mpe-fx-host the level is the patch's own.** The insert always runs
(`MPE_FX_HOST` gates only the effects), so normalization, the Norm trim, device
mode's headroom and the trim-mode Vol fader all live in this one stage. When
the insert is absent (stopped, crashed, restarting, or the direct monitor path
restored) Surge goes straight out: every patch plays as authored, normalization
is off in effect, and the Vol fader does nothing. The DAC level in device mode
still applies. No second level site exists in Surge to fall back to; that was
the bug the stage removes. The sender cannot tell: a `gain` datagram is
fire-and-forget and the browser has no fx-host health indicator, so this state
is not shown on screen. A restarted insert starts at the last gain it was
told (below), so a restart does not undo a cut.

The client keeps the last gain it was told in a file (`gain_state_path`,
`/run/mpe/fx-stage-gain` unless `MPE_FX_GAIN_STATE` says otherwise; one line in
the datagram's own format) and a restarted client starts at it held to unity,
with no ramp from unity (`native/mpe-fx-host/fx_gain_state.h`): a restart keeps
a cut, never a boost, and a boost waits for the sender's next resend. No file:
unity.
"""

from __future__ import annotations

import math
import os

DEFAULT_HOST = "127.0.0.1"
#: Must equal CONTROL_PORT_DEFAULT in native/mpe-fx-host/mpe-fx-host.c.
#: `tests/test_fx_host.py` reads the C source and fails if they drift.
DEFAULT_PORT = 9958

#: Effect ids, in the order of `FX_EFFECTS[]` in fx_dsp.c. Append only: an id is
#: a number on the wire, and renumbering would silently change what an old
#: sender selects.
EFFECT_CHORUS = 0
EFFECT_TUBE_DRIVE = 1
EFFECT_PHASER = 2
EFFECT_TREMOLO = 3
EFFECT_FLANGER = 4
EFFECT_ROTARY = 5
EFFECT_AUTO_WAH = 6
EFFECT_IDS: tuple[int, ...] = (
    EFFECT_CHORUS,
    EFFECT_TUBE_DRIVE,
    EFFECT_PHASER,
    EFFECT_TREMOLO,
    EFFECT_FLANGER,
    EFFECT_ROTARY,
    EFFECT_AUTO_WAH,
)
EFFECT_NAMES: dict[int, str] = {
    EFFECT_CHORUS: "Chorus",
    EFFECT_TUBE_DRIVE: "Tube drive",
    EFFECT_PHASER: "Phaser",
    EFFECT_TREMOLO: "Tremolo",
    EFFECT_FLANGER: "Flanger",
    EFFECT_ROTARY: "Rotary",
    EFFECT_AUTO_WAH: "Auto-wah",
}

#: What the Effect fader and the mod-knob switch call an effect where room is
#: tight (their pixel budget): the name itself unless listed here. The picker
#: rows, which have the room, use `EFFECT_NAMES`.
EFFECT_SHORT_NAMES: dict[int, str] = {EFFECT_TUBE_DRIVE: "Tube"}

#: The order the enabled effects run in, first to last. A property of the DSP,
#: not of the wire: the mask's bit order is the id order above, never this.
#: Must equal FX_CHAIN_ORDER in fx_dsp.c; `tests/test_fx_host.py` reads it.
CHAIN_ORDER: tuple[int, ...] = (
    EFFECT_TUBE_DRIVE,
    EFFECT_AUTO_WAH,
    EFFECT_PHASER,
    EFFECT_FLANGER,
    EFFECT_CHORUS,
    EFFECT_ROTARY,
    EFFECT_TREMOLO,
)

#: No effect enabled, and every effect this build ships enabled.
MASK_NONE = 0
MASK_ALL = (1 << len(EFFECT_IDS)) - 1

#: An effect's level when nothing says otherwise: it plays at the shared depth.
LEVEL_DEFAULT = 1.0
#: Every effect at LEVEL_DEFAULT, by effect id: the `chain` command's levels.
LEVELS_DEFAULT: tuple[float, ...] = tuple(LEVEL_DEFAULT for _ in EFFECT_IDS)
#: Must equal FX_MAX_EFFECTS in fx_dsp.h: the most levels a `chainl` may carry.
MAX_LEVELS = 8

#: The bass bypass (`bass <hz>`): Off, and the crossover range the UI offers.
#: A patch that never set one plays at BASS_DEFAULT_HZ (`patch_fx`).
BASS_OFF = 0.0
BASS_MIN_HZ = 80.0
BASS_MAX_HZ = 300.0
BASS_DEFAULT_HZ = 120.0
#: The slider's positions, left to right, evenly spaced on it: Off, then 10 Hz
#: steps where a few hertz is audible on the low notes, 25 Hz steps above.
BASS_STEPS_HZ: tuple[float, ...] = (
    BASS_OFF,
    80.0, 90.0, 100.0, 110.0, 120.0, 130.0, 140.0, 150.0,
    175.0, 200.0, 225.0, 250.0, 275.0, 300.0,
)


#: Must equal FX_GAIN_MAX_DB in native/mpe-fx-host/fx_dsp.h: the most stage
#: gain the client applies. `tests/test_fx_host.py` reads the C and pins it.
GAIN_MAX_DB = 48.0
#: Unity: what a fresh client plays at.
GAIN_UNITY = 1.0


def enabled(value: str | None = None) -> bool:
    """One law for "is the FX host on", shared with the shell.

    `scripts/start-mpe-fx-host.sh` lowercases the same variable and matches the
    same four words — the law `live_monitor.enabled` and its start script
    share, for the same reason: half a feature running is worse than either
    state.
    """
    raw = value if value is not None else os.environ.get("MPE_FX_HOST", "0")
    return (raw or "").strip().lower() in ("1", "true", "yes", "on")


def resolve_port() -> int:
    raw = os.environ.get("MPE_FX_HOST_PORT", "")
    try:
        port = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_PORT
    return port if 1 <= port <= 65535 else DEFAULT_PORT


#: Must equal FX_GAIN_STATE_DEFAULT in native/mpe-fx-host/fx_gain_state.h
#: (`tests/test_fx_host.py` reads the C).
GAIN_STATE_DEFAULT = "/run/mpe/fx-stage-gain"


def gain_state_path() -> str:
    """Where the client keeps the last stage gain it was told (MPE_FX_GAIN_STATE)."""
    return os.environ.get("MPE_FX_GAIN_STATE", "").strip() or GAIN_STATE_DEFAULT


def read_gain_state(path: str | None = None) -> float | None:
    """The last stage gain the client applied, from its state file; None if the
    file is missing or is not one well-formed `gain` line. Only a client that is
    running and was told a new value writes it, so a reader that sees the value
    it just sent knows the client took it."""
    try:
        with open(path or gain_state_path(), "rb") as handle:
            raw = handle.read(65)
    except OSError:
        return None
    if len(raw) > 64:
        return None
    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError:
        return None
    parts = text.split()
    if len(parts) != 2 or parts[0] != "gain" or not text.startswith("gain "):
        return None
    number = parts[1]
    if not number or any(c not in "0123456789." for c in number):
        return None
    try:
        value = float(number)
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def encode(effect_id: int, depth: float) -> bytes:
    """The single-effect datagram: `effect_id` alone at `depth` (clamped to
    0..1). The client reads it as `encode_chain(1 << effect_id, depth)`.

    Raises ValueError for an effect id this build does not ship, and for a
    non-finite depth: a caller holding NaN has a bug, and sending 0 or 1 in its
    place would hide it behind an audible guess.
    """
    if isinstance(effect_id, bool) or not isinstance(effect_id, int):
        raise ValueError(f"effect id must be an int, got {effect_id!r}")
    if effect_id not in EFFECT_IDS:
        raise ValueError(f"unknown effect id {effect_id} (known: {EFFECT_IDS})")
    value = float(depth)
    if not math.isfinite(value):
        raise ValueError(f"depth must be finite, got {depth!r}")
    value = max(0.0, min(1.0, value))
    return f"fx {effect_id} {value:.6f}".encode("ascii")


def _check_id(effect_id: int) -> int:
    if isinstance(effect_id, bool) or not isinstance(effect_id, int):
        raise ValueError(f"effect id must be an int, got {effect_id!r}")
    if effect_id not in EFFECT_IDS:
        raise ValueError(f"unknown effect id {effect_id} (known: {EFFECT_IDS})")
    return effect_id


def _check_mask(mask: int) -> int:
    if isinstance(mask, bool) or not isinstance(mask, int):
        raise ValueError(f"mask must be an int, got {mask!r}")
    if mask < 0 or mask & ~MASK_ALL:
        raise ValueError(f"mask {mask} names an effect this build does not ship (all: {MASK_ALL})")
    return mask


def mask_of(effect_ids) -> int:
    """The mask with exactly these effects enabled (any order, repeats allowed)."""
    mask = MASK_NONE
    for effect_id in effect_ids:
        mask |= 1 << _check_id(effect_id)
    return mask


def effects_in(mask: int) -> tuple[int, ...]:
    """The enabled effect ids, in the order they run (`CHAIN_ORDER`)."""
    _check_mask(mask)
    return tuple(effect_id for effect_id in CHAIN_ORDER if mask & (1 << effect_id))


def is_enabled(mask: int, effect_id: int) -> bool:
    return bool(_check_mask(mask) & (1 << _check_id(effect_id)))


def toggled(mask: int, effect_id: int) -> int:
    """`mask` with `effect_id` flipped: a tap on that effect's row."""
    return _check_mask(mask) ^ (1 << _check_id(effect_id))


def encode_chain(mask: int, depth: float) -> bytes:
    """The datagram that enables the effects in `mask` at one `depth` (clamped
    to 0..1).

    Raises ValueError for a mask naming an effect this build does not ship, and
    for a non-finite depth, for the same reason `encode` does.
    """
    _check_mask(mask)
    value = float(depth)
    if not math.isfinite(value):
        raise ValueError(f"depth must be finite, got {depth!r}")
    value = max(0.0, min(1.0, value))
    return f"chain {mask} {value:.6f}".encode("ascii")


def level_value(value) -> float | None:
    """`value` as a level, clamped to 0..1, or None if it is not a finite number."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value):
        return None
    return max(0.0, min(1.0, float(value)))


def levels_from(value) -> tuple[float, ...] | None:
    """A full levels tuple (one per shipped effect, by id) from a stored or
    published list, or None when `value` is not a list at all.

    Append-only, as on the wire: a list written by a build with fewer effects
    is read with LEVEL_DEFAULT for the ones it lacks, and entries for ids this
    build does not ship are dropped. An entry that is not a finite number is
    LEVEL_DEFAULT: nothing read from a file is trusted, and 1.0 is what the
    patch played at before it had levels.
    """
    if not isinstance(value, (list, tuple)):
        return None
    out = []
    for effect_id in EFFECT_IDS:
        level = level_value(value[effect_id]) if effect_id < len(value) else None
        out.append(LEVEL_DEFAULT if level is None else level)
    return tuple(out)


def with_level(levels, effect_id: int, level: float) -> tuple[float, ...]:
    """`levels` with `effect_id`'s level set (clamped to 0..1)."""
    _check_id(effect_id)
    value = level_value(level)
    if value is None:
        raise ValueError(f"level must be finite, got {level!r}")
    full = list(_check_levels(levels))
    full[effect_id] = value
    return tuple(full)


def _check_levels(levels) -> tuple[float, ...]:
    if not isinstance(levels, (list, tuple)) or len(levels) != len(EFFECT_IDS):
        raise ValueError(f"levels must be {len(EFFECT_IDS)} numbers by effect id, got {levels!r}")
    out = []
    for value in levels:
        level = level_value(value)
        if level is None:
            raise ValueError(f"level must be finite, got {value!r}")
        out.append(level)
    return tuple(out)


def encode_chain_levels(mask: int, depth: float, levels) -> bytes:
    """The datagram that enables the effects in `mask` at the shared `depth`,
    each at its own level: one per shipped effect, by effect id (each clamped
    to 0..1). Effect i plays at `depth * levels[i]`.

    Raises ValueError for a mask naming an effect this build does not ship, a
    non-finite depth or level, or a levels list that is not one per effect,
    for the reason `encode` does.
    """
    _check_mask(mask)
    value = float(depth)
    if not math.isfinite(value):
        raise ValueError(f"depth must be finite, got {depth!r}")
    value = max(0.0, min(1.0, value))
    fields = " ".join(f"{level:.6f}" for level in _check_levels(levels))
    return f"chainl {mask} {value:.6f} {fields}".encode("ascii")


def bass_value(value) -> float | None:
    """`value` as a crossover: BASS_OFF, or a finite number in
    BASS_MIN_HZ..BASS_MAX_HZ. None for anything else (unset): nothing read
    from a file is trusted, and a value out of range is not guessed at."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value):
        return None
    hz = float(value)
    if hz == BASS_OFF:
        return BASS_OFF
    return hz if BASS_MIN_HZ <= hz <= BASS_MAX_HZ else None


def encode_bass(hz: float) -> bytes:
    """The bass bypass's datagram: the crossover in Hz, or BASS_OFF.

    Anything above 0 is clamped to BASS_MIN_HZ..BASS_MAX_HZ, the range this
    side offers. Raises ValueError for a non-finite or negative value, for the
    reason `encode` does.
    """
    if isinstance(hz, bool) or not isinstance(hz, (int, float)):
        raise ValueError(f"bass must be a number, got {hz!r}")
    value = float(hz)
    if not math.isfinite(value) or value < 0.0:
        raise ValueError(f"bass must be finite and >= 0, got {hz!r}")
    if value > 0.0:
        value = max(BASS_MIN_HZ, min(BASS_MAX_HZ, value))
    return f"bass {value:.6f}".encode("ascii")


def encode_gain(linear: float) -> bytes:
    """The stage gain's datagram: a linear gain, 1.0 for unity.

    Clamped to GAIN_MAX_DB, as the client clamps, so what is sent is what is
    applied. Raises ValueError for a non-finite or negative value, for the
    reason `encode` does.
    """
    if isinstance(linear, bool) or not isinstance(linear, (int, float)):
        raise ValueError(f"gain must be a number, got {linear!r}")
    value = float(linear)
    if not math.isfinite(value) or value < 0.0:
        raise ValueError(f"gain must be finite and >= 0, got {linear!r}")
    value = min(value, 10.0 ** (GAIN_MAX_DB / 20.0))
    return f"gain {value:.8f}".encode("ascii")
