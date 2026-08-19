# Phase 3M criteria 46 and 47 — crash blast radius and CPU — 2026-08-19

> **Provenance.** Measured on `raspberrypi2` at commit **`b9bf98e`** (`main`, promoted
> 2026-08-19). Tree clean before and after. Buffer **1024 × 3**. Re-take replaces the
> earlier run on `c006fa8` (`yolo/looper-poll-tail-fix`), which had false branch
> attribution.

`raspberrypi2`, `1024 x 3`. 60 s idle windows, `/proc/<pid>/stat` fields 14–17 so forked
children are counted ([`DECISIONS.md`](../../Documents/DECISIONS.md) 2026-08-18).

## Criterion 47 — CPU no worse than the two processes it replaces

The pre-merge units are gone, but their entry points survive, so "before" can be
reconstructed: `--bench-only` and `--hud-only` as two processes on two ports
(9953 / 9952, the pre-merge topology).

| condition | CPU, % of one core |
|---|---:|
| **before** — bench process | 39.417 |
| **before** — HUD process | 10.867 |
| **before, total** | **50.284** |
| **after** — merged session | **42.900** |
| | **−7.384 points** |

**Criterion 47 passes.** The merge is 7.4 points cheaper than the two-process topology.
Both absolutes are **higher** than the `c006fa8` re-take (38.767 / 32.983 before/after
there) — `main` carries stop-then-weld seam logic the poll-tail branch did not — but the
comparison this criterion cares about (merged vs two processes on identical code) still
shows a clear win.

**Worth stating separately: 43% of a core is a lot.** It is the largest single Python CPU
consumer on the appliance, and it is the ~2 ms bench poll loop, not the HUD. The merge
improved it and the criterion is satisfied, but if CPU is ever the binding constraint
again this is where it is, and the fix is the poll loop rather than anything the merge
introduced.

## Criterion 46 — crash blast radius

`kill -9` the merged process mid-session, `Restart=always` recovers. Two runs after
`systemctl restart mpe-looper-session` on `b9bf98e`.

| run | OSC 9953 rebound | APC re-bound | audio |
|---|---:|---:|---|
| 1 | 10.67 s | ~10.7 s * | survived, `wired=1`, +1 xrun |
| 2 | 10.56 s | ~10.7 s * | survived, `wired=1`, +0 xruns |

\* The APC kernel client keeps a stale `Connecting To` line while the bench process is
dead, so wall-clock APC recovery aligns with OSC rebound (~10.6 s), not the ~0.1 s the
`aconnect` line appears to show if you grep it naïvely during `RestartSec`.

**Audio never stopped.** `mpe-jackd` and `surge-xt-cli` are separate units and the graph
stayed wired throughout — D4 holds under a real kill, not just on paper. The cost is zero
to one xrun around the crash, which is a click, not a dropout.

**The control surface is dead for ~10.7 seconds**, and one crash now takes bench *and*
HUD together — that is the regression Phase 3M accepted, and this is the number the spec
required be attached to it.

### Almost all of that is configuration, not recovery

`RestartSec=10` in `config/mpe-looper-session.service` is a floor under every figure
above. Process startup to a bound OSC port is roughly **0.6 s**; the other 10 s is
systemd waiting.

**Recommended, not applied** — it changes live behaviour and is Mitch's call: drop
`RestartSec` to 1–2 s and bound restart storms with `StartLimitIntervalSec` /
`StartLimitBurst` instead. Ten seconds of dead pads mid-song is a long time to pay for
storm protection that a burst limit expresses better. The pre-merge blast radius was
smaller only because a bench crash left the HUD alive; it did not recover faster.

## A bug found while setting this up

`--bench-only` with no other argument was **broken on `dev`**. `run_session` coerced an
empty passthrough list to `None`, and argparse falls back to `sys.argv` when given
`None` — so the bench re-parsed the session's own flags and exited 2 with
`unrecognized arguments: --bench-only`.

It only looked like it worked because every previous invocation passed something else
through (`--measure-latency 24`), which made the list non-empty. Fixed by passing the
list as-is, with a test that runs the flag with an otherwise empty argv.
