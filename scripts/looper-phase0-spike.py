#!/usr/bin/env python3
"""Phase 0 throwaway spike: loopback capture → mix → Sound Blaster playback.

Manual Pi test for LOOPER-PLAN.md Phase 0 (passthrough, xruns, one-bar loop).

Requires: arecord, aplay, snd-aloop loaded, Surge output routed to Loopback.

Examples:
  # 0.1 passthrough (512-frame periods, 48 kHz)
  python3 scripts/looper-phase0-spike.py passthrough --buffer-size 512

  # 0.4 record one 4-bar loop at 120 BPM then overdub-free playback mix
  python3 scripts/looper-phase0-spike.py loop --bars 4 --bpm 120 --buffer-size 512

  # 0.2 soak helper — print xrun totals every 5s for 600s
  python3 scripts/looper-phase0-spike.py passthrough --duration 600 --report-interval 5
"""

from __future__ import annotations

import argparse
import signal
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from patch_browser.looper_devices import (  # noqa: E402
    prepare_looper_audio_path,
    surge_loopback_hint,
)
from patch_browser.looper_engine import (  # noqa: E402
    StereoRingBuffer,
    apply_gain_s16_stereo,
    frames_to_bytes,
    loop_length_frames,
    mix_s16_stereo,
)
from patch_browser.looper_xruns import read_xrun_counts, total_xruns  # noqa: E402

_STOP = False


def _handle_stop(_signum: int, _frame: object) -> None:
    global _STOP
    _STOP = True


def _open_arecord(device: str, *, sample_rate: int, period_frames: int) -> subprocess.Popen[bytes]:
    buffer_frames = max(period_frames * 2, period_frames + 1)
    return subprocess.Popen(
        [
            "arecord",
            "-q",
            "-D",
            device,
            "-f",
            "S16_LE",
            "-c",
            "2",
            "-r",
            str(sample_rate),
            "--buffer-size",
            str(buffer_frames),
            "--period-size",
            str(period_frames),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _open_aplay(device: str, *, sample_rate: int, period_frames: int) -> subprocess.Popen[bytes]:
    buffer_frames = max(period_frames * 2, period_frames + 1)
    return subprocess.Popen(
        [
            "aplay",
            "-q",
            "-D",
            device,
            "-f",
            "S16_LE",
            "-c",
            "2",
            "-r",
            str(sample_rate),
            "--buffer-size",
            str(buffer_frames),
            "--period-size",
            str(period_frames),
        ],
        stdin=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _report_xruns(label: str, baseline: dict[str, int]) -> int:
    current = read_xrun_counts()
    delta = 0
    for path, count in current.items():
        delta += max(0, count - baseline.get(path, 0))
    print(f"[{label}] xrun delta={delta} total={total_xruns(current)}", flush=True)
    return delta


def _ensure_audio_procs_started(*procs: tuple[str, subprocess.Popen[object]]) -> int | None:
    """Return exit code if any ALSA child failed to start; else None."""
    for label, proc in procs:
        if proc.poll() is None:
            continue
        err = proc.stderr.read() if proc.stderr is not None else b""
        text = err.decode("utf-8", errors="replace").strip()
        print(f"Error: {label} failed to start: {text or 'unknown'}", file=sys.stderr)
        return 2
    return None


def run_passthrough(
    *,
    capture: str,
    playback: str,
    sample_rate: int,
    period_frames: int,
    duration_s: float | None,
    report_interval_s: float,
) -> int:
    baseline = read_xrun_counts()
    period_bytes = frames_to_bytes(period_frames)
    rec = _open_arecord(capture, sample_rate=sample_rate, period_frames=period_frames)
    play = _open_aplay(playback, sample_rate=sample_rate, period_frames=period_frames)
    assert rec.stdout is not None
    assert play.stdin is not None

    if (code := _ensure_audio_procs_started(("arecord", rec), ("aplay", play))) is not None:
        return code

    start = time.monotonic()
    last_report = start
    periods = 0
    short_reads = 0

    try:
        while not _STOP:
            if duration_s is not None and (time.monotonic() - start) >= duration_s:
                break
            chunk = rec.stdout.read(period_bytes)
            if not chunk:
                break
            if len(chunk) < period_bytes:
                short_reads += 1
                chunk = chunk + b"\x00" * (period_bytes - len(chunk))
            play.stdin.write(chunk)
            play.stdin.flush()
            periods += 1
            now = time.monotonic()
            if now - last_report >= report_interval_s:
                elapsed = now - start
                print(
                    f"[passthrough] {elapsed:.0f}s periods={periods} short_reads={short_reads}",
                    flush=True,
                )
                _report_xruns("passthrough", baseline)
                last_report = now
    finally:
        for proc in (rec, play):
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()

    elapsed = time.monotonic() - start
    xrun_delta = _report_xruns("final", baseline)
    print(
        f"Done: {elapsed:.1f}s periods={periods} short_reads={short_reads} xrun_delta={xrun_delta}",
        flush=True,
    )
    return 1 if xrun_delta or short_reads else 0


def run_loop(
    *,
    capture: str,
    playback: str,
    sample_rate: int,
    period_frames: int,
    bars: int,
    bpm: float,
    loop_gain: float,
    duration_s: float | None,
    report_interval_s: float,
) -> int:
    """Record one loop length, then mix live input with loop playback (wrap)."""
    baseline = read_xrun_counts()
    loop_frames = loop_length_frames(bars=bars, bpm=bpm, sample_rate=sample_rate)
    ring = StereoRingBuffer(loop_frames)
    period_bytes = frames_to_bytes(period_frames)

    rec = _open_arecord(capture, sample_rate=sample_rate, period_frames=period_frames)
    play = _open_aplay(playback, sample_rate=sample_rate, period_frames=period_frames)
    assert rec.stdout is not None
    assert play.stdin is not None

    if (code := _ensure_audio_procs_started(("arecord", rec), ("aplay", play))) is not None:
        return code

    start = time.monotonic()
    last_report = start
    playback_frame = 0
    recording = True
    periods = 0

    print(
        f"Loop: {bars} bars @ {bpm} BPM = {loop_frames} frames ({loop_frames / sample_rate:.2f}s)",
        flush=True,
    )

    try:
        while not _STOP:
            if duration_s is not None and (time.monotonic() - start) >= duration_s:
                break
            chunk = rec.stdout.read(period_bytes)
            if not chunk:
                break
            if len(chunk) < period_bytes:
                chunk = chunk + b"\x00" * (period_bytes - len(chunk))

            if recording:
                stored = ring.write_frames(chunk)
                if ring.is_full:
                    recording = False
                    playback_frame = 0
                    print("[loop] capture full — switching to playback mix", flush=True)

            if recording:
                out = chunk
            else:
                loop_pcm = ring.read_frames(playback_frame, period_frames)
                loop_pcm = apply_gain_s16_stereo(loop_pcm, loop_gain)
                out = mix_s16_stereo(chunk, loop_pcm, gains=(1.0, 1.0))
                playback_frame = (playback_frame + period_frames) % loop_frames

            play.stdin.write(out)
            play.stdin.flush()
            periods += 1

            now = time.monotonic()
            if now - last_report >= report_interval_s:
                mode = "record" if recording else "mix"
                print(f"[loop/{mode}] {now - start:.0f}s periods={periods}", flush=True)
                _report_xruns("loop", baseline)
                last_report = now
    finally:
        for proc in (rec, play):
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()

    xrun_delta = _report_xruns("final", baseline)
    print(f"Done: periods={periods} xrun_delta={xrun_delta}", flush=True)
    return 1 if xrun_delta else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "mode",
        choices=("passthrough", "loop"),
        help="passthrough = Phase 0.1; loop = Phase 0.4 one-bar capture + mix",
    )
    parser.add_argument("--sample-rate", type=int, default=48000)
    parser.add_argument("--buffer-size", type=int, default=512, help="ALSA period frames")
    parser.add_argument("--duration", type=float, default=None, help="Stop after N seconds")
    parser.add_argument("--report-interval", type=float, default=5.0)
    parser.add_argument("--bars", type=int, default=4)
    parser.add_argument("--bpm", type=float, default=120.0)
    parser.add_argument("--loop-gain", type=float, default=0.85, help="Loop layer gain in mix mode")
    parser.add_argument(
        "--skip-modprobe",
        action="store_true",
        help="Do not load snd-aloop (already loaded)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    signal.signal(signal.SIGINT, _handle_stop)
    signal.signal(signal.SIGTERM, _handle_stop)

    args = build_parser().parse_args(argv)
    try:
        capture, playback = prepare_looper_audio_path(load_loopback=not args.skip_modprobe)
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    print(f"Capture:  {capture}", flush=True)
    print(f"Playback: {playback}", flush=True)
    print(f"Hint: {surge_loopback_hint()}", flush=True)

    if args.mode == "passthrough":
        return run_passthrough(
            capture=capture,
            playback=playback,
            sample_rate=args.sample_rate,
            period_frames=args.buffer_size,
            duration_s=args.duration,
            report_interval_s=args.report_interval,
        )

    return run_loop(
        capture=capture,
        playback=playback,
        sample_rate=args.sample_rate,
        period_frames=args.buffer_size,
        bars=args.bars,
        bpm=args.bpm,
        loop_gain=args.loop_gain,
        duration_s=args.duration,
        report_interval_s=args.report_interval,
    )


if __name__ == "__main__":
    raise SystemExit(main())
