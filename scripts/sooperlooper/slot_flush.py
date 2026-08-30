"""Has this take reached disk? One owner, one key, one answer.

The key is **`(loop, slot)`**, and every method that can produce a verdict
takes both. That is not a tidiness preference — it is the entire module.

From the day the split save landed until 2026-08-30 the in-flight saves lived
in `SlotRuntime._flush`, a `dict[int, tuple[int, Path, Path, float]]` keyed by
**loop**, whose tuple's first element was the **slot**. The code already knew
the slot mattered; nothing ever compared it. `_ensure_flushed(loop)` asked
about the track's *active* slot and then wrote:

    if loop not in self._flush:
        self._begin_flush(loop, track.active_slot)
    return self.poll_flush(loop)

`loop not in self._flush` reads "a job exists for this loop" as "a job exists
for THIS SLOT". With a save for slot 0 still in flight and the active slot now
slot 1 and dirty, no save was ever started for slot 1 — and `poll_flush(loop)`
then resolved the **slot-0** job, renamed slot 0's temp over slot 0's path,
marked **slot 0** clean and returned `clean`.

The caller had asked about slot 1. Its own words for what it does with that
answer:

    "REFUSING to switch — the take on the current slot did not reach disk,
     and switching would overwrite the buffer holding it"

So the wrong key routed around the one safety net standing between an unsaved
take and the buffer about to be reused. It is silent: the pad lights, the model
says saved, and the audio is gone. Measurement-integrity shape — the reading is
identical whether it worked or not.

**Charter §3, the three questions.**

1. *Who owns it?* This module. `FlushLedger` is the only code in the repo that
   renames a recorded temp over a live clip path.
2. *Who may write it?* `FlushLedger` alone. `SlotRuntime` starts and drops
   jobs; it never touches the files.
3. *How would you know if that were violated?* `tests/test_track_state_ownership.py`.
   It fails, naming file and line, if any verdict-producing method loses its
   `slot` parameter (the exact API shape that made the bug possible), and if
   any module outside this one renames onto a live clip path.

**Two questions, not one.** This ledger answers *"is a save for this cell still
in flight, and how did it end?"*. Whether a cell holds audio that is not on
disk at all is `Slot.dirty`, which belongs to the track model — `SlotRuntime`
asks that one first and only comes here when the answer is yes. Keeping them
apart is why `poll` can honestly return `clean` for a cell it has never heard
of: no save is running, which is all it was asked.

**A job dies with the audio it is about.** A save is a promise about the bytes
that were in the engine's buffer for one cell at one moment. The moment that
stops being true the job is dropped — `drop()` from a new take on that cell, a
clear of that cell, or a whole-model reset. It is deliberately *not* dropped
when queued intent is abandoned (Stop All): the take is still real, still
belongs at that path, and completing the save is the right thing to do late.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from looper_songs import _fsync_dir, _fsync_file

#: `poll` outcomes.
FLUSH_CLEAN = "clean"
FLUSH_PENDING = "pending"
FLUSH_FAILED = "failed"

#: How long a save gets before it is called failed. Overridable per call so a
#: test does not have to wait out the real one.
SAVE_TIMEOUT_S = 2.0

#: Below this the file on disk is a header and nothing else — SooperLooper has
#: created it but not finished writing. Treating it as complete would rename a
#: stub over a real take.
MIN_CLIP_BYTES = 512


@dataclass(frozen=True)
class _Job:
    """One save in flight, for exactly one cell."""

    tmp: Path
    path: Path
    deadline: float
    #: Kept so the failure message can say how long the save was given. The
    #: deadline alone cannot: by the time it is reported, it has passed.
    timeout_s: float


class FlushLedger:
    """In-flight saves, keyed by `(loop, slot)`.

    There is deliberately **no method that takes a loop alone and returns a
    verdict.** Asking "is track 3 flushed?" is the question that lost a take;
    it is not in the vocabulary, so it cannot be asked by accident.
    """

    def __init__(
        self,
        *,
        send: Callable[[str, list], None],
        now: Callable[[], float],
        log: Callable[[str], None],
    ) -> None:
        self._send = send
        self._now = now
        self._log = log
        self._jobs: dict[tuple[int, int], _Job] = {}

    # -- asking ------------------------------------------------------------

    def poll(self, loop: int, slot: int) -> str:
        """clean | pending | failed for one cell. Cheap enough for the idle loop.

        `clean` with no job means "no save is in flight for this cell" — see
        the module docstring on the two questions. Whether the cell has unsaved
        audio is `Slot.dirty`, and it is not ours.
        """
        job = self._jobs.get((loop, slot))
        if job is None:
            return FLUSH_CLEAN
        try:
            if job.tmp.stat().st_size >= MIN_CLIP_BYTES:
                # Durable before it is authoritative: fsync the data, then
                # rename, then fsync the directory. A rename that reaches the
                # SD card before the bytes do leaves a manifest naming a
                # truncated file after a power cut.
                _fsync_file(job.tmp)
                os.replace(job.tmp, job.path)
                _fsync_dir(job.path.parent)
                del self._jobs[(loop, slot)]
                return FLUSH_CLEAN
        except OSError:
            pass
        if self._now() < job.deadline:
            return FLUSH_PENDING

        # Nothing usable arrived. Leave the original exactly as it was, drop
        # the partial, and say so — the caller keeps the cell dirty and the
        # surface keeps telling the truth.
        del self._jobs[(loop, slot)]
        job.tmp.unlink(missing_ok=True)
        self._log(
            f"track {loop + 1} slot {slot + 1}: save did not land "
            f"in {job.timeout_s:.1f}s — the take is still only in the "
            f"engine buffer, and the clip already on disk is untouched"
        )
        return FLUSH_FAILED

    def running(self, loop: int, slot: int) -> bool:
        """True while a save for this cell is in flight. Reads nothing."""
        return (loop, slot) in self._jobs

    def in_flight(self) -> tuple[tuple[int, int], ...]:
        """Every cell with a save in flight. For tests and diagnostics."""
        return tuple(sorted(self._jobs))

    # -- starting and stopping --------------------------------------------

    def begin(self, loop: int, slot: int, path: Path, *, timeout_s: float) -> None:
        """Ask the engine to write this cell's buffer to a sibling temp file.

        Save to a temp and rename over the original only once a complete file
        exists. This used to unlink `path` first and ask the engine to write it
        — so a save that never landed destroyed the take it was trying to
        preserve, and the caller then "refused to switch" to protect a clip it
        had already deleted. Reported from the appliance 2026-08-27 as "when I
        record clip 2, clip 1 is deleted".

        The unlink was not gratuitous: SooperLooper will not overwrite an
        existing file. A fresh temp path satisfies that without ever putting
        the recorded take at risk.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".part")
        tmp.unlink(missing_ok=True)
        self._send(f"/sl/{loop}/save_loop", [str(tmp), "", "", "", ""])
        self._jobs[(loop, slot)] = _Job(
            tmp=tmp,
            path=path,
            deadline=self._now() + timeout_s,
            timeout_s=timeout_s,
        )

    def drop(self, loop: int, slot: int) -> None:
        """Abandon this cell's save — the bytes it promised are no longer true.

        Called when a **new** take lands on the cell, or the cell is cleared.
        Both make the in-flight save a promise about audio that is gone: left
        alone it would later rename stale bytes over the clip path and mark the
        *new* take clean, which is the compounding half of the 2026-08-30
        defect.

        Not called for abandoned *intent* (Stop All). Stop All pauses; the take
        is still in the buffer and still belongs at that path, so its save is
        allowed to finish late.

        Unlinking the temp is best effort: SooperLooper opened it before we got
        here, so an unlink leaves the engine writing to an unlinked inode
        rather than stopping it. That is the outcome we want and it is not
        something we can guarantee.
        """
        job = self._jobs.pop((loop, slot), None)
        if job is not None:
            job.tmp.unlink(missing_ok=True)
