# SooperLooper evaluation — YYYY-MM-DD

Copy to `sooperlooper-eval-YYYY-MM-DD.md` and fill in. Plan: OM-Repo
`internal/projects/mpe-synth-launch/research/looper-vetting.md` §7.
Decisions this feeds: `Documents/DECISIONS.md` 2026-08-14 entries.

**Verdict:** *(adopt / build our own / inconclusive — fill in last)*

## Preconditions — run BEFORE any measurement

A number taken on an appliance that was not actually realtime, or whose grid
clock was not running, is not a weaker measurement. It is a **wrong** one, and
it looks identical to a right one on the page. Record the gate output, not a
claim that you checked.

| # | Gate | Command | Required | Recorded output |
|---|---|---|---|---|
| P1 | **Realtime is live** — every audio client has a SCHED_FIFO *thread*, governor is `performance` | `mpe rt check` | exit **0** | |
| P2 | Grid clock is running (grid-mode runs only) | `mpe looper sl-bench status` | startup line names the clock: `clock=internal` or a rolling transport | |
| P3 | JACK graph wired, `dry=0` | `mpe looper sl-rewire` | no `wire-jack:` errors | |

**P1 note.** `mpe rt check` reads the **audio thread**, not the process. A JACK
client main thread is `SCHED_OTHER priority 0` on a perfectly healthy appliance —
process-level policy is the answer most likely to mislead, which is why the gate
exists as an exit code rather than a line a human interprets.

**If any gate fails, stop.** Fix it, then start the session over. Do not record
results next to a failed precondition and a note explaining it away.

## Conditions

| Field | Value |
|---|---|
| Pi model / RAM | |
| OS | Debian 13 trixie arm64 |
| MPE-Module commit | |
| mpe-cli version | |
| SooperLooper version | *(tarball or git tag)* |
| Built with rubberband? | yes / no — **if no, B13 matters** |
| `configure` flags | `--without-gui …` |
| Buffer / periods / rate | 256 / 3 / 48 kHz 24-bit |
| Governor | *(P1 also checks this)* |
| RT audio threads | *(from `mpe rt check` — tid + SCHED_FIFO prio, not process policy)* |
| Grid clock | internal / jack transport / none (free-form) |
| Interface | |
| Throttled at start / end | `vcgencmd get_throttled` |

## Session A — does it exist on this hardware? (≈ 1.5 h)

| # | Test | Result | Notes |
|---|---|---|---|
| A1 | Build deps resolve from trixie | | |
| A2 | Builds `--without-gui` | | **Kill point.** If it fails on rubberband 3.3, retry once with rubberband disabled, then stop |
| A3 | Engine starts, ports appear in `jack_lsp` | | |
| A4 | `/ping` replies over OSC | | needs `liblo-tools` |
| A5 | Idle `VmRSS` at `-t 40`, 1 loop | | |

**Verdict A:** *(continue / build our own)*

## Session B — is it the right shape? (≈ 3–4 h)

| # | Test | Result | Notes |
|---|---|---|---|
| B1 | **`dry=0` removes passthrough** | | **Load-bearing.** If passthrough survives, the parallel fail-open plan is dead |
| B2 | Free-form record | | |
| B3 | Timing alignment — record latency values used | | |
| B4 | Overdub + undo | | not central to the target UX, but confirms the engine |
| B5 | **16 loops** record and play | | 16 is the design target |
| B5b | Recorded-but-idle memory | see table below | |
| B6 | **Fail open** — `pkill -KILL` while playing | | Live audio must never stop |
| B7 | 10-min soak, **16 loops** + heavy patch + 8-note chord | | Pass = no worse than Phase 1 at same load |
| B8 | Persistence — save/load loop and session | | |
| B9 | Footswitch via `pedal-to-osc.py` | | |
| B10 | **A/B: free-form vs grid-synced** | | Mitch's call on feel. Pad-per-loop is already decided |
| B11 | Per-pad clear (`undo_all` on one loop) | | Primary mistake-fix gesture |
| B12 | Multiply → 2× / 4× / 8× | | Needed for the held-pad multiplier UI |
| B13 | `pitch_shift` with/without rubberband | | Decides if rubberband is a hard build requirement |
| B14 | **Headroom measurement** | see table below | Not a gate — specs the limiter |

### B5b — memory (recorded but idle)

| Loops | `-t` (s) | Predicted `VmRSS` | Measured |
|---|---|---|---|
| 16 | 40 | ~246 MB | |
| 64 | 40 | ~983 MB | |
| 64 | 20 | ~492 MB | |

### B7 — load

| Metric | Phase 1 baseline (Surge only) | With 16 loops |
|---|---|---|
| `jack_cpu_load` | | |
| xruns over 10 min | | |
| `VmRSS` | | |

### B14 — headroom (specs the limiter, does not gate adoption)

| Condition | Peak at `system:playback` | Over 0 dBFS by |
|---|---|---|
| 16 loops, no per-loop `wet` law | | |
| 16 loops, `wet` law applied | | |
| 16 loops + live 8-note chord, `wet` law | | |

**If the `wet` law alone holds the sum under 0 dBFS, the limiter may be
smaller than assumed or unnecessary.**

## Failures, surprises, and anything improvised

*(Record deviations from the plan here. An undocumented workaround at the
bench is the next stale claim.)*

## What this changes

*(Which DECISIONS.md entries this confirms, contradicts, or supersedes.)*
