# Review loop — live monitor and fader pickup, 2026-09-20

Four review cycles, four audits, on one uncommitted change: the fader pickup
fix in `loop_mix.py` and the new live monitor feature. Builder, reviewer and
auditor were separate contexts throughout; no reviewer or auditor modified
product code.

| Cycle | Review | Audit | P0 found | What the fixes were |
|---|---|---|---|---|
| 1 | [grumpy](grumpy-review-live-monitor-and-fader-pickup-2026-09-20.md) | [audit](review-audit-live-monitor-and-fader-pickup-cycle1-2026-09-20.md) | 3 | `JackUseExactName`; both graph guards test the whole path; `ExecStopPost` restore + a `surge_playback` sensor and a watchdog arm; CI compiles the C; one enable law; a lock and a capture TTL; connected socket with refusal reporting; three tautological tests rewritten |
| 2 | [grumpy](grumpy-review-live-monitor-and-fader-pickup-cycle2-2026-09-20.md) | [audit](review-audit-live-monitor-and-fader-pickup-cycle2-2026-09-20.md) | 5 | TTL deleted and replaced with asking the engine; the sensor rewritten to test both legs; `--check` so a test cannot start audio; the client can re-detach; a failed restore no longer clears the flag |
| 3 | [grumpy](grumpy-review-live-monitor-and-fader-pickup-cycle3-2026-09-20.md) | — | 2 | The sensor's answer survives a missing `meter.state`; the arm left the meter-only gate; the state file gained a lifetime; unanswered questions are retried |
| 4 | [grumpy](grumpy-review-live-monitor-and-fader-pickup-cycle4-2026-09-20.md) | [audit](review-audit-live-monitor-and-fader-pickup-cycle5-2026-09-20.md) | 2 | Staleness means the file stopped moving, not that its clock reading is old; the repair stopped deleting the file its own success check reads; one run-dir resolution; the repair decision extracted and executed by tests |

Final audit: **0 P0, 0 P1.** 2236 tests pass; both native clients compile clean
under `-Werror`.

## What the loop actually caught

Nine of the twelve critical findings were in the new feature's *failure*
handling, not its function — the paths that run when something is already
wrong. Four were introduced by an earlier cycle's fix. Two patterns recurred
often enough to name:

**A sensor that the failure it watches for can satisfy.** Three separate
versions of "can you hear yourself play" answered *yes* while the instrument was
silent: ports existing rather than audio flowing, one channel standing for two,
and a health flag whose answer the caller discarded. Each was written as the fix
for the previous one.

**A test that passes whether the code works or not.** Found in every cycle,
including inside the fixes for it: a fader test whose arithmetic was a no-op, a
TTL test that certified a premise the engine disproved, an assertion about where
a line sits in a file. The cure each time was to execute the thing rather than
describe it — which is how cycle 2's audit settled a design question by running
a real SooperLooper, and how cycle 4's second P0 was found.

## What has never been tested

**None of this has run on the Pi.** It was unreachable throughout. Nothing here
has made a sound, and no level has been heard by a person. Unverified: that the
gain stage starts and carries audio; that `ExecStopPost` restores the path; that
the client re-detaches within 2 s; that the watchdog's repair arm has ever
executed end to end; that the idle poll costs on the Pi what it costs on a
laptop; that the ramp sounds like a ramp.

**Deploy verdict, unchanged from cycle 4 and confirmed by the final audit:**
`MPE_LIVE_MONITOR=0` — the default, and what the Pi runs — is safe; the only
reachable change is the fader law. `MPE_LIVE_MONITOR=1` is a supervised bench
session on speakers at low level, not headphones and not unattended.
