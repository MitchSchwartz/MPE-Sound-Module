# SooperLooper evaluation — 2026-08-14

Plan: OM-Repo `internal/projects/mpe-synth-launch/research/looper-vetting.md` §7.
Decisions this feeds: `Documents/DECISIONS.md` 2026-08-14 entries.

**Verdict:** Session A **continue** · Session B **partial** (2026-08-14, updated 18:14 America/Toronto). B2 pass (free-form, ear). **B9 regressed** after grid-sync integration — loop 0/1 UX broken; stop/reset not validated. B8 fail blocks adoption. Full log: this file.

**Branch / commit (grid work):** `docs/sooperlooper-eval` @ `d08663d` (Pi redeployed 2026-08-14 ~18:07 America/Toronto).

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
| Governor | **`performance`** (set via `MPE_CPU_GOVERNOR=performance` in `/etc/mpe/mpe.env`, Session B start) |
| Interface | Sound Blaster Play! 3 (card 1) |
| Throttled at start / end | `0x0` / `0x0` |

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
| B7 | 10-min soak, 16 loops | **partial** | 90 s proxy + 20 note attempts; **`jack_cpu_load` ~15%** with 16-loop engine up (timeout 3 s sample). Full 10-min + xrun count **not completed** (`jack_cpu_load` blocks without timeout) |
| B8 | Persistence | **fail (ear + agent)** | Mitch: loop plays from RAM (B2 pass) but **`save_loop` OSC does nothing** — no file after repeated tries with loop audibly playing. Agent: `save_session` also writes nothing; strace shows **no `openat` for `.wav`** on save OSC (nonrt file path never runs). Not “one command away” — **disk persistence broken/stuck on this headless Pi session** |
| B9 | Footswitch | **partial — regressed (grid, 2026-08-14 evening)** | **Free-form pass (ear, ~15:00)** — tap cycle + hold clear on pad 0. **Grid-sync integration (evening) not passing:** loop 0 pad goes green but may not keep looping; **soft pop at loop boundary** (new, not present pre-grid); **loop 2 does not play after record**; **Shift+Stop All stop / 3 s reset not working** in practice despite code on branch. See §Grid-sync integration |
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

### B7 — load (partial)

| Metric | Value |
|---|---|
| `jack_cpu_load` (16-loop instance, ~15 s sample) | **~15%** |
| VmRSS (16 loops) | **150736 kB** |
| xruns / 10 min | **not captured** |

**Verdict B:** **partial** — **B1/B2 pass (free-form ear)** · B6 and B5/B13 look fine · **B9 partial** (free-form ok; grid + transport **fail Mitch ear gate 2026-08-14 evening**) · **B8 fail** blocks adoption verdict (RAM loop works, disk save does not). CPU headroom at 16 loops (~15% DSP) is encouraging but B7 soak not validated.

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

**Status note (2026-08-14 ~15:00):** B9 **pass** applied to **free-form** bench only (`MPE_SL_SYNC_MODE=free` / pre-grid defaults). Do not treat as grid-sync validation.

### Grid-sync integration (2026-08-14 evening — Mitch ear gate)

**Goal:** loop 0 = free-form master length; loops 1–15 quantize to bar; beat **1/4** in touch HUD; Shift+Stop All (release) = stop all; Shift+Stop All (3 s hold) = full reset.

**Deployed:** `mpe looper sl-restart` + HUD monitor + APC bench on `d08663d` (multiple redeploy attempts; services running via absolute python paths when wrapper scripts failed from wrong cwd).

| Symptom | Mitch report (2026-08-14 ~18:00) | Agent read |
|---|---|---|
| Loop 0 “green but not looping” | Pad green after record stop; **no sustained loop playback** | HUD file showed `loop_len: 0`, `state: 0` while bench LED green earlier in session — bench/SL desync |
| **Soft pop at loop wrap** | **New** — audible click/pop at end of loop; **not present before grid work** | Suspect: post-record `trigger` + deferred `_refresh_grid_sync` / grid re-apply smothering boundary; **unverified** — needs A/B vs pre-`792b490` |
| Loop 2 (slave) after record | **Does not play** after saving/recording | Original bar-quantize bug **still open**; slave end-record → WaitStop path not closed |
| Beat counter (1/4 HUD) | **Not visible** during failing sessions | HUD monitor path was wrong early (`start-sooperlooper-hud-monitor.sh`); fixed on branch; counter only valid when loop 0 actually playing with `loop_len > 0` |
| **Shift+Stop All stop** | **Not implemented / not working** | Code exists (`apc_transport.py`, `stop_all_loops`) — **Mitch cannot use it**; transport MIDI may not reach bench or combo detection fails on hardware |
| **Shift+Stop All 3 s reset** | **Not implemented / not working** | Code exists (`reset_all_loops`) — same gap as stop |

**Engineering churn this session (for posterity — do not re-layer without fixing above):**

- Internal master clock when loop 0 cleared (`8d7a426`) — **unwired** in stabilization pass
- OSC state listener on slaves (`sl_bench_listener`) — loop 0 made bench-authoritative again in `d08663d`
- Inline vs deferred grid-sync on loop 0 land — moved to ~350 ms defer + explicit `trigger`

**Open before grid can ship:**

1. Loop 0 must **audibly loop** with no wrap pop (regression vs free-form B2).
2. Loop 2+ must complete record → play on bar boundary (quantize wait).
3. Shift+Stop All **stop** and **reset** must work on APC mini mk2 (Mitch-verified).
4. Beat HUD must track live loop 0 (not bench LED alone).

**Next step (human):** do **not** add mode layers — fix the four items above against free-form B2 baseline, one variable at a time.

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

**Smoke without manual recording:** `mpe looper sl-clips` + `mpe looper sl-smoke` (or `scripts/sooperlooper/*.sh` on the Pi). Builds 16 distinct sine WAVs, starts `-l 16`, `load_loop` each clip, triggers all loops, samples VmRSS/`jack_cpu_load`. Uses **`load_loop`** (works on eval Pi) — not `save_loop` (B8 fail).

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
- **B9 partial (2026-08-14)** — **free-form pass (~15:00)**; **grid-sync integration fail (evening)** — loop 0 playback/HUD/transport not Mitch-validated; wrap pop regression; slave loop 2 still broken.
- **B10 pass (verbal, 2026-08-14)** — ship both modes eventually; **grid-synced is Mitch's default** for v1 — **direction unchanged; implementation not ready**.
- **B8 fail** — in-memory loop works; **`/sl/0/save_loop` and `/save_session` write no files** on Pi (2026-08-14). Likely nonrt OSC/event path not executing file I/O (strace: no wav `openat`). Needs restart/debug or blocks N5 “free persistence” claim.
- Pi may still have SooperLooper from automation — kill when done: `pkill sooperlooper`.
- Automation script + log on Pi: `/tmp/session-b.sh`, `/tmp/session-b-results.log`.
