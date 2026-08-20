# T2 — bug-class sweep (2026-08-20)

**Scope:** `scripts/`, `patch_browser/`, watchdogs. **Findings only — no fixes in this task.**
Fixes land in separate commits (T3 guards, follow-on P0s).

Pi verification: `raspberrypi2` @ 2026-08-20T22:36+01:00 (post E1 revert).

---

## Ranked findings

| Rank | Class | File:line | What | Pi live? | Notes |
|---:|---|---|---|---|---|
| P0 | B | `measure-latency-run.sh:337` | `grep meter.state \|\| echo 0` | meter **LIVE** | missing meter → RESULT xruns=0 |
| P0 | B | `xrun-corr.sh:22-23` | same pattern | meter **LIVE** | crackle tool reads 0 when blind |
| P0 | B | `bench-xruns.sh:117-118` | journal xrun grep | journal has lines* | *5 xrun-ish lines/h — not jackd softmode counter; still wrong source |
| P0 | B-live | `sl-watchdog.py` (was) | `/tmp/sooperlooper.log` | **DEAD** | fixed → meter.state (T3) |
| P0 | B-live | `looper_health.py` (was) | journal xrun lines | sparse | fixed → MeterXrunCounter |
| P0 | B | `sl-watchdog.py:322-326` | poll returned `(0, err)` | — | **T3 fix:** `(None, err)` + alarm |
| P1 | A | `surge-watchdog.sh:86,95-101` | `jack_lsp` fallback / reconcile inner while | meter **LIVE** | steady path uses meter.state |
| P1 | A | `sl-watchdog.py:487` | `read_graph_snapshot` → `jack_lsp` fallback | meter **LIVE** | hot path file read |
| P1 | A | `surge_cpu_monitor.py:101-108` | `pgrep` @ 2s in worker | n/a | touch UI only |
| P1 | A | `surge_poly_governor.py:150-183` | `pgrep` via check_health | n/a | touch UI only |
| P1 | A | `session-snapshot-publisher.py:53-57` | batched `systemctl` @ 5s TTL | snapshot **DEAD** when session stopped | expected when unit off |
| P2 | A | `sl-watchdog.py:259,536` | `systemctl` / `wire-jack-graph.sh` | n/a | repair paths only |
| P2 | B | `looper_health.py:406-407` | carry-forward xruns on poll None | meter **LIVE** | HUD shows stale count |
| P2 | B | `audio_engine.py:171-172` | `read_engine_state` → `{}` | engine.state **LIVE** | missing ≡ empty |
| P2 | B-live | `engine-reconcile.state` | reconcile KV | **MISSING** | ok when no recent restart |
| P2 | B-live | `session.snapshot.json` | publisher output | **MISSING** | session unit inactive |
| P2 | B-live | `/tmp/sooperlooper.log` | engine stdout | **MISSING** | not used for xruns after 2026-08-20 |

### Class A — fixed instances (confirmed)

| File | Was | Now |
|---|---|---|
| `sl-watchdog.py` | `jack_lsp` every 10s | `meter.state` 5 Hz |
| `sl_hud_monitor.py` / session HUD | `journalctl` @ 2 Hz | `MeterXrunCounter` |
| `surge-watchdog.sh` | `jack_lsp` steady | `meter.state` + fallback |
| `looper_health.py` HUD | `jack_cpu_load` per sample | one long-lived client |

---

## Class B-live — Pi command output (2026-08-20)

```
LIVE meter.state path=/run/mpe/meter.state size=118
  peak_linear=0
  wired=1
  jack_online=1
LIVE engine.state path=/run/mpe/engine.state size=71
LIVE jack.state path=/run/mpe/jack.state size=63
LIVE surge.state path=/run/mpe/surge.state size=42
DEAD engine-reconcile path=/run/mpe/engine-reconcile.state MISSING
DEAD session.snapshot path=/run/mpe/session.snapshot.json MISSING
LIVE sl_hud path=/home/mitch/.mpe_sl_hud_state.json size=393
DEAD sooperlooper.log path=/tmp/sooperlooper.log MISSING
MPE_PEAK_METER=1 · mpe-peak-meter.service active
journalctl -u mpe-jackd --since 1h | grep -ci xrun → 5
```

---

## T3 — doctrine guards (2026-08-20, commit `656b081`)

### T3a — periodic-loop lint

- **Module:** `scripts/lib/periodic_loop_lint.py` · **tests:** `tests/test_periodic_loop_lint.py`
- **Laptop:** `mpe test local all` → 1021 tests OK (includes deliberate `jack_lsp`/`journalctl` snippet failures + production modules pass)
- **Pi:** not required — AST walk is repo-local; production modules linted on laptop match Pi checkout

### T3b — boot-time source liveness (`patch_browser/health_source_liveness.py`)

Wired into `sl-watchdog.py` (startup) and `looper_session.py` (HUD thread). Demonstrated on `raspberrypi2` via `sl-watchdog.service` (uses `/usr/bin/python3` in unit — not a blind counter path).

**Pass** — meter live, service starts:

```
Aug 20 22:42:28 raspberrypi2 python3[34098]: health-source-liveness: sl-watchdog ok
Aug 20 22:42:28 raspberrypi2 python3[34098]: [22:42:28] sl-watchdog: watching every 10s — repairs JACK graph, alarms on wedge
```

**Fail** — `mpe-peak-meter` stopped, `/run/mpe/meter.state` removed, service start:

```
Aug 20 22:43:01 raspberrypi2 python3[34540]: HEALTH_SOURCE_FAIL: meter.state: missing (/run/mpe/meter.state)
Aug 20 22:43:01 raspberrypi2 systemd[1]: sl-watchdog.service: Main process exited, code=exited, status=1/FAILURE
Aug 20 22:43:01 raspberrypi2 systemd[1]: sl-watchdog.service: Failed with result 'exit-code'.
```

Restored: `mpe-peak-meter` + `sl-watchdog` both **active** after meter.state returned.

*Last updated: 2026-08-20 (America/Toronto)*
