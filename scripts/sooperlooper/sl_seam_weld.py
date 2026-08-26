"""OSC orchestration: save main + scratch loops, seam merge, reload main.

**DO NOT CHANGE** seam weld logic, timing, or merge geometry in this module (or
``seam_merge.py`` / tail-capture paths in ``apc_footswitch.py``) without
**explicit written permission from Mitch**. Revert to known-good code; do not
re-derive or reinvent. Canon: ``Documents/specs/looper-loop-seam-spec.md``,
``docs/measurements/PI5-LOOPER-SEAM-WRAP.md``.
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Callable

from seam_merge import (
    DEFAULT_DECLICK_SAMPLES,
    DEFAULT_FADE_IN_SAMPLES,
    merge_tail_at_seam,
)

# Default 14, not 15: Pi 5 SooperLooper 1.7.9 (arm64 trixie build) accepts save_loop
# on loops 0–14 but loop index 15 always returns an empty 88 B WAV — tail capture
# silently fails. Override via MPE_SL_SCRATCH_LOOP if a native build fixes loop 15.
SCRATCH_LOOP = int(os.environ.get("MPE_SL_SCRATCH_LOOP", "14"))
# Retired 2026-08-25: named the head/end crossfade that stepped full-scale at the
# seam. Read only so an old export in the environment cannot resurrect it.
SEAM_MERGE_SAMPLES = 0
# Linear fade on both tail edges before it is summed into the head, in samples.
SEAM_DECLICK_SAMPLES = int(
    os.environ.get("MPE_SL_SEAM_DECLICK_SAMPLES", str(DEFAULT_DECLICK_SAMPLES))
)
# Waiting for the PLAYING state costs an OSC round-trip. Measured across four
# takes the scratch armed 17, 36, 53 and 68 ms after the loop wrapped — that
# much release tail was never captured by anything, which is why the wrap still
# steps down to ~0.5-0.7x of the take-end level after the fade fix, and why the
# amount varies take to take. Arming at WAIT_STOP starts the scratch before the
# boundary so the release is captured continuously.
#
# The cost: the scratch head is then take content, which the merge skips (see
# _tail_skip_seconds). Kill switch if it disturbs the take: MPE_SL_SEAM_EARLY_ARM=0.
SEAM_EARLY_ARM = os.environ.get("MPE_SL_SEAM_EARLY_ARM", "1").strip().lower() not in (
    "",
    "0",
    "off",
    "false",
)
# Trim a little extra off the skip. Erring long clips a few ms of ring-out and
# is inaudible; erring short leaves take content on the head and flams.
SEAM_EARLY_ARM_BIAS_S = (
    float(os.environ.get("MPE_SL_SEAM_EARLY_ARM_BIAS_MS", "10")) / 1000.0
)
# Fade-in at the wrap. Short on purpose — a long one digs a hole at the seam.
SEAM_FADE_IN_SAMPLES = int(
    os.environ.get("MPE_SL_SEAM_FADE_IN_SAMPLES", str(DEFAULT_FADE_IN_SAMPLES))
)
# Where the tail lands in the head. The scratch loop only arms once SL reports
# the main loop PLAYING, so tail[0] is already some ms past the stop instant;
# a positive offset pushes it later still. Ear-tune on the Pi, then pin here.
SEAM_TAIL_OFFSET_SAMPLES = int(
    os.environ.get("MPE_SL_SEAM_TAIL_OFFSET_SAMPLES", "0")
)
# Align the tail to where the scratch loop actually armed? Default OFF.
#
# It sounds right and it measures wrong. The scratch arms ~36-53 ms after the
# stop, so placing the tail there is physically truthful — but the ring-out
# from those 36 ms was never captured by anything, so truthful placement just
# uncovers the hole. Measured on the 21:58 take (5.823 s clip, pos=0.036 s),
# head RMS in 10 ms windows:
#
#   align off  0.138  0.166  0.167  0.166  0.167 ...
#   align on   0.035  0.000  0.000  0.044  0.163 ...   <- 20 ms of silence
#
# The take's own head is empty (record hit before the first note) and the loop
# end is at 0.164, so every wrap is an energy cliff the tail exists to bridge.
# Landing it at 0 bridges it. A 36 ms timing error on a decaying tail is
# inaudible; a 20 ms dropout is a stutter. Turn on only with a take whose head
# is already full, where there is no hole to uncover.
SEAM_TAIL_ALIGN = os.environ.get("MPE_SL_SEAM_TAIL_ALIGN", "0").strip().lower() in (
    "1",
    "on",
    "true",
)
SEAM_WELD_ENABLED = os.environ.get("MPE_SL_SEAM_WELD", "1").strip().lower() not in (
    "",
    "0",
    "off",
    "false",
)
# The merged buffer is swapped in at a WRAP boundary, never mid-pass.
# `trigger` restarts the loop from sample 0 (tests/test_apc_footswitch.py
# ::test_launch_is_a_quantized_trigger_from_the_clip_start), so firing it at a
# random moment yanks the playhead to the start — that is a jump, not a seam.
# Fired at the wrap, the restart IS the wrap and nothing moves.
# How early load_loop goes out, so the engine has the buffer before the wrap.
SEAM_LOAD_LEAD_S = float(os.environ.get("MPE_SL_SEAM_LOAD_LEAD_MS", "150")) / 1000.0
# Slack for OSC + engine dispatch on the trigger itself.
SEAM_TRIGGER_LAG_S = float(os.environ.get("MPE_SL_SEAM_TRIGGER_LAG_MS", "5")) / 1000.0
SEAM_SWAP_POLL_S = float(os.environ.get("MPE_SL_SEAM_SWAP_POLL_MS", "3")) / 1000.0
# Give up waiting for a wrap after this long and swap anyway (audible seam).
SEAM_SWAP_TIMEOUT_S = float(os.environ.get("MPE_SL_SEAM_SWAP_TIMEOUT_S", "12.0"))
SEAM_TMP_DIR = Path(os.environ.get("MPE_SL_SEAM_TMP", "/tmp/mpe-seam-weld"))
SAVE_POLL_S = float(os.environ.get("MPE_SL_SEAM_SAVE_POLL_S", "0.05"))
SAVE_TIMEOUT_S = float(os.environ.get("MPE_SL_SEAM_SAVE_TIMEOUT_S", "8.0"))
# Scratch loop is capture-only — mute its live mix, not the record path.
SCRATCH_CAPTURE_WET = float(os.environ.get("MPE_SL_SCRATCH_CAPTURE_WET", "0"))
SCRATCH_CAPTURE_FEEDBACK = float(
    os.environ.get("MPE_SL_SCRATCH_CAPTURE_FEEDBACK", "0")
)
# save_loop files smaller than this are empty headers — skip merge.
MIN_TAIL_WAV_BYTES = int(os.environ.get("MPE_SL_MIN_TAIL_WAV_BYTES", "512"))
SCRATCH_RECORD_SETTLE_S = float(
    os.environ.get("MPE_SL_SCRATCH_RECORD_SETTLE_S", "0.05")
)


def _save_loop_blocking(send, loop: int, path: Path) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    send(f"/sl/{loop}/save_loop", [str(path), "", "", "", ""])
    deadline = time.monotonic() + SAVE_TIMEOUT_S
    while time.monotonic() < deadline:
        if path.exists() and path.stat().st_size > 64:
            return True
        time.sleep(SAVE_POLL_S)
    return False


class SeamWeldWorker:
    """Runs save → merge → load off the MIDI hot path."""

    def __init__(self, send: Callable[[str, list], None], *, log=print) -> None:
        self._send = send
        self._log = log
        self._lock = threading.Lock()
        self._busy = False
        self._done_cb: Callable[[], None] | None = None

    @property
    def busy(self) -> bool:
        with self._lock:
            return self._busy

    def request(
        self,
        main_loop: int,
        scratch_loop: int,
        *,
        done: Callable[[], None],
        position: Callable[[], tuple[float, float] | None] | None = None,
        tail_offset_s: float = 0.0,
        tail_skip_s: float = 0.0,
    ) -> bool:
        with self._lock:
            if self._busy:
                self._log(
                    f"seam-weld: busy — dropping merge for loop {main_loop}",
                    flush=True,
                )
                return False
            self._busy = True
            self._done_cb = done
        thread = threading.Thread(
            target=self._run,
            args=(main_loop, scratch_loop, position, tail_offset_s, tail_skip_s),
            daemon=True,
            name="seam-weld",
        )
        thread.start()
        return True

    def _run(
        self,
        main_loop: int,
        scratch_loop: int,
        position: Callable[[], tuple[float, float] | None] | None,
        tail_offset_s: float,
        tail_skip_s: float,
    ) -> None:
        ok = False
        try:
            ok = self._merge(
                main_loop, scratch_loop, position, tail_offset_s, tail_skip_s
            )
        finally:
            cb = None
            with self._lock:
                self._busy = False
                cb = self._done_cb
                self._done_cb = None
            if cb is not None:
                cb()

        if not ok:
            self._log(
                f"seam-weld: merge failed for loop {main_loop} — "
                f"loop unchanged except scratch cleared",
                flush=True,
            )

    def _merge(
        self,
        main_loop: int,
        scratch_loop: int,
        position: Callable[[], tuple[float, float] | None] | None,
        tail_offset_s: float = 0.0,
        tail_skip_s: float = 0.0,
    ) -> bool:
        tag = f"{main_loop}-{int(time.time() * 1000)}"
        main_wav = SEAM_TMP_DIR / f"main-{tag}.wav"
        tail_wav = SEAM_TMP_DIR / f"tail-{tag}.wav"
        out_wav = SEAM_TMP_DIR / f"merged-{tag}.wav"
        self._log(
            f"seam-weld: saving loop {main_loop} + scratch {scratch_loop}",
            flush=True,
        )
        if not _save_loop_blocking(self._send, main_loop, main_wav):
            self._log(f"seam-weld: save loop {main_loop} timed out", flush=True)
            self._clear_scratch(scratch_loop)
            return False
        if not _save_loop_blocking(self._send, scratch_loop, tail_wav):
            self._log(
                f"seam-weld: save scratch {scratch_loop} timed out",
                flush=True,
            )
            self._clear_scratch(scratch_loop)
            return False
        tail_bytes = tail_wav.stat().st_size if tail_wav.exists() else 0
        if tail_bytes < MIN_TAIL_WAV_BYTES:
            self._log(
                f"seam-weld: scratch tail too small ({tail_bytes} B) — "
                f"skip merge for loop {main_loop}",
                flush=True,
            )
            self._clear_scratch(scratch_loop)
            return False
        try:
            merge_tail_at_seam(
                main_wav,
                tail_wav,
                out_wav,
                merge_samples=SEAM_MERGE_SAMPLES,
                declick_samples=SEAM_DECLICK_SAMPLES,
                fade_in_samples=SEAM_FADE_IN_SAMPLES,
                offset_samples=SEAM_TAIL_OFFSET_SAMPLES,
                offset_seconds=tail_offset_s if SEAM_TAIL_ALIGN else 0.0,
                skip_seconds=tail_skip_s,
            )
        except (OSError, ValueError) as exc:
            self._log(f"seam-weld: merge error: {exc!r}", flush=True)
            self._clear_scratch(scratch_loop)
            return False
        self._swap_at_wrap(main_loop, out_wav, position)
        self._clear_scratch(scratch_loop)
        self._log(f"seam-weld: done loop {main_loop}", flush=True)
        return True

    def _time_to_wrap(
        self, position: Callable[[], tuple[float, float] | None] | None, lead: float
    ) -> float | None:
        """Block until the playhead is ``lead`` seconds from wrapping.

        Returns the seconds still left to the wrap at the moment it returns, or
        None if there is no usable position feed (caller then swaps blind).
        """
        if position is None:
            return None
        deadline = time.monotonic() + SEAM_SWAP_TIMEOUT_S
        while time.monotonic() < deadline:
            snap = position()
            if snap is None:
                return None
            pos, length = snap
            if length <= 0.0:
                return None
            remaining = length - pos
            if remaining <= lead:
                return max(0.0, remaining)
            time.sleep(min(SEAM_SWAP_POLL_S, remaining - lead))
        self._log(
            f"seam-weld: no wrap within {SEAM_SWAP_TIMEOUT_S:.1f}s — swapping blind",
            flush=True,
        )
        return None

    def _swap_at_wrap(
        self,
        main_loop: int,
        out_wav: Path,
        position: Callable[[], tuple[float, float] | None] | None,
    ) -> None:
        """Land load_loop + trigger on the wrap, so the restart IS the wrap."""
        remaining = self._time_to_wrap(position, SEAM_LOAD_LEAD_S)
        if remaining is None:
            self._log(
                f"seam-weld: loading merged buffer onto loop {main_loop} "
                f"(no position feed — expect a seam)",
                flush=True,
            )
        else:
            self._log(
                f"seam-weld: loading merged buffer onto loop {main_loop} "
                f"{remaining * 1000:.0f}ms before wrap",
                flush=True,
            )
        self._send(f"/sl/{main_loop}/load_loop", [str(out_wav), "", ""])
        # NOTE (unverified on Pi 5): whether load_loop halts playback decides
        # whether this lead is a safe pre-load or an audible dropout. Settle it
        # by watching loop_pos across a load_loop with SEAM_LOAD_LEAD_MS=600 —
        # if the position freezes, the lead must shrink to the load cost.
        wait = SEAM_LOAD_LEAD_S if remaining is None else remaining
        time.sleep(max(0.0, wait - SEAM_TRIGGER_LAG_S))
        self._send(f"/sl/{main_loop}/hit", ["pause_off"])
        self._send(f"/sl/{main_loop}/hit", ["trigger"])

    def _clear_scratch(self, scratch_loop: int) -> None:
        self._send(f"/sl/{scratch_loop}/hit", ["undo_all"])

    def _silence_scratch_live(self, scratch_loop: int) -> None:
        """Mute scratch playback in the mix; do not touch dry (record path)."""
        for control, value in (
            ("wet", SCRATCH_CAPTURE_WET),
            ("feedback", SCRATCH_CAPTURE_FEEDBACK),
        ):
            self._send(f"/sl/{scratch_loop}/set", [control, float(value)])

    def prepare_scratch(self, scratch_loop: int = SCRATCH_LOOP) -> None:
        """Ensure scratch slot is empty and inaudible before tail capture."""
        self._clear_scratch(scratch_loop)
        self._silence_scratch_live(scratch_loop)

    def start_scratch_record(self, scratch_loop: int = SCRATCH_LOOP) -> None:
        self._silence_scratch_live(scratch_loop)
        # Bare record on an off loop often yields empty save_loop on Pi — arm first.
        self._send(f"/sl/{scratch_loop}/hit", ["pause_off"])
        self._send(f"/sl/{scratch_loop}/hit", ["trigger"])
        self._send(f"/sl/{scratch_loop}/hit", ["record"])

    def stop_scratch_record(self, scratch_loop: int = SCRATCH_LOOP) -> None:
        self._send(f"/sl/{scratch_loop}/hit", ["record"])
        time.sleep(SCRATCH_RECORD_SETTLE_S)
