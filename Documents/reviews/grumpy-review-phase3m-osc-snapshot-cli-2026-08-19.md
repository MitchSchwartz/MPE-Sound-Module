# Grumpy review — phase3m-osc-snapshot-cli

*Reviewed: 2026-08-19 (America/Toronto)*

**Branch:** `yolo/phase3m-osc-snapshot-cli` (MPE-Module, uncommitted) + `yolo/snapshot-cli-criterion-6` (mpe-cli, uncommitted)
**Scope:** Criterion 41 (unified `SlOscSession`), Criterion 42 (MIDI→OSC latency measurement), Criterion 6 prep (snapshot `services`/`processes`/`graph`)
**Reviewer note:** `~/.claude/skills/grumpy-dev-code-review/SKILL.md` and `AGENTS.md` could not be read — the `Read` tool is blocked by the `agentjail-hook` in this environment. Review was conducted against the AGENTS.md content supplied in the session rules, `Documents/specs/next-work-order-2026-08-19.md`, and `Documents/DECISIONS.md`. If the skill defines a specific rubric or severity scale, re-map the findings below accordingly.

---

## Verdict

**Do not merge.** Criterion 41 is genuinely well executed and I would take it on its own. Criterion 42 and the Criterion 6 prep are not ready: one produces a measurement that measures nothing, the other adds ~22 subprocess forks to the appliance's hottest planned code path, ahead of the spike that was supposed to decide whether that was affordable at all.

Three P0s, seven P1s. The P0s are not style disagreements — each one causes the system to state something false: a latency number that isn't latency, a service status that may be hours old, and a benchmark that can never produce a sample.

| Criterion | State |
|---|---|
| 41 — unified `SlOscSession` | **Sound design, ships with dead tests** (P1-4, P1-5, P1-8) |
| 42 — MIDI→OSC latency | **Rejected** — measures nothing (P0-3) |
| 6 prep — snapshot fields | **Rejected** — CPU budget + staleness (P0-1, P0-2) |

---

## What is good

Credit where it's earned, because the core of criterion 41 is the best thing in this branch:

- **The merge is real.** Bench and HUD genuinely share one listen port and one cache. `SlQuery` is deleted rather than left rotting beside its replacement, 9952 is retired everywhere including the unit comment, and `install-units.sh` stops the retired client units so the port is actually free on upgrade. That is a complete migration, not a half one.
- **The fatal-bind behaviour survived the refactor.** `SlOscSession.start()` still raises `SystemExit` with an actionable message rather than limping on with a dead listener (`sl_osc_session.py:85-92`). The 2026-08-14 incident that motivated it is still being honoured even though the code moved.
- **`SlBenchStateListener` is now a router with no I/O of its own**, which is the right shape and makes it testable without a socket.
- **mpe-cli bumped `MPE_CLI_VERSION` to 1.2.3.** The version discipline is being followed.

---

## P0 findings

### P0-1 — Snapshot adds ~22 subprocess forks per build, ahead of the spike that was meant to authorize them

**Files:** `patch_browser/session_snapshot.py:43-176` (new `build_services`, `build_processes`, `build_graph_probe`), wired at `build_snapshot` (`:512-519`)

`build_snapshot()` gains three probe blocks. Counting forks against the memoization that is actually in place:

| Probe | New forks | Note |
|---|---:|---|
| `build_services` — `is-active` × 11 units | 8 | 3 units already memoized by the main body |
| `build_services` — `is-enabled` × 11 units | 11 | fresh memo, zero prior hits |
| `build_processes` — `pgrep -x jackd`, `pgrep -f surge-xt-cli` | 2 | |
| `build_graph_probe` — `jack_lsp` | 1 | |
| **Total new** | **22** | |

The repo has already measured the constant this multiplies against. `Documents/specs/next-work-order-2026-08-19.md:88-89`:

> `build_snapshot()` is **57.3 ms**, of which **55.9 ms is three `systemctl` forks**; everything else is 1.4 ms. At the spec's 0.5 s publish interval that is ~11.5% of a core.

That is **18.6 ms per `systemctl` fork**. Nineteen new `systemctl` forks is **~353 ms**; `pgrep`/`jack_lsp` are lighter (no D-Bus round trip) but call it ~20 ms. New `build_snapshot()` lands around **430 ms**, up from 57.3 ms — roughly **7.5×**.

At the `PUBLISH_INTERVAL_S = 0.5` already declared at `session_snapshot.py:29`, that is **~86% of a core, continuously**. The build would also take 430 ms against a 500 ms publish interval, leaving ~70 ms of slack before `seq_stale` starts flapping.

Three separate rules say don't do this:

1. **AGENTS.md**, verbatim: *"Before adding any polling loop, watchdog tick, or timer: CPU is the scarcest resource on this appliance... Compute cost × cadence and put it in the PR."* No such calculation appears in this branch.
2. **Work order task 3 acceptance** (`next-work-order-2026-08-19.md:82`): *"No new subprocess spawning: `mpe status` must not fork per field."* This forks per field, twice per service.
3. **Work order task 5 is explicitly blocked on task 4** (`:101-105`) — the D-Bus fork-free liveness spike, whose acceptance bar is *"<1% of a core at 2 Hz"*. These fields were built before that spike ran. The spike exists precisely to decide whether this data is affordable, and the answer has been assumed.

**Honest caveat on blast radius:** no publisher timer exists yet, so today this cost is paid per CLI invocation, not continuously. This is a landmine rather than a fire. But `PUBLISH_INTERVAL_S` is already in the file and these fields exist only to feed that publisher — the moment task 5 unblocks, this ships at 2 Hz.

**Fix:** run task 4 first. If D-Bus `ActiveState` is ≪1 ms, this all becomes free and the code is fine. If not, batch into a single `systemctl is-active unit1 unit2 ...` invocation (one fork, not 22) plus `systemctl show -p UnitFileState` for enablement — and per the work order, carry the age of any cached liveness in the snapshot.

### P0-2 — The CLI renders an arbitrarily stale snapshot as current truth

**Files:** `mpe-cli/lib/snapshot.sh:4-25`, consumed by `commands/status.sh`, `diagnose.sh`, `engine.sh`, `jack.sh`

```
if path.is_file():
    snap = read_snapshot()
else:
    snap = build_snapshot()
```

The file is read whenever it exists, with **no freshness gate**. `read_snapshot()` does the work — it computes `_meta.snapshot_stale` and `_meta.age_s` (`session_snapshot.py:581-586`) — and then nothing in mpe-cli ever looks at them. I grepped the whole CLI repo for `_meta`, `snapshot_stale`, and `age_s`: zero hits.

Because **no publisher exists yet**, any `session.snapshot.json` on the Pi got there from a manual debug run of `python3 -m patch_browser.session_snapshot`. So the realistic failure is not theoretical: someone debugs once, a file lands in the run dir, and from then on `mpe status` reports that appliance's service health **as of that moment, forever**. `mpe status` will confidently print `active=active` for a unit that died three days ago.

The per-field `.stale` checks in the renderers do not save this. `services[*].stale` is only true when the systemctl probe failed *at build time* — it says nothing about the document being old. And `processes.stale`/`config.stale` are hardcoded `False` (see P1-10), so a PID read from an ancient file is reported as definitively fresh.

The work order named this exact failure twice — *"Stale fields render as unknown, never as a last-known value (criterion 4)"* (`:81`) and *"a cached judgement is the last-known-good problem wearing a different hat"* (`:97`). The mechanism to prevent it was written and then not called.

**Fix:** gate on `_meta.snapshot_stale` — rebuild when stale, or render every field as `unknown` and print the age. Do not silently serve a cached judgement.

### P0-3 — Criterion 42 measures nothing, and the doc reports it as a result

**Files:** `scripts/sooperlooper/measure_midi_osc_latency.py`, `docs/measurements/looper-midi-osc-latency-2026-08-19.md`

Two independent defects, either of which invalidates the deliverable.

**(a) The synthetic harness measures `time.monotonic()` plus a list append.**

```python
def fake_send(_path: str, _args: list) -> None:
    sent_at.append(time.monotonic())

t0 = time.monotonic()
fake_send(f"/sl/{note % 16}/hit", ["trigger"])
latencies.append((sent_at[-1] - t0) * 1000.0)
```

`fake_send` is called synchronously between `t0` and the read. The measured interval contains one function call, one `monotonic()` and one `append` — no MIDI decode, no OSC serialization, no socket write, no poll loop. The reported **p50 of 0.005 ms** is 5 microseconds, which is exactly what that costs and nothing like a MIDI→OSC path.

The `time.sleep(0.002)` that the doc calls "the bench's ~2 ms poll cadence" sits *after* the sample is taken, so the cadence never participates in the measurement.

This makes the doc's conclusion — *"Synthetic harness shows no measurable HUD-thread penalty at p99"* — unsupported. The only thing the HUD thread could contend for here is the GIL around an append. The work order asked this question for a specific reason (`:52-55`): CPython's 5 ms switch interval means a background thread can hold the GIL through a MIDI callback. A harness that never runs a MIDI callback cannot answer it.

The results table also carries **`(synthetic, nerdrack 2026-08-19)`** — x86 laptop numbers in a table for an appliance criterion. The doc is honest that Pi live numbers are still required, which I credit, but it draws a conclusion anyway.

**(b) `measure_live()` can never produce a single sample.**

```python
def tracked_send(path: str, args: list) -> None:
    if pending:
        t0 = pending.pop(0)
        latencies.append((time.monotonic() - t0) * 1000.0)
    session.client.send_message(path, args)
```

`tracked_send` is defined and **never called**. Nothing is wired to it. `on_midi` fills `pending`, `latencies` stays empty, the loop spins to `deadline`, and `_summarize([])` returns `{count: 0, p50: 0.0, p99: 0.0, max: 0.0}`.

So the live path — the one the doc says to run during the next appliance soak — will print `live: n=0 p50=0.000ms p99=0.000ms max=0.000ms` and exit 0. Someone will run it on the Pi, get a clean zero, and have no signal that the tool is inert.

**Fix:** the live harness needs `tracked_send` actually installed as the bench's send hook. The synthetic harness needs to route through real OSC serialization and a real socket, or be deleted — a harness that can only produce 5 µs is worse than no harness, because it produces a number someone will cite.

---

## P1 findings

### P1-4 — Both new criterion-41 tests are dead code and never execute

**File:** `tests/test_looper_session.py:114-130`

```
if __name__ == "__main__":
    unittest.main()


    def test_single_osc_session_module_present(self) -> None:
```

The new `def`s are indented 4 spaces *after* `if __name__ == "__main__":`, so Python parses them as further statements **inside that block**. Under `unittest discover`, `__name__ != "__main__"` and the block never runs — the tests do not exist. Run the file directly and they are defined only *after* `unittest.main()` has already exited. They are also bare functions taking `self`, not methods on any `TestCase`.

Net: the two tests that assert criterion 41's headline claims (`SlOscSession` exists, 9952 is gone, the shared session is wired) **never run in any invocation**. `python3 -m unittest discover -s tests -q` will pass and prove nothing about criterion 41's wiring.

`tests/test_session_snapshot.py` has the same placement smell but at column 0, so `SnapshotServicesTests` *is* collected. It works by luck of indentation. Move both above the `__main__` guard.

### P1-5 — The refactor deleted protocol assertions and replaced them with delegation checks and a tautology

Three separate losses, all in the same direction — tests now assert that code calls a mock, not that the right bytes go on the wire.

**`tests/test_sl_bench_listener.py`** previously asserted the actual OSC registration:

```python
paths = [c.args[0] for c in client.send_message.call_args_list]
self.assertIn("/sl/0/register_auto_update", paths)
```

now asserts only `session.register_bench.assert_called_once_with(num_loops=2)`. I checked whether the new `tests/test_sl_osc_session.py` picks up the slack: it does not. It covers `_cache_key`, cache sharing, `seed_tempo` branching, and bind failure — **nothing asserts what `register_bench` or `register_hud` put on the wire**. So control names, update intervals, return URL and return path are now untested anywhere. That is precisely the surface criterion 41 rewrote.

**`tests/test_sl_engine_restart.py`** dropped its sentinel guard:

```python
global_regs = [c for c in client.send_message.call_args_list if c.args[0] == "/register_auto_update"]
self.assertEqual(global_regs, [])
```

That asserted the bench sends **no global registrations**. It is gone, replaced by another delegation check. This matters more after the merge, not less: bench and HUD now share one client, and `register_hud()` *does* send a global `/register_auto_update` for tempo (`sl_osc_session.py:146-148`). The guard was weakened exactly where the merge raised the risk.

**`tests/test_sl_hud_seed.py`** is now tautological:

```python
def _seed():
    if sl.cached("tempo", -1) is None:
        sl.get("tempo", -1)
sl.seed_tempo = MagicMock(side_effect=_seed)
```

The test reimplements `seed_tempo`'s logic in the stub and then asserts the stub behaves that way. `SlOscSession.seed_tempo` is never exercised. The docstring still claims *"Seeding is additional to the subscription, not a replacement for it"* — but `register_hud` is now a bare `MagicMock` that sends nothing, so that property is no longer verified at all.

### P1-6 — `mpe engine status` silently lost the MASKED warning

**File:** `mpe-cli/commands/engine.sh`

Deleted with no replacement:

```bash
if systemctl is-enabled mpe-jackd.service 2>/dev/null | grep -q masked; then
    echo "  mpe-jackd.service is MASKED"
fi
```

`systemd_unit_enabled` maps `masked` and `masked-runtime` to `False` (`session_snapshot.py:316`), which renders as `enabled=disabled`. A **masked** `mpe-jackd` — which cannot be started even manually — is now indistinguishable from one an operator merely disabled.

This repo cares about the difference: `systemd_unit_enabled`'s own docstring says *"`disabled` is an explicit operator decision"*, and `cmd_engine_mask_jackd` sits directly below in the same file, so masking is a first-class supported operation whose status readout was just removed. Losing this on the command an operator runs *because JACK isn't working* is a bad trade.

### P1-7 — Output shape changed: units that aren't installed now render as `inactive`

**Files:** `mpe-cli/commands/status.sh`, `mpe-cli/lib/snapshot.sh:61-73`

The old `cmd_status` skipped units that don't exist on this appliance:

```bash
if ! systemctl list-unit-files "$unit" >/dev/null 2>&1; then continue; fi
```

There is no equivalent now. For a unit that isn't installed, `systemctl is-active` prints `inactive` → `False`, and `is-enabled` prints nothing → `None`. So `stale` (`active is None and enabled is None`) is **False**, and the row renders `active=inactive enabled=unknown` where it previously printed nothing at all.

The renderer's `'.services[$u] // empty'` guard doesn't help — all 11 units are always emitted by `STATUS_SERVICE_UNITS`, so the key is never absent.

Work order task 3 acceptance (`:78-79`): *"Output shape unchanged: diff each command before/after, byte-identical or a documented delta."* This is an undocumented delta, and it reports absence as a negative health signal.

Related: `STATUS_SERVICE_UNITS` publishes **11** units; `mpe_cli_render_services_block` hardcodes **8**. The three looper units are collected — six `systemctl` forks — and then never displayed. Two hardcoded lists in two repos that must agree, already disagreeing on the day they were written. (I verified all 11 unit files do exist in `config/`, so the list itself is correct.)

### P1-8 — Bind-failure message tells the operator to run a command that fails

**File:** `scripts/sooperlooper/sl_osc_session.py:89`

```
Fix: mpe restart looper (or stop mpe-looper-session.service), then start again.
```

`mpe restart looper` runs `sudo systemctl restart mpe-looper.service` (`mpe-cli/commands/restart.sh:14-16`). There is no `mpe-looper.service` — `config/` contains `mpe-looper-session.service` and `mpe-sooperlooper.service`. The command fails with *"Unit mpe-looper.service not found"*.

The stale target in `restart.sh` is pre-existing, but this branch newly promotes it as the recommended recovery step, printed at the exact moment an operator is stuck with a held port. The parenthetical fallback works, so this is recoverable — but an error message that lies during an incident is worse than no message, and this file's own comment history shows the team knows that. Fix `restart.sh` to target `mpe-looper-session.service`, or drop the suggestion.

### P1-9 — `jack_lsp` runs unguarded with a 3-second timeout

**File:** `patch_browser/session_snapshot.py:143-156`

`build_graph_probe()` invokes `jack_lsp` on every `build_snapshot()` with no check that jackd is running. The old `cmd_engine_status` guarded it properly:

```bash
if command -v jack_lsp >/dev/null 2>&1 && pgrep -x jackd >/dev/null; then
```

When JACK is down, `jack_lsp` can block until the `timeout=3` fires. So on a broken appliance — the only time anyone runs these commands — `mpe status` gains up to **3 seconds**. Under the eventual 2 Hz publisher this is unbounded: a 3 s probe on a 0.5 s interval.

`build_processes()` already computes `jackd_pid`. Gate the graph probe on it.

Also note the match is `"surge" in stdout.lower()` — a substring test against the whole `jack_lsp` output, which any client or port name containing "surge" satisfies.

### P1-10 — `processes.stale` and `config.stale` are hardcoded `False` and can never be true

**File:** `patch_browser/session_snapshot.py:158-176`

```python
def build_processes() -> dict[str, Any]:
    return {"jackd_pid": ..., "surge_pid": ..., "stale": False}
```

Both `pgrep` calls can return `None` — on `OSError`, on timeout, on a non-integer parse — and the block still reports `stale: False`. Same for `config.stale`, which stays `False` even when `_read_mpe_env_keys` swallowed an `OSError` and returned `{}`.

The rest of this file maintains a careful staleness contract (`field_age_stale`, `stale = active is None and enabled is None`, the tri-state `bool | None` convention). These two fields break it by asserting freshness unconditionally. Combined with P0-2, a PID read from a week-old file is published as definitively current.

---

## P2 findings

- **Load-bearing "why" comments deleted during a mechanical refactor** (`sl_bench_listener.py`). Three casualties: the 2026-08-14 incident record explaining why bind failure must be fatal (the *behaviour* moved to `SlOscSession`, the *reason* did not); *"Handled before the footswitch lookup: the fader layer wants this even for loops with no pad bound"*; and the network-budget rationale *"Slower than state on purpose... would cost a datagram per loop per 100 ms for no benefit."* That last one is exactly the class of constraint AGENTS.md tells you to preserve, and it now lives nowhere. A refactor should carry the reasons across, not launder them out.
- **Snapshot schema not bumped.** Four new top-level keys (`services`, `processes`, `graph`, `config`) with `SCHEMA_VERSION` still `1`, while the docstring still says "schema v1 document". The jq `// empty` / `// true` defaults mean an old snapshot degrades to "unknown" rather than erroring — but there is then no way to distinguish *"old writer"* from *"all probes failed"*. Given the Pi tracks `main` and mpe-cli is installed separately on the laptop, version skew is guaranteed.
- **Mangled `printf` format strings.** `engine.sh` and `jack.sh` both now contain a literal newline inside the quoted format where `\n` used to be. Output is unchanged (a literal newline prints a newline), so this is not a correctness bug — but it is unmistakable evidence of an unreviewed machine edit, and it will confuse the next reader.
- **Missing `local` in `cmd_engine_status`.** `stale`, `active`, `enabled`, `jack_pid`, `surge_pid`, `graph_stale`, `on_graph` are all assigned without `local`, leaking into the shell's global namespace. `mpe_cli_render_services_block` declares its locals properly — be consistent, especially in sourced functions.
- **New hard dependency on `jq`** for four commands (`status`, `diagnose`, `engine status`, `jack status`) that previously had none. `mpe_cli_require_jq` exits 1 if it's absent. A patch bump (1.2.2→1.2.3) understates a new external dependency plus changed output; this reads as a minor bump.
- **`SlBenchStateListener.register(self, _client, ...)` takes a client and ignores it**, and `sooperlooper-apc-bench.py` still passes one. A parameter that lies about being used is a trap. Likewise `start()` is now a docstring-only no-op that callers still call.
- **`_probe_process_pid(pattern, *, exe=None)` has a dead parameter** — when `exe` is set, `pattern` is discarded entirely, and the sole caller passes `_probe_process_pid("", exe="jackd")`. Two behaviours in one function. Split it.
- **`pgrep -f surge-xt-cli` is over-broad.** It matches any command line containing that string — including an operator's `journalctl -u surge-xt-cli` in another shell — and would report a bogus `surge_pid`. The old code had the same issue, so this isn't a regression, but it's now feeding a structured field that other tooling will trust.
- **`build_processes()` and `build_graph_probe()` accept no injection**, unlike `build_services(unit_active=..., unit_enabled=...)`. They cannot be unit-tested without forking real `pgrep`/`jack_lsp` on the test host, which makes `tests/test_session_snapshot.py` environment-dependent and non-hermetic.
- **Double memoization**: `build_snapshot` wraps the callables, then `build_services` wraps them again. Sharing the memo across the main body is deliberate and good; the second wrap is redundant and obscures that.
- **`NUM_LOOPS` vs `num_loops` inconsistency.** `register_hud()` iterates the module constant while `register_bench()` takes a parameter, and `maybe_reregister()` re-registers HUD over `NUM_LOOPS` but bench over `self._bench_num_loops`. Run the bench with 8 loops and the HUD still subscribes 16.
- **Inconsistent handler robustness.** `_on_hud_reply` guards with `len(args) >= 3`; `_on_bench_state` declares typed positionals with no guard, so a short `/sl/bench/state` message raises in the server thread. `_on_hud_reply` is also the *default* handler and blindly coerces `int(args[0])` on any unmatched address with 3+ args — any stray OSC traffic to this port either poisons the cache or throws.
- **Env-var fallback is a silent migration hazard.** `LISTEN_PORT` falls back `MPE_SL_SESSION_LISTEN_PORT` → `MPE_SL_BENCH_LISTEN_PORT` → `MPE_SL_HUD_LISTEN_PORT` → 9953. An appliance with a leftover `MPE_SL_HUD_LISTEN_PORT=9952` in `/etc/mpe/mpe.env` binds **9952** with no warning, while the comment two lines up says 9952 is retired. Warn when resolving via the HUD alias.
- **`SlOscSession` has no `stop()`/`close()`.** The server thread and bound port live until process exit, so two sessions can't coexist in one process and tests can't clean up.
- **Duplicated poll loop with divergent failure semantics.** The new inline `hud_only` loop in `looper_session.py:94-101` repeats `_hud_thread_main`'s body but omits its `except Exception → os._exit(1)` hard-fail. Two modes, two behaviours on error.

---

## P3 findings

- `SlOscSession.get()` pops the cache key then polls while the server thread writes it — an auto-update landing mid-window is returned as if it were the reply. Benign for tempo seeding, but the method is generic and undocumented on this point. `self.last` is also cross-thread without a lock (fine in CPython, but it's an unstated assumption).
- `seed_tempo()` is reachable from `maybe_reregister()` every 15 s and can block the calling thread for up to 400 ms if the engine never answers. On the bench poll thread that is a dropped-input window — worth confirming, given criterion 42 is about exactly this path.
- `_summarize` uses `statistics.median` for p50 but `_percentile` for p99 — two different estimators in one summary.
- `--hud-on --hud-off` together silently means "run both conditions" rather than erroring (`args.hud_on == args.hud_off`).
- `host` and `port` locals in `run_bench` are now dead after `osc = osc_session.client`.
- Three consecutive blank lines introduced before `snapshot_path` in `session_snapshot.py`.
- `mpe_cli_render_services_block` forks `jq` (and `wc`) per field — roughly 40 local forks per `mpe status`. Laptop-side so cheap, but it is literally "fork per field", which task 3's acceptance prohibits.
- `mpe diagnose` now makes two SSH round trips (snapshot fetch, then `diagnose-pi-state.sh`) where it made one.

---

## Recommended sequence

1. **Fix P0-3 or drop criterion 42 from this branch.** Wire `tracked_send`, and either make the synthetic path traverse real serialization/socket or delete it. Do not leave a doc citing 5 µs.
2. **Hold the criterion 6 snapshot fields until task 4 (D-Bus spike) reports a per-query cost.** That spike exists to answer this question; running it is ~20 minutes and may make P0-1 evaporate entirely.
3. **Gate the CLI on `_meta.snapshot_stale`** (P0-2). Small change, and the mechanism is already written.
4. **Fix the test indentation** (P1-4) and re-run discovery — nothing about criterion 41 is currently proven.
5. **Restore protocol-level assertions** in `test_sl_osc_session.py` for `register_bench`/`register_hud` wire format (P1-5), and restore the "no global registrations from bench" guard.
6. **Restore the MASKED readout and the not-installed skip** (P1-6, P1-7), or document both deltas as the acceptance criterion requires.
7. Split criterion 41 into its own PR. It is close to mergeable and shouldn't wait behind the other two.

**Deploy note:** nothing here has been run on the Pi, and I did not execute the test suite (no direct Python invocation in this environment). All findings are static, but P1-4 and P0-3(b) are the kind that a green test run would have hidden rather than caught.
