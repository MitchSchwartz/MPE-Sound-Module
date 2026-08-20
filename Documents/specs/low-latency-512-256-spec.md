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
| IRQ 30/41/44 `smp_affinity_list` | `0-3` — permissive | nothing is pinned |
| IRQ 41/44 `effective_affinity_list` | `0` | GICv2 picked lowest core in mask |
| IRQ 30 `effective_affinity_list` | **empty** | MSI ctrl may ignore `set_affinity` |
| `irqbalance` | not installed, not running | nothing has ever spread them |
| Cores | 4 | enough to isolate one |

### Why everything is on CPU0

Nobody pinned it. The BCM2711 uses a **GIC-400 (GICv2)**, which has no usable 1-of-N
interrupt distribution under Linux — the driver must name exactly one target CPU, and
given a mask it takes the **lowest-numbered core in that mask**. `default_smp_affinity` is
`f`, so every interrupt on the system resolves to CPU0. `irqbalance`, which would normally
redistribute them, is not installed and is not shipped by Raspberry Pi OS.

This matters for sequencing: **the affinity masks are already permissive**, so moving an
IRQ does not require a cmdline change or a reboot. It is a runtime write with instant
rollback.

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
- **Per-callback period jitter** — see the box below. This replaces the "xrun delay in µs"
  requirement in the original revision of this spec, which is a dead end.
- DSP load median and p99.
- Appends, never truncates (trap 4).

### Do not use `jack_get_xrun_delayed_usecs` — measure period jitter instead

**Resolved 2026-08-19.** The original spec required per-xrun delay in µs. A probe was
built; the callback fires, but the call returns `0.000` on JACK 1.9.22 / ALSA. JACK2's
ALSA backend never populates that field — it is filled on some driver paths and not that
one. No flag, no build option. **Do not spend time in the JACK2 source.**

It would not have been enough regardless: one sample per xrun is ~12 samples per run,
which does not address the variance documented under Step 1.

Record instead, in the probe's process callback: `clock_gettime(CLOCK_MONOTONIC)` and the
delta from the previous callback. Expected period at 512/48 kHz is **10,667 µs**; the
deviation from it is the jitter.

- ~94 callbacks/s at 512 → **~5,600 samples per 60 s run**, against ~12 xruns.
- Report median, p99, p99.9, max — a distribution, not a count.
- An IRQ fix that tightens p99 from 3 ms to 400 µs is unmistakable at n = 5,600 even when
  the xrun count does not move.
- An xrun becomes the visible tail of a distribution instead of the only observable.

`jack_frames_since_cycle_start()` at callback entry is a useful second signal — how late
the callback entered its period, backend-independent.

Also land `cyclictest` as a hardware floor:

```sh
cyclictest -m -t1 -p 80 -i 200 -l 300000
```

(`-n` was removed in rt-tests 2.6 — `clock_nanosleep` is now the default and `-x` opts out
to POSIX timers. Passing it makes cyclictest print usage and exit non-zero.)

Worst-case wakeup latency bounds everything. Record it before and after every
kernel-level change.

**Measured 2026-08-19, stock kernel, idle: worst case 209–320 µs across runs.** A
256-frame period is 5,333 µs, so scheduler wakeup latency is ~5% of the budget.
**256 is not blocked by scheduler jitter on the stock kernel**, which substantially
weakens the case for Step 6 (PREEMPT_RT) — expect it to buy nothing. The 256 wall is
likely the projected ~77% DSP instead. Caveat: this is an idle floor; re-take under audio
load before relying on it.

Two wrapper bugs were found taking this measurement and are worth knowing about, since
both are the shape this project keeps hitting (PR #82): `rt-tests` 2.6 removed `-n`, and
the wrapper logged the tool's **usage text** as if it were a measurement, exiting 0. The
fix then rejected valid output because `set -o pipefail` plus `grep -q` turns a
successful match into SIGPIPE on the writer — length-dependent, so it passed at 44 KB and
failed at 875 KB.

## Step 1 — is the escalation real?

D condition went 7 → 24 → 29 across three consecutive runs. That is a trend, not noise
around a mean. If something accumulates across runs — a growing OSC subscription list, a
leaked registration, thermal ramp, an unrotated log — then **every measurement after it is
contaminated**, and Steps 2–4 will appear to help when they did not.

### RESOLVED 2026-08-19 — no accumulation, but the variance is the real problem

Run in two blocks totalling 15×60 s. Findings, in full at
[`docs/measurements/low-latency-step0-step1-2026-08-19.md`](../../docs/measurements/low-latency-step0-step1-2026-08-19.md):

- **Escalation disproved.** The original block gave ρ = 0.90, p = 0.042 — but it failed to
  replicate (ρ = 0.50, p = 0.225, then ρ = 0.20). A false positive.
- **No restart effect.** Post-restart run sits at z = −0.75 against all ten runs; two runs
  hit the same value with no restart at all. An earlier "stack-scoped accumulation"
  verdict was withdrawn — do **not** hunt a leak on this evidence.
- **Thermal ruled out** — 54–56 °C flat, `throttled` 0x0 throughout.

**What replaces it as the blocker:** xrun counts span 4–24, sd 7.1 on mean 12.3
(CV 0.58). Detecting a genuine 50% improvement at n = 5 per arm gives **~25% power** — a
real halving would be missed three times in four. **Counting xruns cannot evaluate
Step 2.** Hence the period-jitter metric in Step 0.

## Next steps, in order — as of 2026-08-19

Steps 0 and 1 are done. What remains, in dependency order:

| # | task | needs Mitch? | gates |
|---|---|---|---|
| A | Period-jitter metric in `mpe-xrun-probe` (median/p99/p99.9/max per run) | no | everything below |
| B | Baseline 5×60 s at 512, conditions A and D, jitter histogram recorded | no | Step 2 readout |
| C | **Step 2** — IRQ 30 affinity, runtime write | no | Step 3 |
| D | Re-take cyclictest floor **under audio load** | no | Step 6 decision |
| E | **Step 3** — `threadirqs` + RT priority on `irq/30` | reboot | Step 4 |
| F | **Step 4** — core isolation | reboot | Step 5 |
| G | **Step 5** — full A/B/C/D ladder at 512 | no | 512 default |
| H | **Step 6** — PREEMPT_RT | reboot + image backup | probably unnecessary |

**A is the only thing standing between you and Step 2.** It is a small change to a probe
that already exists and already fires. Do it first; nothing else is blocked on anything
else.

Note the gate on Step 2 changed: it is **not** "Mitch at reboot" and **not** "non-zero
`delay_usec`". Both were wrong in earlier revisions. It is the jitter histogram plus a
baseline, and the affinity write itself needs no reboot.

## Step 2 — get the USB audio IRQ off CPU0

Cheapest change with the highest prior probability of mattering. **Runtime only — no
cmdline edit, no reboot, and therefore this step does not need Mitch.**

The masks are already `0-3`; the effective CPU is 0 only because GICv2 takes the lowest
core in the mask. So move it directly:

```sh
cat /proc/irq/30/smp_affinity_list        # record the original first
echo 2 > /proc/irq/30/smp_affinity_list   # move USB audio to core 2
```

**Then prove it took.** IRQ 30 is `BRCM-PCI-MSI`, not a plain GIC line, and its
`effective_affinity_list` reads empty — some MSI controllers ignore `set_affinity`
silently. This is exactly the appliance's recurring bug shape: a reading that looks the
same whether it worked or not.

```sh
grep -E "^ *30:" /proc/interrupts   # before, then after N seconds of audio
```

The per-core counts must show the delta landing on core 2. A changed
`smp_affinity_list` is **not** evidence — only moved counts are.

**If the write is ignored**, fall back to `irqaffinity=0,1` in
`/boot/firmware/cmdline.txt`, which narrows the default mask so "lowest core in mask" can
no longer resolve to a core you want kept clear. That path does need a reboot and does
need Mitch. Copy the original `cmdline.txt` aside before editing.

While here, consider moving the other CPU0 squatters off as well — mmc0 (41) and the
codec (44) are on the same core for the same reason.

Measure: 5×60 s, conditions A and D, 512.

**Rollback:** write the original value back. No reboot involved.

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

**Needs him** — the first reboot after each cmdline change (Steps 3 and 4, and Step 2
*only* if the runtime affinity write turns out to be ignored), in case the Pi comes back
wrong; any PREEMPT_RT work; and every judgement about *feel* at the new latency, which no
measurement in this document addresses.

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
