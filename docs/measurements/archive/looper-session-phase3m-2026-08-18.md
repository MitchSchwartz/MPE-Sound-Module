# Looper session Phase 3M merge — 2026-08-18

PR [#72](https://github.com/MitchSchwartz/MPE-Sound-Module/pull/72) — merge `mpe-apc-bench` +
`sl-hud-monitor` into `mpe-looper-session.service`. Spec criteria: session-control-plane
Phase 3M (38–48); this run covers Pi soak, criterion **47** (idle CPU), and xrun sanity.

**Verdict:** **PASS for merge soak** — basic looper + HUD OK; import fix required on deploy;
idle CPU ~3.3% merged; **0 xruns** through soak. Criterion **42** (MIDI latency) and hot
**40** (engine restart while grid active) still open.

## Preconditions

| # | Gate | Command | Required | Recorded output |
|---|---|---|---|---|
| P1 | RT audio threads live | `mpe rt status` | SCHED_FIFO on jackd + Surge audio threads | jackd tid=4713 SCHED_FIFO 70; Surge tid=5013 SCHED_FIFO 65 |
| P2 | Looper session running | `systemctl is-active mpe-looper-session` | active | active @ `2c9fab4` (no PYTHONPATH drop-in) |
| P3 | JACK graph wired | `mpe jack status` | xruns named, graph connected | xruns: **0**; Surge → loop0..15_in, common_out → playback |

## Conditions

| Field | Value |
|---|---|
| Pi model / RAM | Raspberry Pi 4 Model B Rev 1.5 / 7.6 GiB |
| OS | Debian 13 (trixie) arm64 |
| MPE-Module commit | `2c9fab4` (`yolo/looper-session-phase3m`) |
| Profile / UI | `standalone` / `touch` |
| Buffer / periods / rate | **1024** / 3 / 48 kHz 24-bit *(not Phase-1 baseline 256)* |
| Governor | `performance` (via `mpe-cpu-governor.service`) |
| Power | USB-C |
| Deploy | `git pull` → `install-units.sh` → start `mpe-looper-session`; retired units disabled |

## Functional soak (hand)

| # | Test | Result | Notes |
|---|---|---|---|
| 44 | Record → clear → grid-establish | **PASS** | Mitch hand-check; pads + touch HUD |
| 43 | Port hold (9952/9953) | not re-run | Ports held by single merged `python3` |
| 46 | `kill -9` merged process recovery | not timed | Restart=always observed in earlier bounce |
| 40 | Engine restart → grid re-apply | **partial** | Stack bounce OK; hot path (engine-only restart with established grid) not recorded |

## Criterion 47 — idle CPU (60 s `/proc/<pid>/stat`, HZ=100)

Sampled on Pi 2026-08-18 ~18:26–18:29 BST, idle graph (no playing load).

| Process | pid | VmRSS | CPU % (60 s idle) |
|---|---|---:|---:|
| **mpe-looper-session** (merged bench + HUD) | 52604 | 29 116 kB | **3.28** |
| sooperlooper | 5648 | 158 180 kB | 7.25 |
| surge-xt-cli | 4988 | 168 912 kB | 11.02 |

**Read:** merged session **~3.3% of one core** at idle — at the spec owner budget line (&lt;3%
steady state). No before/after for the retired two-process split on this session; merged
footprint is 29 MB RSS vs running bench + HUD separately.

## Xruns / performance

| Check | Result |
|---|---|
| JACK xrun counter (`mpe jack status`) | **0** (unchanged through ~3 min soak) |
| Journal xruns (`mpe-jackd`, `mpe-sooperlooper`, 2 min window) | none |
| `mpe looper sl-health` | **skipped** — OSC ports 9952/9953 held by merged session (expected) |

## Deploy bug found + fixed

| Issue | Fix |
|---|---|
| `ModuleNotFoundError: patch_browser` on service start | `looper_session.py`: set `sys.path` before `patch_browser` import — commit `2c9fab4` |
| Temporary workaround | systemd `PYTHONPATH` drop-in removed after fix landed |

## Follow-ups (not blocking merge)

- **42** — MIDI-in → OSC-out latency before/after HUD thread (deferred in PR)
- **40 hot** — `sudo systemctl restart mpe-sooperlooper` with grid established; expect `bench: looper.engine.started — re-applying grid config`
- **Stop timeout** — `systemctl restart mpe-looper-session` hits 10 s `TimeoutStopSec` (HUD `jack_cpu_load` SIGKILL)
- **Load xruns** — `bench-xruns.sh --strict` while playing not run (idle graph only)
