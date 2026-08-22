# V8 review — the ceiling table is probably optimistic

**2026-08-22.** Reading of `v8-patch-capacity-2026-08-21.md` (branch `docs/v8-patch-capacity`
@ `64ab87d`).

## The internal contradiction

**V8-a** ramped Cloud Horn and reported it clean at **7 voices**.
**V8-b** ran Cloud Horn at **7 voices** for 3 x 45 s and measured **18 / 28 / 18 xruns**,
DSP median **~78-81%**, on *both* `1024 x 2` and `1024 x 3`.

**A patch the ramp called clean overruns at that same voice count when measured for longer.**

### Why — and it is a familiar shape

The ramp's confirm window is short; V8-b's is 45 s. **At the knee the xrun rate is low**
(~20-28 per 45 s = ~30/min), so a short probe reads zero by chance. This is the
`MEASUREMENT-DISCIPLINE.md` rule about **sizing the window from the expected event rate**
rather than convention — the same error class as the 10 Hz fill poller and `dsp_p99`.

Corroboration: **Crystals moved 4 -> 3** between V7 (12 s probe + confirm) and V8-a (shorter
probe). The agent reported this as "same knee, shorter probe"; it is more likely evidence
that **the knee position is probe-duration dependent.**

**Consequence: every "sustained clean" figure in V8-a is an upper estimate, and the numbers we
would act on are the ones most likely wrong.** Do not set any ceiling from this table until
the duration sensitivity is measured.

## Right-censoring

**38 of 53 patches never failed before the 15-voice probe cap.** Their ceilings are unknown —
`>= 15`, not 15.

The reported "5:1 governor spread (3 -> 15+)" is therefore a **lower bound**. The true spread
could be 13:1 or more. This does not change what binds (the bottom of the range does), but it
must not be quoted as a measured range.

## Zero unplayable — contradicts the user

V8-a found **no patch failing at 1 voice**. Mitch has stated some patches "don't work at all."

Candidate reconciliations, in order of likelihood:

1. They are **outside the Quick Select set** that was surveyed.
2. They fail at **512 / 256**, not 1024 — **V8 tested 1024 only.**
3. "Don't work" means something other than xruns — bad sound, failure to load, voice
   allocation issues.

**Resolve by naming one specific patch and checking it directly.** Do not infer.

## What is solidly established

**At ~80% DSP, `nperiods` makes no difference:** 20/28/18 (`x2`) vs 18/24/28 (`x3`).
Exactly as the model predicts — `nperiods` changes the cushion, not the deadline, and the
deadline is what binds. Good confirmation.

**No ALSA underrun lines anywhere.** Graph overruns only, consistent with W1.

**Zero unplayable patches at 1024** is genuinely good news for the product, subject to the
reconciliation above.

## Still open — and V8 did not close it

**The `1024 x 2` question.** It has now been compared against `x3` **twice under overload**
(V3 at 100% DSP, V8-b at ~80% DSP with both arms overrunning) and **never once at a load where
`x3` is verified clean.** That is the cell that decides whether 64.0 ms -> 42.7 ms is free.

## Next — two short cells, no new ground

| # | cell | Pi time | decides |
|---|---|---|---|
| **V9-a** | **Duration sensitivity**: 3 patches (one from each tier, incl. Crystals), ramp-derived clean count vs a **60 s** confirm at that count | ~15 min | whether V8-a's table is usable at all |
| **V9-b** | **`1024 x 2` at a verified-clean load**: find a count where `x3` is clean over 60 s, then compare `x2` at n >= 3 | ~15 min | the free 21 ms |

**Run V9-a first.** If the table shifts down, V8-a needs re-running with a longer confirm
before any ceiling is derived from it, and V9-b's "clean load" must be chosen from the
corrected numbers.

**Do not tune the governor from V8-a's table** until V9-a says whether it holds.

## Process note

The V8-b auto-pick initially mis-parsed a patch name containing spaces and silently ran a
3-voice test instead of 7, producing 0 xruns. It was caught and redone. **Worth recording:
that failure produced a clean-looking result that was simply wrong** — the exact
"indistinguishable from success" shape `AGENTS.md` warns about. The redo was correct; the
lesson is that the harness should echo the resolved patch **and** voice count into the result
file so the mismatch is visible without re-reading logs.
