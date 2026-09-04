# Storage robustness — consumer product design

**Purpose:** define how MPE survives sudden power loss without SD corruption becoming a bricking event. This is the product-side complement to [`RESTORE.md`](RESTORE.md) (manual rebuild) and [`SHUTDOWN.md`](SHUTDOWN.md) (graceful shutdown).

*Last updated: 2026-08-20 (America/Toronto)*

> **Status: design — not implemented.** The live appliance still runs a full mutable Raspberry Pi OS on ext4. Atomic write helpers and tmpfs runtime state exist; immutable OS, A/B slots, and a dedicated state partition do not.

---

## Problem

Removable SD + ext4 + sudden power cut can corrupt metadata or leave half-written files. On a general-purpose Linux desktop that is annoying. On a consumer instrument it is unacceptable if the outcome is:

- will not boot
- black panel with no recovery path
- requires Mitch (or a laptop) to fix

The card is inherently corruptible. The product requirement is that corruption is **non-fatal**.

---

## Requirement reframe

| Outcome | Acceptable? |
|---------|-------------|
| Power cut during play → reboot → sound in under a minute | ✅ |
| Lose the loop currently recording | ✅ (expected) |
| Revert one prefs file to last good snapshot | ✅ |
| Touch calibration lost unless backed up | ⚠️ painful but bootable |
| OS root or home tree corrupted → no boot | ❌ |

**Design goal:** power loss may lose **in-flight work**; it must not lose **bootability**.

This aligns with the decision to treat the appliance as expendable: that only holds if recovery is fast and proven. **Immutable-release architecture** (build → artifact → atomic activation) is the work that retires the current restore-gap table.

---

## Current state (2026-08-20)

### Already mitigates single-file tears

| Mechanism | Where | Effect |
|-----------|-------|--------|
| Runtime engine/HUD state on **tmpfs** | `/run/mpe` (`RuntimeDirectoryPreserve=yes` on units) | Power loss clears volatile state; does not corrupt root |
| Atomic KEY=value writes | `mpe_state_write_atomic()` in `scripts/lib/audio-engine.sh` | Partial engine state files unlikely |
| Atomic JSON writes + fsync | `patch_browser/json_store.py` | Favorites, normalization, pressure maps resist truncate-on-write |
| Bounded shutdown + external splash | [`SHUTDOWN.md`](SHUTDOWN.md) | Graceful path reduces dirty unmount; does not help cable-yank |
| Corrupt Surge defaults backup | `scripts/surge-watchdog.sh` | Falls back instead of SEGV on bad XML |

### Still exposed on power cut

| Risk | Why it hurts |
|------|--------------|
| **Full mutable root** | systemd journal, `/var`, apt, logs write constantly during play |
| **User state in `$HOME`** | `~/.patch_browser_*.json`, `~/.local/share/Surge XT/`, calibration logs |
| **Looper WAV capture** | Large, long-duration writes mid-record are the worst case for half-written files |
| **Restore unrehearsed** | [`RESTORE.md`](RESTORE.md) has never been executed end to end — "expendable SD" is still asserted |

---

## Consumer stack — four layers

### Layer 1 — Stop writing the OS during normal play

**Read-only root** is the largest software lever. Standard embedded Linux layout:

| Partition | Mount at runtime | Contents |
|-----------|------------------|----------|
| **system A** (and **B**) | read-only | Kernel, units, application, Surge binary, patch library (immutable image) |
| **state** | read-write, small | User prefs, calibration, optional session exports only |
| **overlay / volatile** | tmpfs | `/var/log`, `/tmp`, disposable caches |

Boot succeeds because the OS image is not mid-write. Power cut may lose today's log line; it should not tear ext4 on `/`.

Pi paths: `overlayroot` / raspi-config read-only overlay, custom image, or Buildroot/Yocto for full control.

### Layer 2 — A/B firmware slots

Two identical system partitions. Updates write the **inactive** slot, verify checksum or signature, flip bootloader preference, reboot. If fsck fails on slot A, boot slot B automatically.

Same shape as the immutable-release pipeline: CI builds a signed artifact; activation is atomic; rollback is "boot the other slot."

### Layer 3 — User data contract

Classify writes by preciousness:

| Data | Power-cut behaviour | Strategy |
|------|---------------------|----------|
| Engine/HUD snapshot | Gone | tmpfs — correct (`/run/mpe`) |
| Favorites, normalization, pressure | One file may revert | Atomic JSON (done) + rolling `.bak` or append-only journal |
| Touch calibration | Device-specific loss | Live under `/state`; export-to-USB path; version in backup repo |
| Looper clip in progress | Lost take | Expected — do not fsync multi-MB WAV continuously; **commit on stop** (aligns with stop-then-weld) |
| Surge user defaults | Can crash loader if corrupt | Factory read-only fallback + watchdog backup (partial today) |

**Rule:** never truncate the only copy. Atomic rename protects one file; add a second generation for anything a customer would care about.

### Layer 4 — Hardware (production BOM)

Software cannot fully eliminate hard power loss on a rw state partition.

| Option | Role |
|--------|------|
| **Hold-up power** | Supercap or small battery on PMIC power-fail: ~0.5–2 s to `sync`, stop services, unmount `/state` |
| **eMMC / CM module** | Fewer connector failures than consumer microSD; better for gig vibration |
| **Graceful shutdown UX** | Required courtesy, not sufficient alone — see [`SHUTDOWN.md`](SHUTDOWN.md), [`POWER_BUTTON_SETUP.md`](POWER_BUTTON_SETUP.md) |

---

## Recommended path for MPE

Phased so bench/dev can continue on mutable SD until Phase 1 is ready to bake.

### Phase 1 — "Won't brick" (software, no BOM change)

1. **Immutable OS image from CI** — squashfs or ro root + tmpfs overlay for `/var`.
2. **Dedicated `/state` partition** — symlink or bind-mount all precious `$HOME` state:
   - `~/.patch_browser_*.json`
   - `~/.local/share/Surge XT/`
   - `~/surge-cli-calibration.log`, `~/.patch_browser_calibration_backups`
3. **Volatile or redirected journal** during performance (`Storage=volatile` or tmpfs logs; persist only for support captures).
4. **Boot health gate** — fsck `/state`; on failure mount empty factory tree or restore last `.bak`; **still boot to sound**.
5. **Rehearse restore** — fill the rehearsal log in [`RESTORE.md`](RESTORE.md) via [`PI4-GOLDEN-IMAGE.md`](PI4-GOLDEN-IMAGE.md); until then treat expendable-SD as a hypothesis.

### Phase 2 — "Updates don't kill it"

6. **A/B system slots** + signed image + `mpe update` writing inactive slot then rebooting.
7. **Factory reset** — flash known-good slot + wipe `/state` (optional USB export first).

### Phase 3 — "Hardware-grade" (production units)

8. Hold-up on 5 V input, or **CM4/eMMC** (or Pi 5 + industrial NVMe) for shipping BOM.
9. Looper scratch on **tmpfs or RAM disk** during record; flush to `/state` only on **stop**.

---

## What not to rely on

| Approach | Why insufficient |
|----------|------------------|
| Constant `sync` | Does not help between sync and unmount; adds latency spikes on an RT audio box |
| "Users must shut down properly" | Necessary UX, not a safety property |
| ext4 journaling alone | Helps metadata; does not save half-written large files or a corrupted writable `/` |
| Backup-only ([`BACKUP_GUIDE.md`](BACKUP_GUIDE.md)) | Laptop-side backup does not fix a bricked appliance in the customer's room |

---

## Mapping to existing paths

When `/state` lands, these move off the mutable root (today scattered under `/home/mitch`):

| Today | File / directory |
|-------|------------------|
| Patch browser prefs | `~/.patch_browser_favorites.json`, `~/.patch_browser_normalization.json`, `~/.patch_browser_pressure.json`, `~/.patch_browser_ui.json`, `~/.patch_browser_last_patch.json` |
| Looper HUD / clock | `~/.mpe_sl_hud_state.json`, `~/.mpe_midi_clock_state.json` |
| Surge | `~/.local/share/Surge XT/SurgeXTUserDefaults.xml` |
| Calibration | `~/surge-cli-calibration.log`, `~/.patch_browser_calibration_backups/` |
| Appliance config | `/etc/mpe/mpe.env` (may stay separate or join `/state` — TBD) |

Runtime-only (stay tmpfs — already correct):

| Path | Notes |
|------|-------|
| `/run/mpe/*` | Engine, jack, surge, snapshot, maintenance flag |
| Looper record scratch (Phase 3) | SooperLooper WAVs during capture — candidate for tmpfs until stop |

---

## Open questions

1. **Image builder** — overlayroot on stock Pi OS vs custom image vs Buildroot (cost vs control).
2. **`/etc/mpe/mpe.env`** — part of immutable image with overrides in `/state`, or fully in `/state`?
3. **MPE-Library** — bake into ro system partition vs mount read-only from second partition?
4. **Update channel** — USB stick vs network (Tailscale already on bench units; customer policy TBD).
5. **Acceptance test** — scripted "yank power during X" matrix (idle, recording loop, writing favorites, OTA write).

---

## Related docs

| Doc | Relationship |
|-----|--------------|
| [`RESTORE.md`](RESTORE.md) | Manual rebuild from blank SD — must stay valid; Phase 1 rehearsal is a gate |
| [`SHUTDOWN.md`](SHUTDOWN.md) | Graceful shutdown — reduces but does not replace ro root |
| [`PI-BOOT-RECOVERY.md`](PI-BOOT-RECOVERY.md) | Cmdline/config rollback when kernel still boots |
| [`BACKUP_GUIDE.md`](BACKUP_GUIDE.md) | Developer/laptop backup — not customer-facing recovery |
| [`Documents/specs/session-control-plane-spec.md`](../Documents/specs/session-control-plane-spec.md) | D6 — `/run/mpe` tmpfs snapshot design |
| [`Documents/DECISIONS.md`](../Documents/DECISIONS.md) | Locked engineering decisions |

---

## Summary

Consumer-grade MPE storage:

> **Immutable OS (A/B) + small rw state partition + tmpfs for ephemeral + atomic writes for prefs + hold-up or eMMC on production hardware.**

RT audio discipline (atomic writes, tmpfs runtime state) is largely in place. The product gap is **storage topology** — stop using the SD as a general-purpose Linux workstation — and **proven automatic recovery** (rehearsed restore, fallback boot).
