#!/usr/bin/env python3
"""Interactive v0 looper — one loop, APC Scene Launch transport, mk1 LED feedback.

Run manually on the Pi (no systemd yet). Surge must output to Loopback; this process
captures loopback and plays to the Sound Blaster.

Transport (mk1 default, Scene Launch):
  Sc1 Record   — tap to start; tap again to close early; auto-closes when loop full
  Sc5 Play/Stop — toggle loop playback
  Sc8 Clear    — wipe loop

Examples:
  python3 scripts/mpe-looper.py
  python3 scripts/mpe-looper.py --bars 4 --bpm 120 --buffer-size 512
  python3 scripts/mpe-looper.py --no-apc          # keyboard-less passthrough + state via logs only
  python3 scripts/mpe-looper.py --soak 60         # exit after 60s (stability check, not 10 min)
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
from patch_browser.looper_session import LooperMode, LooperSession  # noqa: E402
from patch_browser.looper_xruns import read_xrun_counts, total_xruns  # noqa: E402

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
            play.stdin.flush()
            periods += 1

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
    parser.add_argument("--sample-rate", type=int, default=int(os.environ.get("MPE_SURGE_SAMPLE_RATE", "48000")))
    parser.add_argument("--buffer-size", type=int, default=int(os.environ.get("MPE_SURGE_BUFFER_SIZE", "512")))
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
    return parser


def main(argv: list[str] | None = None) -> int:
    signal.signal(signal.SIGINT, _handle_stop)
    signal.signal(signal.SIGTERM, _handle_stop)
    args = build_parser().parse_args(argv)

    soak_s = args.soak
    if soak_s is not None and soak_s > _MAX_SOAK_S:
        print(f"Note: --soak capped at {_MAX_SOAK_S:.0f}s (use spike script for longer)", flush=True)
        soak_s = _MAX_SOAK_S

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

    return run_looper(
        capture=capture,
        playback=playback,
        sample_rate=args.sample_rate,
        period_frames=args.buffer_size,
        bars=args.bars,
        bpm=args.bpm,
        loop_gain=args.loop_gain,
        use_apc=not args.no_apc,
        soak_s=soak_s,
    )


if __name__ == "__main__":
    raise SystemExit(main())
