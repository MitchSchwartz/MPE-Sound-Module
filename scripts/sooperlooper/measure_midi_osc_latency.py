"""Measure MIDI-in → OSC-out latency for criterion 42.

Produces p50/p99 numbers — does not assert a threshold. Run on the Pi with the
merged looper session and APC connected::

    python3 scripts/sooperlooper/measure_midi_osc_latency.py --samples 200

With HUD thread comparison (stop session, restart without HUD is manual A/B)::

    python3 scripts/looper-session.py   # normal merged session
    python3 scripts/sooperlooper/measure_midi_osc_latency.py --samples 200

The measurement hooks the bench ``_send`` path: timestamp at pad-down, delta at
the next ``/hit`` OSC message.
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_SOOPER = _REPO / "scripts" / "sooperlooper"
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))
if str(_SOOPER) not in sys.path:
    sys.path.insert(0, str(_SOOPER))

SAMPLES_DEFAULT = 200


def _percentile(sorted_vals: list[float], pct: float) -> float:
    if not sorted_vals:
        return 0.0
    idx = int(round((pct / 100.0) * (len(sorted_vals) - 1)))
    return sorted_vals[max(0, min(idx, len(sorted_vals) - 1))]


def summarize(samples_ms: list[float]) -> dict[str, float]:
    if not samples_ms:
        return {"count": 0, "p50_ms": 0.0, "p99_ms": 0.0, "max_ms": 0.0}
    ordered = sorted(samples_ms)
    return {
        "count": float(len(ordered)),
        "p50_ms": statistics.median(ordered),
        "p99_ms": _percentile(ordered, 99.0),
        "max_ms": ordered[-1],
    }


def fmt_summary(summary: dict[str, float], *, label: str) -> str:
    return (
        f"{label}: n={int(summary['count'])} "
        f"p50={summary['p50_ms']:.3f}ms "
        f"p99={summary['p99_ms']:.3f}ms "
        f"max={summary['max_ms']:.3f}ms"
    )


def summarize_and_print(samples_ms: list[float], *, label: str = "live") -> None:
    print(fmt_summary(summarize(samples_ms), label=label), flush=True)


def measure_live(*, samples: int) -> dict[str, float]:
    """Run the APC bench until N pad-driven /hit samples are collected."""
    import importlib.util

    bench_path = _REPO / "scripts" / "sooperlooper-apc-bench.py"
    spec = importlib.util.spec_from_file_location("sooperlooper_apc_bench", bench_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load bench from {bench_path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    print(
        f"measure-midi-osc: tap pads ({samples} /hit sends)...",
        flush=True,
    )
    rc = mod.run_bench(["--measure-latency", str(samples)])
    if rc != 0:
        raise SystemExit(rc)
    return summarize([])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=SAMPLES_DEFAULT)
    args = parser.parse_args(argv)
    measure_live(samples=args.samples)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
