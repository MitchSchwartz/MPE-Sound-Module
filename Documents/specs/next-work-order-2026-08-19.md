# Work order — agent-ready tasks after the 2026-08-18 crackle fix

**Status 2026-08-19:** tasks 1, 3, 4 and 5 are done; task 6 is half done. What remains
needs Mitch at the instrument — see the summary table below. Original text follows.

| # | Task | State |
|---|---|---|
| 1 | Criterion 41 — one OSC session | ✅ PR #76 |
| 2 | Criterion 42 — MIDI→OSC latency | ✅ p50 0.19 ms, no HUD penalty; harness is synthetic and repeatable |
| 3 | Criterion 6 — CLI on the snapshot | ✅ mpe-cli #6/#7 |
| 4 | Liveness spike | ✅ D-Bus batched; 11.5% → 0.53% of a core |
| 5 | Snapshot publisher | ✅ 1 Hz, installed, **not enabled** — your call |
| 6 | Re-measure the looper stack | ✅ 1024 cost + **512 A vs D** on `b9bf98e` |
| 7 | Buffer floor / per-patch headroom | ❌ **needs Mitch playing** |

**Re-take done 2026-08-19:** criteria 42/46/47 and task 6 (512 × 3) measured on `b9bf98e`
(`main`) — see [`rerun-order-2026-08-19.md`](rerun-order-2026-08-19.md) and
[`looper-stack-cost-2026-08-19.md`](../../docs/measurements/looper-stack-cost-2026-08-19.md).

**Original status:** ready to hand to an agent. Each task below is self-contained, has acceptance
criteria, and states its dependencies. Tasks 1–3 depend on **no** pending measurement.
Task 4 is a spike whose result decides task 5.

**Read first:** [`DECISIONS.md`](../DECISIONS.md) 2026-08-18 (both entries),
[`docs/measurements/crackle-root-cause-2026-08-18.md`](../../docs/measurements/crackle-root-cause-2026-08-18.md),
[`session-control-plane-spec.md`](session-control-plane-spec.md).

**House rules that apply to every task:** measure cost × cadence before adding any poll;
a probe has two costs and CPU is the cheaper one; anything touching the audio path must
be declared. Verify on the Pi, not by inspection.

---

## 1. ~~Phase 3M criterion 41 — one OSC connection, one lifecycle~~ — **DONE, PR #76**

**Independent of any measurement. Highest-value remaining Phase 3M work.**

`mpe-looper-session` merged the bench and HUD into one process but kept **two** OSC
endpoints and two caches of engine state:

| | Port | Owner |
|---|---|---|
| bench state listener | 9953 | `sl_bench_listener.SlBenchStateListener` |
| HUD auto-updates | 9952 | `sl_hud_monitor.SlQuery` (inside `HudWriter`) |

That leaves fault #2 from the spec's Problem Statement (*state is copied, not derived*)
intact inside the merged process: tempo is cached in the HUD writer while loop state is
cached in the bench.

**Do:** collapse to one OSC client and one listen port, with a single registration
lifecycle. Both consumers read from one cache.

**Acceptance:**
- One `ThreadingOSCUDPServer` in the process; `MPE_SL_HUD_LISTEN_PORT` retired or aliased.
- `maybe_reregister()` semantics preserved (re-register every 15 s).
- Tempo still **seeded** on registration, not awaited — `register_auto_update` delivers
  on CHANGE, and a subscriber that starts after the engine never learns it
  (`tests/test_sl_hud_seed.py` must still pass).
- Loud failure on a held port survives (criterion 43) — refuse to start, do not warn and
  continue.
- Pi: pad-driven record → clear → grid-establish unchanged by hand (criterion 44).

---

## 2. Phase 3M criterion 42 — prove the MIDI path, do not assert it

**Independent. Produces a number; does not depend on one.**

The merge put the HUD on a background thread and claimed MIDI latency was unaffected.
It has never been measured. The bench polls at ~2 ms, the HUD writes and shells to
`journalctl` at 2 Hz, and CPython's 5 ms switch interval means a background thread can
still delay the MIDI loop while holding the GIL.

**Do:** measure worst-case MIDI-in → OSC-out latency on the Pi, with the HUD thread
running and stopped.

**Acceptance:** a p99 figure for both conditions in `docs/measurements/`, with the method
recorded. If the HUD thread costs measurable latency, move its file I/O and `journalctl`
work off the poll path entirely.

---

## 3. ~~Phase 1 criterion 6 — re-point `mpe-cli` at the snapshot~~ — **DONE, mpe-cli #6/#7**

**Independent. Currently the snapshot is a net negative.**

`session.snapshot.json` exists and is correct, but nothing reads it. `mpe status`,
`mpe engine status`, `mpe jack status` and `mpe diagnose` still each assemble their own
view — so the snapshot is a **fifth** view, which is precisely what criterion 6 exists
to prevent.

**Do:** re-point those four commands at the snapshot.

**Acceptance:**
- Output shape unchanged: diff each command before/after, byte-identical or a documented
  delta.
- A reader on an unknown `schema` major errors rather than printing nulls (criterion 8).
- Stale fields render as unknown, never as a last-known value (criterion 4).
- No new subprocess spawning: `mpe status` must not fork per field.

---

## 4. ~~Spike — fork-free systemd liveness~~ — **DONE 2026-08-19**

`build_snapshot()` is **57.3 ms**, of which **55.9 ms is three `systemctl` forks**;
everything else is 1.4 ms. At the spec's 0.5 s publish interval that is ~11.5% of a core.

**Do:** find out whether systemd's `ActiveState` can be read over D-Bus without spawning
a process, from Python, on this appliance.

**Acceptance:** a measured per-query cost. If ≪ 1 ms, criterion 7 passes at 2 Hz with no
cache and no staleness compromise. If not, fall back to one batched `systemctl is-active`
plus a TTL — and then the snapshot **must carry the age of the cached liveness**, because
a cached judgement is the last-known-good problem wearing a different hat.

---

## 5. ~~Snapshot publisher~~ — **DONE 2026-08-19, installed opt-in**

Build only after task 4 gives a liveness cost that clears criterion 7 (<1% of a core at
2 Hz). Call `build_snapshot()` in-process; never invoke the module CLI on a timer
(418 ms vs 58 ms — 360 ms of that is interpreter start).

---

## 6. Re-measure the looper stack — **PARTLY DONE 2026-08-19**

The numbers that made the looper opt-in are **void**: they were taken while
`surge-watchdog`'s `jack_lsp` probe was the dominant xrun source, and the run that blamed
the looper stopped the looper *and* both watchdogs together.

**Do:** repeat the protocol with the probe fixed, n≥3 per condition (single runs vary
±30%; do not interpret differences below ~2×).

```sh
python3 scripts/midi-load.py 75 &   # deterministic load, DSP median ~42%
sleep 8                             # skip the start transient
scripts/xrun-corr.sh 60
```

Conditions: all off (baseline) · `mpe-sooperlooper` only · `+ mpe-looper-session` ·
`+ sl-watchdog`.

**Acceptance:** a per-component cost in `docs/measurements/`, and either the stack
returns to `ENABLED` in `install-units.sh` or the opt-in default is restated on evidence.
**This gates D15** — the SooperLooper adopt/kill verdict must not inherit void numbers.

---

## 7. Buffer floor — **needs Mitch playing, not an agent**

`512 × 3` now runs 0 xruns at DSP median 42%. Criterion 35 wants `128 × 3` under playing
load in strict mode.

**UPDATE 2026-08-19: retried post-fix, and it holds.** Mitch re-tried those patches after
the graph-probe fix — *"512 works for most, some patches seem to need 1024 still."* So it
is **not** pre-fix residue; it is genuine per-patch variance, and it is bounded to
specific presets rather than the appliance.

Cause not yet measured, and "tune the poly governor" is not obviously the answer — the
governor watches Surge *process CPU* (a proxy its own docstring calls a proxy), reacts in
~14 periods so it cannot prevent a transient, and tuning it harder buys 512 with voice
stealing. Full analysis, the three failure modes to distinguish, and the ranked levers:
[`docs/measurements/per-patch-headroom-open-2026-08-19.md`](../../docs/measurements/per-patch-headroom-open-2026-08-19.md).

Measure before choosing a lever. Name the specific patches.

---

## Not agent work

**SooperLooper B7 / B8 / B10 + mic-guitar** — the adopt/kill verdict needs Mitch at the
instrument. B10 (feel) cannot be delegated at all.
