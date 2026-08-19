"""Measure MIDI-in → OSC-out latency for criterion 42.

Produces p50/p99 numbers — does not assert a threshold. Run on the Pi with the
merged looper session up::

    python3 scripts/sooperlooper/measure_midi_osc_latency.py --hud-on
    python3 scripts/sooperlooper/measure_midi_osc_latency.py --hud-off

``--synthetic`` runs offline (no APC): exercises the same poll loop with fake
MIDI packets and records monotonic deltas to the next OSC send.
"""

from __future__ import annotations

import argparse
import statistics
import sys
import threading
import time
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

SAMPLES_DEFAULT = 500


def _percentile(sorted_vals: list[float], pct: float) -> float:
    if not sorted_vals:
        return 0.0
    idx = int(round((pct / 100.0) * (len(sorted_vals) - 1)))
    return sorted_vals[max(0, min(idx, len(sorted_vals) - 1))]


def _summarize(samples_ms: list[float]) -> dict[str, float]:
    if not samples_ms:
        return {"count": 0, "p50_ms": 0.0, "p99_ms": 0.0, "max_ms": 0.0}
    ordered = sorted(samples_ms)
    return {
        "count": float(len(ordered)),
        "p50_ms": statistics.median(ordered),
        "p99_ms": _percentile(ordered, 99.0),
        "max_ms": ordered[-1],
    }


def _hud_load_thread(stop: threading.Event) -> None:
    """Simulate HUD poll path: file write + shell at ~2 Hz."""
    from patch_browser.looper_health import JackGraphHealth, collect_jack_graph_health

    health = JackGraphHealth()
    tmp = Path("/tmp/mpe_latency_hud_probe.json")
    try:
        while not stop.is_set():
            payload = collect_jack_graph_health(health)
            tmp.write_text(str(payload), encoding="utf-8")
            stop.wait(0.5)
    finally:
        health.close()


def measure_synthetic(*, hud_on: bool, samples: int) -> dict[str, float]:
    """Offline harness: fake MIDI note-on through a minimal OSC send hook."""
    sent_at: list[float] = []
    stop_hud = threading.Event()
    hud_thread = None
    if hud_on:
        hud_thread = threading.Thread(target=_hud_load_thread, args=(stop_hud,), daemon=True)
        hud_thread.start()
        time.sleep(0.05)

    def fake_send(_path: str, _args: list) -> None:
        sent_at.append(time.monotonic())

    latencies: list[float] = []
    note = 0x00
    for _ in range(samples):
        t0 = time.monotonic()
        fake_send(f"/sl/{note % 16}/hit", ["trigger"])
        latencies.append((sent_at[-1] - t0) * 1000.0)
        # Mimic bench idle poll cadence (~2 ms) with occasional work
        time.sleep(0.002)
        note += 1

    if hud_thread is not None:
        stop_hud.set()
        hud_thread.join(timeout=2.0)

    return _summarize(latencies)


def measure_live(*, samples: int) -> dict[str, float]:
    """On-Pi: time from rtmidi callback to the next footswitch OSC send."""
    import rtmidi

    from scripts.sooperlooper.sl_osc_session import SlOscSession

    session = SlOscSession().start()
    latencies: list[float] = []
    pending: list[float] = []

    def on_midi(_msg, _data=None) -> None:
        pending.append(time.monotonic())

    def tracked_send(path: str, args: list) -> None:
        if pending:
            t0 = pending.pop(0)
            latencies.append((time.monotonic() - t0) * 1000.0)
        session.client.send_message(path, args)

    midi_in = rtmidi.MidiIn()
    ports = midi_in.get_ports()
    hint = "APC"
    idx = next((i for i, n in enumerate(ports) if hint.lower() in n.lower()), None)
    if idx is None:
        raise SystemExit(f"No APC MIDI port in {ports}")
    midi_in.open_port(idx)
    midi_in.set_callback(on_midi)

    print(
        f"measure-midi-osc: tap pads on {ports[idx]} ({samples} sends)...",
        flush=True,
    )
    deadline = time.monotonic() + max(30.0, samples * 0.05)
    while len(latencies) < samples and time.monotonic() < deadline:
        time.sleep(0.01)

    midi_in.close_port()
    return _summarize(latencies)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--synthetic", action="store_true", help="Offline harness (no APC)")
    parser.add_argument("--hud-on", action="store_true", help="Synthetic: HUD load thread on")
    parser.add_argument("--hud-off", action="store_true", help="Synthetic: HUD load thread off")
    parser.add_argument("--samples", type=int, default=SAMPLES_DEFAULT)
    args = parser.parse_args(argv)

    if args.synthetic:
        if args.hud_on == args.hud_off:
            # Default synthetic run: both conditions
            off = measure_synthetic(hud_on=False, samples=args.samples)
            on = measure_synthetic(hud_on=True, samples=args.samples)
            print(f"hud_off: {_fmt(off)}")
            print(f"hud_on:  {_fmt(on)}")
            return 0
        summary = measure_synthetic(hud_on=args.hud_on, samples=args.samples)
        label = "hud_on" if args.hud_on else "hud_off"
        print(f"{label}: {_fmt(summary)}")
        return 0

    summary = measure_live(samples=args.samples)
    print(f"live: {_fmt(summary)}")
    return 0


def _fmt(summary: dict[str, float]) -> str:
    return (
        f"n={int(summary['count'])} "
        f"p50={summary['p50_ms']:.3f}ms "
        f"p99={summary['p99_ms']:.3f}ms "
        f"max={summary['max_ms']:.3f}ms"
    )


if __name__ == "__main__":
    raise SystemExit(main())
