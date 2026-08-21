#!/usr/bin/env python3
"""Publish session.snapshot.json on a timer (Phase 1, work-order task 5).

Calls ``build_snapshot()`` **in-process**. Never invoke the module CLI on a timer:
measured 418 ms per invocation against 42 ms in-process, 360 ms of which is
interpreter start. See docs/measurements/systemd-liveness-cost-2026-08-19.md.

Default cadence is 1 Hz, not 2. Nothing in the snapshot changes faster than a second
except ``loop_pos``, which the HUD owns on its own path, and 1 Hz halves the dominant
per-build term for no loss of fidelity. Measured cost at 1 Hz: 0.39% of a core.
"""

from __future__ import annotations

import argparse
import os
import signal
import sys
import time
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from patch_browser.session_snapshot import (  # noqa: E402
    build_snapshot,
    next_seq,
    write_snapshot,
)

DEFAULT_INTERVAL_S = float(os.environ.get("MPE_SNAPSHOT_PUBLISH_INTERVAL_S", "1.0"))
# A publisher that cannot keep up is a publisher whose readings are older than they
# claim. Log it rather than silently drifting; the age fields would still be honest,
# but the cadence would not be what the unit says it is.
OVERRUN_WARN_RATIO = 0.5


class _Stop:
    def __init__(self) -> None:
        self.flag = False
        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            signal.signal(sig, self._handle)

    def _handle(self, _signum: int, _frame: object) -> None:
        self.flag = True


def run(interval_s: float, *, run_dir: Path | None = None, once: bool = False) -> int:
    stop = _Stop()
    overruns = 0
    published = 0
    while True:
        started = time.monotonic()
        try:
            snap = build_snapshot(run=run_dir, seq=next_seq(run=run_dir))
            write_snapshot(snap, run=run_dir)
            published += 1
        except Exception as exc:  # noqa: BLE001 — a publisher must not die on one bad build
            print(f"session-snapshot-publisher: build failed: {exc}", file=sys.stderr, flush=True)
        elapsed = time.monotonic() - started
        if elapsed > interval_s * OVERRUN_WARN_RATIO:
            overruns += 1
            if overruns in (1, 10, 100) or overruns % 1000 == 0:
                print(
                    f"session-snapshot-publisher: build took {elapsed * 1000:.1f} ms "
                    f"against a {interval_s:.2f} s interval ({overruns} so far)",
                    file=sys.stderr,
                    flush=True,
                )
        if once or stop.flag:
            return 0
        remaining = interval_s - elapsed
        # Sleep in slices so SIGTERM is honoured promptly rather than at interval edge.
        while remaining > 0 and not stop.flag:
            nap = min(0.2, remaining)
            time.sleep(nap)
            remaining -= nap
        if stop.flag:
            return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL_S)
    parser.add_argument("--run-dir", default=None)
    parser.add_argument("--once", action="store_true", help="Publish one snapshot and exit")
    args = parser.parse_args(argv)
    if args.interval <= 0:
        print("session-snapshot-publisher: --interval must be > 0", file=sys.stderr)
        return 2
    run_dir = Path(args.run_dir) if args.run_dir else None
    print(
        f"session-snapshot-publisher: publishing every {args.interval:.2f} s",
        flush=True,
    )
    return run(args.interval, run_dir=run_dir, once=args.once)


if __name__ == "__main__":
    raise SystemExit(main())
