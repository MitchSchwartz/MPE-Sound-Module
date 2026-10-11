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
publishes the resulting CC 1 destination to `fx-route.json` in the run dir.
The remapper process sees every controller message, so it owns `Cc1Router`:
it reads that file when its mtime moves and asks `route_cc1` what to do with
each message. Neither process decides anything the other decides.

**Off means off.** With MPE_FX_HOST unset, neither object is active: no file,
no socket, and the remapper forwards CC 1 to Surge exactly as before.

**Per-patch overrides come later.** `resolve_config` already takes the
patch's own value ahead of the global default (the `get_effective_*` order of
the sidecar stores); nothing supplies a patch value yet.
"""

from __future__ import annotations

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

ROUTE_FILE_NAME = "fx-route.json"

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


def route_state_path() -> Path:
    from patch_browser.mpe_run_dir import run_dir

    return run_dir() / ROUTE_FILE_NAME


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


def read_route(path: Path) -> tuple[str, int]:
    """(destination, effect id) from the route file. Surge when unknown."""
    data = read_json_dict(path)
    destination = data.get("cc1")
    if destination not in (CC1_TO_SURGE, CC1_TO_FX):
        destination = CC1_TO_SURGE
    effect_id = data.get("effect")
    if (
        isinstance(effect_id, bool)
        or not isinstance(effect_id, int)
        or effect_id not in fx_host_protocol.EFFECT_IDS
    ):
        effect_id = fx_host_protocol.EFFECT_CHORUS
    return destination, effect_id


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
    """The touch UI's half: resolves each patch load and publishes the route.

    Two controls set the mod-wheel target and they do not compete:

    * the Settings value is the global default. It is applied at every patch
      load, and changing it re-resolves the loaded patch at once, exactly as if
      the patch had been loaded again;
    * the play-screen switch sets the target for the loaded patch only. The
      next patch load replaces it with the resolved config.
    """

    def __init__(
        self,
        *,
        enabled: bool | None = None,
        sender: FxHostSender | None = None,
        route_path: Path | None = None,
        global_target: str | None = None,
    ) -> None:
        self.enabled = fx_host_protocol.enabled() if enabled is None else bool(enabled)
        self._sender = sender
        self._route_path = route_path
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

    @property
    def destination(self) -> str:
        return cc1_destination(self.target, self.uses_mod_wheel)

    def on_patch_loaded(self, patch_path) -> None:
        self.uses_mod_wheel = patch_uses_mod_wheel(patch_path)
        self.patch_loaded = True
        self.config = resolve_config(global_target=self.global_target)
        self.target = self.config.mod_wheel_target
        self.publish()

    def set_target(self, target: str) -> None:
        """Play-screen switch: the loaded patch only."""
        self.target = normalize_target(target)
        self.publish()

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
        self.publish()

    def set_depth(self, depth: float) -> None:
        """Effect fader."""
        self.depth = max(0.0, min(1.0, float(depth)))
        if not self.enabled:
            return
        if self._sender is None:
            self._sender = FxHostSender()
        self._sender.send(self.config.effect_id, self.depth)

    def set_patch_mod(self, value: float) -> tuple[str, list[float]]:
        """Patch mod fader: the OSC message that sets Surge's mod wheel.

        Sent over Surge's own `/cc` (channel, controller, value; floats), so it
        reaches the patch the same way a hardware wheel on the master channel
        would. It never touches the FX, whatever the target.
        """
        self.patch_mod = max(0, min(CC_MAX, int(round(float(value)))))
        return "/cc", [0.0, float(CC_MOD_WHEEL), float(self.patch_mod)]

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
                },
            )
        except OSError as exc:
            print(f"Warning: could not publish FX route ({exc})")


class Cc1Router:
    """The remapper's half: applies the published route to incoming CC 1.

    Remembers the last mod-wheel position even while CC 1 is going to Surge,
    so when a patch load hands the wheel to the effect, the depth moves to
    where the knob already is. The FX host smooths the jump.
    """

    def __init__(self, *, sender: FxHostSender | None = None, route_path: Path | None = None, clock=time.monotonic) -> None:
        self._sender = sender or FxHostSender()
        self._path = route_path or route_state_path()
        self._clock = clock
        self._mtime = 0.0
        self._next_refresh = 0.0
        self.destination = CC1_TO_SURGE
        self.effect_id = fx_host_protocol.EFFECT_CHORUS
        self.last_cc: int | None = None

    @classmethod
    def from_env(cls) -> "Cc1Router | None":
        if not fx_host_protocol.enabled():
            return None
        return cls()

    def refresh(self, now: float | None = None) -> None:
        stamp = self._clock() if now is None else now
        if stamp < self._next_refresh:
            return
        self._next_refresh = stamp + ROUTE_REFRESH_S
        try:
            mtime = self._path.stat().st_mtime
        except OSError:
            return
        if mtime <= self._mtime:
            return
        self._mtime = mtime
        previous = self.destination
        self.destination, self.effect_id = read_route(self._path)
        if previous != CC1_TO_FX and self.destination == CC1_TO_FX and self.last_cc is not None:
            self._sender.send(self.effect_id, depth_from_cc(self.last_cc))

    def consume(self, raw) -> bool:
        """True when the message was taken for the effect and must not reach Surge."""
        if is_master_mod_wheel(raw):
            self.last_cc = int(raw[2])
        depth = route_cc1(raw, self.destination)
        if depth is None:
            return False
        self._sender.send(self.effect_id, depth)
        return True
