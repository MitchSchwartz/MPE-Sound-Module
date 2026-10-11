"""What the FX host plays and where the mod wheel goes, decided here only.

`native/mpe-fx-host` holds no policy: it is told an effect and a depth and
applies them. This module is the policy, the same split as
`scripts/sooperlooper/live_monitor.py` and `native/mpe-live-monitor`. It is the
only place that decides:

* which effect is active, and at what depth;
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
it owns `Cc1Router`, the one process that talks to the FX host: it reads that
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

**Per-patch overrides come later.** `resolve_config` already takes the
patch's own value ahead of the global default (the `get_effective_*` order of
the sidecar stores); nothing supplies a patch value yet.
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
from patch_browser.json_store import atomic_write_json, read_json_dict

MOD_WHEEL_PATCH_FIRST = "patch_first"
MOD_WHEEL_EFFECT = "effect"
MOD_WHEEL_TARGETS = (MOD_WHEEL_PATCH_FIRST, MOD_WHEEL_EFFECT)
DEFAULT_MOD_WHEEL_TARGET = MOD_WHEEL_PATCH_FIRST
#: Key in the touch UI prefs file (`ui_prefs.UI_STATE_FILE`).
MOD_WHEEL_PREF_KEY = "mod_wheel_target"

CC1_TO_SURGE = "surge"
CC1_TO_FX = "fx"

#: ms_modwheel in Surge's ModulationSource.h.
MS_MODWHEEL = 6
#: Control change on MIDI channel 1: the lower-zone master channel.
MASTER_CONTROL_CHANGE = 0xB0
CC_MOD_WHEEL = 1
CC_MAX = 127

#: UI -> remapper: CC 1 destination, effect, and the Effect fader's request.
ROUTE_FILE_NAME = "fx-route.json"
#: Remapper -> UI: the depth in force (knob or fader) and the last CC 1 sent to Surge.
LEVELS_FILE_NAME = "fx-levels.json"
DEPTH_FROM_KNOB = "knob"
DEPTH_FROM_FADER = "fader"
#: Fader requests while dragging are written at most this often.
DEPTH_PUBLISH_S = 0.05
#: How often the UI checks the levels file for a knob move.
LEVELS_POLL_S = 0.1

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


#: The two faders the knob can drive. `mixer_controls` labels them with these,
#: so the play-screen switch names exactly the fader the knob moves.
PATCH_MOD_FADER_LABEL = "Patch mod"
EFFECT_FADER_LABEL = "Effect"

MOD_KNOB_LABELS = {
    CC1_TO_SURGE: f"Mod knob \u2192 {PATCH_MOD_FADER_LABEL}",
    CC1_TO_FX: f"Mod knob \u2192 {EFFECT_FADER_LABEL}",
}


def mod_knob_label(destination: str) -> str:
    """The play-screen switch's label: where the knob goes now, not the setting."""
    return MOD_KNOB_LABELS.get(destination, MOD_KNOB_LABELS[CC1_TO_SURGE])


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

    effect_id: int = fx_host_protocol.EFFECT_CHORUS
    mod_wheel_target: str = DEFAULT_MOD_WHEEL_TARGET


def resolve_config(*, global_target: str, patch_config: FxConfig | None = None) -> FxConfig:
    """The patch's own config if it has one, else the global default."""
    if patch_config is not None:
        return patch_config
    return FxConfig(mod_wheel_target=normalize_target(global_target))


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
    effect_id: int = fx_host_protocol.EFFECT_CHORUS
    route_stamp: str | None = None
    depth_request: float | None = None
    depth_stamp: str | None = None


def _valid_effect(value) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, int)
        and value in fx_host_protocol.EFFECT_IDS
    )


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
    effect_id = data.get("effect")
    return Route(
        destination=destination if destination in (CC1_TO_SURGE, CC1_TO_FX) else CC1_TO_SURGE,
        effect_id=effect_id if _valid_effect(effect_id) else fx_host_protocol.EFFECT_CHORUS,
        route_stamp=_stamp(data.get("route_stamp")),
        depth_request=_depth_value(data.get("depth_request")),
        depth_stamp=_stamp(data.get("depth_stamp")),
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
    """Fire-and-forget effect + depth at the FX host, deduplicated.

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
        self._last: tuple[int, float] | None = None
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

    def send(self, effect_id: int, depth: float) -> bool:
        try:
            payload = fx_host_protocol.encode(effect_id, depth)
        except ValueError as exc:
            self._say(f"fx host: not sending ({exc})")
            return False
        value = max(0.0, min(1.0, float(depth)))
        with self._lock:
            last = self._last
            if last is not None and last[0] == effect_id and abs(last[1] - value) <= SEND_TOLERANCE:
                return False
            if self._send is not None:
                self._send(payload)
            else:
                sock = self._socket()
                if sock is None:
                    return False
                try:
                    sock.send(payload)
                except OSError as exc:
                    self._note_refusal(exc)
                    return False
            self._last = (effect_id, value)
            self.delivered += 1
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
      load, and changing it re-resolves the loaded patch at once, exactly as if
      the patch had been loaded again;
    * the play-screen switch sets the target for the loaded patch only. The
      next patch load replaces it with the resolved config.

    The Effect fader is a request, not a level: the remapper applies it,
    and publishes the depth actually in force (fader or knob, whichever moved
    last) to the levels file, which `poll_levels` reads back. The Patch mod
    fader sends to Surge itself; `poll_levels` moves it when the knob, routed
    to Surge, sent a later value.
    """

    def __init__(
        self,
        *,
        enabled: bool | None = None,
        route_path: Path | None = None,
        levels_path: Path | None = None,
        global_target: str | None = None,
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
        self.config = resolve_config(global_target=self.global_target)
        self.target = self.config.mod_wheel_target
        self.uses_mod_wheel: bool | None = None
        self.patch_loaded = False
        self.depth = 0.0
        self.patch_mod = 0
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
        self._patch_mod_set_at: float | None = None
        self._patch_mod_seen_at: float | None = None
        self._levels_file_id: tuple[int, int] | None = None
        self._next_levels_poll = 0.0
        self.dragging_depth = False
        self.dragging_patch_mod = False

    def _next_stamp(self) -> str:
        self._seq += 1
        return f"{os.getpid()}-{self._seq}"

    @property
    def destination(self) -> str:
        return cc1_destination(self.target, self.uses_mod_wheel)

    @property
    def knob_label(self) -> str:
        return mod_knob_label(self.destination)

    def on_patch_loaded(self, patch_path) -> None:
        self.uses_mod_wheel = patch_uses_mod_wheel(patch_path)
        self.patch_loaded = True
        self.config = resolve_config(global_target=self.global_target)
        self.target = self.config.mod_wheel_target
        self._route_changed()

    def set_target(self, target: str) -> None:
        """Play-screen switch: the loaded patch only."""
        self.target = normalize_target(target)
        self._route_changed()

    def set_global_default(self, target: str, *, persist: bool = True) -> None:
        """Settings: the default for every load, applied to the loaded patch now."""
        self.global_target = normalize_target(target)
        if persist:
            from patch_browser.ui_prefs import read_ui_prefs_file, write_ui_prefs_file

            data = read_ui_prefs_file()
            data[MOD_WHEEL_PREF_KEY] = self.global_target
            write_ui_prefs_file(data)
        self.config = resolve_config(global_target=self.global_target)
        self.target = self.config.mod_wheel_target
        self._route_changed()

    def _route_changed(self) -> None:
        self._route_stamp = self._next_stamp()
        self.publish()

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
        """
        if not self.enabled or (self.dragging_depth and self.dragging_patch_mod):
            return False
        stamp = self._clock() if now is None else now
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
        return True

    def _follow_patch_mod(self, levels: Levels) -> bool:
        if levels.patch_mod is None or levels.patch_mod_at == self._patch_mod_seen_at:
            return False
        self._patch_mod_seen_at = levels.patch_mod_at
        # The fader moved after the knob did: Surge has the fader's value.
        if self._patch_mod_set_at is not None and levels.patch_mod_at <= self._patch_mod_set_at:
            return False
        if levels.patch_mod == self.patch_mod:
            return False
        self.patch_mod = levels.patch_mod
        return True

    def set_patch_mod(self, value: float, *, final: bool = True) -> tuple[str, list[float]]:
        """Patch mod fader: the OSC message that sets Surge's mod wheel.

        Sent over Surge's own `/cc` (channel, controller, value; floats), so it
        reaches the patch the same way a hardware wheel on the master channel
        would. It never touches the FX, whatever the target. `final` is False
        while the fader is held.
        """
        self.patch_mod = max(0, min(CC_MAX, int(round(float(value)))))
        self._patch_mod_set_at = self._clock()
        self.dragging_patch_mod = not final
        return "/cc", [0.0, float(CC_MOD_WHEEL), float(self.patch_mod)]

    def finish_patch_mod(self) -> None:
        """Patch mod fader released: the knob may move it again."""
        self.dragging_patch_mod = False

    def publish(self) -> None:
        if not self.enabled:
            return
        path = self._route_path or route_state_path()
        try:
            atomic_write_json(
                path,
                {
                    "cc1": self.destination,
                    "effect": self.config.effect_id,
                    "target": self.target,
                    "uses_mod_wheel": self.uses_mod_wheel,
                    "route_stamp": self._route_stamp,
                    "depth_request": self._depth_request,
                    "depth_stamp": self._depth_stamp,
                },
            )
        except OSError as exc:
            print(f"Warning: could not publish FX route ({exc})")


class Cc1Router:
    """The remapper's half, and the only process that talks to the FX host.

    It applies the published route to incoming CC 1, applies the fader's depth
    requests, and publishes the depth in force for the UI. Depth has one owner
    here: the knob and the fader are two inputs to it, last one wins, and
    nothing else sends to UDP 9958. It also publishes the last CC 1 value it
    sent Surge (forwarded or replayed), so the Patch mod fader follows the knob.

    It remembers the last mod-wheel message and the source it came from, even
    while CC 1 is going to Surge. When a route change hands the wheel to the
    effect, the depth moves to where the knob is (the FX host smooths the
    jump). When a route change leaves the wheel with the patch, that message
    is replayed to Surge through its own source's transform, so the patch's
    mod state matches the knob: on the switch, the setting, and every load.
    """

    def __init__(
        self,
        *,
        sender: FxHostSender | None = None,
        route_path: Path | None = None,
        levels_path: Path | None = None,
        forward=None,
        clock=time.monotonic,
    ) -> None:
        self._sender = sender or FxHostSender()
        self._path = route_path or route_state_path()
        self._levels_path = levels_path or levels_state_path()
        #: forward(source, raw): emit `raw` to Surge as if it came from `source`.
        #: Returns True when it sent, False when it could not (that device is gone).
        self._forward = forward
        #: Must be the UI's clock: moves from both processes are compared.
        self._clock = clock
        self._file_id: tuple[int, int] | None = None
        self._next_refresh = 0.0
        self._route_stamp: str | None = None
        self._depth_stamp: str | None = None
        self._levels: dict = {}
        self._levels_dirty = False
        self.destination = CC1_TO_SURGE
        self.effect_id = fx_host_protocol.EFFECT_CHORUS
        self.depth: float | None = None
        self.last_cc: int | None = None
        self._last_cc_source = None
        #: The last CC 1 value sent to Surge, and when.
        self.patch_mod: int | None = None

    @classmethod
    def from_env(cls, *, forward=None) -> "Cc1Router | None":
        if not fx_host_protocol.enabled():
            return None
        return cls(forward=forward)

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
        previous = self.destination
        effect_changed = route.effect_id != self.effect_id
        self.destination = route.destination
        self.effect_id = route.effect_id
        if route.route_stamp != self._route_stamp:
            self._route_stamp = route.route_stamp
            if self.last_cc is not None:
                if self.destination == CC1_TO_FX and previous != CC1_TO_FX:
                    self._set_depth(depth_from_cc(self.last_cc), DEPTH_FROM_KNOB)
                elif self.destination == CC1_TO_SURGE and self._forward is not None:
                    sent = self._forward(
                        self._last_cc_source,
                        [MASTER_CONTROL_CHANGE, CC_MOD_WHEEL, self.last_cc],
                    )
                    if sent:
                        self._note_patch_mod(self.last_cc)
        if route.depth_stamp is not None and route.depth_stamp != self._depth_stamp:
            self._depth_stamp = route.depth_stamp
            if route.depth_request is not None:
                self._set_depth(route.depth_request, DEPTH_FROM_FADER, route.depth_stamp)
        elif effect_changed and self.depth is not None:
            self._sender.send(self.effect_id, self.depth)

    def _set_depth(self, depth: float, source: str, request: str | None = None) -> None:
        self.depth = max(0.0, min(1.0, float(depth)))
        self._sender.send(self.effect_id, self.depth)
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

    def consume(self, raw, source=None) -> bool:
        """True when the message was taken for the effect and must not reach Surge."""
        if not is_master_mod_wheel(raw):
            return False
        self.last_cc = int(raw[2])
        self._last_cc_source = source
        depth = route_cc1(raw, self.destination)
        if depth is None:
            # Forwarded to Surge: the Patch mod fader follows it.
            self._note_patch_mod(self.last_cc)
            return False
        self._set_depth(depth, DEPTH_FROM_KNOB)
        return True
