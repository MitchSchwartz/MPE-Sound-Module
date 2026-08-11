#!/usr/bin/env python3
"""Interactive looper — APC Session View grid (default) or legacy Scene transport.

Run manually on the Pi (no systemd yet). Surge must output to Loopback; this process
captures loopback and plays to the Sound Blaster.

Grid mode (default, see docs/APC-LOOPER-UX.md):
  Row 0 (all 8 pads) — independent clips
  Scene Launch 1 — launch/stop row 0 at bar boundary
  Shift + Scene Launch 8 — stop all clips

Examples:
  python3 scripts/mpe-looper.py
  python3 scripts/mpe-looper.py --bars 4 --bpm 120 --buffer-size 512
  python3 scripts/mpe-looper.py --legacy-transport   # v0 Sc1/5/8 hack
  python3 scripts/mpe-looper.py --no-apc
  python3 scripts/mpe-looper.py --soak 60
"""

from __future__ import annotations

import argparse
import os
import signal
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from patch_browser.control_surfaces import (  # noqa: E402
    get_apc_map,
    looper_transport_from_message,
)
from patch_browser.control_surfaces.apc_led import ApcLedFeedback  # noqa: E402
from patch_browser.control_surfaces.midi import find_input_port_index  # noqa: E402
from patch_browser.looper_audio_io import (  # noqa: E402
    ensure_audio_procs_started,
    open_aplay,
    open_arecord,
)
from patch_browser.looper_devices import (  # noqa: E402
    prepare_looper_audio_path,
    surge_loopback_hint,
)
from patch_browser.looper_engine import (  # noqa: E402
    StereoRingBuffer,
    frames_to_bytes,
    loop_length_frames,
)
from patch_browser.clip_matrix import ClipMatrix  # noqa: E402
from patch_browser.control_surfaces.apc_session_midi import (  # noqa: E402
    ApcMidiContext,
    check_clear_session_hold,
    handle_apc_session_message,
)
from patch_browser.looper_period_debug import (  # noqa: E402
    LooperPeriodDebug,
    count_playing_layers,
    looper_debug_enabled,
)
from patch_browser.looper_session import LooperMode, LooperSession  # noqa: E402
from patch_browser.looper_timing_publisher import LooperTimingPublisher  # noqa: E402
from patch_browser.looper_timing_state import clear_timing_state  # noqa: E402
from patch_browser.looper_xruns import read_xrun_counts, total_xruns  # noqa: E402
from patch_browser.surge_audio import current_buffer_size, current_sample_rate  # noqa: E402

_STOP = False
_MAX_SOAK_S = 60.0


def _handle_stop(_signum: int, _frame: object) -> None:
    global _STOP
    _STOP = True


def _find_apc_port_indices() -> tuple[int | None, int | None]:
    import rtmidi

    surface = get_apc_map()
    in_names = rtmidi.MidiIn().get_ports()
    out_names = rtmidi.MidiOut().get_ports()
    in_idx = find_input_port_index(in_names, surface)
    out_idx = find_input_port_index(out_names, surface)
    return in_idx, out_idx


def _open_apc_midi(*, enable: bool) -> tuple[object | None, object | None, ApcLedFeedback | None]:
    if not enable:
        return None, None, None
    try:
        import rtmidi
    except ImportError:
        print("Warning: python-rtmidi not installed — APC disabled", file=sys.stderr)
        return None, None, None

    surface = get_apc_map()
    in_idx, out_idx = _find_apc_port_indices()
    if in_idx is None or out_idx is None:
        print("Warning: APC mini MIDI port not found — transport disabled", file=sys.stderr)
        return None, None, None

    midi_in = rtmidi.MidiIn()
    midi_out = rtmidi.MidiOut()
    midi_in.open_port(in_idx)
    midi_out.open_port(out_idx)
    in_name = rtmidi.MidiIn().get_ports()[in_idx]
    print(f"APC: {in_name} (map={surface.map_id})", flush=True)
    leds = ApcLedFeedback(midi_out, surface)
    leds.all_off()
    return midi_in, leds, leds


def _poll_apc_grid(midi_in, ctx: ApcMidiContext, matrix: ClipMatrix) -> None:
    if midi_in is None:
        return
    surface = get_apc_map()
    while True:
        msg = midi_in.get_message()
        if msg is None:
            break
        data, _dt = msg
        label = handle_apc_session_message(surface, data, ctx, matrix)
        if label:
            print(f"[apc] {label}", flush=True)


_LOOPER_MIN_PERIOD = 512
# Latency budget: 512 Surge + 512 looper period ≈ 1024 samples one-way (~21 ms @ 48 kHz).
# Do not raise either side independently — optimize CPU instead (parallel route, C mixer).


def _sync_grid_leds(leds: ApcLedFeedback | None, matrix: ClipMatrix) -> None:
    if leds is None:
        return
    leds.show_clip_matrix(matrix)


def _publish_timing(_publisher: LooperTimingPublisher, matrix: ClipMatrix) -> None:
    _publisher.publish_from_matrix(matrix)


def run_looper_grid(
    *,
    capture: str,
    playback: str,
    sample_rate: int,
    period_frames: int,
    bars: int,
    bpm: float,
    loop_gain: float,
    use_apc: bool,
    soak_s: float | None,
) -> int:
    matrix = ClipMatrix.create_v1(
        sample_rate=sample_rate,
        bpm=bpm,
        bars=bars,
        loop_gain=loop_gain,
    )
    period_bytes = frames_to_bytes(period_frames)

    rec = open_arecord(capture, sample_rate=sample_rate, period_frames=period_frames)
    play = open_aplay(playback, sample_rate=sample_rate, period_frames=period_frames)
    assert rec.stdout is not None
    assert play.stdin is not None
    if (code := ensure_audio_procs_started(("arecord", rec), ("aplay", play))) is not None:
        return code

    midi_in, leds, _ = _open_apc_midi(enable=use_apc)
    apc_ctx = ApcMidiContext()
    timing_pub = LooperTimingPublisher()
    _sync_grid_leds(leds, matrix)

    period_budget_s = period_frames / sample_rate
    debug = LooperPeriodDebug(period_budget_s=period_budget_s) if looper_debug_enabled() else None
    if debug is not None:
        print(
            f"[debug] MPE_LOOPER_DEBUG=1 period_budget={period_budget_s * 1000:.2f}ms",
            flush=True,
        )

    baseline = read_xrun_counts()
    start = time.monotonic()
    last_report = start
    last_rev = 0
    periods = 0
    periods_since_flush = 0

    print(
        f"Grid v1: row 0 (8 clips) · {bars} bars @ {bpm} BPM "
        f"({matrix.loop_frames} frames)",
        flush=True,
    )
    print("Pads=clips · Scene 1=row · Shift+Scene 8=stop all · hold 3s=clear", flush=True)

    try:
        while not _STOP:
            if soak_s is not None and (time.monotonic() - start) >= soak_s:
                print(f"[soak] {soak_s:.0f}s elapsed — exiting", flush=True)
                break

            chunk = rec.stdout.read(period_bytes)
            if not chunk:
                break
            if len(chunk) < period_bytes:
                chunk = chunk + b"\x00" * (period_bytes - len(chunk))

            iter_start = time.monotonic() if debug is not None else 0.0

            out = matrix.process_period(chunk, period_frames=period_frames)
            play.stdin.write(out)
            periods += 1
            periods_since_flush += 1
            if periods_since_flush >= 2:
                play.stdin.flush()
                periods_since_flush = 0

            _poll_apc_grid(midi_in, apc_ctx, matrix)
            if check_clear_session_hold(apc_ctx, matrix):
                print("[apc] clear session", flush=True)
                _sync_grid_leds(leds, matrix)

            rev = sum(hash(s.state) for s in matrix.slots.values())
            if rev != last_rev:
                _sync_grid_leds(leds, matrix)
                last_rev = rev
            _publish_timing(timing_pub, matrix)

            if debug is not None:
                debug.record(time.monotonic() - iter_start, count_playing_layers(matrix))

            now = time.monotonic()
            if now - last_report >= 5.0:
                snap = matrix.clock.snapshot()
                print(
                    f"[grid] {now - start:.0f}s beat={snap['beat_in_bar']} "
                    f"bar={snap['bar_in_loop']}/{snap['bars_per_loop']} periods={periods}",
                    flush=True,
                )
                _report_xruns("looper", baseline)
                if debug is not None:
                    debug.flush_window("5s")
                last_report = now
    finally:
        timing_pub.clear()
        clear_timing_state()
        if play.stdin is not None:
            try:
                play.stdin.flush()
            except Exception:
                pass
        if leds is not None:
            leds.all_off()
        for proc in (rec, play):
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except Exception:
                proc.kill()

    xrun_delta = _report_xruns("final", baseline)
    if debug is not None:
        debug.flush_window("final")
        print(f"[debug] session total_overruns={debug.total_overruns}", flush=True)
    print(f"Done: periods={periods} xrun_delta={xrun_delta}", flush=True)
    return 1 if xrun_delta else 0


def _poll_apc(midi_in, session: LooperSession) -> None:
    if midi_in is None:
        return
    while True:
        msg = midi_in.get_message()
        if msg is None:
            break
        data, _dt = msg
        action = looper_transport_from_message(get_apc_map(), data)
        if action is not None:
            session.on_transport(action)
            print(f"[transport] {action.value} → mode={session.mode}", flush=True)


def _sync_leds(leds: ApcLedFeedback | None, session: LooperSession) -> None:
    if leds is None:
        return
    leds.show_looper_state(
        recording=session.mode == LooperMode.RECORDING,
        playing=session.mode == LooperMode.PLAYING,
        has_loop=session.has_loop or session.ring.filled_frames > 0,
    )


def run_looper(
    *,
    capture: str,
    playback: str,
    sample_rate: int,
    period_frames: int,
    bars: int,
    bpm: float,
    loop_gain: float,
    use_apc: bool,
    soak_s: float | None,
) -> int:
    loop_frames = loop_length_frames(bars=bars, bpm=bpm, sample_rate=sample_rate)
    session = LooperSession(
        ring=StereoRingBuffer(loop_frames),
        loop_gain=loop_gain,
    )
    period_bytes = frames_to_bytes(period_frames)

    rec = open_arecord(capture, sample_rate=sample_rate, period_frames=period_frames)
    play = open_aplay(playback, sample_rate=sample_rate, period_frames=period_frames)
    assert rec.stdout is not None
    assert play.stdin is not None
    if (code := ensure_audio_procs_started(("arecord", rec), ("aplay", play))) is not None:
        return code

    midi_in, leds, _ = _open_apc_midi(enable=use_apc)
    _sync_leds(leds, session)

    baseline = read_xrun_counts()
    start = time.monotonic()
    last_report = start
    last_mode = session.mode
    periods = 0
    periods_since_flush = 0

    print(
        f"Loop capacity: {bars} bars @ {bpm} BPM = {loop_frames} frames "
        f"({loop_frames / sample_rate:.2f}s)",
        flush=True,
    )
    print("Ready — Sc1=record Sc5=play/stop Sc8=clear (mk1 Scene Launch)", flush=True)

    try:
        while not _STOP:
            if soak_s is not None and (time.monotonic() - start) >= soak_s:
                print(f"[soak] {soak_s:.0f}s elapsed — exiting", flush=True)
                break

            _poll_apc(midi_in, session)
            chunk = rec.stdout.read(period_bytes)
            if not chunk:
                break
            if len(chunk) < period_bytes:
                chunk = chunk + b"\x00" * (period_bytes - len(chunk))

            session.process_period(chunk)
            out = session.output_pcm(chunk, period_frames=period_frames)
            play.stdin.write(out)
            periods += 1
            periods_since_flush += 1
            # Flush rarely — per-period flush starves aplay and causes DAC xruns (crackle).
            if periods_since_flush >= 8:
                play.stdin.flush()
                periods_since_flush = 0

            if session.mode != last_mode:
                _sync_leds(leds, session)
                last_mode = session.mode

            now = time.monotonic()
            if now - last_report >= 5.0:
                print(
                    f"[{session.mode}] {now - start:.0f}s periods={periods} "
                    f"filled={session.ring.filled_frames}/{loop_frames}",
                    flush=True,
                )
                _report_xruns("looper", baseline)
                last_report = now
    finally:
        if play.stdin is not None:
            try:
                play.stdin.flush()
            except Exception:
                pass
        if leds is not None:
            leds.all_off()
        for proc in (rec, play):
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except Exception:
                proc.kill()

    xrun_delta = _report_xruns("final", baseline)
    print(f"Done: periods={periods} xrun_delta={xrun_delta}", flush=True)
    return 1 if xrun_delta else 0


def _report_xruns(label: str, baseline: dict[str, int]) -> int:
    current = read_xrun_counts()
    delta = sum(max(0, current.get(path, 0) - baseline.get(path, 0)) for path in current)
    print(f"[{label}] xrun delta={delta} total={total_xruns(current)}", flush=True)
    return delta


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample-rate", type=int, default=None)
    parser.add_argument(
        "--buffer-size",
        type=int,
        default=None,
        help="ALSA period frames (default: MPE_SURGE_BUFFER_SIZE from /etc/mpe/mpe.env)",
    )
    parser.add_argument("--bars", type=int, default=int(os.environ.get("MPE_LOOPER_BARS", "4")))
    parser.add_argument("--bpm", type=float, default=float(os.environ.get("MPE_LOOPER_BPM", "120")))
    parser.add_argument("--loop-gain", type=float, default=0.85)
    parser.add_argument("--no-apc", action="store_true", help="Disable APC MIDI/LED")
    parser.add_argument(
        "--soak",
        type=float,
        default=None,
        metavar="SEC",
        help=f"Auto-exit after SEC seconds (capped at {_MAX_SOAK_S:.0f} for quick checks)",
    )
    parser.add_argument("--skip-modprobe", action="store_true")
    parser.add_argument(
        "--legacy-transport",
        action="store_true",
        help="v0 hack: Scene Launch 1/5/8 transport instead of grid Session View",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    signal.signal(signal.SIGINT, _handle_stop)
    signal.signal(signal.SIGTERM, _handle_stop)
    args = build_parser().parse_args(argv)

    soak_s = args.soak
    if soak_s is not None and soak_s > _MAX_SOAK_S:
        print(f"Note: --soak capped at {_MAX_SOAK_S:.0f}s (use spike script for longer)", flush=True)
        soak_s = _MAX_SOAK_S

    surge_buf = current_buffer_size()
    sample_rate = args.sample_rate if args.sample_rate is not None else current_sample_rate()
    period_frames = args.buffer_size if args.buffer_size is not None else surge_buf
    if args.buffer_size is not None and args.buffer_size != surge_buf:
        print(
            f"Warning: looper period {args.buffer_size} != Surge buffer {surge_buf} "
            f"— omit --buffer-size to match Surge automatically",
            file=sys.stderr,
            flush=True,
        )
    print(
        f"Audio period: {period_frames} frames (~{period_frames * 1000.0 / sample_rate:.1f} ms), "
        f"Surge buffer: {surge_buf}",
        flush=True,
    )
    if period_frames < _LOOPER_MIN_PERIOD:
        print(
            f"Warning: period {period_frames} < {_LOOPER_MIN_PERIOD} — breaks 512+512 latency budget; "
            f"expect xruns on Pi",
            file=sys.stderr,
            flush=True,
        )
    elif period_frames > _LOOPER_MIN_PERIOD:
        print(
            f"Warning: looper period {period_frames} > Surge {_LOOPER_MIN_PERIOD} — "
            f"total buffer exceeds 1024-sample target",
            file=sys.stderr,
            flush=True,
        )

    try:
        capture, playback = prepare_looper_audio_path(load_loopback=not args.skip_modprobe)
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    print(f"Capture:  {capture}", flush=True)
    print(f"Playback: {playback}", flush=True)
    print(f"Hint: {surge_loopback_hint()}", flush=True)
    print(
        "Route Surge: sudo ./scripts/looper-audio-route.sh on  (then run this script)",
        flush=True,
    )

    runner = run_looper if args.legacy_transport else run_looper_grid
    return runner(
        capture=capture,
        playback=playback,
        sample_rate=sample_rate,
        period_frames=period_frames,
        bars=args.bars,
        bpm=args.bpm,
        loop_gain=args.loop_gain,
        use_apc=not args.no_apc,
        soak_s=soak_s,
    )


if __name__ == "__main__":
    raise SystemExit(main())
