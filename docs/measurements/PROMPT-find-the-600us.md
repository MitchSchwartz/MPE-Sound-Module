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

**Already done, do not repeat:** Step 2 hygiene pruned `bluetooth`, `avahi-daemon`,
`cron`, `udisks2`, `cloud-init` and masked the maintenance timers; movable IRQs (41/42/43/
28/57) are relocated to CPU1 at boot by `mpe-irq-affinity.service`.

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

## Step 0 — storage and log-write audit (~5 min, no reboot, read-only)

**Do this first. It is read-only and may change what the later steps mean.**

Context: `docs/STORAGE-ROBUSTNESS.md:7` states the appliance still runs a **full mutable
Raspberry Pi OS on ext4**, and volatile journal storage is listed there as **proposed, not
implemented**. Neither swap nor journald persistence has ever been audited on this box.

This matters because **IRQ 41 is `mmc0/mmc1` — the SD card and the SDIO WiFi share one
interrupt line** (1.90 M counts, currently relocated to CPU1 by `mpe-irq-affinity.service`).
Every SD write and every WiFi packet lands on the same handler. Disk tuning and WiFi tuning
are therefore the same problem here, not two.

Report, changing nothing:

| # | check | command |
|---|---|---|
| 0a | swap enabled? size? backing device? | `swapon --show`, `free -h`, `systemctl is-enabled dphys-swapfile` |
| 0b | if swap is on: has it been touched? | `vmstat -s \| grep -i swap`, `/proc/vmstat` `pswpin`/`pswpout` |
| 0c | major faults by the audio processes | `ps -o pid,comm,maj_flt -p $(pgrep -d, 'jackd\|surge')` |
| 0d | journald storage mode + on-disk size | `journalctl --disk-usage`, `grep -r Storage= /etc/systemd/journald.conf*` |
| 0e | SD write rate at idle vs during a stream | `/proc/diskstats` for `mmcblk0`, 60 s delta, both states |
| 0f | does the measurement harness itself write to SD during a window? | inspect harness paths — `/run/mpe` is tmpfs and fine; anything under `/var` or `/home` is not |
| 0g | `vm.swappiness`, and whether jackd/surge use `MemoryLock` / `mlockall` | `sysctl vm.swappiness`; check unit files |

**Why this could matter to the 600 us.** If swap is enabled on the SD card, a cold page
touched inside the audio callback takes an **SD-backed major fault**. That is
device-independent, load-correlated, and would fit "512 crackles with heavy patches"
precisely. It would also **not** show up the way we have been measuring: a major fault
stalls the callback without necessarily registering as callback lateness in the harness.

**Do not treat this as a confirmed cause.** It is an unexamined candidate that is cheap to
rule in or out. Report `pswpin`/`pswpout` and `maj_flt` as the deciding numbers:

- **`pswpin` > 0 or audio-process `maj_flt` growing during play** → swap is live in the
  audio path. Escalate immediately; this outranks Steps 1-2.
- **both flat and swap off** → ruled out. Say so and move on. Do not keep it on the list.

If 0e shows meaningful SD writes during a stream, name **what** is writing before proposing
any change.

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
  fixing anything. **Gate is already cleared** —
  `docs/measurements/step2-hygiene-applied-2026-08-21.md:29` records that pygame does not
  use GL; the blacklist was deferred, not blocked. Do not re-litigate this.
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

---

## AMENDMENT (post Step 1) — URB-rate hypothesis inverted; Step 2 confound

**Step 1 result.** Scarlett cond A, IRQ 30 (`xhci_hcd`, BRCM-PCI-MSI, CPU0 only):

| cell | IRQ30/s | xruns/min |
|---|---|---|
| 1024 x 3 | **1999** | **0** |
| 256 x 3 | ~1000 (approx, per agent) | fails badly |
| 1c Sound Blaster | skipped — unplugged | — |

**The hypothesis is not merely refuted, it is inverted:** the config with double the
interrupt rate has zero xruns, and the failing config has half the rate. Skipping 1c does
not weaken this — 1a vs 1b inverts it on its own.

**It is stronger than the counts suggest.** The byte rate is *identical* at both buffer
sizes — same sample rate, same channels, same bytes/second. Only the **chunking** differs.
The failing config therefore does the same total USB work in fewer, larger URBs.
**Interrupt volume and throughput are off the table as causes.**

**Consequences:**
- `snd_usb_audio.lowlatency=N` is **struck from Step 3**, per Step 1's stated kill condition.
- Every surviving candidate is a **latency** mechanism (how long one wakeup takes), not a
  **volume** one (how many wakeups there are). Steps 0 and 2 measure exactly that.

### Confound on the Step 2 run — read before interpreting

`kernel.sched_rt_runtime_us` defaults to **950000** against a period of 1000000: RT tasks
are throttled at 95% of every period. Step 2 adds `cyclictest` at **FIFO 70** on top of
jackd (FIFO 70) and surge (FIFO 65).

**If the throttle is at its default, the combined RT utilization can trip it, and cyclictest
will be measuring RT throttling rather than scheduler latency** — producing a large max
unrelated to the 600 us.

Do **not** stop a run in flight for this. Instead:

1. Let the run finish.
2. Read `sysctl -n kernel.sched_rt_runtime_us kernel.sched_rt_period_us`.
3. **If it is 950000 / 1000000, the Step 2 max is uninterpretable as scheduler latency.**
   Say so, then re-run 2a/2b with `kernel.sched_rt_runtime_us = -1` (throttling disabled)
   and compare. That is a one-variable change and needs no reboot (`sysctl -w`).
4. If throttling is already disabled (-1), the Step 2 numbers stand as measured.

Report the throttle values in the deliverable **regardless of outcome** — they are not
currently tracked anywhere in the repo.

### Step 0 is now the priority

Run `scripts/audit-storage-rt.sh` (read-only, in the repo, changes nothing). It carries
three live candidates, all latency mechanisms consistent with the Step 1 inversion:

1. **Swap-backed major faults** — `pswpin`/`pswpout`, audio-process `maj_flt` during play
2. **RT throttling** — `kernel.sched_rt_runtime_us` (also gates Step 2 above)
3. **journald SD writes** — persistent journal on the shared `mmc`/SDIO-WiFi IRQ 41 line

Run it **once idle** and **once with a stream up**, the latter in its own window, not inside
a counted one. Use `--delta 60` for the streaming pass.
