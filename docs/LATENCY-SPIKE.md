# Surge latency — can the buffer floor come down?

*Last updated: 2026-08-10 19:07 (America/Toronto)*

**The question, in one line:** *Can we run a smaller Surge buffer than 1024 without losing voices — and what's the cheapest change that gets us there?*

Originally scoped as "enable PREEMPT_RT and turn the buffer down." Measured baseline says start further up the stack: realtime scheduling was never enabled for Surge in the first place.

**Related:** [#44 Buffer size in settings](https://github.com/MitchSchwartz/MPE-Sound-Module/issues/44) · `FAQ.md` · `docs/PATCH_NORMALIZATION.md`

**Status:** **Arm A0 passed; Arm A ran informally and inverted the premise** — 256 samples (~5.3 ms) played acceptably with no xruns, on stock scheduling (§Validation log). **Arms A½ and B are not started and look unnecessary** — every cheap lever (governor, RT priority, `threadirqs`, RT kernel) is still unspent and there is no xrun to fix. The binding constraint is **CPU / voice count**, not block latency. Repo buffer defaults corrected to 1024 (below); looper question **reopened** → [`LOOPER-PLAN.md`](LOOPER-PLAN.md).

> **Headline:** Surge currently runs `SCHED_OTHER` at priority 0 with an `rtprio` hard limit of **0**, on the `ondemand` CPU governor. It has never had realtime scheduling available to it. Chasing an RT *kernel* before fixing that is optimizing the wrong layer.

---

## Why buffer tuning alone is exhausted

| Buffer @ 48 kHz | Block latency | Result today |
|---|---|---|
| **1024** | ~21 ms | **Production floor** — voices hold under real MPE load |
| **768** | ~16 ms | **Loses too many voices** |
| **512 and below** | ~11 ms | Historical xrun/crackle on heavy patches |

The binding constraint is **CPU headroom per callback**, not block math. Direct ALSA is already the thin path and there's no JACK/PipeWire layer to remove, so the remaining levers are all about **when the audio thread gets the CPU** — scheduling policy, priority, and clock speed.

Either something in that layer buys a smaller buffer, or **1024 is the permanent floor** and any Pi-side software looper is dead (looper buffers stack on top of ~21 ms).

---

## Measured baseline (`mpe sysinfo`, 2026-08-10)

| Fact | Value | Consequence |
|---|---|---|
| Board | **Pi 4 Model B Rev 1.5** (BCM2711, aarch64) | Not rev 1.0/1.1, so the `tryboot` EEPROM write-protect caveat **does not apply** |
| OS | **Debian 13 (trixie) 13.5** | Not bookworm — narrows RT kernel options |
| Kernel | **6.18.34+rpt-rpi-v8**, build string `SMP PREEMPT` | Past 6.12 (where `PREEMPT_RT` mainlined) but built plain `PREEMPT` — **no `PREEMPT_DYNAMIC`**, so `preempt=full` at boot is likely unavailable without a rebuild |
| CPU governor | **`ondemand`** | ⚠️ Not `performance`. Clock ramps on demand, which is a classic cause of dropouts on transient polyphony spikes |
| Surge scheduling | **`SCHED_OTHER`, priority 0** | ⚠️ Audio thread runs at *normal* priority — no realtime scheduling at all |
| `Max realtime priority` | **0** (soft and hard) | ⚠️ Surge **cannot** request RT priority even if it tries; JUCE's attempt fails silently |
| `Max locked memory` | 8 MB | Default, not raised for audio |
| `/etc/security/limits.d/` | only `10-coredump-debian.conf` | ⚠️ **No audio limits file exists** |
| `vcgencmd get_throttled` | **`0x50000`** | ⚠️ Under-voltage **has occurred** and throttling **has occurred** (historic, not active) |
| Audio profile | **`usb-host`** | Not the `standalone` rig this plan specifies — must be switched before measuring |
| Buffer / rate | 1024 @ 48000 → **21.33 ms** | Confirms the production floor |

**This changes the plan.** The hypothesis was "the stock kernel's scheduler jitter is the ceiling." But Surge isn't being scheduled as a realtime task *at all*, and the CPU governor isn't pinned. Those are the standard prerequisites for low-latency audio on Linux and neither is in place, so the 768 failure has a much cheaper candidate explanation than kernel preemption model.

**Also a genuine confounder:** under-voltage and throttling have both occurred on this Pi. The repo's own USB-audio notes call out undervoltage as a cause of audio glitches. If the board browns out or thermally throttles during a jam, buffer experiments are measuring a moving target — this needs ruling out *before* Arm A is trusted.

---

## Independent variable

**One change per arm**, in ascending order of risk: diagnose → CPU governor → `threadirqs` → RT scheduling for Surge → RT kernel. Do not bundle them; if governor plus `SCHED_FIFO` land together and 768 starts passing, we won't know which one mattered or whether the kernel arm is needed at all.

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

Run in order. Stop as soon as an arm passes.

### Arm A0 — rule out power and thermal (~30 min)

`get_throttled = 0x50000` says this board has browned out **and** throttled at some point. Until that's excluded, every other result is suspect — a Pi that dips below voltage mid-jam will drop voices regardless of buffer size or scheduler.

| Step | Action |
|---|---|
| A0.1 | Reboot to clear the sticky bits, run the A.3 jam, then re-read `vcgencmd get_throttled` |
| A0.2 | If under-voltage recurs: official PSU, powered hub for the Sound Blaster + Roli, re-test. This is a **hardware fix, not a buffer problem** |
| A0.3 | Log SoC temperature across the jam; confirm no thermal cap under sustained load |

### Arm A — control (stock config, ~1 hour)

Purpose: put the thing we already believe on the record, so the later arms have something to compare against.

| Step | Action |
|---|---|
| A.1 | Confirm `MPE_SURGE_BUFFER_SIZE=1024` in `/etc/mpe/mpe.env` and in touch **Audio → Buffer** |
| A.2 | Build the rig above — switch profile to **`standalone`** (currently `usb-host`), no pedal, no gadget, Roli only |
| A.3 | 10 min jam @ **1024** — note voice behavior, grep log for xrun |
| A.4 | Brief pass @ **768** — **document the failure mode concretely** (which patches, how many voices, how fast it degrades) |
| A.4b | **Diagnose the failure mode** while reproducing A.4: grep `~/surge-cli.log` for xrun/underrun; check `/proc/asound/card*/pcm*/sub*/status` for `xrun`; run `rtla timerlat top` or `cyclictest` under the same load. If max scheduling latency is **well under 16 ms** and there are **no xruns**, the 768 failure is Surge voice-stealing (CPU throughput), not scheduler jitter — **stop here; RT will not help and may make it worse** |
| A.5 | Capture `mpe sysinfo` output and the patch list used |
| A.6 | Validation Log row: control arm result |

A.4 matters more than it looks: "loses voices" needs to be specific enough that later arms can be judged against it rather than against memory.

### Arm A½ — scheduling and clock, no kernel change (before Arm B)

This is now the **most promising arm**, and the cheapest: nothing here needs a kernel, a card pull, or a rollback plan. Both knobs ship **off by default** so pulling the branch changes nothing until opted into.

**Sub-arm 1 — CPU governor**

| Step | Action |
|---|---|
| A½.1 | Set `MPE_CPU_GOVERNOR=performance` in `/etc/mpe/mpe.env`, reboot, confirm via `mpe sysinfo` |
| A½.2 | Re-run A.3 @ 1024, then step to 768 — governor effect in isolation |
| A½.3 | Re-check `get_throttled` and temperature: `performance` costs power and heat, so it could *worsen* an under-voltage problem |

**Sub-arm 2 — realtime scheduling**

`surge-xt-cli.service` now sets `LimitRTPRIO=95` and `LimitMEMLOCK=infinity`, which **permits** rather than forces RT: JUCE can elevate just its audio callback thread while the rest of the process stays normal. That is safer than making the whole process `SCHED_FIFO`.

| Step | Action |
|---|---|
| A½.4 | Restart Surge, then `chrt -p $(pgrep -f surge-xt-cli)`. If it now shows `SCHED_FIFO`, JUCE self-elevated — go to A½.6 |
| A½.5 | If still `SCHED_OTHER`, JUCE isn't asking. Set `MPE_SURGE_RT_PRIORITY=20` to wrap the launch in `chrt --fifo` and re-check |
| A½.6 | Re-run A.3 @ 1024, then step to 768 |

**Sub-arm 3 — `threadirqs` (stock kernel only, no RT package)**

The stock Raspberry Pi arm64 kernel already supports forced threaded IRQs (`CONFIG_IRQ_FORCED_THREADING`). Adding `threadirqs` to `/boot/firmware/cmdline.txt` is reversible and often cited as removing the need for an RT kernel on less powerful systems — but linuxaudio.org still lists Raspberry Pi as a case where RT *can* matter at very low buffers.

| Step | Action |
|---|---|
| A½.7 | Add `threadirqs` to `cmdline.txt`, reboot, confirm with `cat /proc/cmdline` |
| A½.8 | Re-run A.3 @ 1024, then step to 768 — **only if A.4b showed actual xruns or scheduling latency near one buffer period** |

**Note on `limits.d`:** systemd services bypass PAM, so `/etc/security/limits.d/` has **no effect** on `surge-xt-cli.service` — the unit's `Limit*` directives are the correct mechanism. A `limits.d` file would only matter for Surge launched manually over SSH (e.g. calibration), so it's deliberately not part of this arm.

Everything here is **repo-managed** and deploys through GitHub — no hand-editing on the Pi.

**Do not raise the RT priority aggressively.** A `SCHED_FIFO` thread that spins can lock up the box; the touch UI and network stack still need to run.

If 768 passes here, **Arm B is cancelled** and the looper question reopens with no kernel risk at all.

### Arm B — RT kernel (~1 hour install + soak) — last resort

**Gates:** A.4b shows xruns or scheduling latency near one buffer period (not voice-stealing alone) · A½ exhausted · `tryboot` rehearsed (§Rollback) · Mitch approves.

**Feasibility is resolved:** Raspberry Pi ships **`linux-image-rpi-v8-rt`** in the trixie archive, version-locked to the stock `rpi-v8` kernel and built from the same `raspberrypi/linux` tree with only preemption changed (`bcm2711_rt_defconfig` — six-line diff from stock). `vc4`/DSI, touch panels (`ili9881c`, official 5"/7"), `dwc2`, and UAC2 gadget are all present. **No `linux-image-rpi-2712-rt`** exists — irrelevant here since the live unit is Pi 4 Rev 1.5, where `v8` → `v8-rt` changes exactly one variable.

**Rejected paths:** generic Debian `linux-image-rt-arm64` (breaks downstream dtoverlays and DSI drivers); `preempt=full` boot param (not `PREEMPT_RT`, and `PREEMPT_DYNAMIC` is off on Pi kernels anyway); building from source (only if the packaged RT kernel is close but insufficient).

**Caveats before installing:**

- Labelled **experimental** by Raspberry Pi — not in official docs, but apt-packaged and version-matched.
- RT trades **throughput for tail latency**. If 768 fails from voice-stealing, RT may lower the voice ceiling further even as jitter improves.
- **No published measurement** of RT with the `dwc2` UAC2 gadget path — irrelevant for the `standalone` test rig, but matters if you later re-test in `usb-host`.
- **10.1" Touch Display 2** (`ili79600` overlay) is missing from the RT defconfig; the 5"/7" SmartiPi panels are fine. Confirm `dtoverlay=` in `config.txt` if unsure.

| Step | Action |
|---|---|
| B.0 | Re-run `mpe sysinfo`; confirm A.4b outcome justifies continuing |
| B.1 | `sudo apt install linux-image-rpi-v8-rt` — installs alongside stock kernel as `kernel8_rt.img` |
| B.2 | Copy `/boot/firmware/config.txt` → `/boot/firmware/tryboot.txt`; add `kernel=kernel8_rt.img` to **tryboot.txt only** |
| B.3 | Rehearse empty tryboot first (§Rollback), then `sudo reboot '0 tryboot'` |
| B.4 | Confirm RT active: `uname -a` shows `-rt`; `cat /sys/kernel/realtime` = 1 |
| B.5 | Re-run A.3 @ **1024** — sanity check RT didn't regress the known-good config |
| B.6 | Step down: **768**, then **512** — same 10 min protocol, same patches, same pass criteria |
| B.7 | Reliability soak at the best passing buffer: touch UI, USB hotplug, Wi-Fi scan |
| B.8 | Only after a clean soak, promote RT kernel into `config.txt`. Validation Log: adopted or rejected + lowest buffer that held voices |

---

## Outcomes and what each one decides

Ordered cheapest-first. Stop at the first arm that passes — there's no prize for reaching Arm B.

| Result | Production buffer | Pi software looper |
|---|---|---|
| **A0** — under-voltage was the real cause | Retest everything after the PSU fix | Re-ask once the board is stable |
| **A.4b** — voice-stealing with no xruns, latency ≪ buffer period | Stay **1024** — CPU throughput bound | **Closed** — RT won't help; lighter patches or accept 21 ms |
| **A½** governor and/or `SCHED_FIFO` holds **512** | Consider 512 (~11 ms), no kernel change | **Reopen** — best case, zero kernel risk |
| **A½** holds **768** | Consider 768 (~16 ms), no kernel change | **Marginal** — only without a monitored record path |
| A½ no help, **RT** holds voices @ **512** | Consider 512 (~11 ms) | **Reopen** — architecture spike worth doing |
| A½ no help, **RT** holds voices @ **768** | Consider 768 (~16 ms) | **Marginal** — only without a monitored record path |
| RT no better than stock | Stay **1024** (~21 ms) | **Closed** — hardware pedal stays the loop engine |
| RT hurts reliability | Stay **1024**, stock kernel | **Closed** |

Reliability outranks latency: a smaller buffer that survives 10 minutes but fails a set is a regression, not a win.

---

## Validation log

### 2026-08-10 — Arm A0 pass, Arm A informal (buffer sweep)

**Rig:** Pi 4 Rev 1.5 · `standalone` → Sound Blaster → headphones · Roli LUMI only · RC-5 absent · `midi-clock-in` stopped · 48 kHz · poly governor active · touch UI running · stock scheduling (`SCHED_OTHER` prio 0, `ondemand`).

| Buffer | Block latency | Subjective | Objective |
|---|---|---|---|
| **1024** | 21.3 ms | Good | No xruns |
| **768** | 16.0 ms | *Maybe* subtly worse — "couldn't call it in a blind test" | No xruns |
| **512** | 10.7 ms | Good; arguably **best feel** | No xruns |
| **256** | 5.3 ms | "Shockingly not bad", slightly aggressive | No xruns |
| **256** (`usb-host`, UAC2 gadget) | 5.3 ms Pi-side | Similar to standalone | No xruns |

**Arm A0 — pass.** `vcgencmd get_throttled` = `0x0` before and after the jam; 12-minute sampler logged `0x0` on every sample; peak SoC temperature **60.3 °C**. The historic `0x50000` (under-voltage + throttling) did **not** recur under load, so power is no longer a confounder.

**Patch findings (not buffer failures):**

- **Attenbourg → drums** — mostly will not voice at **any** buffer. This is a **patch CPU ceiling**, so it is excluded from buffer pass/fail.
- **Crystal** — crackles past 1–2 notes at every buffer. Suspected patch harmonics/artifact; unconfirmed.

**What this overturns:** the "768 loses voices / 512 is choppy / 1024 is the permanent floor" premise (§Why buffer tuning alone is exhausted) does not reproduce on a clean-power `standalone` rig. The prior finding was likely confounded by under-voltage and/or the `usb-host` gadget path.

**Caveats — this is L1/L2 evidence, not L3:**

- Casual multi-minute jams, **not** the 10-minute worst-case soak this document defines as passing (§Dependent variable).
- **A.4b was not run** — `rtla` is not installed on the Pi, so scheduling latency under load is still unmeasured. Nothing here distinguishes "plenty of headroom" from "no headroom left but no xrun yet".
- Production `/etc/mpe/mpe.env` was left at the session's last value, **not** promoted as a validated default.

**Next if this matters again:** install `rtla`, run A.4b, then a structured 10-minute soak at 512 on worst-case *in-scope* patches before changing any production default.

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
4. **Rev caveat — resolved.** On Pi 4 Model B rev 1.0/1.1 the EEPROM must not be write-protected, since those boards store tryboot state in EEPROM rather than a register. This unit is **Rev 1.5**, so the caveat does not apply.

---

## RT feasibility (research, 2026-08-10)

Web research only — no Pi changes made. Full report from the feasibility pass is linked in [PR #46](https://github.com/MitchSchwartz/MPE-Sound-Module/pull/46#issuecomment-5246232296).

### Resolved

| Question | Answer for this unit |
|---|---|
| Is PREEMPT_RT obtainable on trixie? | **Yes** — `sudo apt install linux-image-rpi-v8-rt`, same version stream as stock. Experimental, not documented on raspberrypi.com, but built from the Pi tree. |
| Generic Debian RT kernel? | **Rejected** — breaks downstream dtoverlays, DSI panel drivers, and Pi 5 isn't mainline-ready anyway. |
| `preempt=full` without RT? | **Dead end** — not the same as `PREEMPT_RT`; `PREEMPT_DYNAMIC` is off on Pi arm64 kernels, so the boot param is ignored. Stock kernel is already `CONFIG_PREEMPT=y`. |
| Does RT help Pi audio? | **Mixed.** Academic Pi 4 work shows RT holding 64-sample buffers under load; forum evidence on Pi 5 shows worse tail latency under memory stress. RT compresses scheduling tails but can cost throughput — the wrong trade if voice-stealing is the failure mode. |
| Pi 4 vs Pi 5 for this test? | **Pi 4 Rev 1.5 confirmed** — `v8` → `v8-rt` is a clean one-variable swap. No `rpi-2712-rt` package exists. |

### Still to check on the live Pi (before Arm B)

| Check | Why |
|---|---|
| **A.4b diagnosis** | Distinguish xruns from Surge voice-stealing. If no xruns and scheduling latency ≪ 16 ms, RT is the wrong lever. |
| **`dtoverlay=` in config.txt** | Only matters if using the 10.1" panel (`ili79600`) — missing from RT defconfig. SmartiPi 5" is `ili9881c`, fine. |
| **HVS / display load** | Pi engineer note: hardware compositor runs at very high priority and affects latency measurements — continuous touch UI animation during the jam is a confounder RT cannot fix. |

### Cheapest RT test path (if A½ fails and A.4b justifies it)

```bash
sudo apt install linux-image-rpi-v8-rt
cp /boot/firmware/config.txt /boot/firmware/tryboot.txt
# add to tryboot.txt only:  kernel=kernel8_rt.img
sudo reboot '0 tryboot'    # failed boot → power cycle rolls back to stock
```

---

## Open questions

**None blocking Arm A0, A, or A½.** Arm B is gated on A.4b outcome and A½ exhaustion, not on kernel availability.

---

## Human gates

- **Any kernel install on the gig SD** — Mitch only, and only after `tryboot` is rehearsed
- **Promoting a kernel into `config.txt`** — Mitch only, and only after a clean soak
- **Production buffer change** in `/etc/mpe/mpe.env` on the live Pi — Mitch only
- Reopening the software looper question — only on a written Arm A½ or Arm B pass

## Deploys

Repo changes reach the Pi **through GitHub only** — land on `dev`, promote to `main`, then pull on the Pi. No direct `scp`/`rsync` of experiment config.
