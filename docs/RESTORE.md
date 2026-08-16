# Appliance restore

**Purpose:** rebuild the instrument from a blank SD card. This document is the reason the appliance can be treated as expendable — see the decision recorded in [`racknerd-pi-access-spec.md`](racknerd-pi-access-spec.md) (§Decision C). If this procedure does not work, that decision is not valid.

*Last updated: 2026-08-16 (America/Toronto).*

> **⚠️ UNREHEARSED.** This procedure has been written from the live appliance's actual state but has **never been executed end to end**. An untested recovery path is an asserted one. Until someone reflashes and gets sound out, treat every time estimate below as a guess and the whole document as a draft.

---

## What is versioned, and what is not

| Component | Where it lives | Restored by |
|---|---|---|
| Application + scripts | This repo | `git clone` |
| **systemd units (14)** | [`systemd/`](../systemd/) | `scripts/install-units.sh` |
| Appliance config | `/etc/mpe/mpe.env` | Step 4 — **two keys only**, see below |
| Surge XT binary | `~/surge`, built from source | [`SURGE_ARM_BUILD.md`](SURGE_ARM_BUILD.md) — **hours** |
| sooperlooper | `~/src/sooperlooper-1.7.9` | Built from source |
| **Patch library** | `MPE-Library` (private repo) | `git clone` — **verify the Pi copy holds nothing extra** |
| **Touch calibration** | `~/surge-cli-calibration.log`, `~/.patch_browser_calibration_backups` | ❌ **NOT VERSIONED — back these up** |
| GitHub credentials | — | ❌ **None. Deliberate.** See [`PI-GITHUB-ACCESS.md`](PI-GITHUB-ACCESS.md) |

**The two gaps that will hurt:** touch calibration is device-specific and unversioned, and the Surge ARM build takes hours. Everything else is reproducible.

---

## Order

### 1. Base OS

Raspberry Pi OS, **Debian trixie, arm64**. Hostname `raspberrypi2`, user `mitch`.

The units hardcode `/home/mitch` in `ExecStart` and `WorkingDirectory` — `install-units.sh` refuses to run if that home does not exist rather than installing units that point nowhere.

### 2. Repo

```bash
git clone https://github.com/MitchSchwartz/MPE-Sound-Module.git ~/MPE-Module
cd ~/MPE-Module && bash scripts/install-git-hooks.sh
```

Public repo, HTTPS, **anonymous** — no credential needed or wanted.

### 3. Audio stack

Surge XT (see [`SURGE_ARM_BUILD.md`](SURGE_ARM_BUILD.md)) and sooperlooper 1.7.9. Budget hours, not minutes. Consider building on a faster machine and copying the artifacts if the instrument is needed sooner.

### 4. Config

```bash
sudo mkdir -p /etc/mpe
sudo tee /etc/mpe/mpe.env <<'EOF'
MPE_SURGE_SAMPLE_RATE=48000
EOF
```

**Do not restore `MPE_SURGE_BUFFER_SIZE`.** It is dead config under the JACK graph server — nothing reads it for the period, and the value on the live appliance (512) disagreed with what the server actually ran (256), which is where `LATENCY-SPIKE.md`'s stale headline came from.

The period comes from `MPE_JACK_BUFFER_DEFAULT` in `scripts/lib/audio-engine.sh`, overridable per-appliance with `MPE_JACK_BUFFER` in this file. Set it explicitly if this hardware wants something other than the default — measured good on the Sound Blaster Play! 3 is **256 × 3 @ 48 kHz, zero xruns**.

### 5. systemd units

```bash
sudo ./scripts/install-units.sh --dry-run   # review
sudo ./scripts/install-units.sh
```

Reproduces recorded enable state: 10 enabled, 3 deliberately disabled (`midi-clock-out`, `boot-animation`, `mic-to-uac2-bridge`), 1 static (`foot-pedal`). Installs but does **not** start.

### 6. Patch library

```bash
git clone git@github.com:MitchSchwartz/MPE-Library.git ~/MPE-Library
```

Private repo — needs a **read-only deploy key** ([`PI-GITHUB-ACCESS.md`](PI-GITHUB-ACCESS.md)), not a PAT.

> **Unresolved:** on the live appliance `~/MPE-Library` is a plain directory, **not a git checkout**, holding 279 MB. Whether it contains anything absent from the private remote is **unknown**. Resolve this before relying on this step — if the Pi copy is authoritative for any asset, that asset currently exists in exactly one place.

### 7. Calibration

Restore `~/surge-cli-calibration.log` and `~/.patch_browser_calibration_backups` from backup. **There is no backup mechanism yet.** Until there is, re-run touch calibration by hand.

### 8. Bring up and verify

```bash
sudo systemctl start mpe-jackd surge-xt-cli mpe-looper
```

From the laptop:

```bash
mpe jack status     # expect buffer/rate correct, xruns: 0
mpe rt status       # audio thread SCHED_FIFO — NOT `mpe sysinfo`, which reads
                    # process-level scheduling and false-negatives under JACK
mpe diagnose
```

Then play it. Automated checks do not cover the thing that matters.

---

## Drift check

Run periodically — the appliance changing without the repo changing is how a restore silently produces a different instrument:

```bash
sudo ./scripts/install-units.sh --diff
```

---

## Rehearsal log

| Date | Outcome | Wall-clock | Notes |
|---|---|---|---|
| — | **never performed** | — | Until this row is filled in, "the appliance is expendable" is a hope |
