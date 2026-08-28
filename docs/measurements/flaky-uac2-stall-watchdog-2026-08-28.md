# Flaky test: `test_session_mode_stops_bridge_on_close`

**File:** `tests/test_uac2_stall_watchdog.py`
**First noticed:** 2026-08-28, while doing unrelated classic-MIDI work.

## What was observed

Failed in **2 of 4** full-suite runs on 2026-08-28. Passed on every isolated
run of the file, both with and without the working-tree changes present at the
time — so it is not caused by the classic-MIDI work, and it is not a
deterministic failure of the file itself.

```
full suite   FAIL   (1461 passed, 1 failed)
file alone   pass   (6 passed)          <- with the same working tree
file alone   pass   (6 passed)          <- with the changes stashed
full suite   pass   (1473 passed)
full suite   pass   (1488 passed)
full suite   FAIL   (1503 passed, 1 failed)
```

## Why it matters

It is a **false alarm generator**. A test that fails ~50% of the time in the
suite and never alone trains everyone to re-run rather than investigate, which
is precisely how a real regression in that file would get waved through. It
should be fixed or quarantined, not tolerated.

## Not yet diagnosed

Deliberately not chased down, because it was found mid-task and chasing it
would have been a detour. What is known:

- The module uses `os.forkpty()`, so it is process- and timing-sensitive.
- It passes reliably in isolation, which points at cross-test interference or
  a timing assumption that only breaks under the load of a full run.

## Suggested next step

Run the suite with `-p no:randomly` (if ordering is randomised) and with
`--last-failed` reordering to see whether a specific predecessor test triggers
it. If a predecessor is implicated, the fix is isolation, not a longer timeout —
a timeout increase would hide it rather than remove it.
