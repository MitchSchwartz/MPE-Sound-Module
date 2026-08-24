# Pi 4 golden image — flash, provision, external state

*Last updated: 2026-08-23 (America/Toronto)*

**Primary deploy for a configured SD card:** [`PI4-CLONE-SD.md`](PI4-CLONE-SD.md) — master image → write blank SD → boot (no Imager setup).

**Alternate:** fresh Imager flash + [`build-appliance.sh`](../scripts/image/build-appliance.sh) from private assets (Workflow D below). [`build-pi4-appliance.sh`](../scripts/image/build-pi4-appliance.sh) is a thin `--platform pi4` wrapper.

**Status:** scripts shipped; **first full rehearsal not done yet** — fill the row in [`RESTORE.md`](RESTORE.md) when complete.

Companion: [`STORAGE-ROBUSTNESS.md`](STORAGE-ROBUSTNESS.md) Phase 1 (future `/state` partition).

---

## Layers

| Layer | Contents | Changes how often |
|---|---|---|
| **Build script** | OS (Imager) + apt + Surge binary/patches from **private assets** + units + hygiene | Each new unit |
| **First-boot** | Paths, player parity env, units, udev | Once per SD |
| **External state** | Calibration, patch browser JSON, looper HUD prefs, `/etc/mpe/mpe.env` | Per device / after recalibration |
| **Optional `.img.xz`** | Frozen snapshot of above — **private storage only**, not GitHub | On release tag |

**Do not upload `.img` to GitHub** — 2 GB file cap + Surge GPL binary. Use private assets repo (~450 MB once) + build script instead.

---

## Captured vs built vs excluded (Pi 4 audit 2026-08-23)

| Category | Items | Mechanism |
|---|---|---|
| **External state** (capture/apply) | `/etc/mpe/mpe.env`, `~/.patch_browser_*`, `.patch_browser_*_backups`, looper HUD JSON (`.mpe_sl_*`, `.mpe_midi_clock_state.json`, `.mpe_looper_timing.json`), `~/surge-cli-calibration.log`, `~/.local/share/Surge XT/` | [`external-state-paths.list`](../config/platform/external-state-paths.list) |
| **Built on each unit** | Pi OS Lite, apt/JACK/pygame, Surge binary + patches, MPE-Module @ ref, systemd units, cmdline hygiene, **`apply-dsi-config.sh`** (DSI overlay), **`install-jack-audio-limits.sh`**, native meters, touch setup | [`build-appliance.sh`](../scripts/image/build-appliance.sh) |
| **External state extras** | systemd drop-ins (`mpe-*`, `surge-*`, `sl-*`, `touch-*`), boot DSI snippet (audit), `platform.json` kernel stamp | capture/apply provision scripts |
| **Per-unit manual** (not captured) | **SSH `authorized_keys`** (your choice per device), **Tailscale** (`sudo tailscale up` — node creds never in image), WiFi (`NetworkManager` profiles contain PSKs), hostname (unless `--hostname`) | You configure after build |
| **Never in image / capture** (hard-coded) | `~/.ssh/host_*`, `known_hosts`, **`/var/lib/tailscale/*`**, `.mpe_clock_*.json` (tempfiles), shell history, **`/etc/NetworkManager/system-connections/*`** (WiFi PSKs), `machine-id` | `sanitize-for-clone.sh` before `dd`; `--verify` asserts; capture script skips secrets |
| **In private assets, not state** | Surge binary, factory/3rd-party patches, Quick Select tree | [`BACKUP_GUIDE.md`](BACKUP_GUIDE.md) / `deploy-all.sh` |
| **On Pi but empty / symlinked** | `~/.Surge Synth Team/Surge XT/Patches` → MPE-Library via deploy | Build script, not capture |
| **Optional / looper** | SooperLooper build under `~/src/sooperlooper-*` | `--with-looper` stub only; manual build |

**Not missing from capture:** SSH keys and **Tailscale credentials** — excluded on purpose (each unit enrolls fresh). WiFi — excluded (secrets).

Before optional `dd` imaging: `sudo ./scripts/provision/sanitize-for-clone.sh` runs `tailscale logout` and wipes `/var/lib/tailscale/*`.

---

## What is in external state

Canonical path list: [`config/platform/external-state-paths.list`](../config/platform/external-state-paths.list)

Includes `/etc/mpe/mpe.env`, `~/.patch_browser_*`, calibration logs/backups, `~/.local/share/Surge XT/`.

Player tuning defaults (when not restoring a full capture): [`player-env-parity.pi4.env`](../config/platform/player-env-parity.pi4.env) / [`player-env-parity.pi5.env`](../config/platform/player-env-parity.pi5.env) via `apply-player-env-parity.sh` (auto-detects board).

---

## Workflow D — build from assets (recommended, no `.img`)

1. Flash **Lite 64-bit** with Raspberry Pi Imager (SSH on, set username).
2. Ensure private assets repo is beside MPE-Module (`BACKUP_GUIDE.md`).
3. Set `config/mpe.env` (`PI_HOST`, `PI_USER`, `SSH_KEY`).
4. Run:

```bash
./scripts/image/build-appliance.sh --platform pi4 \
  --state state/raspberrypi2-2026-08-23

# Pi 5 (uses appliance-git-ref.pi5 → dev by default):
./scripts/image/build-appliance.sh --platform pi5 --state state/raspberrypi5-2026-08-23
```

5. Add **your** SSH public key to the new Pi (`authorized_keys` — not copied from reference).
6. WiFi / Tailscale if needed. Reboot. Play.

Capture reference state first (once):

```bash
./scripts/provision/capture-external-state.sh
```

---

## Workflow A — bake golden image from reference Pi 4

On **`raspberrypi2`** (certified unit):

```bash
cd ~/MPE-Module
git checkout main    # appliance deploy branch — pin before imaging
sudo ./scripts/image/capture-pi4-golden.sh
# Tailscale creds stripped automatically — no manual logout required
sudo poweroff
```

On **laptop** (SD in reader):

```bash
sudo dd if=/dev/sdX of=~/mpe-pi4-golden-$(date +%Y%m%d).img bs=4M status=progress conv=fsync
xz -9 -T0 ~/mpe-pi4-golden-*.img
```

Verify manifest:

```bash
./scripts/image/bake-pi4-golden.sh verify
```

Store `*.img.xz` on an external drive or private bucket — not in this public repo.

---

## Workflow B — boot a clone SD (no laptop deploy)

**Use when:** master `.img.xz` already exists. Tuning is **baked in** — do not run `flash-and-provision.sh` or `apply-external-state.sh`.

Full runbook: [`PI4-CLONE-SD.md`](PI4-CLONE-SD.md) Part 2–3.

```bash
xz -dc ~/mpe-pi4-golden-YYYYMMDD.img.xz | sudo dd of=/dev/sdX bs=4M status=progress conv=fsync
```

Boot → optional WiFi / `sudo tailscale up` → play.

---

## Workflow B2 — flash generic image + apply state (unusual)

Only if the master image was built **without** reference tuning on disk:

1. Flash `mpe-pi4-golden-*.img.xz`
2. `./scripts/image/flash-and-provision.sh --state state/raspberrypi2-YYYY-MM-DD`

---

## Workflow C — refresh external state only (no reflash)

After recalibration or prefs change on a live unit:

```bash
./scripts/provision/capture-external-state.sh
./scripts/provision/capture-external-state.sh --check   # drift vs last capture
```

Push to another bench unit:

```bash
./scripts/provision/apply-external-state.sh --state state/raspberrypi2-2026-08-23
```

---

## Scripts

| Script | Where | Purpose |
|---|---|---|
| [`capture-external-state.sh`](../scripts/provision/capture-external-state.sh) | Laptop or Pi `--local` | Pull portable `state/` tree |
| [`sanitize-for-clone.sh`](../scripts/provision/sanitize-for-clone.sh) | Pi (sudo) | Strip machine-id, SSH host keys, **Tailscale creds** before imaging |
| [`apply-player-env-parity.sh`](../scripts/apply-player-env-parity.sh) | Pi | Board-specific env (`player-env-parity.pi4.env` / `.pi5.env`) |
| [`apply-dsi-config.sh`](../scripts/apply-dsi-config.sh) | Pi (sudo) | Freenove DSI `config.txt` overlay |
| [`install-jack-audio-limits.sh`](../scripts/install-jack-audio-limits.sh) | Pi (sudo) | `/etc/security/limits.d/audio.conf` for shell jackd |
| [`first-boot.sh`](../scripts/provision/first-boot.sh) | Pi (sudo) | Units, hygiene, parity env |
| [`build-appliance.sh`](../scripts/image/build-appliance.sh) | Laptop | Imager OS → deploy assets → first-boot → state (`--platform pi4\|pi5\|auto`) |
| [`build-pi4-appliance.sh`](../scripts/image/build-pi4-appliance.sh) | Laptop | Wrapper → `build-appliance.sh --platform pi4` |
| [`audit-external-state-paths.sh`](../scripts/provision/audit-external-state-paths.sh) | Laptop or Pi | Compare paths list vs home |
| [`capture-laptop-mpe-config.sh`](../scripts/provision/capture-laptop-mpe-config.sh) | Laptop | Snapshot `~/.config/mpe/mpe.env.*` |
| [`install-pi4-day0-tier1.sh`](../scripts/image/install-pi4-day0-tier1.sh) | Pi | Apt/JACK/pygame (called by build script) |
| [`capture-pi4-golden.sh`](../scripts/image/capture-pi4-golden.sh) | Pi (sudo) | Optional pre-`dd` sanitize + manifest |
| [`bake-pi4-golden.sh`](../scripts/image/bake-pi4-golden.sh) | Laptop | Instructions + manifest verify |
| [`flash-and-provision.sh`](../scripts/image/flash-and-provision.sh) | Laptop | SSH wait → first-boot → state |

Legacy: [`backup-appliance-state.sh`](../scripts/backup-appliance-state.sh) (calibration only) — use `capture-external-state.sh` instead.

---

## Golden image contents checklist

Before calling an image "golden", confirm on the reference Pi:

- [ ] `apply-appliance-hygiene.sh` applied (timers masked, cmdline `irqaffinity=0,1`, HDMI off)
- [ ] Surge `253f8d86` (or pinned SHA) binary + resources present
- [ ] `MPE-Library` patches symlinked into Surge data tree
- [ ] SooperLooper 1.7.9 built under `~/src/sooperlooper-1.7.9`
- [ ] Touch stack: `setup-touch-pi.sh` deps installed, `MPE_UI_MODE=touch`
- [ ] `install-units.sh` enable set matches production
- [ ] Native tools built: `mpe-peak-meter`, `mpe-xrun-probe`
- [ ] Git checkout matches platform ref (`appliance-git-ref.pi4` → **`main`**, `appliance-git-ref.pi5` → **`dev`** until promoted)

---

## Rehearsal (required)

Until this passes, "expendable SD" is still a hypothesis ([`RESTORE.md`](RESTORE.md)):

**Clone SD path** ([`PI4-CLONE-SD.md`](PI4-CLONE-SD.md) § Rehearsal):

1. Master from reference Pi on **`main`**
2. Write blank SD from `.img.xz`
3. Boot clone — touch + sound, no laptop deploy
4. Fill rehearsal log row

---

## Not in v1

- pi-gen reproducible bake (`artifacts/pi-gen/` — tracked as follow-up)
- Dedicated `/state` partition ([`STORAGE-ROBUSTNESS.md`](STORAGE-ROBUSTNESS.md))
- A/B slot updates

---

## Platform direction (Pi 4 → Pi 5)

**Problem:** scripts and docs are Pi 4–named (`build-pi4-appliance.sh`, `PI4-CLONE-SD.md`, …) while the reference player moves to Pi 5 (`install-pi5-*` lives outside `scripts/image/`).

**Split by portability — do not duplicate both paths per board:**

| Path | Scope | Why |
|---|---|---|
| **`dd` clone** (Workflows A/B) | **Board-specific** | Tuning baked into the image is wrong on the other board (e.g. Pi 4 `MPE_JACK_BUFFER=1024` vs Pi 5 `128`; Pi 5 `v3d` blacklist; **`mpe-irq-affinity.service` off on Pi 5** — RP1 IRQs not writable). A Pi 4 image on Pi 5 is actively wrong. |
| **Build-from-assets** (Workflow D) | **Board-neutral base + profile** | [`build-appliance.sh --platform {pi4,pi5,auto}`](../scripts/image/build-appliance.sh) selects day0 tier, git ref, and parity profile via `detect-pi-platform.sh`. |

**Pi 5 golden image — capture now, bake later.** Blockers: 3 A PSU / no cooler, Pi 4–built Surge binary (arm64 functional, not a76-native), governor tune mid-experiment. **`capture-external-state.sh` on Pi 5** once [`docs/LAPTOP-MPE-CLI.md`](LAPTOP-MPE-CLI.md) `mpe.env.pi5` exists.

**Order:** (1) configure laptop `mpe.env.pi5` + capture Pi 5 state, (2) `audit-external-state-paths.sh` on each board, (3) Pi 4 clone rehearsal + RESTORE.md row, (4) Pi 5 golden image after gates close.

**Provisioning gaps (2026-08-23):**

| Gap | Status |
|-----|--------|
| `/boot/firmware/config.txt` DSI overlay | **`apply-dsi-config.sh`** — first-boot; snippet captured for audit |
| `/etc/security/limits.d/audio.conf` | **`install-jack-audio-limits.sh`** — day0 + first-boot |
| Kernel/firmware pin + manifest stamp | **`write-platform-manifest.sh`** — in capture MANIFEST + `platform.json`; golden IMAGE-MANIFEST |
| Pi 4 vs Pi 5 branching | **`build-appliance.sh --platform`** + parity split + `detect-pi-platform.sh` |
| `appliance-git-ref` vs Pi 5 on `dev` | **Split:** `appliance-git-ref.pi4` / `.pi5` |
| Laptop mpe env / SSH pins | **`capture-laptop-mpe-config.sh`** + [`LAPTOP-MPE-CLI.md`](LAPTOP-MPE-CLI.md) examples |
| systemd drop-ins | **Captured/restored** under `state/.../etc/systemd-dropins/` |
| Public image hosting (GPL + size) | Not in v1 |
