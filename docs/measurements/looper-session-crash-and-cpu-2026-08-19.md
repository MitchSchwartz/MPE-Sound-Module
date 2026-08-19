# Phase 3M criteria 46 and 47 — crash blast radius and CPU — 2026-08-19

> **Provenance — read before quoting these numbers.** Measured on `raspberrypi2` while
> its checkout was on `yolo/looper-poll-tail-fix` (`c006fa8`), **not** on `dev`. The Pi's
> reflog shows it left `dev` at 2026-08-19 19:14:35; these runs started after that. That
> branch adds `poll_tail_capture()` per footswitch per idle tick — to the same poll loop
> both criteria measure.
>
> **Comparisons hold**: every condition below ran identical code, so the deltas are
> sound. **Absolutes are upper bounds** for `dev`, which does not carry that extra
> per-tick work. Re-take them against the merged branch before treating any single
> figure as the appliance's cost.

`raspberrypi2`, `1024 x 3`, plus the `--bench-only` fix below. 60 s idle windows,
`/proc/<pid>/stat` fields 14–17 so forked children are counted
([`DECISIONS.md`](../../Documents/DECISIONS.md) 2026-08-18).

## Criterion 47 — CPU no worse than the two processes it replaces

The pre-merge units are gone, but their entry points survive, so "before" can be
reconstructed: `--bench-only` and `--hud-only` as two processes on two ports
(9953 / 9952, the pre-merge topology).

| condition | CPU, % of one core |
|---|---:|
| **before** — bench process | 30.000 |
| **before** — HUD process | 8.767 |
| **before, total** | **38.767** |
| **after** — merged session | **32.983** |
| | **−5.784 points** |

**Criterion 47 passes.** The merge is 5.8 points cheaper, which is roughly the second
interpreter, its OSC server thread and its second listen port. Consistent with the task 6
finding that the merged session adds +0.15 points of DSP over SooperLooper alone — i.e.
nothing measurable at the audio graph.

**Worth stating separately: 33% of a core is a lot.** It is the largest single Python CPU
consumer on the appliance, and it is the ~2 ms bench poll loop, not the HUD. The merge
improved it and the criterion is satisfied, but if CPU is ever the binding constraint
again this is where it is, and the fix is the poll loop rather than anything the merge
introduced.

## Criterion 46 — crash blast radius

`kill -9` the merged process mid-session, `Restart=always` recovers.

| run | OSC 9953 rebound | APC re-bound | audio |
|---|---:|---:|---|
| 1 | 10.72 s | 10.87 s | survived, `wired=1`, +2 xruns |
| 2 | 10.58 s | 10.73 s | survived, `wired=1`, +1 xrun |

**Audio never stopped.** `mpe-jackd` and `surge-xt-cli` are separate units and the graph
stayed wired throughout — D4 holds under a real kill, not just on paper. The cost is one
or two xruns around the crash, which is a click, not a dropout.

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
