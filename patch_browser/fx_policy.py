"""What the FX host plays and where the mod wheel goes, decided here only.

`native/mpe-fx-host` holds no policy: it is told an effect and a depth and
applies them. This module is the policy, the same split as
`scripts/sooperlooper/live_monitor.py` and `native/mpe-live-monitor`. It is the
only place that decides:

* which effects are on (the chain's enable mask), at what depth, and each
  effect's level relative to that depth;
* whether CC 1 (the mod wheel) reaches Surge or drives the effect.

**The CC 1 rule.** The global setting `mod_wheel_target` is `patch_first`
(default) or `effect`. `effect` always takes CC 1 for the effect. `patch_first`
leaves CC 1 with Surge when the loaded patch routes the mod wheel to anything
(`<modrouting source="6"`, 6 = ms_modwheel in Surge's ModulationSource.h), and
takes it for the effect when the patch has no such routing. About two thirds of
the library routes the mod wheel, so taking it unconditionally would break
those patches.

Only CC 1 on the master channel (MIDI channel 1, status 0xB0) as it arrives
from a controller, before the classic-MIDI translator spreads it across member
channels. Member channels 2-16 and CC 74 are never looked at: on an MPE
controller they are per-note expression.

**Who runs what.** The touch UI loads patches, so it owns `FxPolicy`: it reads
the patch's mod-wheel flag once per load, resolves the effective config and
publishes the resulting CC 1 destination, plus any Effect fader request, to
`fx-route.json` in the run dir. The remapper sees every controller message, so
it owns `Cc1Router`, the one process that sends the FX host its effect
commands (the stage `gain` is the patch loader's, one writer per command): it reads that
file when it changes, asks `route_cc1` what to do with each message, and
publishes what the knob and the fader have set to `fx-levels.json`: the depth
in force, for the Effect fader, and the last CC 1 it sent Surge, for the Patch
mod fader. Each file has one writer.

**Last move wins, and the fader shows it.** The Patch mod fader sends to Surge
over OSC, not through the remapper, so the remapper cannot order the two. Both
processes stamp each move with `time.monotonic()`, which on Linux is one
system-wide clock (CLOCK_MONOTONIC), and the UI follows a knob value only when
it is newer than its own last fader move. The Effect fader's requests carry the
same stamp, for the same reason.

**Off means off.** With MPE_FX_HOST unset, neither object is active: no file,
no socket, and the remapper forwards CC 1 to Surge exactly as before.

**Which effects.** The effects run in series in the fixed order
`fx_host_protocol.CHAIN_ORDER`; an enable mask says which are on, and one depth
(the knob) applies to all of them, each scaled by its own level (below). The picker on the play screen is a list of
checkboxes: a tap toggles one effect (`toggle_effect`) for the loaded patch
only; nothing global is written. It reaches the FX host
the way everything else does: the UI writes it to the route file's `mask`, and
the remapper sends it. Toggling moves no depth: the effects that stay on keep
playing at the depth in force, and one switched on comes in at it.

**Per-effect levels.** Each effect has a level, 0-1 (default 1.0), set by its
slider in the picker (`set_level`) for the loaded patch only. Effect i plays at
`depth * level_i`, so the knob and the Effect fader move every enabled effect
and they keep their proportions. An unticked effect keeps its level. The
levels go the way the mask does: the UI writes them to the route file's
`levels` (throttled like a fader drag), and the remapper sends them with every
datagram (`chainl`), so a restarted remapper re-asserts them with the depth.

**Bass bypass** (`set_bass`, #173). Below a crossover the lows skip the
effects and stay dry. One slider in the effect picker's header sets
it for the loaded patch only: Off, or a step of
`fx_host_protocol.BASS_STEPS_HZ` (80-300 Hz). A patch that never set one plays
at `BASS_DEFAULT_HZ` (120 Hz), and its entry keeps it unset, so it follows the
default. It goes the way the levels do: the UI writes it to the route file's
`bass_hz` (throttled like a drag), and the remapper sends it (`bass <hz>`)
ahead of the chain datagram whenever it sends the chain. With no effect ticked
it is inert, as the Effect fader is: greyed out, and nothing moves.

**Labels** (`chain_label`). The Effect fader and the mod-knob switch name the
enabled effects in run order when the names fit, otherwise a
count ("3 FX"), and "No FX" when none is on. Whether a label fits is the
screen's call (a pixel budget it measures); this module only offers the long
form first and the count after it.

**No effect enabled** (`effect_inert`, the one test). The FX host is sent the
empty mask, which is bypass, and the depth has nothing to drive, so it is left
alone: the Effect fader is greyed out, shows 0 and takes no touch (as Patch mod
is for a patch with no wheel routing), and CC 1 routed to the effect moves
nothing. It is still taken from Surge, since the route says the effect, and
the knob's position is still tracked, so pickup holds when an effect comes
back. The stored depth is never touched, so ticking an effect brings back the
depth the patch had. Moving a depth nobody can see or hear would make the
level an effect comes back at a surprise, and an unseen jump in level is the
dangerous direction. The effect picker's label still opens on a tap.

**A patch with no mod-wheel routing** hides the play-screen switch
(`switch_locked`: the knob goes to the effect either way, so there is nothing
to choose) and greys out the Patch mod fader (`patch_mod_locked`, the same
test): the fader shows 0, takes no touch and sends Surge nothing, not even the
resend after a load. The kept Patch mod value is not touched, so the next
patch that routes the wheel gets it back.

**Each patch remembers its setup** (`patch_fx.PatchFxStore`, a sidecar store
like Hold and normalization): its mask, its one depth, its levels, and the
mod-knob target.
Effect state is entirely per patch: a patch with no entry, or a missing field,
plays the factory default (`FACTORY_MASK`, no effect, at depth 0), never what
was used last. Only the target falls back to a global value, the Settings
default. The store is written from the UI loop, `STORE_WRITE_DELAY_S` after
the last change and before the next patch load, never per knob tick.

**Pickup.** After a switch (patch load, mod-knob target) or a move on the
touch screen, the hardware knob's position says nothing about the value in
force. `Cc1Router` uses the scaling law shared with the APC faders in
`loop_mix` (`fader_pickup.scaled`): the knob's moves change the value in
force by the fraction of travel they cover, so it responds at once, never
jumps, and meets the knob at the ends of travel. The faders show the value in
force, not the knob.

**Switching the target only moves the knob.** Flipping between Patch mod and
the effect changes where CC 1 goes and nothing else: the effects keep their
depth and Surge keeps its mod-wheel value, so both stay audible.
"""

from __future__ import annotations

import math
import os
import re
import socket
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from patch_browser import fx_host_protocol
from patch_browser.fader_pickup import Pickup
from patch_browser.json_store import atomic_write_json, read_json_dict
from patch_browser.patch_fx import FACTORY_DEPTH as PATCH_FACTORY_DEPTH
from patch_browser.patch_fx import FACTORY_LEVELS as PATCH_FACTORY_LEVELS
from patch_browser.patch_fx import FACTORY_MASK as PATCH_FACTORY_MASK
from patch_browser.patch_fx import PatchFx, valid_mask

MOD_WHEEL_PATCH_FIRST = "patch_first"
MOD_WHEEL_EFFECT = "effect"
MOD_WHEEL_TARGETS = (MOD_WHEEL_PATCH_FIRST, MOD_WHEEL_EFFECT)
DEFAULT_MOD_WHEEL_TARGET = MOD_WHEEL_PATCH_FIRST
#: Key in the touch UI prefs file (`ui_prefs.UI_STATE_FILE`).
MOD_WHEEL_PREF_KEY = "mod_wheel_target"
#: The factory default effect state, for a patch with no entry or a missing
#: field: no effect on, depth 0. Never the last-used value (#142 round 5, F3).
#: Same values as `patch_fx.FACTORY_MASK` / `FACTORY_DEPTH`.
FACTORY_MASK = PATCH_FACTORY_MASK
FACTORY_DEPTH = PATCH_FACTORY_DEPTH
FACTORY_LEVELS = PATCH_FACTORY_LEVELS

CC1_TO_SURGE = "surge"
CC1_TO_FX = "fx"

#: ms_modwheel in Surge's ModulationSource.h.
MS_MODWHEEL = 6
#: Control change on MIDI channel 1: the lower-zone master channel.
MASTER_CONTROL_CHANGE = 0xB0
CC_MOD_WHEEL = 1
CC_MAX = 127

#: UI -> remapper: CC 1 destination, enable mask, and the Effect fader's request.
ROUTE_FILE_NAME = "fx-route.json"
#: Remapper -> UI: the depth in force (knob or fader) and the last CC 1 sent to Surge.
LEVELS_FILE_NAME = "fx-levels.json"
DEPTH_FROM_KNOB = "knob"
DEPTH_FROM_FADER = "fader"
#: Fader requests while dragging are written at most this often.
DEPTH_PUBLISH_S = 0.05
#: How often the UI checks the levels file for a knob move.
LEVELS_POLL_S = 0.1
#: The per-patch store is written this long after the last change (a knob
#: sweep or a fader drag is one write, not one per tick), and at once before
#: the next patch load.
STORE_WRITE_DELAY_S = 1.0

#: Seconds between mtime checks of the route file in the remapper. The file
#: moves once per patch load, so this bounds how late a new route applies.
#: Cost: one stat() per check, 20/s, no fork and no read unless it changed.
ROUTE_REFRESH_S = 0.05

_MODWHEEL_ROUTING_RE = re.compile(rb'<modrouting\b[^>]*\bsource="6"')


MOD_WHEEL_TARGET_LABELS = {
    MOD_WHEEL_PATCH_FIRST: "Patch first",
    MOD_WHEEL_EFFECT: "Effect",
}


def mod_wheel_target_label(target: str) -> str:
    return MOD_WHEEL_TARGET_LABELS[normalize_target(target)]


#: The two faders the knob can drive. The Patch mod fader is labelled with
#: this; the Effect fader is labelled with the enabled effects (`chain_label`),
#: so the play-screen switch, which uses the same two, names exactly the fader
#: the knob moves.
PATCH_MOD_FADER_LABEL = "Patch mod"
#: The Effect fader's name where no chain is in view (the Settings row, docs).
EFFECT_FADER_LABEL = "Effect"
#: The label with no effect enabled.
NO_FX_LABEL = "No FX"
#: Joins the enabled effects' names, in run order.
CHAIN_JOIN = "+"
MOD_KNOB_PREFIX = "Mod knob \u2192 "


def normalize_mask(value) -> int:
    """A mask of shipped effects, or the factory default (none). Never trusts
    a stored value."""
    mask = valid_mask(value)
    return FACTORY_MASK if mask is None else mask


def _always_fits(_text: str) -> bool:
    return True


def effect_inert(mask: int) -> bool:
    """No effect is ticked: the chain is bypass and the depth drives nothing.

    The one test for it. The Effect fader is greyed out (`FxPolicy.
    effect_locked`) and the knob routed to the effect moves nothing
    (`Cc1Router.consume`); neither touches the stored depth.
    """
    return not fx_host_protocol.effects_in(mask)


def chain_names(mask: int) -> str:
    """The enabled effects' names in run order, or "No FX"."""
    ids = fx_host_protocol.effects_in(mask)
    if not ids:
        return NO_FX_LABEL
    return CHAIN_JOIN.join(fx_host_protocol.EFFECT_SHORT_NAMES.get(i, fx_host_protocol.EFFECT_NAMES[i]) for i in ids)


def chain_count(mask: int) -> str:
    """The short form: how many effects are on ("3 FX"), or "No FX"."""
    count = len(fx_host_protocol.effects_in(mask))
    return f"{count} FX" if count else NO_FX_LABEL


def chain_label(mask: int, fits=_always_fits) -> str:
    """What the screen calls the chain: the names if `fits(names)`, else the count.

    `fits` is the caller's pixel budget (text -> bool); without one the names
    are returned. "No FX" is the label for the empty mask either way.
    """
    names = chain_names(mask)
    return names if fits(names) else chain_count(mask)


def chain_labels() -> tuple[str, ...]:
    """Every label `chain_label` can return, for the measured width tables."""
    out: list[str] = []
    for mask in range(fx_host_protocol.MASK_ALL + 1):
        for label in (chain_names(mask), chain_count(mask)):
            if label not in out:
                out.append(label)
    return tuple(out)


def mod_knob_label(destination: str, mask: int = FACTORY_MASK, fits=_always_fits) -> str:
    """The play-screen switch's label: where the knob goes now, not the setting.

    Routed to the effect, it names the chain as the Effect fader does, and
    `fits` judges the whole label, prefix included.
    """
    if destination != CC1_TO_FX:
        return MOD_KNOB_PREFIX + PATCH_MOD_FADER_LABEL
    return MOD_KNOB_PREFIX + chain_label(mask, lambda names: fits(MOD_KNOB_PREFIX + names))


def mod_knob_labels() -> tuple[str, ...]:
    """Every label the switch can show, for layout checks."""
    return (mod_knob_label(CC1_TO_SURGE),) + tuple(MOD_KNOB_PREFIX + label for label in chain_labels())


def route_state_path() -> Path:
    from patch_browser.mpe_run_dir import run_dir

    return run_dir() / ROUTE_FILE_NAME


def levels_state_path() -> Path:
    from patch_browser.mpe_run_dir import run_dir

    return run_dir() / LEVELS_FILE_NAME


def normalize_target(value) -> str:
    return value if value in MOD_WHEEL_TARGETS else DEFAULT_MOD_WHEEL_TARGET


def patch_uses_mod_wheel(patch_path) -> bool | None:
    """Does the patch route the mod wheel to anything? None when unreadable.

    A Surge .fxp is a binary header, the patch XML, then optional binary
    wavetable data; only the XML up to `</patch>` is searched.
    """
    try:
        raw = Path(patch_path).read_bytes()
    except (OSError, TypeError, ValueError):
        return None
    start = raw.find(b"<patch")
    end = raw.find(b"</patch>", start if start >= 0 else 0)
    if start < 0 or end < 0:
        return None
    return _MODWHEEL_ROUTING_RE.search(raw, start, end) is not None


@dataclass(frozen=True)
class FxConfig:
    """What one patch load resolves to."""

    mask: int = FACTORY_MASK
    mod_wheel_target: str = DEFAULT_MOD_WHEEL_TARGET
    depth: float = FACTORY_DEPTH
    #: One level per shipped effect, by effect id.
    levels: tuple[float, ...] = FACTORY_LEVELS
    #: The bass bypass's crossover in force, Hz, or BASS_OFF. Never unset:
    #: a patch with none plays at the default.
    bass_hz: float = fx_host_protocol.BASS_DEFAULT_HZ


def resolve_config(
    *,
    global_target: str,
    patch_config: PatchFx | None = None,
) -> FxConfig:
    """The patch's own effect state, and its target else the global one.

    Effect state (mask and depth) is entirely per patch: a patch with no
    entry, or a missing field, gets the factory default (no effect, depth 0),
    never a global or last-used value. Only the mod-knob target has a global
    default (the Settings value), on purpose.
    """
    patch = patch_config or PatchFx()
    target = patch.target if patch.target is not None else normalize_target(global_target)
    return FxConfig(
        mask=FACTORY_MASK if patch.mask is None else patch.mask,
        mod_wheel_target=normalize_target(target),
        depth=FACTORY_DEPTH if patch.depth is None else patch.depth,
        levels=FACTORY_LEVELS if patch.levels is None else patch.levels,
        bass_hz=fx_host_protocol.BASS_DEFAULT_HZ if patch.bass_hz is None else patch.bass_hz,
    )


def cc1_destination(target: str, uses_mod_wheel: bool | None) -> str:
    """Where CC 1 goes for this target and this patch.

    A patch that could not be read counts as using the mod wheel, so under
    `patch_first` CC 1 stays with Surge, which is what it did before the FX
    host existed.
    """
    if normalize_target(target) == MOD_WHEEL_EFFECT:
        return CC1_TO_FX
    if uses_mod_wheel is False:
        return CC1_TO_FX
    return CC1_TO_SURGE


def is_master_mod_wheel(raw) -> bool:
    return len(raw) >= 3 and raw[0] == MASTER_CONTROL_CHANGE and raw[1] == CC_MOD_WHEEL


def depth_from_cc(value: int) -> float:
    return max(0, min(CC_MAX, int(value))) / CC_MAX


def route_cc1(raw, destination: str) -> float | None:
    """None: forward the message untouched. A float: consume it, that is the depth."""
    if destination != CC1_TO_FX or not is_master_mod_wheel(raw):
        return None
    return depth_from_cc(raw[2])


@dataclass(frozen=True)
class Route:
    """What the UI published. Anything missing or malformed reads as Surge."""

    destination: str = CC1_TO_SURGE
    #: The enable mask; malformed or missing reads as none enabled (bypass).
    mask: int = fx_host_protocol.MASK_NONE
    #: One level per effect, by id; missing reads as every level 1.0.
    levels: tuple[float, ...] = fx_host_protocol.LEVELS_DEFAULT
    #: The bass bypass's crossover; missing or malformed reads as the default,
    #: as a patch with none does.
    bass_hz: float = fx_host_protocol.BASS_DEFAULT_HZ
    route_stamp: str | None = None
    depth_request: float | None = None
    depth_stamp: str | None = None
    #: The Patch mod fader's value: what Surge's mod wheel is now, for pickup.
    patch_mod: int | None = None
    patch_mod_stamp: str | None = None
    #: `time.monotonic()` at that fader move, to order it against the knob.
    patch_mod_at: float | None = None


def _depth_value(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value):
        return None
    return max(0.0, min(1.0, float(value)))


def _stamp(value) -> str | None:
    return value if isinstance(value, str) and value else None


def read_route(path: Path) -> Route:
    data = read_json_dict(path)
    destination = data.get("cc1")
    mask = valid_mask(data.get("mask"))
    levels = fx_host_protocol.levels_from(data.get("levels"))
    bass_hz = fx_host_protocol.bass_value(data.get("bass_hz"))
    return Route(
        destination=destination if destination in (CC1_TO_SURGE, CC1_TO_FX) else CC1_TO_SURGE,
        mask=fx_host_protocol.MASK_NONE if mask is None else mask,
        levels=fx_host_protocol.LEVELS_DEFAULT if levels is None else levels,
        bass_hz=fx_host_protocol.BASS_DEFAULT_HZ if bass_hz is None else bass_hz,
        route_stamp=_stamp(data.get("route_stamp")),
        depth_request=_depth_value(data.get("depth_request")),
        depth_stamp=_stamp(data.get("depth_stamp")),
        patch_mod=_cc_value(data.get("patch_mod")),
        patch_mod_stamp=_stamp(data.get("patch_mod_stamp")),
        patch_mod_at=_moment(data.get("patch_mod_at")),
    )


def _moment(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def _cc_value(value) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if 0 <= value <= CC_MAX else None


@dataclass(frozen=True)
class Levels:
    """What the remapper published. A part that is missing or malformed is None.

    `depth_at` and `patch_mod_at` are `time.monotonic()` at the move.
    """

    depth: float | None = None
    source: str | None = None
    request: str | None = None
    depth_at: float | None = None
    patch_mod: int | None = None
    patch_mod_at: float | None = None


def read_levels(path: Path) -> Levels:
    data = read_json_dict(path)
    depth = _depth_value(data.get("depth"))
    source = data.get("source")
    if depth is None or source not in (DEPTH_FROM_KNOB, DEPTH_FROM_FADER):
        depth, source = None, None
    patch_mod = _cc_value(data.get("patch_mod"))
    patch_mod_at = _moment(data.get("patch_mod_at"))
    if patch_mod is None or patch_mod_at is None:
        patch_mod, patch_mod_at = None, None
    return Levels(
        depth=depth,
        source=source,
        request=_stamp(data.get("request")) if depth is not None else None,
        depth_at=_moment(data.get("depth_at")) if depth is not None else None,
        patch_mod=patch_mod,
        patch_mod_at=patch_mod_at,
    )


def _file_identity(path: Path) -> tuple[int, int] | None:
    """(inode, mtime ns). Every atomic write is a new inode, so two writes
    inside one mtime tick still read as a change."""
    try:
        st = path.stat()
    except OSError:
        return None
    return st.st_ino, st.st_mtime_ns


#: Seconds between complaints about an unreachable FX host. Time-limited for
#: the reason `live_monitor.REFUSAL_COMPLAIN_S` is: on a connected UDP socket
#: the refusal arrives one send late, so refusals alternate with "successes".
REFUSAL_COMPLAIN_S = 30.0
SEND_TOLERANCE = 1e-4


class FxHostSender:
    """Fire-and-forget enable mask + depth + levels at the FX host
    (`chainl`), and the bass bypass's crossover (`bass`), each deduplicated.

    Modelled on `LiveMonitorSender`: a connected, non-blocking UDP socket so an
    absent FX host shows up as ECONNREFUSED (counted and logged, rate-limited)
    instead of as silence. Never raises and never blocks; it runs on the MIDI
    path in the remapper.
    """

    def __init__(self, *, host: str | None = None, port: int | None = None, send=None, log=print) -> None:
        self.host = host or fx_host_protocol.DEFAULT_HOST
        self.port = fx_host_protocol.resolve_port() if port is None else port
        self._send = send
        self._log = log
        self._sock: socket.socket | None = None
        #: (mask, depth, levels) last delivered.
        self._last: tuple[int, float, tuple[float, ...]] | None = None
        #: The crossover last delivered.
        self._last_bass: float | None = None
        self._lock = threading.Lock()
        self._complained_at: float | None = None
        self.delivered = 0
        self.refused_total = 0

    def _socket(self) -> socket.socket | None:
        if self._sock is None:
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.setblocking(False)
                sock.connect((self.host, self.port))
                self._sock = sock
            except OSError:
                self._sock = None
        return self._sock

    def send(self, mask: int, depth: float, levels=None, bass_hz=None) -> bool:
        """`levels`: one per effect, by id; None is every level 1.0. `bass_hz`:
        the crossover, sent first (its own datagram, `bass`), or None to send
        none. True when anything was delivered.

        Whenever a chain datagram actually goes out, `bass` goes just before it
        whatever its own dedup says: a restarted fx host starts Off and only
        learns the chain again when the chain changes, so the crossover has to
        ride along with it (review of #185). Otherwise `bass` alone is deduped.
        """
        bass_payload = None
        if bass_hz is not None:
            try:
                bass_payload = fx_host_protocol.encode_bass(bass_hz)
            except ValueError as exc:
                self._say(f"fx host: not sending ({exc})")
        levels = fx_host_protocol.LEVELS_DEFAULT if levels is None else levels
        try:
            chain_payload = fx_host_protocol.encode_chain_levels(mask, depth, levels)
        except ValueError as exc:
            self._say(f"fx host: not sending ({exc})")
            chain_payload = None
        sent = False
        with self._lock:
            chain_due = False
            if chain_payload is not None:
                value = max(0.0, min(1.0, float(depth)))
                levels = tuple(max(0.0, min(1.0, float(level))) for level in levels)
                last = self._last
                chain_due = not (
                    last is not None
                    and last[0] == mask
                    and abs(last[1] - value) <= SEND_TOLERANCE
                    and all(abs(a - b) <= SEND_TOLERANCE for a, b in zip(last[2], levels))
                )
            if bass_payload is not None:
                bass_value = float(bass_payload.split()[1])
                bass_due = chain_due or self._last_bass is None or (
                    abs(self._last_bass - bass_value) > SEND_TOLERANCE
                )
                if bass_due and self._deliver(bass_payload):
                    self._last_bass = bass_value
                    self.delivered += 1
                    sent = True
            if chain_due and self._deliver(chain_payload):
                self._last = (mask, value, levels)
                self.delivered += 1
                sent = True
        return sent

    def _deliver(self, payload: bytes) -> bool:
        """One datagram out, under the lock. False when it could not go."""
        if self._send is not None:
            self._send(payload)
            return True
        sock = self._socket()
        if sock is None:
            return False
        try:
            sock.send(payload)
        except OSError as exc:
            self._note_refusal(exc)
            return False
        return True

    def _note_refusal(self, exc: OSError) -> None:
        self.refused_total += 1
        now = time.monotonic()
        if self._complained_at is None or now - self._complained_at >= REFUSAL_COMPLAIN_S:
            self._complained_at = now
            self._say(
                f"fx host unreachable on {self.host}:{self.port} ({exc}) — "
                f"{self.refused_total} update(s) refused; is mpe-fx-host.service running?"
            )

    def _say(self, message: str) -> None:
        if self._log is not None:
            self._log(message)

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None


class FxPolicy:
    """The touch UI's half: resolves each patch load and publishes intent.

    It never talks to the FX host. Everything it decides goes into the route
    file, and `Cc1Router` in the remapper is the one process that sends.

    Two controls set the mod-wheel target and they do not compete:

    * the Settings value is the global default. It is applied at every patch
      load, and changing it re-resolves the loaded patch at once, unless the
      patch has its own target;
    * the play-screen switch sets the loaded patch's own target, which the
      patch keeps (`patch_fx`).

    The Effect fader is a request, not a level: the remapper applies it,
    and publishes the depth actually in force (fader or knob, whichever moved
    last) to the levels file, which `poll_levels` reads back. The Patch mod
    fader sends to Surge itself, and tells the remapper the value so the knob
    picks it up; `poll_levels` moves it when the knob, routed to Surge, sent a
    later value.
    """

    def __init__(
        self,
        *,
        enabled: bool | None = None,
        route_path: Path | None = None,
        levels_path: Path | None = None,
        global_target: str | None = None,
        store=None,
        clock=time.monotonic,
    ) -> None:
        self.enabled = fx_host_protocol.enabled() if enabled is None else bool(enabled)
        self._route_path = route_path
        self._levels_path = levels_path
        #: Must be the remapper's clock: moves from both processes are compared.
        self._clock = clock
        if global_target is None:
            from patch_browser.ui_prefs import read_ui_prefs_file

            global_target = read_ui_prefs_file().get(MOD_WHEEL_PREF_KEY)
        self.global_target = normalize_target(global_target)
        #: `patch_fx.PatchFxStore`, or None to keep the setup in memory only.
        self._store = store
        #: (name, path, stable key) of the loaded patch, for the store.
        self._patch_ref: tuple[str, str | None, str | None] | None = None
        #: The loaded patch's own values (None: the factory default for
        #: mask and depth, the global default for the target).
        self.patch_fx = PatchFx()
        self._store_due: float | None = None
        self.config = self._resolve()
        self.target = self.config.mod_wheel_target
        self.uses_mod_wheel: bool | None = None
        self.patch_loaded = False
        self.depth = 0.0
        self.patch_mod = 0
        #: True once the Patch mod value is known (fader or knob): before
        #: that, 0 is a placeholder, not something Surge was sent.
        self.patch_mod_known = False
        # Each route change and each fader request carries its own stamp, so
        # the remapper can tell "the route moved" from "the fader moved" in one
        # file. pid-prefixed so a restarted UI never repeats an old stamp.
        self._seq = 0
        self._route_stamp = self._next_stamp()
        self._depth_request: float | None = None
        self._depth_stamp: str | None = None
        self._next_depth_write = 0.0
        self._depth_unsent = False
        self._depth_requested_at: float | None = None
        self._next_level_write = 0.0
        self._level_unsent = False
        self.dragging_level = False
        self._next_bass_write = 0.0
        self._bass_unsent = False
        self.dragging_bass = False
        self._patch_mod_request: int | None = None
        self._patch_mod_stamp: str | None = None
        self._next_patch_mod_write = 0.0
        self._patch_mod_unsent = False
        self._patch_mod_set_at: float | None = None
        self._patch_mod_seen_at: float | None = None
        self._levels_file_id: tuple[int, int] | None = None
        self._next_levels_poll = 0.0
        self.dragging_depth = False
        self.dragging_patch_mod = False

    def bind_store(self, store) -> None:
        """The per-patch store (the loader owns it, as it owns Hold's)."""
        self._store = store

    def _next_stamp(self) -> str:
        self._seq += 1
        return f"{os.getpid()}-{self._seq}"

    @property
    def destination(self) -> str:
        return cc1_destination(self.target, self.uses_mod_wheel)

    @property
    def mask(self) -> int:
        """The enable mask in force: what the route file carries."""
        return self.config.mask

    @property
    def levels(self) -> tuple[float, ...]:
        """The levels in force, one per effect by id: what the route file carries."""
        return self.config.levels

    def level(self, effect_id: int) -> float:
        return self.config.levels[effect_id]

    @property
    def bass_hz(self) -> float:
        """The bass bypass's crossover in force (Hz, or BASS_OFF): what the
        route file carries."""
        return self.config.bass_hz

    @property
    def bass_locked(self) -> bool:
        """The bass slider has nothing to act on: no effect is ticked
        (`effect_inert`, the Effect fader's test). Greyed out, it keeps its
        value and takes no touch."""
        return effect_inert(self.mask)

    def chain_label(self, fits=_always_fits) -> str:
        """The Effect fader's label (`chain_label`) within the caller's budget."""
        return chain_label(self.mask, fits)

    def knob_label(self, fits=_always_fits) -> str:
        """The mod-knob switch's label within the caller's budget."""
        return mod_knob_label(self.destination, self.mask, fits)

    def _resolve(self) -> FxConfig:
        return resolve_config(global_target=self.global_target, patch_config=self.patch_fx)

    @property
    def switch_locked(self) -> bool:
        """The play-screen switch has nothing to choose: the loaded patch has no
        mod-wheel routing, so the knob goes to the effect either way. Unknown
        (unreadable patch) is not locked: it counts as using the wheel, as in
        cc1_destination."""
        return self.uses_mod_wheel is False

    @property
    def patch_mod_locked(self) -> bool:
        """The Patch mod fader has nothing to drive: the same patches as
        `switch_locked`, decided there and nowhere else. The fader is greyed
        out, shows 0 and sends nothing; `patch_mod`, the value kept across
        loads, is left as it is for the next patch that routes the wheel."""
        return self.switch_locked

    @property
    def effect_locked(self) -> bool:
        """The Effect fader has nothing to drive: no effect is ticked
        (`effect_inert`). The fader is greyed out, shows 0 and takes no touch;
        `depth`, the patch's stored depth, is left as it is, so ticking an
        effect brings it back."""
        return effect_inert(self.mask)

    # -- the per-patch store ------------------------------------------------

    def _patch_changed(self, **changes) -> None:
        """Record a change to the loaded patch's own values; write later."""
        if not self.enabled or self._patch_ref is None:
            return
        fx = self.patch_fx
        # The effect state is written whole: mask and depth together, as they
        # are in force, so no entry this writes has a field left to default.
        self.patch_fx = PatchFx(
            mask=changes.get("mask", self.mask),
            depth=changes.get("depth", self.depth),
            target=changes.get("target", fx.target),
            levels=changes.get("levels", self.levels),
            # The patch's own crossover, never the default it resolved to: an
            # unset one stays unset until the slider sets it.
            bass_hz=changes.get("bass_hz", fx.bass_hz),
        )
        self._store_due = self._clock() + STORE_WRITE_DELAY_S

    def flush_store(self, now: float | None = None) -> bool:
        """Write the loaded patch's setup if a change is due (or now, with no
        `now`). True when it wrote."""
        if self._store_due is None:
            return False
        if now is not None and now < self._store_due:
            return False
        self._store_due = None
        if self._store is None or self._patch_ref is None:
            return False
        name, path, stable_key = self._patch_ref
        try:
            self._store.set(name, self.patch_fx, patch_path=path, stable_key=stable_key)
        except OSError as exc:
            print(f"Warning: could not save the patch's effect setup ({exc})")
            return False
        return True

    # -- patch loads and switches -------------------------------------------

    def on_patch_loaded(self, patch_path, *, patch_name: str | None = None, stable_key: str | None = None) -> None:
        """Resolve the new patch: its own values first, then the global ones.

        Both faders show the resolved values at once: the Effect fader the
        patch's depth, the Patch mod fader what Surge has.
        """
        self.flush_store()
        self.uses_mod_wheel = patch_uses_mod_wheel(patch_path)
        self.patch_loaded = True
        path = None if patch_path is None else str(patch_path)
        name = patch_name or (Path(path).stem if path else "")
        self._patch_ref = (name, path, stable_key) if name else None
        self.patch_fx = PatchFx()
        if self.enabled and self._store is not None and self._patch_ref is not None:
            self.patch_fx = self._store.get(name, patch_path=path, stable_key=stable_key)
        self.config = self._resolve()
        self.target = self.config.mod_wheel_target
        self._request_depth(self.config.depth)
        self._route_changed()

    def set_target(self, target: str) -> None:
        """Play-screen switch: the loaded patch's own target, kept with it.

        Only where the knob goes changes. The effects keep their depth and
        Surge keeps its mod-wheel value.
        """
        self.target = normalize_target(target)
        self._patch_changed(target=self.target)
        self._route_changed()

    def set_global_default(self, target: str, *, persist: bool = True) -> None:
        """Settings: the default for every load, applied to the loaded patch now
        unless it has its own target."""
        self.global_target = normalize_target(target)
        if persist:
            from patch_browser.ui_prefs import read_ui_prefs_file, write_ui_prefs_file

            data = read_ui_prefs_file()
            data[MOD_WHEEL_PREF_KEY] = self.global_target
            write_ui_prefs_file(data)
        self.config = self._resolve()
        self.target = self.config.mod_wheel_target
        self._route_changed()

    def set_mask(self, mask: int) -> None:
        """The loaded patch's enable mask (stored with it). Writes no global
        default: there is none for effect state. Moves no depth. Inert when
        the FX host is off."""
        if not self.enabled:
            return
        self._patch_changed(mask=normalize_mask(mask))
        self.config = FxConfig(
            mask=normalize_mask(mask),
            mod_wheel_target=self.config.mod_wheel_target,
            depth=self.config.depth,
            levels=self.config.levels,
            bass_hz=self.config.bass_hz,
        )
        self.publish()

    def toggle_effect(self, effect_id: int) -> None:
        """Picker row: switch one effect on or off for the loaded patch.

        Only the mask moves, and only for this patch: the depth in force, the
        mod-wheel target and so the route stamp stay as they are.
        """
        if not self.enabled or isinstance(effect_id, bool) or effect_id not in fx_host_protocol.EFFECT_IDS:
            return
        self.set_mask(fx_host_protocol.toggled(self.mask, effect_id))

    def set_level(self, effect_id: int, level: float, *, final: bool = False) -> None:
        """Picker slider: one effect's level for the loaded patch (stored with
        it). Only that level moves: the mask, the depth and the route stamp
        stay. While dragging, the route file is written at most every
        DEPTH_PUBLISH_S, as the Effect fader's requests are; `final` (release)
        always writes, so the last position lands. Inert when the FX host is
        off, for an id this build does not ship, and for an effect that is not
        enabled: its slider is greyed out, and it keeps the level it has.
        """
        if not self.enabled or isinstance(effect_id, bool) or effect_id not in fx_host_protocol.EFFECT_IDS:
            return
        if not fx_host_protocol.is_enabled(self.mask, effect_id):
            return
        try:
            levels = fx_host_protocol.with_level(self.levels, effect_id, level)
        except ValueError:
            return
        self.dragging_level = not final
        if levels != self.levels:
            self._patch_changed(levels=levels)
            self.config = FxConfig(
                mask=self.config.mask,
                mod_wheel_target=self.config.mod_wheel_target,
                depth=self.config.depth,
                levels=levels,
                bass_hz=self.config.bass_hz,
            )
            self._level_unsent = True
        now = self._clock()
        if self._level_unsent and (final or now >= self._next_level_write):
            self._next_level_write = now + DEPTH_PUBLISH_S
            self._level_unsent = False
            self.publish()

    def set_bass(self, hz: float, *, final: bool = False) -> None:
        """Header slider: the bass bypass's crossover for the loaded patch
        (stored with it), BASS_OFF or a value in BASS_MIN_HZ..BASS_MAX_HZ.
        Only the crossover moves. Throttled while dragging and always written
        on release, as `set_level` is, and inert where it is: the FX host off,
        no effect ticked (`bass_locked`), or a value that is not a crossover.
        """
        if not self.enabled or self.bass_locked:
            return
        value = fx_host_protocol.bass_value(hz)
        if value is None:
            return
        self.dragging_bass = not final
        if value != self.bass_hz:
            self._patch_changed(bass_hz=value)
            self.config = FxConfig(
                mask=self.config.mask,
                mod_wheel_target=self.config.mod_wheel_target,
                depth=self.config.depth,
                levels=self.config.levels,
                bass_hz=value,
            )
            self._bass_unsent = True
        now = self._clock()
        if self._bass_unsent and (final or now >= self._next_bass_write):
            self._next_bass_write = now + DEPTH_PUBLISH_S
            self._bass_unsent = False
            self.publish()

    def finish_bass(self) -> None:
        """Slider released: write the last crossover if a throttle held it back."""
        self.dragging_bass = False
        if self._bass_unsent:
            self._bass_unsent = False
            self.publish()

    def finish_level(self) -> None:
        """Slider released: write the last level if a throttle held it back."""
        self.dragging_level = False
        if self._level_unsent:
            self._level_unsent = False
            self.publish()

    def _route_changed(self) -> None:
        self._route_stamp = self._next_stamp()
        self.publish()

    def _request_depth(self, depth: float) -> None:
        """Ask the remapper for a depth (with the next publish). Not a user move."""
        self.depth = max(0.0, min(1.0, float(depth)))
        if not self.enabled:
            return
        self._depth_request = self.depth
        self._depth_stamp = self._next_stamp()
        self._depth_requested_at = self._clock()
        self._depth_unsent = False

    # -- the faders ---------------------------------------------------------

    def set_depth(self, depth: float, *, final: bool = False) -> None:
        """Effect fader: request a depth. The remapper applies it.

        While dragging, requests are written at most every DEPTH_PUBLISH_S;
        `final` (release, reset) always writes, so the last position lands.
        """
        self.depth = max(0.0, min(1.0, float(depth)))
        if not self.enabled:
            return
        self._depth_request = self.depth
        self._depth_stamp = self._next_stamp()
        self.dragging_depth = not final
        now = self._clock()
        self._depth_requested_at = now
        self._patch_changed(depth=self.depth)
        if final or now >= self._next_depth_write:
            self._next_depth_write = now + DEPTH_PUBLISH_S
            self._depth_unsent = False
            self.publish()
        else:
            self._depth_unsent = True

    def finish_depth(self) -> None:
        """Fader released: write the last request if a throttle held it back."""
        self.dragging_depth = False
        if self._depth_unsent and self._depth_request is not None:
            self._depth_unsent = False
            self.publish()

    def poll_levels(self, now: float | None = None) -> bool:
        """Follow what the knob set: the depth in force and Surge's mod wheel.

        True when either fader moved. Cost: one stat() per LEVELS_POLL_S
        (10/s) on the UI loop, a read only when the remapper rewrote the file,
        which it does at most 20/s and only while something is moving. A held
        fader is never moved, so the file cannot pull it from under the finger.
        Also writes the per-patch store once a change is due.
        """
        if not self.enabled:
            return False
        stamp = self._clock() if now is None else now
        self.flush_store(stamp)
        if self.dragging_depth and self.dragging_patch_mod:
            return False
        if stamp < self._next_levels_poll:
            return False
        self._next_levels_poll = stamp + LEVELS_POLL_S
        path = self._levels_path or levels_state_path()
        file_id = _file_identity(path)
        if file_id is None or file_id == self._levels_file_id:
            return False
        self._levels_file_id = file_id
        levels = read_levels(path)
        moved = False
        if not self.dragging_depth:
            moved = self._follow_depth(levels) or moved
        if not self.dragging_patch_mod:
            moved = self._follow_patch_mod(levels) or moved
        return moved

    def _follow_depth(self, levels: Levels) -> bool:
        if levels.depth is None:
            return False
        # A fader echo older than our latest request is stale: the newer one
        # is on its way. A knob move older than that request lost to it, which
        # also covers a depth re-read because the file moved for patch mod.
        if levels.source == DEPTH_FROM_FADER and levels.request != self._depth_stamp:
            return False
        if (
            levels.source == DEPTH_FROM_KNOB
            and levels.depth_at is not None
            and self._depth_requested_at is not None
            and levels.depth_at <= self._depth_requested_at
        ):
            return False
        if abs(levels.depth - self.depth) <= SEND_TOLERANCE:
            return False
        self.depth = levels.depth
        if levels.source == DEPTH_FROM_KNOB:
            # The knob's depth is now the request in force too (same stamp,
            # so the remapper that set it does not re-apply it). Otherwise the
            # route file keeps the last fader or load request, and every later
            # publish (a flip, a toggle) carries that stale depth to a router
            # that has not seen its stamp: a restarted remapper (#142 round 5).
            if self._depth_request is not None:
                self._depth_request = self.depth
            self._patch_changed(depth=self.depth)
        return True

    def _follow_patch_mod(self, levels: Levels) -> bool:
        if levels.patch_mod is None or levels.patch_mod_at == self._patch_mod_seen_at:
            return False
        self._patch_mod_seen_at = levels.patch_mod_at
        # The fader moved after the knob did: Surge has the fader's value.
        if self._patch_mod_set_at is not None and levels.patch_mod_at <= self._patch_mod_set_at:
            return False
        self.patch_mod_known = True
        if levels.patch_mod == self.patch_mod:
            return False
        self.patch_mod = levels.patch_mod
        return True

    def set_patch_mod(self, value: float, *, final: bool = True) -> tuple[str, list[float]]:
        """Patch mod fader: the OSC message that sets Surge's mod wheel.

        Sent over Surge's own `/cc` (channel, controller, value; floats), so it
        reaches the patch the same way a hardware wheel on the master channel
        would. It never touches the FX, whatever the target. `final` is False
        while the fader is held. The value also goes into the route file, so
        the remapper knows what the knob has to pick up.
        """
        self.patch_mod = max(0, min(CC_MAX, int(round(float(value)))))
        self.patch_mod_known = True
        now = self._clock()
        self._patch_mod_set_at = now
        self.dragging_patch_mod = not final
        if self.enabled:
            self._patch_mod_request = self.patch_mod
            self._patch_mod_stamp = self._next_stamp()
            if final or now >= self._next_patch_mod_write:
                self._next_patch_mod_write = now + DEPTH_PUBLISH_S
                self._patch_mod_unsent = False
                self.publish()
            else:
                self._patch_mod_unsent = True
        return "/cc", [0.0, float(CC_MOD_WHEEL), float(self.patch_mod)]

    def finish_patch_mod(self) -> None:
        """Patch mod fader released: the knob may move it again."""
        self.dragging_patch_mod = False
        if self._patch_mod_unsent:
            self._patch_mod_unsent = False
            self.publish()

    def publish(self) -> None:
        if not self.enabled:
            return
        path = self._route_path or route_state_path()
        try:
            atomic_write_json(
                path,
                {
                    "cc1": self.destination,
                    "mask": self.mask,
                    "levels": list(self.levels),
                    "bass_hz": self.bass_hz,
                    "target": self.target,
                    "uses_mod_wheel": self.uses_mod_wheel,
                    "route_stamp": self._route_stamp,
                    "depth_request": self._depth_request,
                    "depth_stamp": self._depth_stamp,
                    "patch_mod": self._patch_mod_request,
                    "patch_mod_stamp": self._patch_mod_stamp,
                    "patch_mod_at": self._patch_mod_set_at if self._patch_mod_request is not None else None,
                },
            )
        except OSError as exc:
            print(f"Warning: could not publish FX route ({exc})")


#: The one key the mod knob has in `Cc1Router`'s pickup.
_KNOB = "cc1"


class Cc1Router:
    """The remapper's half, and the only process that talks to the FX host.

    It applies the published route to incoming CC 1, applies the fader's depth
    requests, the enable mask, the per-effect levels and the bass bypass's
    crossover, and publishes the
    depth in force for the UI. Depth has one owner
    here: the knob and the fader are two inputs to it, last one wins, and
    nothing else sends to UDP 9958. It also publishes the last CC 1 value it
    sent Surge, so the Patch mod fader follows the knob.

    **Pickup.** The knob has no motor, so its position is not the value it
    drives. It uses the scaling law (`fader_pickup.scaled`, the one
    `loop_mix` uses): each move changes the value in force by the fraction of
    the remaining knob travel it covered, toward the end it is heading for.
    It responds at once, never jumps, and meets the knob at either end. When
    the value moves without the knob (a patch load, an Effect fader request,
    a Patch mod fader move) or the knob changes target (a flip), the next move
    scales from the new value: that is the re-arm. The knob's position stays
    known across all of them (every master CC 1 passes through here), so
    only its very first message since this process started is an anchor and
    nothing else; routed to Surge, that one is swallowed. Messages forwarded to
    Surge carry the scaled value. With no value known yet (nothing published
    since this process started) the knob's position is the value.

    **A route change moves nothing.** The effects keep their depth and Surge
    keeps its mod-wheel value; only where the next knob message goes changes.
    """

    def __init__(
        self,
        *,
        sender: FxHostSender | None = None,
        route_path: Path | None = None,
        levels_path: Path | None = None,
        clock=time.monotonic,
    ) -> None:
        self._sender = sender or FxHostSender()
        self._path = route_path or route_state_path()
        self._levels_path = levels_path or levels_state_path()
        #: Must be the UI's clock: moves from both processes are compared.
        self._clock = clock
        self._file_id: tuple[int, int] | None = None
        self._next_refresh = 0.0
        self._route_stamp: str | None = None
        self._depth_stamp: str | None = None
        self._patch_mod_stamp: str | None = None
        self._levels: dict = {}
        self._levels_dirty = False
        self._pickup = Pickup()
        self.destination = CC1_TO_SURGE
        self.mask = fx_host_protocol.MASK_NONE
        self.levels: tuple[float, ...] = fx_host_protocol.LEVELS_DEFAULT
        self.bass_hz: float = fx_host_protocol.BASS_DEFAULT_HZ
        self.depth: float | None = None
        self.last_cc: int | None = None
        #: Surge's mod-wheel value as far as this process knows: the last CC 1
        #: it forwarded, or the Patch mod fader's value from the route file.
        self.patch_mod: int | None = None
        #: The same value before rounding to a CC, so slow knob moves
        #: accumulate instead of rounding away (as `loop_mix._pickup_ref`).
        self._surge_ref: float | None = None

    @classmethod
    def from_env(cls) -> "Cc1Router | None":
        if not fx_host_protocol.enabled():
            return None
        return cls()

    def refresh(self, now: float | None = None) -> None:
        """Called from the remapper's drain loop; does work every ROUTE_REFRESH_S.

        Per tick: one stat() of the route file (read only when it was
        rewritten) and at most one write of the levels file (only when the
        depth or the patch mod value moved since the last tick). 20 ticks/s.
        """
        stamp = self._clock() if now is None else now
        if stamp < self._next_refresh:
            return
        self._next_refresh = stamp + ROUTE_REFRESH_S
        self._read_route()
        self._flush_levels()

    def _read_route(self) -> None:
        file_id = _file_identity(self._path)
        if file_id is None or file_id == self._file_id:
            return
        self._file_id = file_id
        route = read_route(self._path)
        chain_changed = (
            route.mask != self.mask or route.levels != self.levels or route.bass_hz != self.bass_hz
        )
        self.destination = route.destination
        self.mask = route.mask
        self.levels = route.levels
        self.bass_hz = route.bass_hz
        # A route change (patch load, target flip) moves nothing here: the
        # knob is where it was, and its next move scales from whatever value
        # it now drives.
        self._route_stamp = route.route_stamp
        if route.patch_mod_stamp is not None and route.patch_mod_stamp != self._patch_mod_stamp:
            self._patch_mod_stamp = route.patch_mod_stamp
            # Both reach Surge on their own path, so the later move is what
            # Surge has: a knob message forwarded after this fader move wins
            # (a tie goes to the fader, as in `FxPolicy._follow_patch_mod`).
            forwarded_at = self._levels.get("patch_mod_at")
            if route.patch_mod is not None and (
                forwarded_at is None or route.patch_mod_at is None or route.patch_mod_at >= forwarded_at
            ):
                # Pickup re-arm: the knob's next move scales from this value.
                self.patch_mod = route.patch_mod
                self._surge_ref = float(route.patch_mod)
        if route.depth_stamp is not None and route.depth_stamp != self._depth_stamp:
            self._depth_stamp = route.depth_stamp
            if route.depth_request is not None:
                # Pickup re-arm: the knob's next move scales from this depth.
                self._set_depth(route.depth_request, DEPTH_FROM_FADER, route.depth_stamp)
        elif chain_changed and self.depth is not None:
            self._sender.send(self.mask, self.depth, self.levels, self.bass_hz)

    def _set_depth(self, depth: float, source: str, request: str | None = None) -> None:
        self.depth = max(0.0, min(1.0, float(depth)))
        self._sender.send(self.mask, self.depth, self.levels, self.bass_hz)
        self._levels.update(depth=self.depth, source=source, request=request, depth_at=self._clock())
        self._levels_dirty = True

    def _note_patch_mod(self, value: int) -> None:
        # Not deduplicated by value: the Patch mod fader may have moved Surge
        # since, so a repeat of the same value is still a newer move.
        self.patch_mod = value
        self._levels.update(patch_mod=value, patch_mod_at=self._clock())
        self._levels_dirty = True

    def _flush_levels(self) -> None:
        if not self._levels_dirty:
            return
        self._levels_dirty = False
        try:
            atomic_write_json(self._levels_path, dict(self._levels))
        except OSError as exc:
            print(f"Warning: could not publish FX levels ({exc})", flush=True)

    def _knob(self, value: int, in_force: float | None) -> float | None:
        """Pickup: what the knob at `value` sets the value `in_force` to (CC
        units), or None when this message only anchored it.

        Scaling law (`fader_pickup.scaled`) from the value in force. With no
        value known yet (nothing published since this process started), the
        knob's position is the value.
        """
        if in_force is None:
            self._pickup.moved(_KNOB, value)
            return float(value)
        return self._pickup.follow(_KNOB, value, in_force)

    def consume(self, raw, source=None) -> bool:
        """True when the message must not reach Surge: taken for the effect,
        or swallowed because it only anchored the knob.

        A message for Surge is forwarded with its value rewritten in place
        (`raw[2]`) to the scaled value, so Surge never jumps to the knob.
        """
        if not is_master_mod_wheel(raw):
            return False
        self.last_cc = int(raw[2])
        if route_cc1(raw, self.destination) is None:
            level = self._knob(self.last_cc, self._surge_ref)
            if level is None:
                return True
            self._surge_ref = level
            raw[2] = int(round(level))
            # Forwarded to Surge: the Patch mod fader follows it.
            self._note_patch_mod(raw[2])
            return False
        if effect_inert(self.mask):
            # No effect ticked: nothing to drive, and the depth is left as
            # stored. Only where the knob is gets recorded, so the first move
            # after an effect is ticked scales from the depth, not a jump.
            self._pickup.moved(_KNOB, self.last_cc)
            return True
        level = self._knob(self.last_cc, None if self.depth is None else self.depth * CC_MAX)
        if level is not None and (self.depth is None or level / CC_MAX != self.depth):
            self._set_depth(level / CC_MAX, DEPTH_FROM_KNOB)
        return True
