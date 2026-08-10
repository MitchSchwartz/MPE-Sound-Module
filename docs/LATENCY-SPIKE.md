# Surge latency — RT vs buffer floor experiment

*Last updated: 2026-08-10 17:12 (America/Toronto)*

**The experiment, in one line:** *Does enabling PREEMPT_RT let us run a smaller Surge buffer than 1024 without losing voices?*

Everything else in this doc is control-arm bookkeeping for that one question.

**Related:** [#44 Buffer size in settings](https://github.com/MitchSchwartz/MPE-Sound-Module/issues/44) · `FAQ.md` · `docs/PATCH_NORMALIZATION.md`

**Status:** Arm A not started. Arm B **blocked** — see §Open questions. Repo buffer defaults corrected to 1024 (below).

---

## Why this is the only experiment left

Buffer tuning on the stock kernel is **exhausted**:

| Buffer @ 48 kHz | Block latency | Result today |
|---|---|---|
| **1024** | ~21 ms | **Production floor** — voices hold under real MPE load |
| **768** | ~16 ms | **Loses too many voices** (governor/CPU pressure) |
| **512 and below** | ~11 ms | Historical xrun/crackle on heavy patches |

The binding constraint is **CPU headroom per callback**, not block math. RT changes **scheduling jitter**, which is the plausible mechanism for surviving a shorter callback. Nothing else in the current stack moves this — direct ALSA is already the thin path, and there's no JACK/PipeWire layer to remove.

**So:** either RT buys a smaller buffer, or **1024 is the permanent floor** and any Pi-side software looper is dead (looper buffers stack on top of ~21 ms).

---

## Independent variable

**RT kernel: on / off.** That is the *only* thing that changes between arms.

**Held constant** — changing any of these invalidates the comparison:

- Sample rate **48000**
- Patch set (same 3–5 CPU-heavy MPE patches, same order)
- Poly governor setting and `MPE_POLY_*` values
- Normalization on/off state
- Audio profile: **`standalone`** → Sound Blaster USB DAC → headphones
- Controller: Roli only
- Touch browser running or not — pick one, keep it

**Deliberately excluded from the rig** (each is a confounder):

- RC-5 / any looper pedal, and `midi-clock-in` (sync quantize alters note timing)
- `usb-host` / UAC2 gadget path (adds host buffers; documented as not a monitor path)
- Multi-FX pedal in the audio chain

---

## Dependent variable (what "success" means)

**Voice count under load** — not milliseconds, not xrun count alone.

A buffer "passes" only if, over a **10-minute** MPE jam:

1. Voices hold as well as **1024 does today** (no audible dropped/stolen notes beyond baseline), **and**
2. **Zero** xruns/underruns in `~/surge-cli.log`, **and**
3. It still passes with the **worst-case** patch, not just average ones.

Latency is then simply `buffer × 1000 / 48000` ms — arithmetic, not a measurement.

---

## Procedure

### Arm A — control (stock kernel, ~1 hour)

Purpose: put the thing we already believe on the record, so Arm B has something to compare to.

| Step | Action |
|---|---|
| A.1 | Confirm `MPE_SURGE_BUFFER_SIZE=1024` in `/etc/mpe/mpe.env` and in touch **Audio → Buffer** |
| A.2 | Build the rig above (no pedal, no gadget, Roli only) |
| A.3 | 10 min jam @ **1024** — note voice behavior, grep log for xrun |
| A.4 | Brief pass @ **768** — **document the failure mode concretely** (which patches, how many voices, how fast it degrades) |
| A.5 | Record `uname -a`, Pi model, patch list used |
| A.6 | Validation Log row: control arm result |

A.4 matters more than it looks: "loses voices" needs to be specific enough that Arm B can be judged against it rather than against memory.

### Arm A½ — scheduling priority, no kernel change (do this before Arm B)

`SCHED_FIFO` on the audio thread plus `rtprio`/`memlock` limits and a pinned CPU governor address the *same* mechanism as RT — scheduler jitter — at a fraction of the risk, with no kernel swap and no rollback exposure. If this alone buys 768, the RT arm may never be needed.

Scope to be set by the feasibility research (see §Open questions) before any config lands. Same 10-minute protocol and same pass criteria as Arm A.

### Arm B — RT kernel (1–2 days) — blocked

**Gates:** feasibility research answered · `tryboot` rehearsed (§Rollback) · Mitch approves the kernel install.

| Step | Action |
|---|---|
| B.0 | Confirm board revision, kernel, and OS release via `mpe sysinfo` — RT choice depends on all three |
| B.1 | Obtain an RT kernel that **keeps** `vc4`/DSI, dtoverlays, and `dwc2` UAC2 gadget working. Install **alongside** the stock kernel, never replacing it |
| B.2 | Select it via `tryboot.txt` only — not `config.txt` |
| B.3 | Confirm RT is actually active (`uname -a`) and Surge is actually running at elevated priority — a kernel that boots but doesn't schedule differently proves nothing |
| B.4 | Re-run the **identical** A.3 protocol @ **1024** — sanity check that RT didn't regress the known-good config |
| B.5 | Step down: **768**, then **512** — same 10 min protocol, same patches, same pass criteria |
| B.6 | Reliability soak at the best passing buffer: touch UI, USB hotplug, Wi-Fi scan running |
| B.7 | Only after a clean soak, promote the RT kernel into `config.txt`. Validation Log: adopted or rejected + lowest buffer that held voices |

B.4 is the step that's easy to skip and shouldn't be — if RT changes behavior at 1024, the step-down results mean something different.

---

## Outcomes and what each one decides

| Result | Production buffer | Pi software looper |
|---|---|---|
| RT holds voices @ **512** | Consider 512 (~11 ms) | **Reopen** — architecture spike worth doing |
| RT holds voices @ **768** | Consider 768 (~16 ms) | **Marginal** — only without a monitored record path |
| RT no better than stock | Stay **1024** (~21 ms) | **Closed** — hardware pedal stays the loop engine |
| RT hurts reliability | Stay **1024**, stock kernel | **Closed** |

Reliability outranks latency: a smaller buffer that survives 10 minutes but fails a set is a regression, not a win.

---

## Fixed alongside this plan: repo defaults said 768

Surfaced while building the plan, and arguably more urgent than the experiment: **every buffer default in the repo was 768** — the config that drops voices. `PATCH_NORMALIZATION.md` records that 1024 *was* the prior default and was lowered to 768 to fix 512-era choppiness, so 768 looks like a regression the gig Pi only escaped because `/etc/mpe/mpe.env` overrides it locally.

Nine locations, now aligned to 1024:

| Location | Why it mattered |
|---|---|
| `scripts/configure-pi-paths.sh:73` | **Writes `/etc/mpe/mpe.env` on a fresh Pi** — the actual path by which a rebuild lands on 768 |
| `scripts/start-surge-cli.sh:63` | Runtime fallback when the env var is unset |
| `patch_browser/surge_audio.py` | `DEFAULT_BUFFER`, now the single Python source of truth |
| `patch_browser/midi_sync.py` | Output-offset fallback — **was computing MIDI compensation for ~16 ms while Surge ran ~21 ms blocks** |
| `scripts/calibrate-patch-normalization.py` (×2) | Calibration ran at a different buffer than production |
| `scripts/set-surge-audio.sh:72` | Old-value fallback when deciding whether a restart is needed |
| `config/mpe.env.example`, `docs/PATCH_NORMALIZATION.md`, `docs/USB-AUDIO-PASSTHROUGH-PLAN.md` | Documented the wrong default |

The Python copies now import `DEFAULT_BUFFER` / `DEFAULT_SAMPLE_RATE` from `surge_audio` rather than repeating literals, so this class of drift can only recur in the shell scripts (flagged by comment in `surge_audio.py`).

**Note:** this changes nothing on the current gig Pi, whose env file already pins 1024. It changes fresh builds, `configure-pi-paths.sh` on a Pi with no existing env, and the MIDI offset math.

---

## Rollback without pulling the SD card

The live Pi's card is behind the display ribbon and impractical to remove, so there is no spare-card A/B and no image backup. That makes a **software rollback path mandatory** before the kernel is touched.

**Mechanism: `tryboot`.** The Pi bootloader supports a one-shot flag — `sudo reboot '0 tryboot'` makes the firmware load `tryboot.txt` instead of `config.txt` for exactly one boot. The flag is cleared *before* the firmware starts, so a crash or hang means the next boot returns to `config.txt` and the stock kernel ([Raspberry Pi bootflow docs](https://github.com/raspberrypi/documentation/blob/master/documentation/asciidoc/computers/raspberry-pi/bootflow-eeprom.adoc)).

Rules for this experiment:

1. **Rehearse first.** Run one `tryboot` cycle with a `tryboot.txt` that is just a copy of `config.txt`. Confirm it boots and reverts *before* an RT kernel is involved. Five minutes, zero stakes, and it proves the escape hatch on this specific board.
2. **Install the RT kernel alongside** the stock one under a distinct filename — never replace it. `config.txt` must always point at a known-good kernel.
3. **Never put the RT kernel in `config.txt`** until it has survived a full soak via `tryboot`.
4. **Caveat:** on Pi 4 Model B rev 1.0/1.1 the EEPROM must not be write-protected, since those boards store tryboot state in EEPROM rather than a register. Confirm board revision first.

---

## Open questions blocking Arm B

| Question | Why it blocks |
|---|---|
| **Which board and OS release?** | Diagnostics point to a **Pi 4** (BCM2711 `emmc2bus` path) while `README.md` claims a Pi 5 reference stack; package versions suggest a **trixie**-based OS, not bookworm. RT kernel choice depends on both. `diagnose-pi-state.sh` reports neither — needs an `mpe sysinfo` subcommand. |
| **Is PREEMPT_RT even obtainable?** | Raspberry Pi ships no `-rt` kernel flavor. A generic Debian arm64 RT kernel risks breaking the things this appliance depends on — DSI panel via `vc4`/kmsdrm, `dwc2` UAC2 gadget, dtoverlays. If so, B.1 means *building a kernel*, not installing one. |
| **Is RT even the cheapest test of the hypothesis?** | Much of `PREEMPT_RT` landed in mainline 6.12, and `SCHED_FIFO` + `rtprio`/`memlock` limits + CPU governor pinning may deliver most of the jitter reduction **with no kernel change at all**. If so, do that first and RT may never be needed. |

Until these are answered, **do not write RT config** into `config/surge-xt-cli.service` or a `limits.d` drop-in.

---

## Human gates

- **Any kernel install on the gig SD** — Mitch only, and only after `tryboot` is rehearsed
- **Promoting a kernel into `config.txt`** — Mitch only, and only after a clean soak
- **Production buffer change** in `/etc/mpe/mpe.env` on the live Pi — Mitch only
- Reopening the software looper question — only on a written Arm B pass

## Deploys

Repo changes reach the Pi **through GitHub only** — land on `dev`, promote to `main`, then pull on the Pi. No direct `scp`/`rsync` of experiment config.
