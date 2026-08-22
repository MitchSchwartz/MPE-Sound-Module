# V7 — capacity curve, and fixing the governor's actuation

**2026-08-21.** Supersedes the V7 cell proposed in `V1-VERDICT-no-fixed-cost-2026-08-21.md`
(loaded ladder at fixed poly 16), which asks the wrong question.

## Two ear results that settle a lot

| condition | result |
|---|---|
| poly governor **OFF** | **pops gone** |
| poly governor **OFF**, CPU maxed deliberately | **crackle, on demand** |

**1. The pops were our own controller.** `surge-poly-governor` cut polyphony when CPU rose,
which **stole sounding voices mid-note**. Not the kernel, not USB, not the interface, not the
buffer size. A feedback loop we wrote.

**2. The crackle confirms compute-bound by hand.** With nothing limiting voices, the callback
can be walked past its deadline on demand — `JackEngine::XRun: Surge XT was not finished`,
reproducible without instrumentation.

## The governor is right; its actuator is wrong

Both configurations fail audibly:

| poly governor | failure mode |
|---|---|
| **on** | **pops** — sounding voices truncated when the ceiling drops |
| **off** | **crackle** — deadline exceeded under overload |

The controller's *goal* (protect the deadline) is correct. Its *actuation* (kill voices that
are currently sounding) is what is audible.

### Fix 1 — never truncate a sounding voice

Apply a reduced ceiling to **new** voices only: refuse note-ons above the ceiling rather than
stealing from those already sounding. **A note that never starts is far less audible than a
note that stops.** This removes the pops while keeping overload protection.

Implementation note: the governor sets Surge's poly limit over OSC. When that limit drops
below the currently-sounding count, Surge decides what dies. **Check what Surge actually does
in that case** before assuming the fix belongs in our governor — it may belong in how we
drive the limit (monotonic-down only at note-off boundaries) rather than in Surge.

### Fix 2 — fade, do not cut

If a sounding voice must be removed, ramp it out over a few milliseconds. **A hard cut is a
step discontinuity — that is the pop.** Standard voice-stealing practice.

### Fix 3 — hysteresis

Whatever the ceiling logic, it must not oscillate around the threshold. Rate-limit changes
and separate the raise and lower thresholds.

## The measurement that is actually needed: a capacity curve

**Every cell in this project has used one fixed load (75 voices) and counted xruns.** That
measures failure at an arbitrary point. It does not measure **capacity**, which is what both
the player and the governor need.

**V7: for each buffer size, how many voices of a defined heavy patch can be sustained
cleanly?**

| cell | buffer | method |
|---|---|---|
| V7-a | 1024 x 3 | ramp voice count upward; record first graph overrun and the highest count sustained clean |
| V7-b | 512 x 3 | as above |
| V7-c | 256 x 3 | as above |

**Conditions:** poly governor **OFF**, poly ceiling raised out of the way, strict mode,
governor `performance` @ 1800 MHz (already set), same patch and same voice-entry pattern in
every cell.

**Report:** voices-to-first-overrun, highest sustained-clean count, and DSP in **absolute ms**
with **p99.9 / p99.99 / max** — not p99. Capture the jackd journal per window.

### What it produces

1. **The answer to "what can we actually run reliably?"** — asked early in this project and
   never measured.
2. **A data-derived poly ceiling per buffer size**, replacing the current
   `MPE_POLY_CEILING=12` guess.
3. **The real shape of the buffer tradeoff** — expressed in voices, which is what a player
   experiences, rather than in xruns per minute at an arbitrary fixed load.

### Shortest useful version

Ramping to first failure at three buffer sizes. **Do not run soaks at each voice count** —
the transition is the datum. Confirm the sustained-clean count with a single short window per
buffer, not a long one.

## Still open, unchanged

- **V3 — `1024 x 2`** at n >= 3. Independent free latency win: 64.0 ms -> 42.7 ms with no
  change to the compute deadline. Unaffected by any of the above.
- Re-enable the poly governor **after** Fixes 1-3, and re-test by ear. The controller-on pass
  was deferred, not dropped.

## Retired

`V5` / `V6` (governor, `arm_boost`) — V0 found the box **already `performance` @ 1800 MHz with
`arm_boost=1`**. Nothing to gain; the `ondemand` explanation for the pops is withdrawn and
replaced by the poly-governor finding above.
