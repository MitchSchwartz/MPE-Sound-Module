#!/usr/bin/env python3
"""SooperLooper HUD — bar/beat/phrase position for the touch header.

Pure Python and non-blocking, deliberately:

* **No JACK client.** The transport clock was retired when internal sync won,
  so the only C extension here was dead weight — and this process died with
  `Fatal glibc error: malloc.c: assertion failed` (heap corruption), taking the
  beat display with it. A pure-Python writer cannot corrupt a heap.
* **No blocking OSC in the draw path.** State, length and position arrive by
  `register_auto_update`; the writer only reads its cache. The first cut polled
  16 loops x 2 controls per frame with blocking round-trips and stalled outright,
  leaving the HUD file 130 s stale.

The display cycle is the PHRASE — the longest playing loop — so the sweep always
completes and the counter reads bar-within-phrase.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from patch_browser.looper_health import JackGraphHealth, collect_jack_graph_health  # noqa: E402
from patch_browser.sl_hud_state import SL_HUD_STATE_FILE  # noqa: E402

WRITE_INTERVAL_S = float(os.environ.get("MPE_SL_HUD_WRITE_INTERVAL_S", "0.5"))
SL_HOST = os.environ.get("MPE_SL_OSC_HOST", "127.0.0.1")
SL_PORT = int(os.environ.get("MPE_SL_OSC_PORT", "9951"))
LISTEN_PORT = int(os.environ.get("MPE_SL_HUD_LISTEN_PORT", "9952"))
NUM_LOOPS = int(os.environ.get("MPE_SL_LOOPS", "16"))
SCRATCH_LOOP = int(os.environ.get("MPE_SL_SCRATCH_LOOP", "15"))
PLAYING_STATES = frozenset({4, 5})


def beat_and_bar_from_transport(pos: dict) -> tuple[int | None, int | None]:
    """(beat, bar) from a jack transport_query position dict."""
    beat, bar = pos.get("beat"), pos.get("bar")
    if beat is None or bar is None:
        return None, None
    return int(beat), int(bar)


def beat_and_bar_from_tempo(
    loop_pos: float, tempo: float, *, beats_per_bar: int = 4
) -> tuple[int | None, int | None]:
    """(beat, bar) from elapsed seconds at a tempo.

    Derived from tempo, not from cycle_len: SL reports cycle_len equal to the
    recorded loop length, so a 6 s loop yields a 6 s 'cycle' and beats that do
    not match the BPM the player set.
    """
    if tempo <= 0.0:
        return None, None
    beat_dur = 60.0 / tempo
    total = int(loop_pos / beat_dur)
    return (total % beats_per_bar) + 1, (total // beats_per_bar) + 1


def beat_and_bar(loop_pos: float, cycle_len: float) -> tuple[int | None, int | None]:
    """Legacy cycle-relative helper (kept for tests)."""
    if cycle_len <= 0.0:
        return None, None
    pos = loop_pos % cycle_len
    return int((pos / cycle_len) * 4.0) % 4 + 1, int(loop_pos / cycle_len) + 1


class SlQuery:
    """Minimal blocking OSC getter against the SL engine."""

    def __init__(self) -> None:
        self.last: dict[str, float] = {}
        self.client = None

    def start(self):
        from pythonosc import dispatcher as osc_dispatcher
        from pythonosc import osc_server, udp_client

        disp = osc_dispatcher.Dispatcher()
        disp.set_default_handler(self._on)
        self._server = osc_server.ThreadingOSCUDPServer((SL_HOST, LISTEN_PORT), disp)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        self.client = udp_client.SimpleUDPClient(SL_HOST, SL_PORT)
        return self

    def _on(self, _addr, *args) -> None:
        if len(args) >= 3:
            self.last[f"{args[0]}:{args[1]}"] = args[2]

    def cached(self, ctrl: str, loop: int = 0):
        """Last value delivered by auto-update. Never blocks."""
        return self.last.get(f"{loop if loop >= 0 else -2}:{ctrl}")

    def get(self, ctrl: str, loop: int = 0, timeout: float = 0.4):
        key = f"{loop if loop >= 0 else -2}:{ctrl}"
        self.last.pop(key, None)
        path = "/get" if loop < 0 else f"/sl/{loop}/get"
        self.client.send_message(path, [ctrl, f"{SL_HOST}:{LISTEN_PORT}", "/r"])
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if key in self.last:
                return self.last[key]
            time.sleep(0.02)
        return None


class HudWriter:
    def __init__(self) -> None:
        self._last_write = 0.0
        self._last_key: tuple | None = None
        self._sl = SlQuery().start()
        self._lengths: dict[int, float] = {}
        self._registered_at = 0.0
        self._graph_health = JackGraphHealth()
        self._last_health_sample = 0.0

    def register_auto_updates(self) -> None:
        """Subscribe to state/loop_len/loop_pos rather than polling for them.

        The first cut queried 16 loops x 2 controls per frame with blocking OSC
        round-trips. That is ~32 blocking calls at draw rate for data that only
        changes when a clip is recorded, and it stalled the writer outright.
        """
        for loop in range(NUM_LOOPS):
            for ctrl in ("state", "loop_len", "loop_pos"):
                self._sl.client.send_message(
                    f"/sl/{loop}/register_auto_update",
                    [ctrl, 100, f"{SL_HOST}:{LISTEN_PORT}", "/r"],
                )
        self._sl.client.send_message(
            "/register_auto_update", ["tempo", 200, f"{SL_HOST}:{LISTEN_PORT}", "/r"]
        )
        # Seed tempo with a direct query. register_auto_update only delivers on
        # CHANGE, and the engine's tempo is set once by configure-grid-sync.sh during
        # its own startup. A writer that comes up after that never hears it, so
        # `cached("tempo")` stays None, `_from_sl` returns None, and the HUD never
        # writes — a stale grid with no error anywhere.
        #
        # Start order used to hide this: the HUD was hand-started before the engine,
        # so it was listening when the tempo changed. Giving the stack systemd units
        # made the engine start first (After=mpe-sooperlooper), which turned a race
        # we happened to win into one we always lose.
        if self._sl.cached("tempo", -1) is None:
            self._sl.get("tempo", -1)
        self._registered_at = time.monotonic()

    def _phrase_reference(self, bar_span: float):
        """Longest playing loop = the musical phrase the counter should span.

        A 1-bar first clip and a 4-bar third clip do not share a display cycle:
        counting 1/1 forever while a 4-bar clip runs tells the player nothing
        about where they are in the phrase. Quantization stays at the bar; only
        the DISPLAY follows the longest loop.

        Loop lengths are re-scanned about once a second — position is polled at
        the draw rate, but lengths only change when a clip is recorded.
        """
        lengths = {}
        for loop in range(NUM_LOOPS):
            if loop == SCRATCH_LOOP:
                continue
            state = self._sl.cached("state", loop)
            if state is None or int(state) not in PLAYING_STATES:
                continue
            length = self._sl.cached("loop_len", loop) or 0.0
            if float(length) > 0.0:
                lengths[loop] = float(length)
        self._lengths = lengths
        if not self._lengths:
            return None, 0.0, 1
        loop = max(self._lengths, key=lambda k: self._lengths[k])
        phrase_len = self._lengths[loop]
        bars = max(1, round(phrase_len / bar_span)) if bar_span > 0 else 1
        return loop, phrase_len, bars

    def _from_sl(self) -> dict | None:
        tempo = self._sl.cached("tempo", -1)
        if not tempo:
            return None
        bar_span = 4 * 60.0 / float(tempo)
        ref, phrase_len, bars = self._phrase_reference(bar_span)
        if ref is not None:
            pos = float(self._sl.cached("loop_pos", ref) or 0.0)
            beat, bar = beat_and_bar_from_tempo(pos, float(tempo))
            return {
                "source": "sl_internal", "beat": beat,
                "bar": ((int(bar) - 1) % bars) + 1,
                "bars_in_phrase": bars,
                "bpm": float(tempo), "playing": True, "has_master": True,
                "active": True, "state": 4,
                "loop_pos": pos, "ref_loop": ref,
                "phrase_len": phrase_len,
                "phrase_pos": pos % phrase_len if phrase_len > 0 else 0.0,
            }
        # Nothing playing: tempo is known, phase is not.
        return {
            "source": "sl_internal", "beat": None, "bar": None,
            "bars_in_phrase": 1, "phrase_len": 0.0, "phrase_pos": 0.0,
            "bpm": float(tempo), "playing": False, "has_master": False,
            "active": False, "state": 0, "loop_pos": 0.0, "ref_loop": None,
        }

    def poll(self) -> bool:
        payload = self._from_sl()
        if payload is None:
            return False

        payload.setdefault("phrase_len", 0.0)
        payload.setdefault("phrase_pos", 0.0)
        payload.setdefault("bars_in_phrase", 1)
        now_mono = time.monotonic()
        health_fresh = False
        if now_mono - self._last_health_sample >= WRITE_INTERVAL_S:
            payload["health"] = collect_jack_graph_health(self._graph_health)
            self._last_health_sample = now_mono
            health_fresh = True
        else:
            payload["health"] = self._graph_health.snapshot()
        payload.update({"updated_at": time.time(), "cycle_len": 0.0, "loop_len": 0.0})
        key = (payload.get("beat"), payload.get("bar"), payload.get("active"))
        now = time.time()
        if (
            not health_fresh
            and key == self._last_key
            and (now - self._last_write) < WRITE_INTERVAL_S
        ):
            return False
        self._last_key = key
        self._last_write = now
        tmp = SL_HUD_STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        tmp.replace(SL_HUD_STATE_FILE)
        return True


def main() -> int:
    writer = HudWriter()
    writer.register_auto_updates()
    print(f"sl-hud-monitor: -> {SL_HUD_STATE_FILE} (follows the live clock)", flush=True)
    try:
        while True:
            writer.poll()
            if time.monotonic() - writer._registered_at > 15.0:
                writer.register_auto_updates()  # survive an engine restart
            time.sleep(0.1)
    except KeyboardInterrupt:
        return 0
    finally:
        # Release the held jack_cpu_load client rather than leaving it on the graph.
        writer._graph_health.close()


if __name__ == "__main__":
    raise SystemExit(main())
