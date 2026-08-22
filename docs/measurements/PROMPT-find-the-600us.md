# Agent prompt — locate the ~600 us (2026-08-21)

Copy everything below the line into the agent.

---

## Context you must not re-derive

Read these first. Do not re-litigate their conclusions.

- `docs/measurements/scarlett-verdict-2026-08-21.md` — the Scarlett is ~10x **worse** at
  256x3 cond A (69.7/min vs the Sound Blaster's 7.1/min). Bimodality vanished on the async
  device, so the SB's stream-start lottery was **adaptive clock lock, not frame phase**.
  **Frame alignment is a closed question.** T15/T16/T17 and the aligned drop-in table
  (240/480/1008) are withdrawn.
- `docs/measurements/t13-runway-refuted-2026-08-21.md` — URB queue *depth* is not the
  binding term (128x6 vs 256x3, identical total buffer, 466x apart).
- `docs/measurements/t11-condA-ladder-2026-08-21.md` — at 64 frames the callback **never
  missed its deadline** while ~6% of periods underran. Callback lateness sits near ~900 us
  at every buffer size and does not scale with period.
- `docs/measurements/cpu-census-2026-08-21.md` — **IRQ 30 (xhci) is unmovable and lands on
  CPU0** (empty `effective_affinity`). `mpe-jackd` and `surge-xt-cli` are pinned to CPU 2-3.

**The open question:** bare `cyclictest` floors at 209-320 us, but callback lateness is
~900 us. **~600 us is unexplained.** It is device-independent — two interfaces with
opposite transport characteristics both fail at small buffers. Find where it lives.

## Rules

1. **One variable per step.** If a step would change two things, stop and say so.
2. **Steps 1 and 2 change no configuration and require no reboot.** Keep it that way.
   Do not "helpfully" bundle a fix into a diagnostic step.
3. **Never run any command against the Pi while a measurement window is open**, including
   read-only ones. Batch queries between windows.
4. **Do not resolve card numbers from memory** — the Scarlett's card index has been
   reported inconsistently. Resolve it live from `/proc/asound/cards` and echo what you
   found before using it.
5. Report **withdrawn** claims explicitly if a step refutes something in the docs above.

---

## Step 1 — IRQ 30 rate census (~10 min, no reboot)

**Hypothesis under test:** high speed uses 125 us microframes (8x full speed's 1 ms), and
smaller periods mean fewer packets per URB, so the Scarlett at 256 generates far more URB
completions — all serviced by IRQ 30 on CPU0. If true, IRQ30/s is the mechanism and
`lowlatency=N` (which batches more packets per URB) is the lever.

**Method.** For each cell, bring up the stack in condition A, let the stream settle 15 s,
then `cat /proc/interrupts` twice **60 s apart** and take the delta on IRQ 30. Record the
per-CPU split, not just the total. Also record IRQ 30's `effective_affinity` once to
confirm it is still empty/CPU0.

| # | device | period x nperiods | channels opened |
|---|---|---|---|
| 1a | Scarlett | 256 x 3 | record what jackd actually opens |
| 1b | Scarlett | 1024 x 3 | record what jackd actually opens |
| 1c | Sound Blaster | 256 x 3 | record what jackd actually opens |

Report a table: cell, IRQ30 delta/60s, IRQ30/s, per-CPU split, xruns/min in the same window.

**Interpretation — state which branch you are in:**
- **1a markedly higher than 1b and 1c** → URB-rate hypothesis **supported**. `lowlatency=N`
  earns a reboot slot in Step 3.
- **1a comparable to the others** → URB-rate hypothesis **refuted**. Say so plainly, and
  **drop `lowlatency=N` from Step 3.** Do not run it anyway.

Also note whether jackd opened 4 playback channels on the Scarlett when only 2 are used. If
so, check whether a 2-channel altsetting exists — report only, change nothing.

## Step 2 — cyclictest under real conditions (~15 min, no reboot)

**This is the step that finds the 600 us.** Do not skip or reorder it.

With the **full audio stack running and streaming** (condition A, 256 x 3 — the config that
fails), run `cyclictest` for **5 minutes**, `SCHED_FIFO` priority **70**, pinned:

- **2a — pinned to CPU3** (inside the audio-pinned set, alongside jackd/surge)
- **2b — pinned to CPU0** (where the unmovable xhci IRQ lands)

Report min / avg / p99 / max for each, against the **bare floor of 209-320 us** already on
record. Note that priority 70 sits above jackd's FIFO 65 by design — the point is to
measure what the scheduler *can* deliver, not to be polite. Confirm the audio stack stayed
up and log its xrun rate during the run so we know the load was real.

**Interpretation — state which branch you are in, explicitly:**
- **~900 us under load** → the gap is **generic scheduler latency**, nothing audio-specific.
  Levers become `isolcpus`, `nohz_full`, `threadirqs`, ultimately PREEMPT_RT.
- **still ~300 us under load** → the gap lives **inside the ALSA/JACK/USB wakeup path**, and
  general RT tuning will not touch it. Levers become `threadirqs` (so IRQ 30's *handler
  thread* can be placed even though the IRQ itself cannot), IRQ consolidation off CPU0, and
  USB topology.
- **2a and 2b differ sharply** → say so. That is a placement finding in its own right.

**Stop here and report.** Do not proceed to Step 3 without the results of 1 and 2 in hand.

## Step 3 — one reboot, carrying everything earned (~10 min)

Assemble a **single** cmdline/config change containing only what Steps 1-2 justified:

- **v3d removal** — hygiene, always included. It is on CPU1, outside the audio path, so
  **expect no measurable effect**; we are removing 957k interrupts from the picture, not
  fixing anything. **Gate:** confirm `touch_patch_browser.py` does not use GL before
  removing the driver. If it does, relocate rather than remove.
- **Whatever Step 2's branch implies** — most likely `threadirqs`.
- **`snd_usb_audio.lowlatency=N`** — **only if Step 1 supported it.**

Also verify, in the same window, that the HDMI IRQ and crtc threads actually disappeared
after the earlier cmdline change. That has never been confirmed.

Record in the doc that this reboot changes multiple things at once and is therefore a
**bundle, not an experiment** — it cannot attribute effect to cause. That is accepted
deliberately to save reboots; the attribution comes from Steps 1-2, which were clean.

## Step 4 — spot-check (~12 min)

One cell only: **Scarlett 256 x 3 condition A, 3 streams x 3 runs**, against the 69.7/min
baseline. This confirms the bundle did no harm and shows whether it helped. It is not a
re-baseline and must not be described as one.

## Deliverable

`docs/measurements/find-600us-2026-08-21.md` on a branch off `dev`, containing per-step
tables, the branch you landed in at each interpretation point, and a short **"what is now
ruled out"** section. If a step refutes something in the context docs above, say so
directly and name the doc.
