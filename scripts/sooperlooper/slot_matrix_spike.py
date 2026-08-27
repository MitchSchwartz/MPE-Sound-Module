#!/usr/bin/env python3
"""Slot-matrix spikes SP1/SP2/SP4/SP7 — multi-clip-per-track-spec §Spike.

Measures what the spec says must be measured before P1/P2 implementation:

  SP1  save_loop / load_loop latency across the full 16-track matrix
  SP2  single-swap load_loop latency (the launch path)
  SP4  switch at one boundary: mute outgoing + load + trigger incoming
  SP7  switch queued while the ring-out overdub is running (rev 3, OPEN-4)

Run on the Pi with SooperLooper up:

    python3 scripts/sooperlooper/slot_matrix_spike.py --all
    python3 scripts/sooperlooper/slot_matrix_spike.py --sp1 --slots 4

SP1/SP2 are destructive to loop contents: they load synthetic WAVs into
loops and clear them afterwards. Do not run against a take you want.

Register: everything printed here is **measured**, not modelled. Latency is
wall-clock around the OSC call plus the observable effect (file on disk for
save; state change for load), because SooperLooper does not acknowledge
either over OSC.
"""

from __future__ import annotations

import argparse
import os
import shutil
import socket
import statistics
import struct
import sys
import time
from pathlib import Path

SL_HOST = os.environ.get("MPE_SL_OSC_HOST", "127.0.0.1")
SL_PORT = int(os.environ.get("MPE_SL_OSC_PORT", "9951"))
NUM_LOOPS = int(os.environ.get("MPE_SL_LOOPS", "16"))
SPIKE_DIR = Path(os.environ.get("MPE_SLOT_SPIKE_DIR", "/tmp/mpe-slot-spike"))
SAMPLE_RATE = 48000
_WAVE_FORMAT_IEEE_FLOAT = 3


# --- minimal OSC (SL speaks 1.0; python-osc is not guaranteed on the Pi) ----
def _pad(b: bytes) -> bytes:
    b = b + b"\x00"
    return b + b"\x00" * ((4 - len(b) % 4) % 4)


def _msg(addr: str, args) -> bytes:
    out = _pad(addr.encode())
    tt = ","
    blob = b""
    for a in args:
        if isinstance(a, str):
            tt += "s"
            blob += _pad(a.encode())
        elif isinstance(a, bool):
            tt += "i"
            blob += struct.pack(">i", int(a))
        elif isinstance(a, int):
            tt += "i"
            blob += struct.pack(">i", a)
        else:
            tt += "f"
            blob += struct.pack(">f", float(a))
    return out + _pad(tt.encode()) + blob


class Osc:
    """Send commands, and ask for control values with a real reply socket."""

    def __init__(self) -> None:
        self._tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._rx.bind(("127.0.0.1", 0))
        self._rx.settimeout(0.5)
        self._returl = "osc.udp://127.0.0.1:%d/" % self._rx.getsockname()[1]

    def send(self, addr: str, args) -> None:
        self._tx.sendto(_msg(addr, args), (SL_HOST, SL_PORT))

    def hit(self, loop: int, cmd: str) -> None:
        self.send(f"/sl/{loop}/hit", [cmd])

    def get(self, loop: int, ctrl: str, timeout: float = 0.5):
        # Drain anything stale so a previous reply cannot answer this question.
        self._rx.settimeout(0)
        try:
            while True:
                self._rx.recvfrom(4096)
        except OSError:
            pass
        self._rx.settimeout(timeout)
        self.send(f"/sl/{loop}/get", [ctrl, self._returl, "/reply"])
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                data, _ = self._rx.recvfrom(4096)
            except socket.timeout:
                return None
            vals = _parse(data)
            if len(vals) >= 3 and vals[1] == ctrl:
                return vals[2]
        return None


def _parse(data: bytes):
    def rdstr(b, i):
        e = b.index(b"\x00", i)
        s = b[i:e].decode()
        i = e + 1
        i += (4 - i % 4) % 4
        return s, i

    _addr, i = rdstr(data, 0)
    tt, i = rdstr(data, i)
    vals = []
    for ch in tt[1:]:
        if ch == "s":
            v, i = rdstr(data, i)
            vals.append(v)
        elif ch == "i":
            vals.append(struct.unpack_from(">i", data, i)[0])
            i += 4
        elif ch == "f":
            vals.append(struct.unpack_from(">f", data, i)[0])
            i += 4
    return vals


# --- synthetic clip content ------------------------------------------------
def write_clip(path: Path, *, seconds: float, hz: float) -> Path:
    """IEEE float32 stereo WAV — the format SooperLooper's save_loop writes.

    A distinct pitch per slot so SP4/SP7 can be checked by ear as well as by
    state: if two clips are audible after a switch, you hear an interval.
    """
    import math

    n = int(SAMPLE_RATE * seconds)
    pcm = bytearray()
    for i in range(n):
        # 5 ms fades so a looping clip does not click at its own seam.
        env = min(1.0, i / 240.0, (n - i) / 240.0)
        v = 0.25 * env * math.sin(2 * math.pi * hz * i / SAMPLE_RATE)
        pcm.extend(struct.pack("<ff", v, v))
    fmt = struct.pack("<HHIIHH", _WAVE_FORMAT_IEEE_FLOAT, 2, SAMPLE_RATE,
                      SAMPLE_RATE * 8, 8, 32)
    riff = 4 + (8 + len(fmt)) + (8 + len(pcm))
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"RIFF" + struct.pack("<I", riff) + b"WAVE")
        f.write(b"fmt " + struct.pack("<I", len(fmt)) + fmt)
        f.write(b"data" + struct.pack("<I", len(pcm)) + bytes(pcm))
    return path


def _stats(name: str, xs: list[float]) -> None:
    if not xs:
        print(f"  {name}: no samples")
        return
    xs = sorted(xs)
    p95 = xs[min(len(xs) - 1, int(0.95 * len(xs)))]
    print(f"  {name}: n={len(xs)} min={xs[0]*1000:.1f} "
          f"median={statistics.median(xs)*1000:.1f} p95={p95*1000:.1f} "
          f"max={xs[-1]*1000:.1f} ms")


def control(osc: Osc, *, seconds: float) -> None:
    """Positive control — the instrument must register a bigger job as slower.

    Rule 0.5: an instrument that reads the same whether the work is small or
    large is not measuring the work. If a 16 s clip loads in the same time as
    a 1 s clip, the numbers below are a round-trip floor, not a load time.
    """
    print("\nCONTROL — does measured load time move with clip length?")
    for secs in (1.0, 4.0, 16.0):
        clip = write_clip(SPIKE_DIR / f"ctl-{secs}.wav", seconds=secs, hz=220.0)
        osc.hit(0, "undo_all")
        time.sleep(0.2)
        t0 = time.monotonic()
        osc.send("/sl/0/load_loop", [str(clip), "", ""])
        ok = _wait_len(osc, 0, secs, 10.0)
        dt = (time.monotonic() - t0) * 1000
        print(f"  {secs:>5.1f}s clip ({clip.stat().st_size/1e6:.1f} MB): "
              f"{dt:7.1f} ms{'' if ok else '  !! never landed'}")
    osc.hit(0, "undo_all")
    print("  If these are flat, the load numbers are a floor — do not use them.")


# --- SP1 -------------------------------------------------------------------
def sp1(osc: Osc, *, slots: int, seconds: float) -> None:
    print(f"\nSP1 — save/load latency, {NUM_LOOPS} tracks x {slots} slots "
          f"({NUM_LOOPS * slots} clips, ~{seconds}s each)")
    # Alternate two lengths so every load is observable: see _wait_len.
    a = write_clip(SPIKE_DIR / "src-a.wav", seconds=seconds, hz=220.0)
    b = write_clip(SPIKE_DIR / "src-b.wav", seconds=seconds * 0.75, hz=220.0)
    loads, saves, missed, missed_saves = [], [], 0, 0
    for track in range(NUM_LOOPS):
        for slot in range(slots):
            src, want = (a, seconds) if slot % 2 == 0 else (b, seconds * 0.75)
            dst = SPIKE_DIR / f"t{track}s{slot}.wav"
            shutil.copyfile(src, dst)
            t0 = time.monotonic()
            osc.send(f"/sl/{track}/load_loop", [str(dst), "", ""])
            if _wait_len(osc, track, want, 5.0):
                loads.append(time.monotonic() - t0)
            else:
                missed += 1
            out = SPIKE_DIR / f"save-t{track}s{slot}.wav"
            if out.exists():
                out.unlink()
            want_bytes = 44 + int(SAMPLE_RATE * want) * 8
            t0 = time.monotonic()
            osc.send(f"/sl/{track}/save_loop", [str(out), "", "", "", ""])
            if _wait_file(out, 10.0, want_bytes=want_bytes):
                saves.append(time.monotonic() - t0)
            else:
                missed_saves += 1
    _stats("load_loop", loads)
    _stats("save_loop", saves)
    if missed:
        print(f"  !! {missed} loads never reached the expected length — "
              f"numbers above are incomplete")
    if missed_saves:
        print(f"  !! {missed_saves} saves never reached the expected size")
    print(f"  full-matrix save total: {sum(saves):.1f}s "
          f"({NUM_LOOPS * slots} clips) — this is the touch-UI budget")


def _wait_file(path: Path, timeout: float, *, want_bytes: int | None = None) -> bool:
    """Wait for a save to *finish*, not to start.

    The first version accepted any file over 64 bytes, so it timed the moment
    SooperLooper created the header — flat ~5 ms regardless of clip length,
    which would have meant ~280 MB/s to the SD card. Callers that know the
    expected size pass it; the fallback waits for the size to stop growing.
    """
    deadline = time.monotonic() + timeout
    last, stable_since = -1, None
    while time.monotonic() < deadline:
        if path.exists():
            size = path.stat().st_size
            if want_bytes is not None:
                if size >= want_bytes:
                    return True
            else:
                if size == last and size > 64:
                    if stable_since is None:
                        stable_since = time.monotonic()
                    elif time.monotonic() - stable_since > 0.02:
                        return True
                else:
                    stable_since = None
                last = size
        time.sleep(0.002)
    return False


def _wait_len(osc: Osc, loop: int, want: float, timeout: float) -> bool:
    """Wait for the loop to actually hold a clip of ``want`` seconds.

    NOT "state is no longer empty". A loop that already holds a clip satisfies
    that the instant it is asked, so the first version of this measured one
    OSC round-trip instead of the load and reported a floor of 3.3 ms on every
    call. Loop length is the one observable that a stale buffer cannot fake —
    so consecutive loads alternate between clips of *different* length.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        got = osc.get(loop, "loop_len", timeout=0.1)
        if got is not None and abs(float(got) - want) < 0.02:
            return True
    return False


# --- SP2 -------------------------------------------------------------------
def sp2(osc: Osc, *, trials: int, seconds: float) -> None:
    print(f"\nSP2 — single-swap load_loop latency ({trials} trials)")
    a = write_clip(SPIKE_DIR / "swap-a.wav", seconds=seconds, hz=220.0)
    b = write_clip(SPIKE_DIR / "swap-b.wav", seconds=seconds * 0.75, hz=330.0)
    xs = []
    missed = 0
    for i in range(trials):
        path, want = (a, seconds) if i % 2 else (b, seconds * 0.75)
        t0 = time.monotonic()
        osc.send("/sl/0/load_loop", [str(path), "", ""])
        if _wait_len(osc, 0, want, 5.0):
            xs.append(time.monotonic() - t0)
        else:
            missed += 1
    _stats("swap load_loop", xs)
    if missed:
        print(f"  !! {missed} swaps never took effect")
    print("  budget: must fit inside one bar at the working tempo "
          "(120 BPM 4/4 = 2000 ms) with room for the mute + trigger")


def sp3(osc: Osc, *, seconds: float) -> None:
    """SP3 / SP3b — do mute_off and pause_on cancel a QUEUED action?

    This is an engine question, not a pad question: the bench dispatch is unit
    tested, the unknown is whether SooperLooper honours the cancel. Driven over
    OSC so no hardware gesture is needed.

    Conditions are SET here and printed, because the appliance idles with
    quantize = 0 and mute_quantized = 0 (no grid), where there is no pending
    window at all and both tests would pass vacuously.
    """
    print("\nSP3 / SP3b — cancel a queued mute / queued launch")
    clip = write_clip(SPIKE_DIR / "sp3.wav", seconds=seconds, hz=220.0)
    osc.hit(0, "undo_all")
    time.sleep(0.3)
    osc.send("/sl/0/load_loop", [str(clip), "", ""])
    if not _wait_len(osc, 0, seconds, 5.0):
        print("  !! clip never loaded — cannot run")
        return
    osc.send("/sl/0/set", ["quantize", 3.0])        # 3 = loop boundary
    osc.send("/sl/0/set", ["mute_quantized", 1.0])
    time.sleep(0.2)
    print(f"  conditions: quantize={osc.get(0,'quantize')} "
          f"mute_quantized={osc.get(0,'mute_quantized')} "
          f"cycle={seconds}s")

    # --- SP3: cancel a queued mute -------------------------------------
    osc.hit(0, "trigger")
    time.sleep(0.6)
    if osc.get(0, "state") != 4.0:
        print("  !! loop not playing — SP3 inconclusive")
    else:
        osc.hit(0, "mute_on")
        time.sleep(0.15)
        queued = osc.get(0, "state")
        osc.hit(0, "mute_off")
        time.sleep(seconds + 0.6)          # past the boundary
        after = osc.get(0, "state")
        ok = after == 4.0
        verdict = "state kept playing" if ok else "state muted anyway"
        if queued == 10.0:
            verdict += "  [INCONCLUSIVE: SL reported MUTE immediately, so " \
                       "there was no pending state to cancel]"
        print(f"  SP3  queued-mute state={queued} -> after boundary={after}  {verdict}")

    # --- SP3b: cancel a queued launch ----------------------------------
    osc.hit(0, "mute_on")
    time.sleep(seconds + 0.6)
    muted = osc.get(0, "state")
    osc.hit(0, "trigger")
    time.sleep(0.15)
    queued = osc.get(0, "state")
    osc.hit(0, "pause_on")
    time.sleep(seconds + 0.6)
    after = osc.get(0, "state")
    ok = after != 4.0
    verdict = "state stayed stopped" if ok else "state launched anyway"
    if queued == 4.0:
        verdict += "  [INCONCLUSIVE: SL reported PLAYING immediately, so " \
                   "pause_on stopped a launched loop rather than cancelling " \
                   "a queued one]"
    print(f"  SP3b muted={muted} queued-launch={queued} -> after boundary={after}  {verdict}")
    print("  states: 4=playing 10=mute 14=paused")
    print("  NOTE: SooperLooper sets the target state optimistically and defers")
    print("  the audio, so state polling cannot distinguish 'cancelled' from")
    print("  'happened, then undone'. Settling SP3/SP3b needs the EAR or an")
    print("  audio capture — see the spike write-up.")
    osc.send("/sl/0/set", ["quantize", 0.0])
    osc.send("/sl/0/set", ["mute_quantized", 0.0])
    osc.hit(0, "undo_all")


# --- SP4 -------------------------------------------------------------------
def sp4(osc: Osc, *, seconds: float) -> None:
    print("\nSP4 — switch: mute outgoing + load + trigger incoming")
    a = write_clip(SPIKE_DIR / "sp4-a.wav", seconds=seconds, hz=220.0)
    b = write_clip(SPIKE_DIR / "sp4-b.wav", seconds=seconds, hz=330.0)
    osc.send("/sl/0/load_loop", [str(a), "", ""])
    time.sleep(0.4)
    osc.hit(0, "trigger")
    time.sleep(0.4)
    before = osc.get(0, "state")
    t0 = time.monotonic()
    osc.hit(0, "mute_on")
    osc.send("/sl/0/load_loop", [str(b), "", ""])
    osc.hit(0, "mute_off")
    osc.hit(0, "trigger")
    time.sleep(0.5)
    after = osc.get(0, "state")
    print(f"  state before={before} after={after} "
          f"sequence took {(time.monotonic()-t0)*1000:.0f} ms")
    print("  PASS if after is 4 (playing) and you hear ONE pitch, not two.")
    print("  Listen: A is 220 Hz, B is 330 Hz — a fifth apart if both sound.")


# --- SP7 -------------------------------------------------------------------
def sp7(osc: Osc, *, seconds: float) -> None:
    print("\nSP7 — switch queued while the ring-out overdub runs (OPEN-4)")
    a = write_clip(SPIKE_DIR / "sp7-a.wav", seconds=seconds, hz=220.0)
    b = write_clip(SPIKE_DIR / "sp7-b.wav", seconds=seconds, hz=330.0)
    osc.send("/sl/0/load_loop", [str(a), "", ""])
    time.sleep(0.4)
    osc.hit(0, "trigger")
    time.sleep(0.3)
    osc.hit(0, "overdub")
    time.sleep(0.2)
    during = osc.get(0, "state")
    print(f"  state during overdub = {during} (5 = overdubbing)")
    t0 = time.monotonic()
    osc.hit(0, "overdub")          # end the overdub
    osc.hit(0, "mute_on")
    osc.send("/sl/0/load_loop", [str(b), "", ""])
    osc.hit(0, "mute_off")
    osc.hit(0, "trigger")
    time.sleep(0.5)
    after = osc.get(0, "state")
    print(f"  state after = {after}, sequence took "
          f"{(time.monotonic()-t0)*1000:.0f} ms")
    print("  PASS if after is 4 (playing) and the switch is clean by ear.")
    print("  This settles OPEN-4: can overdub-off + switch share one boundary?")


def clear(osc: Osc) -> None:
    for loop in range(NUM_LOOPS):
        osc.hit(loop, "undo_all")
    print("cleared all loops")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sp1", action="store_true")
    ap.add_argument("--sp2", action="store_true")
    ap.add_argument("--sp3", action="store_true")
    ap.add_argument("--sp4", action="store_true")
    ap.add_argument("--sp7", action="store_true")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--control", action="store_true",
                    help="positive control: does load time move with clip length?")
    ap.add_argument("--slots", type=int, default=8, help="slots per track (SP1)")
    ap.add_argument("--trials", type=int, default=20, help="swaps (SP2)")
    ap.add_argument("--seconds", type=float, default=2.0, help="clip length")
    ap.add_argument("--keep", action="store_true", help="do not clear loops after")
    a = ap.parse_args()
    if not (a.sp1 or a.sp2 or a.sp3 or a.sp4 or a.sp7 or a.control or a.all):
        ap.print_help()
        return 2

    osc = Osc()
    if osc.get(0, "state") is None:
        print("no reply from SooperLooper on "
              f"{SL_HOST}:{SL_PORT} — is the engine up?", file=sys.stderr)
        return 1
    SPIKE_DIR.mkdir(parents=True, exist_ok=True)
    print(f"slot-matrix spike — {NUM_LOOPS} loops, artifacts in {SPIKE_DIR}")

    if a.control or a.all:
        control(osc, seconds=a.seconds)
    if a.sp1 or a.all:
        sp1(osc, slots=a.slots, seconds=a.seconds)
    if a.sp2 or a.all:
        sp2(osc, trials=a.trials, seconds=a.seconds)
    if a.sp3 or a.all:
        sp3(osc, seconds=a.seconds)
    if a.sp4 or a.all:
        sp4(osc, seconds=a.seconds)
    if a.sp7 or a.all:
        sp7(osc, seconds=a.seconds)
    if not a.keep:
        clear(osc)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
