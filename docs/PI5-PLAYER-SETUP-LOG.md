# Pi 5 player setup log — checklist from 2026-08-23 bringup

*Last updated: 2026-08-23 (America/Toronto)*

**Session closeout (2026-08-23):** Player is **live** at 128×2; hygiene + IRQ Phase 1 done; Suite 1
**blocked** on cooler + 27 W PSU. Full state: [`measurements/PI5-SESSION-CLOSEOUT-2026-08-23.md`](measurements/PI5-SESSION-CLOSEOUT-2026-08-23.md).

**Purpose:** Every step needed to go from a fresh Pi 5 SD card to a **working touch player** that matches the live Pi 4.

**Two tracks — do not mix before platform comparison lands:**

| Track | Doc | Tier 3 touch UI | cmdline `irqaffinity` | When |
|-------|-----|-----------------|----------------------|------|
| **Day 0 / measurement** | [`PROMPT-PI5-DAY0.md`](measurements/PROMPT-PI5-DAY0.md) §1a | **Skip** | **Skip** (census only) | Before Suite 1 |
| **Player (this log)** | This file | **Install** | **Required** for `mpe-jackd` | Daily instrument |

Measurement runbook: [`PI5-BRINGUP-RUNBOOK.md`](PI5-BRINGUP-RUNBOOK.md).

**Worked example:** `raspberrypi5` · user `pi` · Pi 4 remains `raspberrypi2` · user `mitch`.

Canon for generic steps: [`BUILD-FROM-ZERO.md`](BUILD-FROM-ZERO.md). Touch UI: [`TOUCH_PATCH_BROWSER.md`](TOUCH_PATCH_BROWSER.md).

---

## A. Before flash (laptop)

| Step | Do | Verify |
|------|-----|--------|
| A1 | **Pi Imager ≥ 2.0.10** (Trixie cloud-init needs it; 1.8.x silently breaks customization) | `rpi-imager --version` |
| A2 | Flash **Lite 64-bit Trixie**. Hostname **`raspberrypi5`** (must differ from Pi 4) | — |
| A3 | Imager: enable SSH, set user (Imager may still create `pi` — note actual user) | First boot: `whoami` |
| A4 | Imager: add laptop SSH public key | `ssh pi@raspberrypi5.local` |
| A5 | **27 W USB-C PSU** + active cooler on Pi 5 | `vcgencmd get_throttled` → `0x0` |
| A6 | Separate Sound Blaster + separate SD from Pi 4 (do not cannibalize control board) | — |

### Laptop SSH (`~/.ssh/config`)

```sshconfig
Host pi4 surge raspberrypi2 raspberrypi2.local
    HostName raspberrypi2.local
    User mitch
    IdentityFile ~/.ssh/surge_pi_key
    IdentitiesOnly yes

Host pi5 raspberrypi5 raspberrypi5.local
    HostName 192.168.1.106
    User pi
    IdentityFile ~/.ssh/surge_pi5_key
    IdentitiesOnly yes
```

Prefer **LAN IP** if mDNS is flaky; avahi is **enabled** on the player (2026-08-23). Pi 4 still
uses `.local` via Tailscale/LAN as configured.

### Laptop `mpe` CLI — two boards

`mpe` reads one target per invocation. Split configs:

| File | Contents |
|------|----------|
| `~/.config/mpe/mpe.env.pi4` | `PI_HOST=raspberrypi2.local` · `PI_USER=mitch` · `SSH_KEY=~/.ssh/surge_pi_key` |
| `~/.config/mpe/mpe.env.pi5` | `PI_HOST=raspberrypi5.local` · `PI_USER=pi` · `SSH_KEY=~/.ssh/surge_pi5_key` |

```bash
alias mpe4='MPE_CLI_CONFIG=~/.config/mpe/mpe.env.pi4 mpe'
alias mpe5='MPE_CLI_CONFIG=~/.config/mpe/mpe.env.pi5 mpe'
```

Default `~/.config/mpe/mpe.env` → whichever board is the daily player.

---

## B. Pi — base software

Run on the **new Pi** (SSH as its user).

**Day 0 first (measurement path only):** `scripts/install-pi5-day0-tier1.sh` — build/JACK deps, no touch UI. See [`PROMPT-PI5-DAY0.md`](measurements/PROMPT-PI5-DAY0.md) §1a.

| Step | Command / action | Verify |
|------|------------------|--------|
| B1 | `sudo apt update && sudo apt install -y git rsync` | — |
| B2 | Clone repo: `git clone https://github.com/MitchSchwartz/MPE-Sound-Module.git ~/MPE-Module && cd ~/MPE-Module && git checkout dev` | No deploy key on appliance — anonymous HTTPS pull only ([`PI-GITHUB-ACCESS.md`](PI-GITHUB-ACCESS.md)) |
| B3 | **Deploy from laptop** (Surge binary + factory patches + MPE-Library): `MPE_PERSONAL_REPO=/path/to/MPE-Library ./scripts/deploy-all.sh` with `config/mpe.env` pointing at Pi 5 | `~/surge/build/surge_xt_products/surge-xt-cli --version` |
| B4 | `echo 'MPE_UI_MODE=touch' >> ~/MPE-Module/config/mpe.env` | — |
| B5 | **`scripts/install-pi5-player-tier3.sh`** (touch UI + `python3-rtmidi` via apt — not pip) | `dpkg-query -W python3-rtmidi`; pygame imports |
| B5alt | Or `./scripts/setup-touch-pi.sh` (legacy — may still pip-install rtmidi; prefer B5) | — |
| B6 | `./scripts/configure-pi-paths.sh --local --force` | `systemctl list-unit-files \| grep mpe` |
| B7 | **`scripts/apply-player-env-parity.sh`** — Pi 4 tuning keys into `/etc/mpe/mpe.env` | No `RTMIDI_API` line; `MPE_PEAK_METER=1` |

---

## C. Boot / display (touch Pi)

| Step | Command / action | Verify |
|------|------------------|--------|
| C1 | Append to `/boot/firmware/cmdline.txt`: `irqaffinity=0,1 threadirqs video=HDMI-A-1:d video=HDMI-A-2:d` | `~/MPE-Module/scripts/boot-assert-cmdline.sh` → ok |
| C2 | `./scripts/apply-dsi-cmdline.sh` (moves fbcon off DSI) | backup beside cmdline.txt |
| C3 | `/boot/firmware/config.txt`: `dtoverlay=vc4-kms-dsi-7inch`; comment out `display_auto_detect=1` if Pi 4 uses explicit overlay | `kmsprint \| head` → DSI 800×480 connected |
| C4 | **Reboot** | — |

Without C1, `mpe-jackd` refuses to start (`boot-assert-cmdline: MISSING irqaffinity=0,1`).

Without C7 graphics packages, `touch-patch-browser` dies with `EGL not initialized`.

---

## D. `/etc/mpe/mpe.env` — match Pi 4 player

After `configure-pi-paths.sh`, run **`scripts/apply-player-env-parity.sh`** (merges
`config/platform/player-env-parity.env`). Or edit **`/etc/mpe/mpe.env`** manually.

| Key | Pi 4 value | Wrong on fresh Pi 5 | Symptom if wrong |
|-----|------------|---------------------|------------------|
| `MPE_FAVORITES_NAME` | `"Quick Select"` | `"!Quick Access"` (template default) | Empty / wrong favorites tab |
| `MPE_PEAK_METER` | `1` | `0` | OUT meter shows **−** |
| `MPE_JACK_BUFFER` | `1024` (Pi 4 ship) · **128** (Pi 5 player today) | `256` / wrong parity overwrite | Use parity script that **preserves** tuned value |
| `MPE_JACK_SOFTMODE` | `0` | — | — |
| `MPE_CPU_GOVERNOR` | `performance` | — | — |
| `MPE_POLY_GOVERNOR` + ceiling/floor | `1` / `64` / `64` | missing | Poly behaviour differs |
| **`RTMIDI_API`** | **unset** | `alsa` (wrong workaround) | Pip rtmidi + wrong backend; remapper empty outputs |

**MIDI matches Pi 4:** apt **`python3-rtmidi`**, no `RTMIDI_API`. JACK is **audio only**; MIDI is
`LUMI → mpe-pressure-remap → ALSA Midi Through → Surge`.

```bash
sudo apt install -y python3-rtmidi
pip3 uninstall --break-system-packages -y python-rtmidi 2>/dev/null || true   # remove pip copy if present
sudo systemctl enable mpe-peak-meter.service   # when MPE_PEAK_METER=1
sudo systemctl restart mpe-pressure-remap mpe-jackd surge-xt-cli touch-patch-browser mpe-peak-meter
```

---

## E. User content — copy from Pi 4 (or MPE-Library)

Pi 5 does **not** inherit Pi 4 home-dir state from `deploy-all.sh` alone.

### E1 — Quick Select (71 patches)

Canon on device: `~/MPE-Library/assets/user-data/quick-select/latest/`

```bash
# On Pi 5 (after MPE-Library deployed):
cd ~/MPE-Module
python3 scripts/restore-quick-select.py \
  ~/MPE-Library/assets/user-data/quick-select/latest --rebuild-index
```

Requires `MPE_FAVORITES_NAME="Quick Select"` first.

### E2 — Theme + calibration (from live Pi 4)

```bash
# Laptop:
scp pi4:~/.patch_browser_ui.json pi5:~/.patch_browser_ui.json
scp pi4:~/.patch_browser_brightness.json pi5:~/.patch_browser_brightness.json
scp pi4:~/.patch_browser_normalization.json pi5:~/
scp pi4:~/.patch_browser_pressure.json pi5:~/
scp pi4:~/.patch_browser_metadata.json pi5:~/
ssh pi5 'chmod 600 ~/.patch_browser_*.json && sudo systemctl restart touch-patch-browser surge-xt-cli'
```

| File | What it controls |
|------|------------------|
| `.patch_browser_ui.json` | Theme mode, accent colour, custom palette |
| `.patch_browser_brightness.json` | DSI backlight % |
| `.patch_browser_normalization.json` | Per-patch loudness calibration |
| `.patch_browser_pressure.json` | MPE pressure curves |
| `.patch_browser_metadata.json` | Calibration metadata |

Long-term: `scripts/backup-appliance-state.sh` → commit `appliance-state/calibration/` in repo.

---

## F. ROLI / MIDI chain

**Architecture (same on Pi 4 and Pi 5):** `LUMI USB → mpe-pressure-remap → ALSA Midi Through → Surge`

| Step | Verify |
|------|--------|
| Plug LUMI into **Pi 5 USB** (not Pi 4) | `lsusb \| grep 2af4` |
| **`python3-rtmidi` via apt** | `dpkg-query -W python3-rtmidi`; **no** pip `python-rtmidi` |
| **No `RTMIDI_API` in `/etc/mpe/mpe.env`** | Pi 4 leaves it unset |
| udev rule present | `/etc/udev/rules.d/99-roli-seaboard.rules` |
| Remapper running | `systemctl status mpe-pressure-remap` → **active**; log: `Listening: LUMI Keys BLOCK MIDI 1` |
| Surge input | `grep "Midi Through" ~/surge-cli.log` |
| Hot-plug | udev runs `scripts/roli-connect-debounce.sh`; or `sudo systemctl restart mpe-pressure-remap surge-xt-cli` |

If remapper is down, Surge still opens Midi Through but **hears nothing**.

---

## G. Final verification checklist

Run from laptop (`mpe5` alias):

```bash
mpe5 ping
mpe5 status          # mpe-jackd, surge-xt-cli, touch-patch-browser, mpe-peak-meter → active
mpe5 osc-check       # OSC 53280 listening
```

On Pi 5:

```bash
# Audio
aplay -l | grep -i "Sound Blaster"
cat /run/mpe/meter.state    # wired=1 online=1 (when MPE_PEAK_METER=1)

# Patches
find ~/Documents/Surge\ XT/Patches/Quick\ Select -name '*.fxp' | wc -l   # expect 71

# MIDI (LUMI plugged in)
lsusb | grep 2af4
aconnect -l | grep -iE 'lumi|RtMidi|Through'
journalctl -u mpe-pressure-remap -n 5 --no-pager
```

Human checks: patch browser on DSI, OUT meter moves, LUMI plays with matched loudness/pressure, theme matches Pi 4.

---

## H. Automation gaps (fix in repo when convenient)

Steps that **`setup-touch-pi.sh` / `deploy-all.sh` do not yet cover** — add here when fixed:

1. **`install-pi5-day0-tier1.sh`** — day-0 apt + jackd RT verify (player track can reuse jackd2 from Tier 3).
2. **`install-pi5-player-tier3.sh`** — touch/SDL/EGL + apt `python3-rtmidi` (skip on day 0).
3. **`apply-player-env-parity.sh`** — Pi 4 tuning keys; explicitly omits `RTMIDI_API`.
4. **`MPE_FAVORITES_NAME`** — template defaults to `!Quick Access`; Pi 4 uses `Quick Select`.
5. **User state sync** — no single script yet for ui + normalization + pressure + quick-select.
6. **Two-Pi laptop config** — document in `mpe-cli` README (`mpe.env.pi4` / `mpe.env.pi5` pattern).

---

## J. Session status — 2026-08-23 bringup

| Item | State |
|------|--------|
| Services | mpe-jackd, surge-xt-cli, touch-patch-browser, mpe-peak-meter, mpe-pressure-remap → active |
| Audio | 128×2 @ 48 kHz, Sound Blaster hw:1 |
| RT FIFO | jackd **70**, Surge audio **65** — `verify-jack-rt-limits.sh pi` passes |
| Affinity | Audio **2–3**; UI + poly governor **0–1** |
| Hygiene | v3d blacklisted; BT off; avahi on; performance governor |
| IRQ Phase 1 | [`pi5-irq-phase1-2026-08-23.md`](measurements/pi5-irq-phase1-2026-08-23.md) — partial Pi 4 map |
| PSU / cooling | **3 A, no cooler** — throttle expected under soak; cooler ordered |
| Blocked | Reference suite, Suite 1, 64-voice census until cooler + 27 W PSU |

**SR&ED:** U10 platform replication — instrument live, predictions unscored. See
[`PI5-SESSION-CLOSEOUT-2026-08-23.md`](measurements/PI5-SESSION-CLOSEOUT-2026-08-23.md).

---

## I. What this log intentionally skips

- **`build-surge.sh --arch a76`** + **`install-surge-from-build.sh --arch a76`** — Pi 5 runtime binary must be a76-tuned at the same Surge revision as Pi 4 (`253f8d86`). Generic/stock (`c3680d6b…`) was smoke-only.
- **IRQ census / Suite 0–3** — see [`PI5-BRINGUP-RUNBOOK.md`](PI5-BRINGUP-RUNBOOK.md).
- **Renaming `pi` → `mitch`** — optional; paths in `/etc/mpe/mpe.env` must match actual user.
- **Touch sudoers** (power menu NOPASSWD) — one-time, [`TOUCH_PATCH_BROWSER.md`](TOUCH_PATCH_BROWSER.md).

---

## Related paths

| Doc | Use |
|-----|-----|
| [`BUILD-FROM-ZERO.md`](BUILD-FROM-ZERO.md) | Generic greenfield |
| [`PI5-BRINGUP-RUNBOOK.md`](PI5-BRINGUP-RUNBOOK.md) | Measurement / overnight suites |
| [`measurements/PROMPT-PI5-DAY0.md`](measurements/PROMPT-PI5-DAY0.md) | Day 0 tiers, RT kernel asymmetry, skip Tier 3 |
| [`scripts/install-pi5-day0-tier1.sh`](../scripts/install-pi5-day0-tier1.sh) | Tier 1 apt + jackd RT verify |
| [`scripts/install-pi5-player-tier3.sh`](../scripts/install-pi5-player-tier3.sh) | Touch UI + apt rtmidi (after comparison) |
| [`scripts/apply-player-env-parity.sh`](../scripts/apply-player-env-parity.sh) | Pi 4 env keys into `/etc/mpe/mpe.env` |
| [`scripts/verify-jack-rt-limits.sh`](../scripts/verify-jack-rt-limits.sh) | RT priority / audio group check |
| [`config/platform/player-env-parity.env`](../config/platform/player-env-parity.env) | Parity key source |
| [`scripts/backup-appliance-state.sh`](../scripts/backup-appliance-state.sh) | Pull calibration into repo |
| [`scripts/restore-quick-select.py`](../scripts/restore-quick-select.py) | Restore Quick Select folder |
| [`Documents/specs/system-hygiene-baseline.md`](../Documents/specs/system-hygiene-baseline.md) | Roli detection / remapper behaviour |
