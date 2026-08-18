"""OSC orchestration: save main + scratch loops, seam merge, reload main."""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Callable

from seam_merge import merge_tail_at_seam

SCRATCH_LOOP = int(os.environ.get("MPE_SL_SCRATCH_LOOP", "15"))
SEAM_MERGE_SAMPLES = int(os.environ.get("MPE_SL_SEAM_MERGE_SAMPLES", "2048"))
SEAM_WELD_ENABLED = os.environ.get("MPE_SL_SEAM_WELD", "1").strip().lower() not in (
    "",
    "0",
    "off",
    "false",
)
SEAM_TMP_DIR = Path(os.environ.get("MPE_SL_SEAM_TMP", "/tmp/mpe-seam-weld"))
SAVE_POLL_S = float(os.environ.get("MPE_SL_SEAM_SAVE_POLL_S", "0.05"))
SAVE_TIMEOUT_S = float(os.environ.get("MPE_SL_SEAM_SAVE_TIMEOUT_S", "8.0"))


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
            args=(main_loop, scratch_loop),
            daemon=True,
            name="seam-weld",
        )
        thread.start()
        return True

    def _run(self, main_loop: int, scratch_loop: int) -> None:
        ok = False
        try:
            ok = self._merge(main_loop, scratch_loop)
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

    def _merge(self, main_loop: int, scratch_loop: int) -> bool:
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
        try:
            merge_tail_at_seam(
                main_wav,
                tail_wav,
                out_wav,
                merge_samples=SEAM_MERGE_SAMPLES,
            )
        except (OSError, ValueError) as exc:
            self._log(f"seam-weld: merge error: {exc}", flush=True)
            self._clear_scratch(scratch_loop)
            return False
        self._log(
            f"seam-weld: loading merged buffer onto loop {main_loop}",
            flush=True,
        )
        self._send(
            f"/sl/{main_loop}/load_loop",
            [str(out_wav), "", ""],
        )
        time.sleep(0.15)
        self._clear_scratch(scratch_loop)
        self._log(f"seam-weld: done loop {main_loop}", flush=True)
        return True

    def _clear_scratch(self, scratch_loop: int) -> None:
        self._send(f"/sl/{scratch_loop}/hit", ["undo_all"])

    def prepare_scratch(self, scratch_loop: int = SCRATCH_LOOP) -> None:
        """Ensure scratch slot is empty before tail capture."""
        self._clear_scratch(scratch_loop)

    def start_scratch_record(self, scratch_loop: int = SCRATCH_LOOP) -> None:
        self._send(f"/sl/{scratch_loop}/hit", ["record"])

    def stop_scratch_record(self, scratch_loop: int = SCRATCH_LOOP) -> None:
        self._send(f"/sl/{scratch_loop}/hit", ["record"])
