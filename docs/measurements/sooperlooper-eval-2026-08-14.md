# SooperLooper evaluation — 2026-08-14

Plan: OM-Repo `internal/projects/mpe-synth-launch/research/looper-vetting.md` §7.
Decisions this feeds: `Documents/DECISIONS.md` 2026-08-14 entries.

**Verdict:** Session A **continue** · Session B **PASS** (updated 2026-08-15 23:20 BST). B2/B9 pass (ear + APC bench). **B8 corrected to PASS** and **B7 now PASSES** on a full 600 s 16-loop soak — 0 xruns, 22.2% DSP load. **Nothing outstanding could disqualify SooperLooper.** Grid mode shipped; see `Documents/specs/looper-transport-clock-spec.md` §K–§M. Full log: this file.

## Rollback baseline (captured before A1)

Gate C reference Pi was green 2026-08-13. Session A adds only build tooling and
`~/src/sooperlooper-1.7.9` — no systemd, no `/etc/mpe` changes, no repo edits on Pi.

**Packages present before A1:**

| Package | Version |
|---|---|
| build-essential | 12.12 |
| pkg-config | 1.8.1-4 |

**Rollback after verdict (if not adopted):**

```bash
sudo apt remove --purge \
  build-essential pkg-config autoconf automake libtool libtool-bin autopoint \
  libjack-jackd2-dev liblo-dev liblo-tools libsigc++-2.0-dev \
  libxml2-dev libsndfile1-dev libsamplerate0-dev librubberband-dev \
  libfftw3-dev libncurses-dev libasound2-dev
sudo apt autoremove
rm -rf ~/src/sooperlooper-1.7.9
```

*(Do not remove `build-essential` if other Pi work needs it — adjust list to match what A1 actually installed.)*

## Conditions

| Field | Value |
|---|---|
| Pi model / RAM | Raspberry Pi 4 Model B Rev 1.5 / 7.6 GiB |
| OS | Debian 13 (trixie) arm64 |
| MPE-Module commit | `d01d9c3` (Pi clone) |
| mpe-cli version | laptop `mpe ping` / `mpe sysinfo` 2026-08-14 |
| SooperLooper version | v1.7.9 (`92515b2` — git clone `--branch v1.7.9`) |
| Built with rubberband? | **yes** (`HAVE_RUBBERBAND 1`) |
| `configure` flags | `--without-gui` |
| Buffer / periods / rate | 256 / 3 / 48 kHz 24-bit |
| Governor | ⚠️ **UNVERIFIED — recorded as `performance`, cannot be confirmed** (see note below) |
| Interface | Sound Blaster Play! 3 (card 1) |
| Throttled at start / end | `0x0` / `0x0` |

> **⚠️ Governor caveat, added 2026-08-17.** This table originally asserted
> `performance`, set via `MPE_CPU_GOVERNOR=performance` in `/etc/mpe/mpe.env` at
> Session B start. Writing that variable does not change the governor: it is read
> by `mpe-cpu-governor.service`, which is `Type=oneshot` and runs at boot. That
> unit last ran **2026-08-13 22:37:53**, and `uptime -s` says the appliance has not
> rebooted since **2026-08-13 22:40** — both *before* the variable was written. So
> unless `set-cpu-governor.sh` was also run by hand on the day (possible; no trace
> survives), the governor during these measurements was the distro default,
> `ondemand`.
>
> This is not hypothetical drift. On 2026-08-17 the appliance was found on
> `ondemand` with SooperLooper reporting a steady **15 xruns per 30 s**. Pinning
> `performance` took that to **0 per 30 s**, same session, nothing else changed.
> `docs/LATENCY-SPIKE.md:61` already flagged `ondemand` as a classic dropout cause.
>
> Treat every timing number below as taken on an unknown governor. The verdicts
> about *what SooperLooper can do* still stand; the latency and xrun figures need
> re-taking with `mpe rt check` passing first — which is exactly the precondition
> rule this document's own §"Precondition rule" adds, and which would have caught
> it: `mpe rt check` exits 1 on a wrong governor.

## Session A — does it exist on this hardware? (≈ 1.5 h)

| # | Test | Result | Notes |
|---|---|---|---|
| A1 | Build deps resolve from trixie | **pass** | Mitch apt install + agent added `libtool-bin`, `autopoint` (autogen requires `/usr/bin/libtool`) |
| A2 | Builds `--without-gui` | **pass** | `./autogen.sh && ./configure --without-gui && make -j4`. **Required liblo 0.32 patch** — see Failures |
| A3 | Engine starts, ports appear in `jack_lsp` | **pass** | `./src/sooperlooper -q -D yes -l 1 -c 2 -t 40 -p 9951 -j mpe-looper`. Ports: `mpe-looper:loop0_in_{1,2}`, `loop0_out_{1,2}`, `common_in/out_{1,2}` |
| A4 | `/ping` replies over OSC | **partial** | Inbound OSC confirmed: `/quit` terminates engine. UDP `:9951` listening. `/ping` reply not captured — `oscdump` fails on Pi (`IP_ADD_MEMBERSHIP` / liblo). Re-test in Session B from laptop or via `pedal-to-osc.py` |
| A5 | Idle `VmRSS` at `-t 40`, 1 loop | **pass** | **116940 kB** (~114 MiB) idle, 1 loop preallocated |

**Verdict A:** **continue** — builds and runs on trixie arm64 with a known liblo ABI patch. Maintenance cost is real but bounded (~1 h to first binary).

## Session B — is it the right shape? (≈ 3–4 h)

**Status:** automated pass completed 2026-08-14 (`/tmp/session-b.sh` on Pi). Ear-gated items attempted via OSC/MIDI/ffmpeg; **Surge produced no usable level above noise floor** via `/mnote` + `/patch/load` (same class of issue as `docs/USB-AUDIO-PASSTHROUGH-SPIKE.md`).

Parallel topology wired and `dry=0` set via:

```bash
jack_connect "Surge XT:out_1" "mpe-looper:loop0_in_1"
jack_connect "Surge XT:out_2" "mpe-looper:loop0_in_2"
jack_connect "mpe-looper:loop0_out_1" "system:playback_1"
jack_connect "mpe-looper:loop0_out_2" "system:playback_2"
oscsend osc.udp://127.0.0.1:9951 /sl/0/set sf dry 0.0
```

| # | Test | Result | Notes |
|---|---|---|---|
| B1 | **`dry=0` removes passthrough** | **pass (ear)** | Mitch @ keys 2026-08-14: parallel graph + `dry=0` sounds **pretty similar** to direct Surge — no obvious doubling/phasing. Same baseline crackle class as Surge-only (512×3 JACK). Automation ffmpeg path inconclusive (noise floor) |
| B2 | Free-form record | **pass (ear)** | Mitch 2026-08-14: record → playback → play over loop audibly works (LUMI → Surge → looper). **Reconfirmed** same session with APC pad footswitch after JACK rewire + bench script fixes |
| B3 | Timing alignment | **not run** | Blocked on audible loop |
| B4 | Overdub + undo | **OSC only** | overdub×3 · undo×2 · redo — no audibility check |
| B5 | 16 loops record/play | **pass (OSC)** | `-l 16`; all 16 loops record/play driven; **VmRSS 151564 kB** after |
| B5b | Recorded-but-idle memory | **pass** | See Session A table + B5 measured 151 MB with 16 active loops |
| B6 | Fail open | **pass** | `pkill -KILL sooperlooper` ×2 — Surge → playback intact each time |
| B7 | 10-min soak, 16 loops | **PASS (2026-08-15)** | Full 600 s soak, 16 loops playing: **0 xruns** (jackd journal, unit verified talking), **22.2% DSP load**, 256@48k. The ALSA-node `0/0` reading it used to print was a fallback, not a measurement — see §B7 |
| B8 | Persistence | **PASS (corrected 2026-08-15)** | Wrote a 3 MB WAV first try against a *fresh* engine. The 2026-08-14 "disk persistence broken" reading (no file, no `openat` under strace) was a **wedged engine**, not disk I/O — see §B8 correction. Detect with `mpe looper sl-health` before retesting |
| B9 | Footswitch | **pass (ear + agent)** | APC mini pad (0,0) via `scripts/sooperlooper-apc-bench.py` on Pi. Tap cycle: record → play → pause (stop) → trigger (restart). Hold ~1 s → `undo_all`. See §B9 footswitch bench below |
| B10 | Free-form vs grid A/B | **pass (verbal)** | 2026-08-14: **Both modes should ship eventually.** Mitch's personal default: **grid-synced** (bar/tempo-locked clips under the pad-per-loop UI). Free-form validated in B2 bench is fine for eval, not the v1 preference. See §B10 |
| B11 | Per-pad clear | **OSC only** | `undo_all` sent |
| B12 | Multiply | **OSC only** | `multiply` ×2 sent |
| B13 | `pitch_shift` / rubberband | **pass (no crash)** | OSC 2.0 / 0.0 — engine survived |
| B14 | Headroom measurement | **not run** | Needs audible 16-loop + live sum |

### B5b — memory (idle prealloc)

| Loops | `-t` (s) | Predicted VmRSS | Measured |
|---|---|---|---|
| 16 | 40 | ~246 MB | **150380 kB (~147 MiB)** idle; **151564 kB with 16 recorded/playing** |
| 64 | 40 | ~983 MB | **257460 kB (~251 MiB)** |
| 64 | 20 | ~492 MB | **257452 kB (~251 MiB)** |

### B7 — load (**PASS — full 10-min soak run 2026-08-15 23:05–23:16 BST**)

| Metric | Value |
|---|---|
| Soak duration | **600 s**, 16 loops loaded and playing + Surge path active |
| JACK config | 256 frames @ 48000 Hz (5.3 ms period, 3 periods), playback fan-in 4 |
| **xruns over 10 min** | **0** — see method below |
| `jack_cpu_load` (16 loops playing) | **22.2%** |
| VmRSS (16 loops) | **150736 kB** (2026-08-14) |
| Peak dBFS | **not measured** — `jack_capture` not installed on the Pi |

**How the zero was verified, because two things nearly faked it.**

1. The script's ALSA-node reading printed `0/0`. That was **not a measurement**:
   this Pi's playback node is JACK-held and carries no `xrun`, `underflow` or
   `overrun` line at all, so the function's "default to 0" fallback produced a
   string indistinguishable from a genuine zero. Fixed — it returns `n/a` now.
2. The first launch of the soak found **zero fixture clips** on the Pi and would
   have soaked 16 *empty* loops to a clean result. Caught before it ran.

The real source is jackd's stderr, which prints `xrun of at least X msecs`.
`mpe-jackd` has `StandardError=journal`, and jackd's own startup lines **are**
in the journal — so the unit demonstrably reaches it. The journal has **no
entries whatsoever** across 23:04–23:17. Silence from a unit that is known to be
talking is a real zero.

**Caveat.** The soak used 16 fixture sine loops via `load_loop`, not 16
performed takes, and Surge was in the graph but not being played. It measures
the steady-state mixing load, which is what B7 asks; it does not measure
record-path load under performance.

**Still open from this run:** peak dBFS (B14 clipping) needs `jack_capture`
installed. Fan-in is 4, well under the >8 heuristic.

**Verdict B (updated 2026-08-15):** **pass, adoption not blocked, and B7 no
longer open.** B1/B2/B9/B10 pass · B5/B6/B13 fine · **B8 PASSES** (see §B8
correction) · **B7 PASSES** — 0 xruns and 22% DSP load over a full 10-minute
16-loop soak. Nothing outstanding could now disqualify SooperLooper; the
remaining gap is the B14 clipping measurement, which is a gain question rather
than an engine question.

### B9 — APC footswitch bench (2026-08-14 ~15:00 America/Toronto)

**Script:** `scripts/sooperlooper-apc-bench.py` + `scripts/start-sooperlooper-apc-bench.sh` (Pi: run from `~/MPE-Module/scripts/` after pull; log `/tmp/sooperlooper-apc-bench.log`).

**Parallel JACK graph** (required — lost after service restarts; re-apply if looper ports are disconnected):

```bash
jack_connect "Surge XT:out_1" "mpe-looper:loop0_in_1"
jack_connect "Surge XT:out_2" "mpe-looper:loop0_in_2"
jack_connect "Surge XT:out_1" "system:playback_1"
jack_connect "Surge XT:out_2" "system:playback_2"
jack_connect "mpe-looper:common_out_1" "system:playback_1"
jack_connect "mpe-looper:common_out_2" "system:playback_2"
# Per-loop loopN_out -> playback is WRONG for 16 loops (sums 16× + Surge = clip)
bash scripts/sooperlooper/wire-jack-graph.sh   # or: mpe looper sl-rewire
```

**Tap cycle (short press):** record (red) → end+play (green) → pause/stop (yellow) → trigger/restart play (green). **Hold ~1 s on a clip pad:** `undo_all` (off). **Shift + Stop All Clips, hold 3 s:** pause + clear all loops (track reset).

**Fixes applied this session:**

| Issue | Cause | Fix |
|---|---|---|
| Pad “does nothing” | Wrong MIDI note (64 = below-grid button; grid bottom-left = **0**) | `pad_note = row * 8 + col` |
| Bridge crash on press | `get_message()` returns `(msg, delta)` tuple | Unpack tuple |
| Tap ignored / only clear | APC note-off is **`0x80`**, not `0x90` vel 0 | `pad_event()` handles both |
| No loop audio | JACK graph disconnected (Surge → playback only) | Reconnect parallel topology |
| Stop → next tap re-enters record | Bench state machine sent `record` from `stopped` | **Not lost memory** — loop stays in RAM (SooperLooper state **14 = Paused**). Mapping bug: stopped should **`trigger`** (restart play) + **yellow LED**, not `record` |

**Mitch observation (stop → restart):** After stopping a clip, next press felt like “back to record” with no yellow “stopped but saved” indication. **Verdict:** loop content was still in RAM (audible play worked); gap was **bench LED + OSC mapping**, not SooperLooper forgetting the loop. Yellow + `trigger` added to script after this report.

### B10 — free-form vs grid-synced (2026-08-14 verbal)

**Not a UI vote** — pad-per-loop clip grid is already decided (`DECISIONS.md` 2026-08-14).

**Question:** Under that UI, should the engine default to free-form loops (performance sets length) or grid-synced loops (BPM + bar length)?

| Mode | SooperLooper (eval) | Product fit |
|---|---|---|
| **Free-form** | `sync_source=0`, `quantize=0` — used for B2/B9 bench | Keep as **alternate mode** |
| **Grid-synced** | `tempo` + `quantize=cycle` + fixed cycle length | **Mitch's personal default / v1 target** |

**Mitch (2026-08-14):** *Both modes should be available in the future; I personally want grid.*

**Eval implication:** B10 **pass** for adopt direction — SooperLooper supports both; grid-synced is the preferred v1 engine config. No hardware A/B required. Aligns with existing `MPE_LOOPER_BPM` / bar-length spec in the clip-grid design.

### APC 16-loop clip grid + automated smoke (2026-08-14)

**UI layout (Mitch):** clip pads on **row 0** (loops 0–7) and **row 3** (loops 8–15). Rows 1, 2, 4–7 reserved for per-loop controllers later. Canon: `scripts/sooperlooper/apc_grid.py`.

**Smoke without manual recording:** `mpe looper sl-clips` + `mpe looper sl-smoke` (or `scripts/sooperlooper/*.sh` on the Pi). Builds 16 distinct sine WAVs, starts `-l 16`, `load_loop` each clip, triggers all loops, samples VmRSS/`jack_cpu_load`. Uses **`load_loop`**; `save_loop` also works (B8 corrected to pass 2026-08-15).

### B14 / crackle — 16 loops + Surge playing (2026-08-14 agent)

**Report:** Mitch hears crackle when playing keys with all 16 loops running.

**Mechanical run:** `mpe looper sl-diagnose` — 45 s soak, 16 sine loops triggered, parallel graph (Surge + 16× loop outs → `system:playback`).

| Signal | Result | Interpretation |
|---|---|---|
| Playback fan-in | **17 sources** per channel (Surge + loops 0–15) | No bus/limiter — full-scale layers sum at DAC |
| `jack_cpu_load` | **~28%** start/end | CPU headroom OK at 16 loops |
| `mpe-jackd` journal `xrun` mentions | **0** over 45 s | Not buffer underrun on this soak |
| ALSA pcm `xrun:` | *(absent — JACK holds device)* | Use journal + CPU instead |
| Peak capture | `jack_capture` not installed on Pi | Install `jack-capture` for dBFS proof |

**Working hypothesis (updated):** smoke script wired **each `loopN_out` + Surge → playback** (17× sum). **`common_out` was not connected.** Fix: `mpe looper sl-rewire` — listen on `common_out`, `dry=0` on all loops, disconnect per-loop outs.

**If crackle persists after rewire:** likely **baseline Surge @ 256×3** (B1 noted same class Surge-only) — try `MPE_SURGE_BUFFER_SIZE=512` in `/etc/mpe/mpe.env` and restart Surge.

**Next checks (human):**
1. Shift+Stop reset → record **one** loop → add Surge — crackle gone?
2. Repeat with 16 loops at fixture levels — crackle returns? (isolates count vs sum)
3. Optional: `sudo apt install jack-capture` on Pi, re-run `mpe looper sl-diagnose` for peak dBFS

**Product note (not eval scope):** Phase 2 spec’s headroom law / mix bus — do not ship 17-way direct fan-in to `system:playback`.

## Failures, surprises, and anything improvised

- GitHub tag is **`v1.7.9`**, not `1.7.9`. No GitHub Release tarball; cloned from
  `https://github.com/essej/sooperlooper.git`.
- `configure` is not in the clone — run `./autogen.sh` first (`autoconf automake
  libtool-bin autopoint`).
- **liblo 0.32 ABI break** (Debian #1071364 / Gentoo #925275): `lo_method_handler`
  5th arg changed from `void*` to `lo_message`. Stock v1.7.9 **does not build**
  on trixie without patch. Applied on Pi (eval only, not committed):

  ```bash
  find ~/src/sooperlooper-1.7.9/src \( -name '*.cpp' -o -name '*.hpp' \) \
    -exec grep -l 'void \*data, void \*user_data' {} \; \
    -exec sed -i 's/void \*data, void \*user_data/lo_message data, void *user_data/g' {} \;
  ```

  Ongoing cost if adopted: carry this patch (or upstream equivalent) in our build.
- A1 command in vetting doc should add `libtool-bin autopoint liblo-tools`.
- `oscdump` on Pi cannot bind a receive port (liblo multicast) — use laptop
  listener or `socat UDP-RECV` for Session B OSC reply tests.

## What this changes

- Confirms **Option A (adopt SooperLooper) is not killed at A2** — but adoption
  implies maintaining a **liblo 0.32 patch** on trixie (DECISIONS open question
  #4 in `looper-vetting.md` §8).
- **rubberband builds cleanly** on trixie — B13 OSC accepts `pitch_shift` without crash.
- Memory at 16 loops (~151 MiB active) and 64 idle (~251 MiB) is fine on 8 GiB Pi.
- **B1 pass (ear, 2026-08-14)** — parallel fail-open wiring validated; `dry=0` does not audibly double the live path.
- **B9 pass (2026-08-14)** — APC pad footswitch via eval bench script; B2 reconfirmed same session.
- **B10 pass (verbal, 2026-08-14)** — ship both modes eventually; **grid-synced is Mitch's default** for v1.
- **B8 fail** — in-memory loop works; **`/sl/0/save_loop` and `/save_session` write no files** on Pi (2026-08-14). Likely nonrt OSC/event path not executing file I/O (strace: no wav `openat`). Needs restart/debug or blocks N5 “free persistence” claim.
- Pi may still have SooperLooper from automation — kill when done: `pkill sooperlooper`.
- Automation script + log on Pi: `/tmp/session-b.sh`, `/tmp/session-b-results.log`.


## §B8 correction — persistence is NOT broken (2026-08-15)

**The 2026-08-14 B8 "fail" verdict was wrong and is withdrawn.**

Recorded as: *"`save_loop` OSC does nothing — no file after repeated tries...
strace shows no `openat` for `.wav`... disk persistence broken/stuck"*, and it
was listed as **blocking the adoption verdict**.

**Retested against a freshly started engine: `save_loop` wrote a 3 MB WAV on the
first attempt.**

**Actual cause — a wedged engine, since named and now detectable.** SooperLooper
can reach a state where OSC `/get` answers correctly while `/set`, `/hit` and
`save_loop` are silently ignored: `get` reads state directly, the others go
through `push_nonrt_event()`. When that non-realtime thread stops draining, every
read-only check reports healthy and every command does nothing. The missing
`openat` was the same wedge, not disk I/O.

**Cost of not detecting it:** an evening debugging the Python control layer
against a dead engine, plus this false verdict sitting in the eval log as an
adoption blocker.

**Now detectable:** `mpe looper sl-health` round-trips a `set` and fails if it
does not stick. `mpe looper sl-watchdog` alarms on it (and refuses to
auto-restart, because a restart destroys every recorded loop).

**Root cause found 2026-08-15 — it was an orphaned JACK client.** SooperLooper
survived a `jackd` restart as a process but lost its JACK client and never
re-registered. `push_nonrt_event()` is drained from the JACK *process callback*,
so with no callback the queue never drains and every command vanishes — while
`/get` reads state directly and keeps answering. Not an engine bug, and not a
disk bug. The `-j mpe-looper` client simply was not on the graph.

This does not change the B8 verdict, but it changes the precondition: **check
that `mpe-looper:*` ports exist before trusting any measurement**, not just that
the engine answers. `sl-watchdog` now checks JACK visibility first. See
`Documents/DECISIONS.md` 2026-08-15 and spec §M.

**Precondition rule** — added to `TEMPLATE-sooperlooper-eval.md`: no measurement
is recorded without `mpe rt check` and `mpe looper sl-health` passing first. A
number taken against a wedged engine is not a weaker measurement, it is a wrong
one, and it looks identical to a right one on the page. This entry is the proof.

## §Technical footprint — measured 2026-08-15

Answering "is SooperLooper efficient, and how does it sit in the audio chain?"

| Measure | Value |
|---|---|
| Processes | **1** (16 loops are internal instances, not 16 clients) |
| Threads | 7, of which **1 is SCHED_FIFO 65** (the audio callback) |
| RSS | 172 MB at `-l 16 -t 40` (40 s preallocated per loop) |
| JACK clients in graph | 3 — `Surge XT`, `mpe-looper`, `system` |
| JACK ports | 68 on `mpe-looper` (16 loops x 2 in + 2 out, plus `common_in/out`) |
| DSP load | **8.8%** for the whole graph |
| Uptime at measurement | 16 h continuous, healthy |
| Mix bus | internal — all loops sum to `common_out`, one stereo pair to playback |

**Read:** SL is already the consolidated single-engine architecture, not a
process per loop. Building our own would land on the same shape and would not
simplify the audio chain — it would only move clip-launcher logic from Python
into C++.

**Real costs, in order:**

1. **64 of the 68 ports are per-loop ins/outs we never use** — we listen on
   `common_out` only. Graph clutter and traversal overhead. Reducible with a
   smaller `-l` if 16 clips is more than the product needs.
2. **Memory is preallocation, not usage.** 172 MB comes from `-t 40`; a shorter
   max loop time trades headroom for RAM directly.
3. **The process sprawl is ours, not SL'''s** — three Python control-plane
   processes (bench, HUD, watchdog) plus two JSON state files and three UDP
   ports. None touch the audio path, and all of it would survive an engine swap.
