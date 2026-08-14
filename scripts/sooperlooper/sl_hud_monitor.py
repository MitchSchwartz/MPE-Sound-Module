"""SooperLooper HUD state from JACK transport (bar/beat)."""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from patch_browser.sl_hud_state import SL_HUD_STATE_FILE  # noqa: E402

WRITE_INTERVAL_S = float(os.environ.get("MPE_SL_HUD_WRITE_INTERVAL_S", "1.0"))
JACK_CLIENT = os.environ.get("MPE_JACK_HUD_CLIENT", "mpe-sl-hud")


def beat_and_bar_from_transport(pos: dict) -> tuple[int | None, int | None]:
    """Return (beat, bar) from jack transport_query position dict."""
    beat = pos.get("beat")
    bar = pos.get("bar")
    if beat is None or bar is None:
        return None, None
    return int(beat), int(bar)


def beat_and_bar(loop_pos: float, cycle_len: float) -> tuple[int | None, int | None]:
    """Legacy helper — quarter-note beat within cycle (tests / fallback)."""
    if cycle_len <= 0.0:
        return None, None
    pos = loop_pos % cycle_len
    beat = int((pos / cycle_len) * 4.0) % 4 + 1
    bar = int(loop_pos / cycle_len) + 1
    return beat, bar


class TransportHudWriter:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last_payload: dict | None = None
        self._last_write = 0.0
        self._client = None

    def _open_client(self):
        import jack

        if self._client is not None:
            return self._client
        self._client = jack.Client(JACK_CLIENT, no_start_server=True)
        self._jack = jack
        return self._client

    def poll(self, *, force: bool = False) -> bool:
        try:
            client = self._open_client()
        except Exception as exc:
            print(f"sl-hud-monitor: JACK unavailable: {exc}", file=sys.stderr, flush=True)
            return False

        state, pos = client.transport_query()
        pos_dict = dict(pos) if pos else {}
        beat, bar = beat_and_bar_from_transport(pos_dict)
        rolling = state in (
            getattr(self._jack, "TRANSPORT_ROLLING", 1),
            getattr(self._jack, "TRANSPORT_STARTING", 3),
        )
        now = time.time()
        payload = {
            "updated_at": now,
            "source": "jack_transport",
            "transport_state": str(state),
            "beat": beat,
            "bar": bar,
            "bpm": pos_dict.get("beats_per_minute"),
            "playing": rolling and beat is not None,
            "has_master": beat is not None,
            "active": rolling and beat is not None,
            "state": 4 if rolling else 0,
            "cycle_len": 0.0,
            "loop_len": 0.0,
            "loop_pos": 0.0,
        }
        with self._lock:
            unchanged = payload.get("beat") == (self._last_payload or {}).get("beat") and (
                payload.get("bar") == (self._last_payload or {}).get("bar")
            )
            if not force and unchanged and (now - self._last_write) < WRITE_INTERVAL_S:
                return False
            self._last_payload = payload
            self._last_write = now

        tmp = SL_HUD_STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        tmp.replace(SL_HUD_STATE_FILE)
        return True

    def close(self) -> None:
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None


def main() -> int:
    writer = TransportHudWriter()
    print(
        f"sl-hud-monitor: JACK transport → {SL_HUD_STATE_FILE} "
        f"(interval ≥{WRITE_INTERVAL_S}s)",
        flush=True,
    )
    try:
        while True:
            writer.poll()
            time.sleep(0.1)
    except KeyboardInterrupt:
        return 0
    finally:
        writer.close()


if __name__ == "__main__":
    raise SystemExit(main())
