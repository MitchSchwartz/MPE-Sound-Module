# Low-latency work order — make 512, then 256, xrun-clean

**Hand this to a fresh agent.** Self-contained; assumes no prior context.

## The goal

Low latency is one of the two primary goals of this instrument. It is played live.
1024×3 (64 ms) is the current safe default and it is not the destination — 512×3 (32 ms)
and then 256×3 (16 ms) are.

This work order does not ask "can we afford the looper stack at 512." That question is
answered and the answer is yes. It asks why the audio callback is **late** when there is
60% of a core sitting idle.

## What is already measured — do not re-derive

**D15, 2026-08-19, `raspberrypi2` at commit `b9bf98e`, 512×3, 3×60 s per condition:**

| condition | xruns / 60 s | DSP median |
|---|---|---|
| A — baseline (JACK + Surge only) | 2, 0, 0 | ~38.6% |
| D — full stack (+ sooperlooper, session, watchdog) | 7, 24, 29 | ~38.7% |

**Task 6, 2026-08-19, same host, 1024×3, 3×60 s per condition:**

| condition | DSP median | delta | xruns |
|---|---|---|---|
| A — baseline | 19.14% | — | 0 |
| B — + sooperlooper | 24.64% | +5.50 | 0 |
| C — + looper-session | 24.79% | +0.15 | 0 |
| D — + sl-watchdog | 25.09% | +0.30 | 0 |

Three conclusions follow, and they are the premises of this work order:

1. **The 512 failure is jitter, not load.** DSP is identical between A and D (38.6 vs
   38.7) while xruns differ by an order of magnitude. If the looper stack were consuming
   headroom, DSP would rise. It does not. The stack is not starving the callback — it is
   *interrupting* it. **Making the looper cheaper will not fix this.** Do not spend
   effort there.
2. **512 is marginal even at baseline.** Condition A produced 2 xruns in one of three
   runs with nothing else running. The full stack makes a thin margin fail; it did not
   create the thinness.
3. **DSP doubles from 1024 to 512** (19.14% → 38.6%) for the same audio work. The cost is
   per-callback overhead, not per-sample work. Expect ~77% at 256. That is a real ceiling
   and it may be what stops 256 regardless of jitter.

## Idle-state configuration found on the Pi, 2026-08-19

Probed read-only while the audio stack was stopped.

| item | state | verdict |
|---|---|---|
| CPU governor | `performance`, all 4 cores | already correct, leave alone |
| Kernel | `6.18.34+rpt-rpi-v8 SMP PREEMPT` | stock, **not** `PREEMPT_RT` |
| `isolcpus` / `nohz_full` / `rcu_nocbs` | absent from cmdline | unexploited |
| `irqaffinity` | absent from cmdline | unexploited |
| `threadirqs` | not set | `xhci_hcd` is an unschedulable hard IRQ |
| `xhci_hcd` (IRQ 30) | 4,915,801 interrupts, **100% on CPU0** | prime suspect |
| Other threaded IRQs | mmc0, codec, HDMI CEC, 6× CRTC — all CPU0 | contending on CPU0 |
| Cores | 4 | enough to isolate one |

The USB sound card's data path shares a single core with SD-card I/O and HDMI hotplug, as
a hard IRQ that cannot be preempted or prioritized. At 1024 (21 ms) that is invisible. At
512 (10.7 ms) it is exactly the shape of the measured failure.

## Read first

- `AGENTS.md` — *"Never ask Mitch to run a test you could have run yourself"* and
  *"Self-test the instrument before it costs him anything"*.
- `docs/measurements/README.md` — measurement-integrity doctrine. The recurring bug on
  this appliance is **a reading that looks the same broken or fine**.
- `Documents/DECISIONS.md` — CPU doctrine: no forks in periodic loops; bash is acceptable,
  Python is not always, C is on the table when measured to be necessary.
- `Documents/specs/rerun-order-2026-08-19.md` — the six traps that voided prior runs.

## Traps that have already voided runs on this hardware

Every one of these has happened. Read them before writing a script.

1. **`pgrep -f` / `pkill -f` over ssh matches the invoking command line** and kills the
   ssh command itself, silently. Use bracket patterns (`[l]ooper-sess`) or script files.
2. **A remote command that returns no output is not evidence that it ran.** Two ssh calls
   returned nothing and were treated as success; the fix under test was never on the Pi.
   Echo a sentinel and check it.
3. **Overlapping runs.** A prior script's `trap` stopped looper units partway through the
   next run. Caught only because sample counts were 259/111 instead of 180. **Assert the
   expected sample count in every run** — it is the only reading that fails loudly.
4. **Output files that truncate per run.** `xrun-corr.sh` writes to `~/xrun-corr.out` and
   overwrites. 12 clean exits produced zero retained data.
5. **`set-surge-audio.sh` without sudo** fails on `/etc/mpe/mpe.env` and *continues*,
   leaving the buffer size unchanged while the run is labelled with the new one. Verify
   the actual JACK buffer size from JACK, per run, and record it.
6. **A `)` inside a shell comment truncates the DISABLED-unit test parsers.** Has happened
   twice, once in a comment *about* parentheses.

## Step 0 — build the harness. Nothing else starts until this exists.

D15 is a lower bound rather than a measurement because xruns-per-60 s over n=3 cannot
distinguish a fix from luck, and a bare count cannot distinguish a 200 µs miss from an
8 ms miss — which point at completely different causes.

Deliverable: a script that, given a buffer size and a condition label, runs N×60 s and
records per run:

- **Provenance** — Pi commit, `git status --porcelain` clean, kernel, cmdline, governor.
- **Verified buffer size read back from JACK**, not from the arg (trap 5).
- **Sample count asserted** against expected (trap 3).
- **Each xrun with a wall-clock timestamp and its delay in µs.** JACK reports the delay;
  it is the single most informative number available and it is currently being discarded.
- DSP load median and p99.
- Appends, never truncates (trap 4).

Also land `cyclictest` as a hardware floor:

```sh
cyclictest -m -t1 -p 80 -n -i 200 -l 300000
```

Worst-case wakeup latency bounds everything. If it is 3 ms, then 256 frames (5.3 ms
period) is arithmetically impossible on this kernel and Step 5 becomes mandatory rather
than optional. Record it before and after every kernel-level change.

## Step 1 — is the escalation real?

D condition went 7 → 24 → 29 across three consecutive runs. That is a trend, not noise
around a mean. If something accumulates across runs — a growing OSC subscription list, a
leaked registration, thermal ramp, an unrotated log — then **every measurement after it is
contaminated**, and Steps 2–4 will appear to help when they did not.

Run 5×60 s at 512, full stack, no changes, back to back. Record `vcgencmd measure_temp`
and `get_throttled` per run alongside the xrun counts.

- Flat across five runs → it was noise, proceed.
- Still climbing → **stop and find it.** This is now the top priority and the rest of the
  work order waits.

## Step 2 — get the USB audio IRQ off CPU0

Cheapest change with the highest prior probability of mattering. One cmdline line,
reversible, no code.

Add to `/boot/firmware/cmdline.txt`: `irqaffinity=0,1`

That confines housekeeping IRQs to cores 0–1 and leaves 2–3 clear. Then pin `xhci_hcd`
(IRQ 30) to a core the audio thread does not run on, and record its `/proc/interrupts`
distribution per core afterwards to prove the pin took.

Measure: 5×60 s, conditions A and D, 512.

**Rollback:** revert the one line, reboot. Keep a copy of the original `cmdline.txt`
alongside it before the first edit.

## Step 3 — thread and prioritize the USB IRQ

`xhci_hcd` is currently a hard IRQ, so it cannot be scheduled at all. Add `threadirqs` to
cmdline, confirm `irq/30-xhci_hcd` appears in `ps -eLo cls,rtprio,comm`, and set its RT
priority **above** the JACK audio thread.

Note the existing threaded IRQs all sit at `FF 50`. Choose the new priority deliberately
relative to both that floor and JACK's own priority — record what JACK is actually running
at, since it was not observable while the stack was stopped.

Measure: 5×60 s, A and D, 512. Also re-run `cyclictest` — `threadirqs` moves the floor.

## Step 4 — isolate a core

The big lever, and the most invasive. Only after 2 and 3 are individually measured.

`isolcpus=3 nohz_full=3 rcu_nocbs=3`, then pin JACK and Surge to core 3 and keep
everything else off it.

This changes the CPU budget available to the looper stack as well, so the full A/B/C/D
ladder must be re-run afterward, not just A and D.

**Rollback:** revert cmdline, reboot. A bad `isolcpus` value on a 4-core machine can
leave the system unusable — verify the core index against `nproc` before rebooting.

## Step 5 — the full ladder at 512

A / B / C / D, 5×60 s each, exactly as task 6 ran at 1024. A-vs-D cannot tell you whether
the culprit is sooperlooper, the session supervisor, or the watchdog — and at 1024 those
cost +5.50, +0.15, and +0.30 points respectively, so they are not equal suspects.

**Exit criterion for 512:** 0 xruns across 5×60 s in condition D. Then 512×3 becomes the
default and B10 feel gets re-asked at the new latency.

## Step 6 — PREEMPT_RT, only if 256 needs it

If 512 is clean after Steps 2–4 and 256 is not, this is the remaining lever.

It is a different class of change: it touches the boot path, and a bad kernel leaves an
instrument that does not boot. **Do not do this speculatively** and do not do it without
Mitch present and a known-good SD card image. Gate it on the `cyclictest` number from
Step 0 — if the stock kernel's worst case already fits inside 5.3 ms, RT buys nothing and
the 256 blocker is the ~77% projected DSP instead.

## What needs Mitch, and what does not

Per `AGENTS.md`, run everything you can without him.

**Does not need him** — the harness, `cyclictest`, all measurement runs, IRQ affinity and
priority changes, the ladder, every doc update.

**Needs him** — the first reboot after each cmdline change (Steps 2, 3, 4), in case the Pi
comes back wrong; any PREEMPT_RT work; and every judgement about *feel* at the new
latency, which no measurement in this document addresses.

## Definition of done

- Harness exists, is committed, and records provenance + µs-resolution xrun delays.
- `cyclictest` floor recorded for the stock kernel and after each kernel-level change.
- Step 1 resolved: escalation is either disproved or explained.
- Each of Steps 2–4 measured **independently**, one variable at a time.
- A measurement doc per step in `docs/measurements/`, each carrying the Pi commit it ran
  on, verified against `git log` on the Pi — not assumed. A prior run wrote a provenance
  line without checking and it was false.
- `Documents/specs/session-control-plane-spec.md` updated if the default buffer size
  changes.
