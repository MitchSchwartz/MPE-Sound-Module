# Pi 4 clone SD — configured card, no Imager setup

*Last updated: 2026-08-23 (America/Toronto)*

**Use this when:** you want to hand someone (or yourself) an SD card that boots straight into a working MPE touch instrument — no Raspberry Pi Imager OS wizard, no laptop build script, no Surge compile.

> ### ⚠️ Handing this card to someone else is distribution
>
> The moment a card, `.img.xz`, or compiled binary leaves your hands — free, a loan, a
> friend, a bench spare for someone else's unit — **GPL-3.0 obligations attach** for Surge XT
> and every other GPL component on the image. Keeping it on your own boards is not
> distribution and owes nothing.
>
> **Before the card goes out:**
>
> ```bash
> sudo ./scripts/install-license-payload.sh          # licenses + corresponding source
> sudo ./scripts/install-license-payload.sh --verify # asserts provenance matches the binary
> sudo ./scripts/provision/sanitize-for-clone.sh --verify
> ```
>
> `first-boot.sh` installs the payload automatically on build-from-assets units. A **`dd`
> clone inherits whatever the master had** — so verify on the master before imaging.
>
> Patch content (CC0 / permissive packs) imposes no distribution obligation, but Surge
> factory content is unconfirmed. See [`THIRD-PARTY-NOTICES.md`](../THIRD-PARTY-NOTICES.md).

**Canonical alternative:** fresh Imager flash + [`build-pi4-appliance.sh`](../scripts/image/build-pi4-appliance.sh) from private assets — see [`PI4-GOLDEN-IMAGE.md`](PI4-GOLDEN-IMAGE.md) Workflow D.

**Status:** procedure documented; **first end-to-end rehearsal not done** — fill [`RESTORE.md`](RESTORE.md) rehearsal log when complete.

Appliance git branch: **`main`** ([`config/platform/appliance-git-ref`](../config/platform/appliance-git-ref)).

---

## Two paths (pick one)

| | **Clone SD** (this doc) | **Build from assets** |
|---|---|---|
| **You give** | Written SD card (or `.img.xz`) | Blank SD + laptop runs build |
| **Imager** | Not used (custom image or `dd` only) | Lite flash + SSH setup once |
| **Private assets on laptop** | Not needed at clone time | Required (`deploy-all.sh`) |
| **Player tuning** | **Baked in** by `dd` | Applied via `state/` tree |
| **Surge binary** | On the image | Copied from private assets |
| **Best for** | Bench clones, second unit, field spares | First unit, dev, when no master exists yet |

---

## What “baked in” means (no separate state apply)

A full-card `dd` copies the **entire root filesystem** from the reference Pi. These land on the clone automatically:

| On the card after clone | Path (examples) |
|---|---|
| Appliance config | `/etc/mpe/mpe.env` |
| Patch browser prefs | `~/.patch_browser_*.json`, `~/.patch_browser_*_backups/` |
| Calibration | `~/surge-cli-calibration.log`, normalization / pressure maps |
| Surge user defaults | `~/.local/share/Surge XT/` |
| Looper HUD prefs | `~/.mpe_sl_hud_state.json`, `~/.mpe_midi_clock_state.json`, … |
| Code + units | `~/MPE-Module` @ **`main`**, systemd units, hygiene, cmdline |
| Surge + patches | `~/surge/`, `~/MPE-Library/` (or symlinks) |
| **SooperLooper binary** | **Only if** built on master at `~/src/sooperlooper-*` before `dd` — **not** in build-from-assets automation |

You do **not** run `apply-external-state.sh` on a clone SD unless you deliberately built a **generic** image without tuning and want to push `state/` afterward.

`capture-external-state.sh` before imaging is an optional **laptop backup** of the same files — insurance, not part of the clone boot path.

### What sanitize removes (identity only)

[`sanitize-for-clone.sh`](../scripts/provision/sanitize-for-clone.sh) is a **separate** pre-`dd` step (not called by capture-golden). It strips:

- `/etc/machine-id` (regenerated on first boot)
- `/etc/ssh/ssh_host_*` (regenerated on first boot)
- `/var/lib/tailscale/*` + `tailscale logout`
- `/var/lib/mpe/first-boot.stamp`

It does **not** touch patch browser JSON, calibration, or `/etc/mpe/mpe.env` player keys.

### Never on the card

| Item | Why |
|---|---|
| Tailscale node credentials | Per-device enrollment — `sudo tailscale up` after boot |
| WiFi profiles | Contain PSKs — **stripped by `sanitize-for-clone.sh`** before imaging; reconfigure per site on each clone |
| SSH **host** keys | Regenerated on clone boot |
| Shell history | Truncated by `sanitize-for-clone.sh` before imaging |
| `~/.ssh/authorized_keys` | **Kept by default** so your laptop key still works; use `--strip-authorized-keys` on sanitize for a blank SSH slate |

Store the `.img.xz` **privately** — not on public GitHub (size + Surge GPL binary in the image).

---

## Part 1 — Create the master image (once)

On the **certified reference Pi** (`raspberrypi2` or successor):

### 1. Certify

- [ ] Sound + touch UI good on hardware you want to clone
- [ ] `MPE_UI_MODE=touch`, Sound Blaster Play! 3, SmartiPi DSI
- [ ] Hygiene applied (`apply-appliance-hygiene.sh` if unsure)
- [ ] **`main` pinned:**

```bash
cd ~/MPE-Module
git fetch origin
git checkout main
git pull
git rev-parse --short HEAD   # record in manifest
```

- [ ] Surge version noted: `~/surge/build/surge_xt_products/surge-xt-cli --version`

### 2. Optional — backup tuning to laptop

```bash
# on laptop
./scripts/provision/capture-external-state.sh
# → state/raspberrypi2-YYYY-MM-DD/
```

### 3. Sanitize + manifest (on Pi, sudo)

```bash
cd ~/MPE-Module
sudo ./scripts/image/capture-golden.sh --platform pi4
# or: sudo ./scripts/image/capture-pi4-golden.sh
sudo poweroff
```

This captures a local state backup, strips Tailscale/SSH host keys/machine-id, writes `artifacts/golden-pi4/IMAGE-MANIFEST.md`.

### 4. Image the SD (laptop, card in reader)

Replace `/dev/sdX` with the block device (not a partition).

```bash
sudo dd if=/dev/sdX of=~/mpe-pi4-golden-$(date +%Y%m%d).img bs=4M status=progress conv=fsync
xz -9 -T0 ~/mpe-pi4-golden-*.img
```

Verify:

```bash
./scripts/image/bake-golden.sh --platform pi4 verify
# or: ./scripts/image/bake-pi4-golden.sh verify
```

Store `*.img.xz` on an external drive or private bucket.

---

## Part 2 — Write a clone SD (every card)

No Imager OS configuration — only write the bits.

**Raspberry Pi Imager:** Choose OS → **Use custom** → select `mpe-pi4-golden-*.img` (decompress first) or use `xz` + `dd` below.

**Command line:**

```bash
xz -dc ~/mpe-pi4-golden-YYYYMMDD.img.xz | sudo dd of=/dev/sdX bs=4M status=progress conv=fsync
sync
```

Label the card (date + `main` SHA from manifest). Repeat for each spare.

---

## Part 3 — First boot on clone hardware

1. Insert SD, power on (same hardware class as reference: Pi 4 + SmartiPi + Sound Blaster).
2. Wait ~30s — touch browser should appear; audio graph starts via systemd.
3. **Per unit (if needed):**
   - WiFi: `nmtui` or Imager only helped the *master* — set on clone if different site
   - Tailscale: `sudo tailscale up` (fresh node — never cloned)
   - Hostname: `sudo hostnamectl set-hostname mpe-bench-2` if collisions matter on LAN
   - SSH: if you used `--strip-authorized-keys`, add your public key to `~/.ssh/authorized_keys`
4. **Play it** — automated checks do not cover the thing that matters.

### Same panel vs different panel

| Hardware | Calibration on clone |
|---|---|
| Identical SmartiPi + DAC setup | Normalization / pressure maps from master usually carry over |
| Different touch panel or DAC | Re-run touch calibration; do not assume baked maps are valid |

---

## Rehearsal checklist (required once)

Record in [`RESTORE.md`](RESTORE.md) when done:

| Step | Pass? |
|---|---|
| Master created from Pi on **`main`** | |
| `sanitize-for-clone.sh` ran (Tailscale absent on clone: `ls /var/lib/tailscale`) | |
| Blank SD written from `.img.xz` | |
| Clone boots without Imager setup | |
| Touch UI + sound without laptop deploy | |
| `mpe diagnose` or play test | |
| `sudo tailscale up` works as **new** node | |
| Wall-clock time (master + one clone write + boot) | |

Until this table is filled, “hand someone an SD” is still a hypothesis.

---

## Updating the master

When to rebuild the image:

- Meaningful **`main`** release (code + units)
- Surge binary revision
- Patch library / Quick Select overhaul
- Calibration baseline change on reference hardware

Workflow: certify reference → Part 1 again → new dated `mpe-pi4-golden-YYYYMMDD.img.xz`. Old images are rollback references only.

Day-to-day **code-only** updates on an existing clone (without re-imaging):

```bash
# on clone, as mitch
cd ~/MPE-Module && git checkout main && git pull
./scripts/configure-pi-paths.sh --local --force
sudo systemctl restart mpe-jackd surge-xt-cli touch-patch-browser
```

---

## Scripts (quick reference)

| Script | Role in clone SD path |
|---|---|
| [`capture-golden.sh`](../scripts/image/capture-golden.sh) | Pre-`dd` on master Pi (`--platform pi4\|pi5\|auto`) |
| [`capture-pi4-golden.sh`](../scripts/image/capture-pi4-golden.sh) | Wrapper → `--platform pi4` |
| [`sanitize-for-clone.sh`](../scripts/provision/sanitize-for-clone.sh) | Manual pre-`dd` step |
| [`capture-external-state.sh`](../scripts/provision/capture-external-state.sh) | Optional laptop backup only |
| [`bake-golden.sh`](../scripts/image/bake-golden.sh) | Verify manifest / print instructions |
| [`bake-pi4-golden.sh`](../scripts/image/bake-pi4-golden.sh) | Wrapper → `--platform pi4` |
| [`apply-external-state.sh`](../scripts/provision/apply-external-state.sh) | **Not used** for normal clone SD |
| [`build-pi4-appliance.sh`](../scripts/image/build-pi4-appliance.sh) | Alternate path — fresh Imager + assets |

---

## Related docs

- [`PI4-GOLDEN-IMAGE.md`](PI4-GOLDEN-IMAGE.md) — full matrix (build path, state paths, checklist)
- [`RESTORE.md`](RESTORE.md) — expendable-SD claim + rehearsal log
- [`BACKUP_GUIDE.md`](BACKUP_GUIDE.md) — private assets repo (build path)
- [`STORAGE-ROBUSTNESS.md`](STORAGE-ROBUSTNESS.md) — future `/state` partition
