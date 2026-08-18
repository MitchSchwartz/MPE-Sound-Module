"""Phase 3M — one process owns bench + HUD. HUD runs off the MIDI path."""

from __future__ import annotations

import argparse
import importlib.util
import sys
import threading
import time
from pathlib import Path

from patch_browser.sl_hud_state import SL_HUD_STATE_FILE  # noqa: E402

from sl_hud_monitor import HudWriter  # noqa: E402

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


def _load_bench_module():
    bench_path = _REPO / "scripts" / "sooperlooper-apc-bench.py"
    spec = importlib.util.spec_from_file_location("sooperlooper_apc_bench", bench_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load bench from {bench_path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _hud_thread_main(stop: threading.Event, writer: HudWriter) -> None:
    writer.register_auto_updates()
    print(
        f"looper-session: HUD -> {SL_HUD_STATE_FILE} (background thread)",
        flush=True,
    )
    while not stop.is_set():
        writer.poll()
        if writer.should_reregister():
            writer.register_auto_updates()
        if stop.wait(0.1):
            break


def start_hud_thread() -> tuple[threading.Thread, threading.Event, HudWriter]:
    stop = threading.Event()
    writer = HudWriter()
    thread = threading.Thread(
        target=_hud_thread_main,
        args=(stop, writer),
        name="sl-hud-writer",
        daemon=False,
    )
    thread.start()
    return thread, stop, writer


def run_session(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--bench-only",
        action="store_true",
        help="Run APC bench without HUD (Phase 3M step-1 compat)",
    )
    parser.add_argument(
        "--hud-only",
        action="store_true",
        help="Run HUD writer only (debug)",
    )
    args, bench_argv = parser.parse_known_args(argv)

    if args.bench_only and args.hud_only:
        print("Error: --bench-only and --hud-only are mutually exclusive", file=sys.stderr)
        return 2

    if args.hud_only:
        from sl_hud_monitor import main as hud_main

        return hud_main()

    hud_thread = None
    hud_stop = None
    hud_writer = None
    if not args.bench_only:
        hud_thread, hud_stop, hud_writer = start_hud_thread()

    bench = _load_bench_module()
    try:
        return bench.run_bench(bench_argv or None)
    finally:
        if hud_stop is not None and hud_thread is not None:
            hud_stop.set()
            hud_thread.join(timeout=2.0)
            if hud_writer is not None:
                hud_writer.close()


if __name__ == "__main__":
    raise SystemExit(run_session())
