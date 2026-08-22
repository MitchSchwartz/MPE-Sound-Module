# V8 review — ceiling is optimistic

*Review: 2026-08-22 (America/Toronto)*  
*Supersedes actionable use of [`v8-patch-capacity-2026-08-21.md`](v8-patch-capacity-2026-08-21.md) until V9 completes.*

## Bottom line

**Do not tune the poly governor from V8-a's ceiling table yet.** V8-b showed a patch the ramp called clean at 7 voices **overrunning at 7 voices** when held for 45 seconds. Until probe duration at the knee is characterized, reported ceilings are **upper estimates**, not policy inputs.

---

## V8-a vs V8-b — internal contradiction

**Cloud Horn** was V8-b's mid-weight pick because V8-a ramp reported **sustained clean = 7** (first overrun @ 9).

V8-b redo at **7 voices × 45 s** (n=3, strict mode):

| config | xruns per run | DSP median |
|---|---|---|
| 1024×2 | 20 / 28 / 18 | ~78–81% |
| 1024×3 | 18 / 24 / 28 | ~77–81% |

The ramp's "sustained clean" **did not reproduce** under longer hold. Same failure mode as elsewhere in this campaign: **window sized by convention, not by expected event rate.** At the knee, overrun rate is low (~tens per minute); an **8 s probe** can read zero by chance.

**Crystals 4 → 3** between V7 (12 s probe + confirm) and V8-a (8 s probe only) points the same way.

**Implication:** The limited-patch table is probably **optimistic across the board**. The numbers we'd act on (the low end) are the ones most likely wrong.

---

## 38 of 53 are censored

Patches that never failed before the **15-voice probe cap** have unknown true ceilings — **≥15**, not **15**.

The "3 → 15 spread" in the V8 deliverable is a **lower bound** on bounded patches only. Real spread could be **3 → 28+** (13:1 or wider). That matters less for setting a floor (the bottom binds) but **must not be quoted as a measured range**.

---

## Zero unplayable vs prior expectations

V8-a found **no patch failing @ 1 voice** at **1024×3** in **Quick Select only**.

That does not contradict ear reports that some patches "don't work." Likely gaps:

| hypothesis | check |
|---|---|
| Patch outside Quick Select (factory library, nested favorites) | Name one candidate; survey that path |
| Fails at **512/256**, not 1024 | V8 tested 1024 only; V7 Crystals @ 512 clean = 2 |
| "Don't work" = bad sound, load failure, governor steal — not xrun count | Separate repro |

**Named candidate to check:** **Crystals @ 512×3** — V7 already showed clean **2**, overrun @ 4; ear crackle on heavy content at 512 may be this class of failure, not "unplayable @ 1."

---

## What is solid

1. **53 patches surveyed** at 1024×3 with metadata — first map of the shipped set.
2. **Zero unplayable @ 1 voice** at 1024 in Quick Select (within probe limits).
3. **Zero ALSA underruns** in V8-b windows — still graph overruns only.
4. **At ~80% DSP, nperiods does not matter** — 1024×2 vs ×3 xrun counts overlap (20/28/18 vs 18/24/28). Model prediction: nperiods changes **cushion/latency**, not Surge's **21.3 ms deadline**. Clean confirmation.

---

## 1024×2 is still open

Both V3 and V8-b compared ×2 vs ×3 **under overload** (~100% or ~80% DSP). We have **never** compared them at a load where **×3 is verified clean**.

That cell decides whether **42.7 ms total latency** is free in normal playing — not whether it saves you when you're already past the compute ceiling.

---

## Harness incident (caught)

First V8-b auto-pick **mis-parsed a patch name with spaces** → resolved patch `"A"`, **3 voices**, **0 xruns** — clean-looking, wrong.

Caught on review; Cloud Horn redo superseded it. Same **"failure indistinguishable from success"** shape as `AGENTS.md` measurement doctrine.

**Fix (committed with V9 prompt):** stamp `PROVENANCE patch=… hold_voices=…` into every V8-b/V9 latency log header; fail if `--patch-path` missing.

---

## V9 — two short cells (~30 min total)

**V9-a first** (~15 min) — duration sensitivity, **3 patches**:

- Crystals (heavy knee), Cloud Horn (mid), one light cap-hit (e.g. Closed Hat)
- For each: ramp sustained-clean count → **60 s confirm** at that count
- **Question:** how far does the knee move with probe duration?

If the table shifts down materially → V8-a needs longer confirm; ceilings lower than reported.

**V9-b second** (~15 min) — **1024×2 at genuinely clean load**:

- Pick voice count where **×3 is verified clean over 60 s** (from V9-a or conservative margin)
- Compare ×2 vs ×3, n≥3, same hold pattern
- **Question:** is ×2 clean when ×3 is?

Stop after V9-a + V9-b. Prompt: [`PROMPT-V9-probe-duration-and-clean-x2.md`](PROMPT-V9-probe-duration-and-clean-x2.md).

---

## Governor

**V8 before governor tuning** stands. V9 before governor tuning too, unless V9-a shows ramp+60s confirm matches within one voice.
