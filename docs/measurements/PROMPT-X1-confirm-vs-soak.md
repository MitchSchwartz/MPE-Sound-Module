# X1 — why does the confirm harness read 0 where the soak reads 2/min?

**This decides how much weight the 21.3 ms claim carries.** Start offline. Most of it may be
answerable with no Pi time at all.

---

## The discrepancy

| source | config | window | result |
|---|---|---|---|
| **B2 soak** (2026-08-23) | Cloud Horn @5, `1024x2` | 8 h continuous | **991 xruns = 2.06/min** |
| **V9-d confirm** (2026-08-22) | Cloud Horn @5, `1024x2` | 3 x 45 s | **0 / 0 / 0** |

Same patch, same voice count, same buffer config, same appliance.

At 2.06/min, 135 s of window should yield ~4.6 events. **P(observing zero) ~ 1%.** And the soak's
first hour ran ~3.4/min — *higher* than average — so a cold-start window should have seen
*more*, not fewer.

**These two results are not compatible.** One of them is measuring something other than what it
claims.

---

## Why this matters more than it looks

The project's headline claim — **`512x2` = 21.3 ms is clean**, a 3x latency reduction — rests on
V11's `0/0/0` over **3 x 25 s = 75 s**. At a ~2/min rate, P(zero in 75 s) ~ 8%.

So "clean" in those cells means **"no events observed in 75 seconds,"** which at these rates is
weak evidence rather than proof. If the confirm harness systematically under-observes, the
21.3 ms result is unsupported and the shipping decision changes.

**Do not re-run anything until this is understood.** More short windows will not resolve it.

---

## Step 1 — the cheap check, offline, no Pi (do this first)

**The answer may already be in the soak log.** `docs/measurements/raw-logs/home/instrument-soak-1024x2.log`
carries a `SOAK minute=N xruns_minute=D` line for all 480 minutes.

**Read minutes 1-15 and plot the per-minute rate.**

The soak report says the rate *peaked at ~3.5/min around minute 14* and settled by hour 4. That
phrasing implies the rate **rose** to a peak before declining. If so:

> **A 45-second confirm window sits entirely inside minute 1 — before the ramp.**

If `xruns_minute` for minutes 1-2 is 0 or 1, the confirm harness is not broken at all: it is
**systematically sampling the quietest part of a non-stationary curve.** That would mean every
confirm-based "clean" result in this project measured a transient warm-up quiet period, and it
would explain V9-d and V11 without either harness being defective.

**Record the minute-by-minute numbers for the first 15 minutes in the result doc regardless of
what they show.** This single check may close the whole question.

---

## Step 2 — if minute 1 is NOT quiet, diff the harnesses (offline)

Then the two harnesses genuinely count differently. Compare `measure-soak-instrument.sh` against
`measure-latency-run.sh` / `measure-confirm-at-voices.sh` on:

| dimension | what to check |
|---|---|
| counter source | both `mpe_meter_xruns_read`? same liveness assertion? |
| strict mode | `MPE_JACK_SOFTMODE=0` set, and set **per probe**, in both? (this was the V10-b defect) |
| governor | soak sets `MPE_POLY_GOVERNOR=0` explicitly — does confirm? **If confirm leaves the poly governor on, it is silently limiting voices and cannot overrun.** Check first; this is the highest-prior candidate. |
| looper stack | soak stops `mpe-looper-session`, `sl-watchdog`, `mpe-sooperlooper`. Does confirm? A different graph is a different measurement. |
| load generator | same `midi-load-hold.py`, same voice semantics, same note set? |
| voice verification | does either **confirm** the requested voices are actually sounding? A load that silently plays 2 voices instead of 5 cannot overrun — and would read as clean. |
| jackd lifecycle | confirm restarts jackd per probe; soak does not. Cold graph vs warmed graph. |
| window start | does the confirm window open **before or after** the load reaches full voice count? |

**Report what you eliminated, not only what you found.**

---

## Step 3 — only if Steps 1-2 are inconclusive: one measurement

**Maximum 30 minutes. Requires Mitch's explicit approval before running** (standing rule: any
window over 30 min needs written justification; this is at the limit).

Single cell: Cloud Horn @5, `1024x2`, governor off, strict mode, **30 minutes continuous**, with
per-minute xrun deltas logged the way the soak does.

Expected ~60 events at 2/min — enough to establish the rate and its shape within the first
30 minutes. Pre-register: what per-minute curve would confirm the ramp hypothesis, and what would
refute it.

Then run **three 45-second confirm cells back-to-back at the end of that same window**, hot and
in steady state. If they read 0 while the surrounding minutes read 2/min, the confirm harness is
defective. If they read ~1-2, the earlier zeros were window placement.

---

## What each outcome means

| finding | consequence |
|---|---|
| **Rate ramps; minute 1 is quiet** | Confirm harness is not defective but **is systematically optimistic**. Every "clean" claim from a <2 min window is downgraded to "no events in N seconds." V11's 21.3 ms needs re-measurement over a longer window. **Minimum window length becomes a doctrine item.** |
| **Confirm leaves the governor on** | Confirm cells were voice-limited and could not overrun. **All confirm-based ceilings are invalid**, including the confirmed floors. Largest possible blast radius — check this early. |
| **Load generator under-delivers voices** | Same as above: clean readings from a load that was never applied. Occurrence eleven. |
| **Harnesses agree; the soak rate is real and stationary** | `1024x2` genuinely produces ~2 xruns/min under sustained load. Not a measurement problem — an **audibility** question for B3, and Gate 1 needs a decision rather than a re-measurement. |

---

## Constraints

- **Steps 1 and 2 are offline.** Do them fully before requesting Pi time.
- Do not "fix" either harness before the diff is written down. A silent fix destroys the evidence
  for which one was wrong.
- If a defect is found, it is a **Rule -1 occurrence** — add it to the table in
  `MEASUREMENT-DISCIPLINE.md` with date, what it returned, and what it should have returned.
- Applies to the Pi 5 too: whatever minimum window this establishes becomes part of the frozen
  reference suite, and the current 25 s cells may need to change **before** the platform
  comparison, not after.

## Hand back

The minute 1-15 rate curve, the harness diff table with eliminations, the leading explanation,
whether the 21.3 ms claim survives, and any proposed change to minimum window length.
