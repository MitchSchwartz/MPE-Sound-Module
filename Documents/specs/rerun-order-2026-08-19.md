# Re-run order — re-take three measurements on the merged branch

**Hand this to a fresh agent after `dev` is promoted to `main`.** Self-contained; assumes
no prior context.

## Why

Four Phase 3M criteria were closed on 2026-08-19 with measurements taken on
`raspberrypi2`. Two of them ran while the Pi's checkout had been switched to
`yolo/looper-poll-tail-fix` (`c006fa8`) by another agent — not the `dev` code they
describe. The Pi's reflog shows the switch at 2026-08-19 19:14:35.

The **comparisons** are valid: every condition ran identical code. The **absolutes** are
not attributable to the shipped branch.

| measurement | ran on | action |
|---|---|---|
| Task 6 — looper stack cost | `dev` 84c10ea | clean, **do not redo** |
| Criterion 42 — MIDI→OSC latency | `c006fa8` | re-take |
| Criterion 47 — CPU before/after | `c006fa8` | re-take |
| Criterion 46 — crash blast radius | `c006fa8` | re-take (cheap) |

## Read first

- `AGENTS.md` — *"Never ask Mitch to run a test you could have run yourself"* and
  *"Self-test the instrument before it costs him anything"*. Written because of this work.
- `docs/measurements/looper-session-crash-and-cpu-2026-08-19.md` (provenance note at top)
- `docs/measurements/looper-midi-osc-latency-2026-08-19.md`
- `Documents/specs/session-control-plane-spec.md` — Phase 3M table

## Step 0 — establish and record provenance

```sh
ssh raspberrypi2 'cd /home/mitch/MPE-Module && git log --oneline -1 && git status --porcelain'
```

Tree must be clean and on the merged code. Put the commit in every doc you touch. Do not
assume: a previous run wrote a provenance line without checking and it was false.

## Step 1 — criterion 47, CPU before vs after

The pre-merge units are gone; their entry points survive, so "before" is reconstructable
as two processes on two ports (the pre-merge topology):

- **after** — merged session via `systemctl start mpe-looper-session`
- **before** — `python3 scripts/looper-session.py --bench-only` (binds 9953) plus
  `MPE_SL_SESSION_LISTEN_PORT=9952 python3 scripts/looper-session.py --hud-only`

Sample `/proc/<pid>/stat` fields 14+15+16+17 — utime+stime+**cutime+cstime**, children
counted (`Documents/DECISIONS.md` 2026-08-18) — over a 60 s idle window. `CLK_TCK`=100.

Parser known correct; validate against `ps -o times=` before trusting it:

```sh
awk '{n=split($0,a,") "); split(a[n],f," "); print f[12]+f[13]+f[14]+f[15]}' /proc/$PID/stat
```

**Compare against** (on `c006fa8`; expect equal or lower on the merged branch):
before = bench 30.000% + hud 8.767% = 38.767%; after = 32.983%; delta −5.784.
Criterion passes if after ≤ before.

## Step 2 — criterion 42, MIDI→OSC latency

`scripts/sooperlooper/synthpad.py` drives synthetic presses through a virtual ALSA port.
**Nobody touches the instrument.**

```sh
python3 scripts/looper-session.py --measure-latency 100 > /tmp/lat-on.txt 2>&1 &
sleep 12
python3 scripts/sooperlooper/synthpad.py 180
grep '^live:' /tmp/lat-on.txt
```

`--bench-only` for the HUD-off condition. Run one HUD-on condition with
`scripts/midi-load.py 200` alongside.

**Compare against**: HUD on p50 0.188 / p99 0.835 ms; HUD off p50 0.187 / p99 2.202;
under load p50 0.201 / p99 0.723. Finding is *no measurable HUD penalty*.

## Step 3 — criterion 46, crash blast radius

`kill -9` the merged session's MainPID; time until UDP 9953 rebinds and the APC shows a
`Connecting To` in `aconnect -l`. Two runs.

**Compare against**: OSC 10.58 / 10.72 s, APC 10.73 / 10.87 s, audio never stopped
(`wired=1`), 1–2 xruns. `RestartSec=10` is a floor; startup is ~0.6 s.

## Traps — each of these exited 0 having recorded nothing

1. **`xrun-corr.sh` writes to `~/xrun-corr.out`, not stdout**, and truncates per run.
   Copy it out after each run; assert it contains `TOTAL`.
2. **`set-surge-audio.sh` needs `sudo`** — without it, it fails on `/etc/mpe/mpe.env` and
   continues. A run labelled 512 executed at 1024. Print
   `tr '\0' ' ' < /proc/$(pgrep -x jackd|head -1)/cmdline` and assert the period.
3. **`pgrep -f` / `pkill -f` over ssh matches your own command line** and kills the remote
   shell; the call returns nothing and looks fine. Use `pgrep -f "[l]ooper-sess"`, and
   never combine a kill with a launch mentioning the same string in one ssh invocation.
   Prefer a script file on the Pi.
4. **A remote command returning no output is not evidence it ran.** Verify deploys with
   `grep -c <marker> <file>`.
5. **`synthpad.py` must rotate across all eight pads.** Hammering one walks that loop into
   tail capture, where the gesture is consumed and no OSC is sent.
6. **Strays holding 9953 make the unit refuse to start** — correctly and loudly. Clear
   them first; assert `MainPID` is non-zero before timing.

## Acceptance

- Both measurement docs carry the **actual commit measured**, stated explicitly, and the
  provenance note is replaced rather than left standing.
- Material changes to any absolute are explained; if a conclusion changes, update the
  Phase 3M table in the spec.
- Appliance left as found: buffer restored, looper stack in its prior state, working tree
  clean. Verify with `git status --porcelain`; do not assume.
- Do not ask Mitch to press anything. All three are fully automatable.

## Also outstanding, if you have appliance time

The **512 × 3 looper comparison** (work-order task 6) is void — two measurement scripts
overlapped and the first one's cleanup trap stopped the looper units partway through the
second one's condition. Procedure and traps in
`docs/measurements/looper-stack-cost-2026-08-19.md`. It gates D15. One script at a time,
and confirm the buffer actually changed before starting.
