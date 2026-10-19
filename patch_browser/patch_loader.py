"""Load Surge patches via OSC, and send the trunk's stage gain (no GPIO / OLED dependencies).

Level is the stage gain in mpe-fx-host (`patch_normalization.stage_db`), sent
here and nowhere else as a `gain` datagram. Surge's amp/volume is never set:
patches play at their authored volume.
"""

from __future__ import annotations

import math
import os
import socket
import time
from pathlib import Path

from patch_browser import fx_host_protocol
from patch_browser.fx_policy import FxPolicy
from patch_browser.patch_fx import PatchFxStore
from patch_browser.patch_hold import (
    PatchHoldStore,
    effective_aeg_value,
    iter_hold_osc_paths,
)
from patch_browser.patch_pressure import PatchPressureStore
from patch_browser.patch_normalization import (
    PatchNormalizationStore,
    db_to_linear,
    norm_at_ceiling,
    plausible_authored_volume,
    stage_db,
    volume_fader_trim_to_db,
)
from patch_browser.surge_playback import (
    effective_poly_on_load,
    ensure_reuse_single_patch,
    poly_ceiling,
    query_polylimit,
    reuse_single_enabled,
    send_polylimit,
    write_poly_state,
)
from patch_browser.surge_poly_governor import governor_active
from patch_browser.touch_ui_constants import VOLUME_MAX, VOLUME_MIN

OSC_OUT_PORT = 53270
OSC_QUERY_TIMEOUT_S = 0.08
# Waiting for Surge's /patch reply (the last patch it finished loading).
PATCH_QUERY_TIMEOUT_S = 0.25
PATCH_CONFIRM_POLL_S = 0.1
# A browse load waits this long for Surge to report the new patch before the
# stage raises to it. Past it the load goes on and the raise is held back (the
# keepalive asks again), so a slow load never plays the old patch at the new
# patch's boost. Replaces the blind 60 ms settle timer of the amp/volume days.
BROWSE_CONFIRM_TIMEOUT_S = 0.3
# The keepalive's /q/patch wait. Surge answers from memory in a few ms; it is
# asked only while a boost is in force or a raise is held, so a Surge that does
# not answer (crashed, restarting) costs the UI loop at most this once a second,
# and the boost comes off until it answers again.
KEEPALIVE_CONFIRM_TIMEOUT_S = 0.1


def same_patch_path(reported: str | None, expected: str) -> bool:
    """True when Surge's reported patch is the file that was asked for.

    The whole path decides, never the name: factory ``Basses/Bass 1`` and a
    third-party ``Bass 1`` share a stem and are different patches.
    """
    if not reported:
        return False
    return os.path.normpath(reported) == os.path.normpath(expected)
# The stage gain is re-sent this often (one sendto per second). mpe-fx-host
# restores its last gain itself after a restart (fx_gain_state.h); the resend is
# also how a boost comes off when Surge stops reporting the UI's patch.
STAGE_KEEPALIVE_S = 1.0
# Surge's amp/volume in each scene, read after each load: what the patch was
# authored at. Read only (tests/test_stage_gain_guard.py).
AUTHORED_VOLUME_PATHS = ("/param/a/amp/volume", "/param/b/amp/volume")


class UdpStageSender:
    """Sends one `gain` datagram to mpe-fx-host. A missing listener is not an error:
    with no insert running, the trunk plays as authored and the Vol fader has no
    effect (fx_host_protocol, "Without mpe-fx-host")."""

    def __init__(self, host: str = fx_host_protocol.DEFAULT_HOST, port: int | None = None):
        self.address = (host, fx_host_protocol.resolve_port() if port is None else port)
        self._sock: socket.socket | None = None

    def __call__(self, payload: bytes) -> bool:
        try:
            if self._sock is None:
                self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._sock.sendto(payload, self.address)
            return True
        except OSError:
            return False


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
        fx_policy: FxPolicy | None = None,
        fx_store: PatchFxStore | None = None,
        stage_sender=None,
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
        self.fx_policy = fx_policy or FxPolicy()
        # Each patch's effect setup; the policy reads it at load and writes it
        # back (debounced), resolved patch value first, then the global default.
        self.fx_store = fx_store or PatchFxStore()
        self.fx_policy.bind_store(self.fx_store)
        self.osc_host = osc_host
        self.osc_out_port = osc_out_port
        # The trim-mode Vol fader position (VOLUME_MIN..VOLUME_MAX).
        self.user_volume_trim = 1.0
        # Device mode's fixed cut, in dB; 0 in trim mode
        # (device_volume.DEVICE_MODE_SURGE_TRIM_DB).
        self._headroom_db = 0.0
        # The loaded patch's normalization: gain + trim, and its ceiling.
        self._norm_db = 0.0
        self._ceiling_db: float | None = None
        self._authored_volume: float | None = None
        self._authored_volume_b: float | None = None
        # Where the stage gain goes. None sends nothing: the default, so a
        # loader that is not the instrument's (calibration, tests) can never
        # move a live insert. The UI and the harness that loads like it pass
        # UdpStageSender() (tests/test_stage_gain_guard.py checks they do).
        self._stage_sender = stage_sender
        self._stage_sent_db: float | None = None
        self._stage_next_keepalive = 0.0
        # A browse load Surge has not yet reported: (path, normalize?, the
        # outgoing patch's (norm_db, ceiling_db)). Until it is confirmed the
        # stage plays the quieter of the two levels.
        self._pending_raise: tuple[str, bool, tuple[float, float | None]] | None = None
        # The path asked of Surge by the last load (the Reuse Single copy, as
        # Surge reports it), and whether Surge last said it has that patch. A
        # boost goes out only while it has: Surge can hold another patch without
        # this loader knowing (a harness load, Surge restarted by a crash or an
        # audio-profile switch), and a boost measured for one patch is wrong,
        # and can be loud, on any other.
        self._surge_path: str | None = None
        self._surge_confirmed = False
        self._boost_withheld = False
        self._loaded_patch_name: str | None = None
        self._loaded_patch_path: str | None = None
        self._loaded_stable_key: str | None = None
        self._native_poly_limit: int | None = None
        self._effective_poly_limit: int | None = None

    def set_volume(self, volume=1.0, *, headroom_db: float = 0.0):
        """Set the Vol fader position and device mode's headroom, and send the stage.

        Every call states `headroom_db`, so leaving device mode for trim mode
        returns it to 0.
        """
        self.user_volume_trim = float(volume)
        self._headroom_db = float(headroom_db)
        return self._send_stage()

    def _stage_db_for(self, norm_db: float, ceiling_db: float | None) -> float:
        return stage_db(
            norm_db=norm_db,
            ceiling_db=ceiling_db,
            headroom_db=self._headroom_db,
            vol_db=volume_fader_trim_to_db(
                self.user_volume_trim, fader_min=VOLUME_MIN, fader_max=VOLUME_MAX
            ),
        )

    @property
    def stage_db(self) -> float:
        """The stage gain now, in dB (-inf at mute)."""
        return self._stage_db_for(*self._effective_norm())

    @property
    def norm_at_ceiling(self) -> bool:
        """The loaded patch's gain + trim is being held at its peak ceiling."""
        return norm_at_ceiling(self._norm_db, self._ceiling_db)

    def _send_stage_db(self, db: float) -> bool:
        if self._stage_sender is None:
            return False
        # encode_gain clamps to GAIN_MAX_DB, as the client does.
        linear = 0.0 if db == -float("inf") else db_to_linear(db)
        self._stage_sent_db = db
        return bool(self._stage_sender(fx_host_protocol.encode_gain(linear)))

    def _effective_norm(self) -> tuple[float, float | None]:
        """The (norm_db, ceiling_db) the stage is playing: the loaded patch's, or
        while its load is unconfirmed, the quieter of it and the outgoing patch's."""
        pair = (self._norm_db, self._ceiling_db)
        if self._pending_raise is not None:
            held = self._pending_raise[2]
            if self._stage_db_for(*held) < self._stage_db_for(*pair):
                return held
        return pair

    def _quiet_cap_db(self) -> float:
        """The stage with no normalization: device headroom and the Vol fader only.
        What any patch may safely get: it plays at most as authored (D10)."""
        return self._stage_db_for(0.0, None)

    def _send_stage(self) -> bool:
        """Send the stage: in full while Surge last confirmed the loaded patch,
        otherwise without its boost (`_quiet_cap_db`); a cut always goes out."""
        full = self.stage_db
        if self._surge_confirmed:
            return self._send_stage_db(full)
        return self._send_stage_db(min(full, self._quiet_cap_db()))

    def _set_norm(self, patch_name: str, apply_normalization: bool) -> None:
        if apply_normalization:
            self._apply_patch_normalization(patch_name)
        else:
            self._norm_db, self._ceiling_db = 0.0, None

    def _settle_patch_level(self, patch_name: str, apply_normalization: bool) -> None:
        """The loaded patch is confirmed: read its authored volume (both scenes; an
        implausible reading is unknown) and take its level."""
        a, b = (plausible_authored_volume(self._query_osc_float(path))
                for path in AUTHORED_VOLUME_PATHS)
        self._authored_volume, self._authored_volume_b = a, b
        self._set_norm(patch_name, apply_normalization)

    def keep_stage_alive(self, now: float | None = None) -> None:
        """Re-send the stage gain once every STAGE_KEEPALIVE_S. Call from the UI loop.

        A boost is re-sent only for a patch Surge confirms on this tick (/q/patch
        reports the path the last load asked for). Unconfirmed, whether Surge
        reports another patch or does not answer, the stage goes out without its
        boost: min(stage, headroom + Vol), at most as authored. Sending nothing
        instead would leave the last boost on whatever Surge now holds (a
        restarted Surge's default patch, say), so it is the louder of the two.
        A stage with no boost needs no question: it is right for any patch.

        A raise held back by an unconfirmed load is released here, once Surge
        reports the patch.
        """
        now = time.monotonic() if now is None else now
        if now < self._stage_next_keepalive:
            return
        self._stage_next_keepalive = now + STAGE_KEEPALIVE_S
        if self._stage_sent_db is None:
            return
        if self._pending_raise is None and self.stage_db <= self._quiet_cap_db():
            self._send_stage()
            return
        reported = None
        if self._surge_path is not None:
            reported = self.query_loaded_patch_path(timeout_s=KEEPALIVE_CONFIRM_TIMEOUT_S)
        confirmed = self._surge_path is not None and same_patch_path(reported, self._surge_path)
        if confirmed and self._pending_raise is not None:
            apply_normalization = self._pending_raise[1]
            self._pending_raise = None
            if self._loaded_patch_name is not None:
                self._settle_patch_level(self._loaded_patch_name, apply_normalization)
        self._surge_confirmed = confirmed
        withheld = not confirmed and self.stage_db > self._quiet_cap_db()
        if withheld and not self._boost_withheld:
            print(f"Stage boost withheld: Surge reports {reported!r}, "
                  f"not the loaded {self._surge_path!r}")
        self._boost_withheld = withheld
        self._send_stage()

    def _sidecar_kw(self) -> dict[str, str | None]:
        return {
            "patch_path": self._loaded_patch_path,
            "stable_key": self._loaded_stable_key,
        }

    def _norm_for(
        self,
        patch_name: str,
        *,
        patch_path: str | None,
        stable_key: str | None,
        authored_volume: float | None,
        authored_volume_b: float | None = None,
    ) -> tuple[float, float | None]:
        return self.normalization.stage_norm(
            patch_name,
            patch_path=patch_path,
            stable_key=stable_key,
            authored_volume=authored_volume,
            authored_volume_b=authored_volume_b,
        )

    def _apply_patch_normalization(self, patch_name: str) -> None:
        self._norm_db, self._ceiling_db = self._norm_for(
            patch_name, authored_volume=self._authored_volume,
            authored_volume_b=self._authored_volume_b, **self._sidecar_kw()
        )

    def refresh_patch_volume(self, patch_name: str) -> bool:
        """Re-apply normalization and trim (e.g. after toggling enabled, or a trim drag)."""
        self._apply_patch_normalization(patch_name)
        return self._send_stage()

    @staticmethod
    def _parse_surge_param_query(data: bytes, osc_path: str) -> float | None:
        """The value in Surge's reply to ``/q<osc_path>``; None for anything else.

        Surge answers ``/param/a/aeg/sustain ,fs 0.3964 "39.64 %"``: the address is
        the parameter's, the float its 0..1 value, the string a display. Only a
        message at exactly ``osc_path`` is taken, and only its float, in 0..1: the
        OSC out also carries echoes of other parameters, and a display string is
        in the parameter's own units (a "%" or "dB" of something else would read
        as a volume).
        """
        try:
            from pythonosc.osc_message import OscMessage

            message = OscMessage(data)
        except Exception:
            return None
        if message.address != osc_path:
            return None
        for param in message.params:
            if isinstance(param, bool) or not isinstance(param, (int, float)):
                continue
            value = float(param)
            if math.isfinite(value) and 0.0 <= value <= 1.0:
                return value
            return None
        return None

    def _query_osc_float(self, osc_path: str) -> float | None:
        """Query a Surge parameter via /q/ prefix (requires --osc-out-port).

        Datagrams are read until the deadline and only Surge's reply for this
        parameter is taken (`_parse_surge_param_query`).
        """
        if not self.osc_enabled or self.osc_client is None:
            return None
        osc_path = osc_path.rstrip("/")
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.bind((self.osc_host, self.osc_out_port))
            self.osc_client.send_message(f"/q{osc_path}", [])
            deadline = time.monotonic() + OSC_QUERY_TIMEOUT_S
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                sock.settimeout(remaining)
                try:
                    data, _addr = sock.recvfrom(4096)
                except socket.timeout:
                    return None
                value = self._parse_surge_param_query(data, osc_path)
                if value is not None:
                    return value
        except OSError:
            return None
        finally:
            sock.close()

    @staticmethod
    def _parse_patch_path_reply(data: bytes) -> str | None:
        """The path in a Surge ``/patch`` message; None for any other message."""
        try:
            from pythonosc.osc_message import OscMessage

            message = OscMessage(data)
        except Exception:
            return None
        if message.address != "/patch":
            return None
        for param in message.params:
            if isinstance(param, str):
                return param
        return None

    def query_loaded_patch_path(self, timeout_s: float = PATCH_QUERY_TIMEOUT_S) -> str | None:
        """The patch Surge last finished loading, without extension; None if no reply.

        Surge's OSC out also carries parameter echoes, so datagrams are read
        until the deadline and only a ``/patch`` message is taken.
        """
        if not self.osc_enabled or self.osc_client is None:
            return None
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.bind((self.osc_host, self.osc_out_port))
            self.osc_client.send_message("/q/patch", [])
            deadline = time.monotonic() + timeout_s
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                sock.settimeout(remaining)
                try:
                    data, _addr = sock.recvfrom(65536)
                except socket.timeout:
                    return None
                path = self._parse_patch_path_reply(data)
                if path is not None:
                    return path
        except OSError:
            return None
        finally:
            sock.close()

    def _confirm_patch_loaded(self, expected: str, timeout_s: float) -> bool:
        """Poll Surge until it reports ``expected`` loaded, or give up."""
        deadline = time.monotonic() + timeout_s
        while True:
            reported = self.query_loaded_patch_path()
            if same_patch_path(reported, expected):
                return True
            if time.monotonic() >= deadline:
                break
            time.sleep(PATCH_CONFIRM_POLL_S)
        print(
            f"Patch load not confirmed: asked for {expected!r}, "
            f"Surge reports {reported!r}"
        )
        return False

    def _capture_hold_baseline(self, patch_name: str) -> bool:
        """Read AEG sustain/decay/release from Surge after patch load."""
        kw = self._sidecar_kw()
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
                stored = self.hold.get_baseline(patch_name, **kw)
                if stored and stage in stored.get(scene, {}):
                    baseline[scene][stage] = stored[scene][stage]
                else:
                    baseline[scene][stage] = 0.0
        self.hold.set_baseline(patch_name, baseline, **kw)
        return True

    def _send_hold_osc(self, patch_name: str) -> bool:
        kw = self._sidecar_kw()
        baseline = self.hold.get_baseline(patch_name, **kw)
        if not baseline:
            return False
        mult = self.hold.get_effective_hold_mult(patch_name, **kw)
        try:
            for scene, stage, osc_path in iter_hold_osc_paths():
                base_val = baseline[scene][stage]
                effective = effective_aeg_value(base_val, mult)
                self.osc_client.send_message(osc_path, effective)
            return True
        except Exception as e:
            print(f"Error applying Hold via OSC: {e}")
            return False

    def send_patch_mod(self, value: float, *, final: bool = True) -> bool:
        """Patch mod fader: set Surge's mod wheel. fx_policy builds the message."""
        if not self.osc_enabled:
            return False
        address, args = self.fx_policy.set_patch_mod(value, final=final)
        try:
            self.osc_client.send_message(address, args)
            return True
        except Exception as e:
            print(f"Error sending patch mod via OSC: {e}")
            return False

    def _resend_patch_mod(self) -> None:
        """After a load, give Surge the Patch mod fader's value again.

        Surge may not keep its mod wheel across a patch load (unverified), and
        nothing else re-sends it: switching patches moves neither fader, so the
        fader's value has to be what the new patch hears. Only with the FX
        host on (otherwise the knob reaches Surge directly and the fader does
        not follow it), only once the value is known, and never to a patch
        with no mod-wheel routing (`patch_mod_locked`: its fader sends nothing).
        """
        policy = self.fx_policy
        if policy.enabled and policy.patch_mod_known and not policy.patch_mod_locked:
            self.send_patch_mod(policy.patch_mod, final=True)

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
        effective = effective_poly_on_load(
            native,
            ceiling=ceiling,
            governor_active=governor_active(),
        )
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
    def load_patch(
        self,
        patch_path,
        *,
        apply_normalization: bool = True,
        stable_key: str | None = None,
        capture_hold: bool = True,
        confirm_timeout_s: float | None = None,
    ):
        """Load ``patch_path`` into Surge by its full path.

        With ``confirm_timeout_s``, wait until Surge reports this exact path as
        loaded (``/q/patch``, which needs Surge's ``--osc-out-port``) before
        sending anything else, and return False if it does not in that time.

        Without it (a browse), wait up to BROWSE_CONFIRM_TIMEOUT_S and load either
        way. The stage gain moves in two steps: a cut goes out before
        ``/patch/load``, a raise only once Surge has confirmed the patch. An
        unconfirmed raise is held back, the stage staying at the quieter of the
        outgoing and incoming levels, until keep_stage_alive sees the patch.
        """
        if not self.osc_enabled:
            print(f"OSC disabled, cannot load: {patch_path}")
            return False

        try:
            load_path = ensure_reuse_single_patch(Path(patch_path))
            path_no_ext = str(load_path)
            if path_no_ext.endswith(".fxp") or path_no_ext.endswith(".FXP"):
                path_no_ext = path_no_ext[:-4]

            patch_name = Path(patch_path).stem
            # A cut lands before the load, so the outgoing patch's tail ducks
            # rather than playing at the new patch's boost; a raise waits until
            # Surge confirms the load below. Before the load the authored volume
            # is unknown, which errs quiet.
            early = (0.0, None)
            if apply_normalization:
                early = self._norm_for(
                    patch_name, patch_path=str(patch_path), stable_key=stable_key,
                    authored_volume=None,
                )
            early_db = self._stage_db_for(*early)
            if early_db < (self._stage_sent_db if self._stage_sent_db is not None else 0.0):
                self._send_stage_db(early_db)
            outgoing = self._effective_norm()
            self.osc_client.send_message("/patch/load", [path_no_ext])
            # Surge loads in the background and refuses a missing file without
            # a word, so unconfirmed, the old patch can still be the one
            # playing, and a level raised now would play it at the new boost.
            if confirm_timeout_s is not None:
                if not self._confirm_patch_loaded(path_no_ext, confirm_timeout_s):
                    return False
                confirmed = True
            else:
                # A browse never fails on a slow answer; only the raise waits.
                confirmed = self._confirm_patch_loaded(path_no_ext, BROWSE_CONFIRM_TIMEOUT_S)
            self._loaded_patch_path = str(patch_path)
            self._loaded_stable_key = stable_key
            print(f"Loaded patch: {Path(patch_path).name}")

            self._loaded_patch_name = patch_name
            self._surge_path = path_no_ext
            self._surge_confirmed = confirmed
            self._apply_playback_policy(patch_name)
            if confirmed:
                self._pending_raise = None
                self._settle_patch_level(patch_name, apply_normalization)
            else:
                # The authored volume read now could be the old patch's.
                self._authored_volume = self._authored_volume_b = None
                self._set_norm(patch_name, apply_normalization)
                self._pending_raise = (path_no_ext, apply_normalization, outgoing)
                print("Level held at the quieter of the two patches until Surge confirms")
            self._send_stage()
            # Calibration measures the patch as authored and must not write
            # the Hold store: its loads carry no stable key.
            if capture_hold:
                if not self._capture_hold_baseline(patch_name):
                    print(f"Hold baseline query failed for {patch_name}; using stored values if any")
                self._send_hold_osc(patch_name)
            # The source file, not the Reuse Single copy: same routings, and
            # the copy may not exist when the feature is off.
            self.fx_policy.on_patch_loaded(
                patch_path, patch_name=patch_name, stable_key=stable_key
            )
            self._resend_patch_mod()
            return True
        except Exception as e:
            print(f"Error loading patch via OSC: {e}")
            return False
