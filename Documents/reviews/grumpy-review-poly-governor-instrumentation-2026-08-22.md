# Grumpy review — poly-governor-instrumentation (2026-08-22)

**Scope:** Tasks A–D from `docs/measurements/PROMPT-YOLO-poly-governor.md`
**Branch:** `yolo/poly-governor-instrumentation`

## Summary

Instrumentation is appropriately scoped: state-change journal logging, env-configurable
thresholds with identical defaults, CPU affinity fix for the governor unit, Task C
documented without actuation changes. No threshold retune, no re-enable.

## Findings

### P1 — `append_verbose_trace()` is dead code today

The helper exists and is tested indirectly only via import; nothing calls it from the tick
loop. Acceptable as opt-in infrastructure (`MPE_POLY_GOVERNOR_VERBOSE`), but document clearly
that verbose mode requires future call sites — or wire a single call behind the env flag on
state change only (not per tick).

### P2 — Suppressed transition count lost on abrupt stop

`PolyGovernorJournal._flush_suppressed()` runs on window roll, not on `stop()`. A crashing
daemon could lose the pending suppressed count. Low severity — journal is best-effort.

### P2 — Emergency path re-sets `_high_since` every tick while hot

Harmless (no log without limit change) but slightly noisy state churn. Pre-existing pattern.

### P3 — Test module leaked print in `test_emergency_slam_at_90`

That test does not mock `print`; passes but spews to stdout during suite run. Mock for consistency.

## Positive

- Guard before f-string in `_apply_limit` when limit unchanged — correct hot-path discipline.
- Spam guard at 10/s addresses oscillating-controller I/O amplification explicitly.
- `CPUAffinity=0 1` + `RuntimeDirectory=mpe` matches prompt Task A2.
- Defaults test with cleared environ — good regression guard.
- Deliverable doc covers unpinned-unit survey and Surge source evidence for Task C.

## Verdict

Ship after P1 disposition (document or minimal wire) and test import cleanup. No behavioural
regression expected with defaults.
