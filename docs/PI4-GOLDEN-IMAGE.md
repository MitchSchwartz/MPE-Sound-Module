# Pi 4 golden image — flash, provision, external state

*Last updated: 2026-08-23 (America/Toronto)*

**Primary deploy for a configured SD card:** [`PI4-CLONE-SD.md`](PI4-CLONE-SD.md) — master image → write blank SD → boot (no Imager setup).

**Alternate:** fresh Imager flash + [`build-pi4-appliance.sh`](../scripts/image/build-pi4-appliance.sh) from private assets (Workflow D below).

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
| **Built on each unit** | Pi OS Lite, apt/JACK/pygame, Surge binary + factory/3rd-party/user patches, MPE-Module @ ref, systemd units, cmdline hygiene, native meters, touch setup | [`build-pi4-appliance.sh`](../scripts/image/build-pi4-appliance.sh) |
| **Per-unit manual** (not captured) | **SSH `authorized_keys`** (your choice per device), **Tailscale** (`sudo tailscale up` — node creds never in image), WiFi (`NetworkManager` profiles contain PSKs), hostname (unless `--hostname`) | You configure after build |
| **Never in image / capture** (hard-coded) | `~/.ssh/host_*`, `known_hosts`, **`/var/lib/tailscale/*`**, `.mpe_clock_*.json` (tempfiles), shell history, `machine-id` | `sanitize-for-clone.sh` before `dd`; capture script skips |
| **In private assets, not state** | Surge binary, factory/3rd-party patches, Quick Select tree | [`BACKUP_GUIDE.md`](BACKUP_GUIDE.md) / `deploy-all.sh` |
| **On Pi but empty / symlinked** | `~/.Surge Synth Team/Surge XT/Patches` → MPE-Library via deploy | Build script, not capture |
| **Optional / looper** | SooperLooper build under `~/src/sooperlooper-*` | `--with-looper` stub only; manual build |

**Not missing from capture:** SSH keys and **Tailscale credentials** — excluded on purpose (each unit enrolls fresh). WiFi — excluded (secrets).

Before optional `dd` imaging: `sudo ./scripts/provision/sanitize-for-clone.sh` runs `tailscale logout` and wipes `/var/lib/tailscale/*`.

---

## What is in external state

Canonical path list: [`config/platform/external-state-paths.list`](../config/platform/external-state-paths.list)

Includes `/etc/mpe/mpe.env`, `~/.patch_browser_*`, calibration logs/backups, `~/.local/share/Surge XT/`.

Player tuning defaults (when not restoring a full capture): [`config/platform/player-env-parity.env`](../config/platform/player-env-parity.env) via `apply-player-env-parity.sh`.

---

## Workflow D — build from assets (recommended, no `.img`)

1. Flash **Lite 64-bit** with Raspberry Pi Imager (SSH on, set username).
2. Ensure private assets repo is beside MPE-Module (`BACKUP_GUIDE.md`).
3. Set `config/mpe.env` (`PI_HOST`, `PI_USER`, `SSH_KEY`).
4. Run:

```bash
./scripts/image/build-pi4-appliance.sh \
  --git-ref main \
  --state state/raspberrypi2-2026-08-23
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
| [`apply-external-state.sh`](../scripts/provision/apply-external-state.sh) | Laptop or Pi `--local` | Restore `state/` tree |
| [`first-boot.sh`](../scripts/provision/first-boot.sh) | Pi (sudo) | Units, hygiene, parity env |
| [`build-pi4-appliance.sh`](../scripts/image/build-pi4-appliance.sh) | Laptop | **Primary:** Imager OS → deploy assets → first-boot → state |
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
- [ ] Git checkout is **`main`** (see `config/platform/appliance-git-ref`)

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
- Public image hosting (GPL + size)
