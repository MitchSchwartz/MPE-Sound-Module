# Paths and configuration

Machine-specific paths are **not hardcoded** in scripts. Defaults assume:

- **Laptop:** `MPE-Module` (this repo) and a **personal library checkout** as siblings
- **Device:** Surge under `$HOME/surge`; the personal library checkout under `$HOME` once the device is wired to it

Override via `config/mpe.env` (copy from [`config/mpe.env.example`](../config/mpe.env.example)) or environment variables.

## Two library repos

| Role | What it is | Git remote name |
|------|------------|-----------------|
| **Template** | The clean default library every user starts from: stock Surge patches, CC0 collections, `defaults/`, a starter favorites set. Nothing from one person's device. | `upstream` (read-only) |
| **Personal library** | A full copy of the template with its history, plus that user's patches, settings snapshots (`settings/`) and device captures (`state/`). One per user. This is what `MPE_PERSONAL_REPO` points at. | `origin` (read/write) |

Names, URLs and accounts of those repos are never written into this repo: they come from the environment (`MPE_LIBRARY_UPSTREAM_URL`) or from the git remotes of your own checkout.

```
parent/
├── MPE-Module/            # this repo: code, docs, scripts
└── <personal-library>/    # your checkout (any folder name)
    ├── assets/
    │   ├── patches/patches_factory/                stock factory library
    │   ├── patches/third-party/patches_3rdparty/   stock third-party library
    │   ├── <CC0 collections>/
    │   └── user-data/Patches/                      your patches (favorites are pointers in the index, not files)
    ├── settings/                                   copies of the per-patch stores (see below)
    │   └── device/<device-id>/                     device-bound: DAC level, brightness, mpe.env
    ├── state/appliance-state/                      captures (never under assets/)
    └── .library/kept-template-versions.json        template versions your edits overrode
```

`MPE_PERSONAL_REPO` wins; otherwise the scripts look for a sibling folder named `MPE-Library`, `MPE-Personal` or `mpe-assets`, in that order. `patch_scanner.resolve_personal_repo()` and `scripts/lib/paths.sh` use the same list.

## What the device uses today

The patch browser scans three Surge-owned roots: `~/surge/resources/data/patches_factory`, `~/surge/resources/data/patches_3rdparty` and `~/Documents/Surge XT/Patches`. Until the device is wired to a library checkout they are **real directories**, and the library copy on the device (if any) is not read at runtime.

Wiring the device means replacing those three directories with symlinks into the checkout, **keeping the link names**: the stable key of every patch starts with a label derived from the root's *name* (`patches_factory` -> `factory`, `patches_3rdparty` -> `thirdparty`, anything else -> `user`), so a renamed link orphans every per-patch setting. The path below the root is part of the key too. Third-party patches sit one level deep: `patches_3rdparty` points at the library's `patches/third-party/patches_3rdparty`, so authors sit directly under the scanned root and keys read `thirdparty:<author>/...`. Devices deployed before 2026-10-15 have an extra level (`patches_3rdparty/patches_3rdparty/<author>`, keys `thirdparty:patches_3rdparty/<author>/...`); the link check accepts that layout when its single inner `patches_3rdparty/` matches the library, and linking flattens it, so those keys are re-keyed first (`scripts/mpe-library rekey-thirdparty`, see [`LIBRARY-MIGRATION.md`](LIBRARY-MIGRATION.md)). Empty directories on the device do not block a link; git cannot carry them, and they stay in the backup. [`scripts/setup-pi-symlinks.sh`](../scripts/setup-pi-symlinks.sh) does it and refuses if a live directory holds files the checkout lacks. The full sequence, with rollback, is in [`LIBRARY-MIGRATION.md`](LIBRARY-MIGRATION.md).

Settings JSON (`~/.patch_browser_*.json`) is **copied**, never symlinked: every store writes with `os.replace`, which swaps a symlink for a regular file on its first save. [`scripts/library-snapshot-settings.sh`](../scripts/library-snapshot-settings.sh) copies in either direction.

`assets/appliance-state/` and `state/` are never patch roots: the scanner skips them (device captures contain hundreds of backup `.fxp` copies).

## Environment variables

| Variable | Default | Used on |
|----------|---------|---------|
| `MPE_PERSONAL_REPO` | sibling library checkout (see above) | Laptop scripts and device runtime. Written to `/etc/mpe/mpe.env` by `configure-pi-paths.sh` |
| `MPE_LIBRARY_ORIGIN_REMOTE` | `origin` | Remote that backups push to |
| `MPE_LIBRARY_UPSTREAM_REMOTE` | `upstream` | Remote holding the template |
| `MPE_LIBRARY_UPSTREAM_REF` | `main` | Template branch that restore and update read |
| `MPE_LIBRARY_UPSTREAM_URL` | none | Clone URL of the template. Read by `configure-pi-paths.sh` and `library-migrate-device.sh` to add the `upstream` remote; never written to a file |
| `MPE_LIBRARY_PERSONAL_URL` | none | Clone URL of the personal library, for `library-migrate-device.sh clone` |
| `MPE_DEVICE_ID` | none | Names this device's folder under `settings/device/` (`library-snapshot-settings.sh`, `library-writeback.sh`) |
| `SURGE_XT_DIR` | `$HOME/Documents/Surge XT` | PC symlink setup |
| `PI_HOST` | set in `config/mpe.env` | PC → Pi SSH |
| `PI_USER` | **none — required** | PC → Pi SSH. No safe default: Raspberry Pi Imager makes you set a custom username per device, so this must be set in `config/mpe.env`. Scripts error clearly if it's unset. |
| `SSH_KEY` | `$HOME/.ssh/id_ed25519` or path in `mpe.env` | PC → Pi SSH |
| `PI_MPE_MODULE` | `$HOME/MPE-Module` on Pi | Pi clone path override |
| `PI_MPE_PERSONAL` | library checkout path on Pi | Pi clone path override |
| `MPE_MODULE_REPO` | script location / `$HOME/MPE-Module` | Pi runtime |
| `MPE_PERSONAL_REPO` | library checkout on Pi | Pi runtime |
| `MPE_SURGE_ROOT` | `$HOME/surge` | Pi runtime |
| `MPE_FAVORITES_NAME` | `!Quick Access` | **Patch browser UI** — quick-access folder under `~/Documents/Surge XT/Patches/`; **use leading `!`** to pin first. Curate on PC and deploy. See [`docs/PATCH_BROWSER_UI.md`](PATCH_BROWSER_UI.md). |
| `MPE_UI_MODE` | `oled` | **Which patch browser boots:** `oled` (encoder/OLED) or `touch` (SmartiPi). Applied by `configure-pi-paths.sh`. |
| `MPE_JACK_BUFFER` | `256` | **The only source of the JACK period**, in frames. Valid: 64, 128, 256, 512, 1024 (`mpe_buffer_env_canonical`). Deliberately does *not* fall back to `MPE_SURGE_BUFFER_SIZE` — that key is retired as a period (see [`RESTORE.md`](RESTORE.md)) and aliasing the two let stale config reassign the live period. *(Corrected 2026-08-17 — this said "Valid: 32–2048", which no validator ever accepted.)* |
| `MPE_JACK_PERIODS` | `3` | JACK periods per buffer (server-side). Valid: 2, 3, 4. Real output latency is `MPE_JACK_BUFFER × MPE_JACK_PERIODS`, which is what the MIDI output offset derives from. |
| `MPE_JACK_SOFTMODE` | `1` | `jackd -s`. On (default) a client that misses its deadline is tolerated — correct for a gig. Set `0` on the bench so jackd zombifies the offender and names it in the journal. |
| `MPE_PEAK_METER` | `0` | Live OUT meter via compiled `mpe-peak-meter` service (Phase 5). **Off by default** — set to `1`, then `systemctl enable --now mpe-peak-meter`. Enable only after `scripts/bench-xruns.sh --strict` passes. Build: `scripts/build-mpe-peak-meter.sh --required` (needs libjack-jackd2-dev). State includes `xruns=` (Q10 softmode counter). |
| `MPE_LIVE_MONITOR` | `0` | Live monitor gain stage via compiled `mpe-live-monitor` service — how loud you hear yourself, separate from what is recorded. **Off by default** — set to `1`, then `systemctl enable --now mpe-live-monitor`. Build: `scripts/build-mpe-live-monitor.sh --required` (needs libjack-jackd2-dev). It takes the direct `Surge XT:out_N -> system:playback_N` path out of the graph while it runs and restores it on clean stop. A crash cannot run that code, so `ExecStopPost=` restores it however the process died, and `sl-watchdog.py` repairs it from `live-monitor.state` (or `meter.state` when `MPE_PEAK_METER=1`) if both fail. Nothing covers power loss. Level policy: [`scripts/sooperlooper/live_monitor.py`](../scripts/sooperlooper/live_monitor.py). |
| `MPE_LIVE_MONITOR_CC` | `127` | What you hear yourself at when no track is capturing, as a 0–127 fader position. Unity by default, so an enabled monitor with nothing configured is inaudible. |
| `MPE_LIVE_MONITOR_PORT` | `9957` | UDP control port for the gain stage. Both sides read it; `live_monitor.DEFAULT_PORT` pins the Python default. |
| `MPE_LIVE_MONITOR_RAMP_MS` | `80` | Ramp time in ms for monitor level changes. The level moves while notes are sounding (every record start and stop), so a step would click. |
| `MPE_LIVE_MONITOR_VERIFY_AFTER_S` | `3.0` | How long a take may go unmentioned before the bench asks the engine whether it is still recording. SooperLooper reports state **on change**, not on a timer (measured against the real engine, 2026-09-20: one datagram in five seconds of a held take), so silence is not evidence a take ended — the monitor asks rather than assuming. |
| `MPE_LIVE_MONITOR_VERIFY_ATTEMPTS` | `3` | Unanswered questions before the take is treated as over and monitoring returns to the live level. More than one so a single lost datagram cannot end a take that is still recording. |
| `MPE_LIVE_MONITOR_VERIFY_TIMEOUT_S` | `1.5` | How long each of those questions may go unanswered before it is asked again. |

`MPE_AUDIO_ENGINE` is **retired** (spec amended 2026-08-13) — JACK is the only audio engine, so there is nothing left to select. A jackd that will not start is a hard failure (`state=failed`), not a route to an alternate engine.

## Runtime state (`/run/mpe`)

Written by jackd, Surge, and the supervisor. tmpfs — cleared on reboot (correct lifetime for cooldown counters).

| File | Writer | Contents |
|------|--------|----------|
| `engine.state` | Surge start, jackd start, watchdog | `engine` (always `jack`), `active` (jack/none), `state` (ok/recovering/failed — `degraded` retired), `reason`, `looper`, `updated` |
| `surge.state` | `start-surge-cli.sh` | `active` (jack/none), `device`, `started` |
| `jack.state` | `start-jackd.sh` | `device`, `period`, `periods`, `rate`, `started` |
| `engine-reconcile.state` | watchdog | Supervisor cooldown: `last_restart`, `restarts` |
| `jack-device` | `jackd-prestart.sh` | Selected `JACK_DEVICE=hw:N` for this start |
| `meter.state` | `mpe-peak-meter` | `peak_linear` and `clip` (post-FX: the fan-out source, FX host output while it carries; `clip=1` held 1.6 s after a sample ≥ 0.999), `wired` (Surge's outputs on the graph), `looper_client`, `looper_playback`, `surge_playback` (can you hear yourself play — Surge to playback, or the live monitor insert with Surge actually feeding it), `xruns`, `dsp_percent`, `updated` |
| `live-monitor.state` | `mpe-live-monitor` | `carrying` (audio traverses the insert end to end), `detached` (the direct Surge → playback path has been removed), `gain`, `surge_client`, `updated`. **Its staleness is the alarm:** the file stops being written when the insert dies, and `detached=1` in a stale file is how `sl-watchdog.py` knows the fail-open path was taken away by a process that is no longer there to give it back. |

Units declare `RuntimeDirectory=mpe` + `RuntimeDirectoryPreserve=yes` on `surge-xt-cli`, `mpe-jackd`, and `surge-watchdog` so sibling restarts do not wipe shared state.

Touch HUD reads `engine.state` via `patch_browser/audio_engine.py`.

Full list: [`config/mpe.env.example`](../config/mpe.env.example).

### `MPE_ENV_FILE` (tests only)

Subprocess unit tests set `MPE_ENV_FILE` so `paths.sh` does not read `/etc/mpe/mpe.env` on a configured Pi:

| Value | Behavior |
|-------|----------|
| unset | Appliance default — source `/etc/mpe/mpe.env` when present |
| empty (`MPE_ENV_FILE=`) | Hermetic — skip all env files; use process environment |
| path to file | Source that file only (temp profile for subprocess tests) |

Production systemd units never set this variable.

## Pi setup — reconfigure paths

When you first set up (or move) the Pi, **verify or reconfigure** where repos and Surge paths live:

1. **Clone** MPE-Module (default: `$HOME/MPE-Module`) and, when you want the library on the device, your personal library beside it. The personal library needs an `upstream` remote pointing at the template (`configure-pi-paths.sh` adds it when `MPE_LIBRARY_UPSTREAM_URL` is set).
2. **If paths differ**, create `/etc/mpe/mpe.env` on the Pi (or `~/.config/mpe/mpe.env`):
   ```bash
   sudo mkdir -p /etc/mpe
   sudo cp config/mpe.env.example /etc/mpe/mpe.env
   sudo nano /etc/mpe/mpe.env   # set MPE_MODULE_REPO, MPE_PERSONAL_REPO, MPE_PI_USER, etc.
   ```
3. **Install/reinstall systemd units** so `User=` and paths match your Pi user:
   ```bash
   cd MPE-Module
   ./scripts/configure-pi-paths.sh
   ```
   To change only where the library lives (no unit install, nothing restarted):
   `./scripts/configure-pi-paths.sh --local --personal-repo <path> --paths-only`. It rewrites one key and keeps the old file as `mpe.env.pre-library.<ts>`.
4. **Point Surge's patch roots at the library checkout** (on the device; add `--dry-run` first):
   ```bash
   ./scripts/setup-pi-symlinks.sh --local --dry-run
   ./scripts/setup-pi-symlinks.sh --local
   ./scripts/setup-pi-symlinks.sh --local --rollback   # undo
   ```
   Existing directories are renamed `*.backup.<ts>`, never deleted. Nothing is restarted unless you pass `--restart`.
5. **Restart services** after any path change:
   ```bash
   ssh $PI_USER@$PI_HOST 'sudo systemctl daemon-reload && sudo systemctl restart surge-xt-cli patch-browser'
   ```

If you previously had everything under one repo with patches in `MPE-Module/assets/`, recreate the links so they target the library checkout's `assets/` tree.

## PC quick start

```bash
cd MPE-Module
cp config/mpe.env.example config/mpe.env   # optional — edit PI_USER, PI_HOST, paths
./scripts/setup-windows-symlinks.sh
```

## Related docs

- [`docs/LIBRARY-MIGRATION.md`](LIBRARY-MIGRATION.md) — wiring a device to the personal library, step by step
- [`assets/README.md`](../assets/README.md) — where patches live
- [`docs/PATCH-EDITING-WORKFLOW.md`](PATCH-EDITING-WORKFLOW.md) — edit → commit → deploy
- [`docs/BACKUP_GUIDE.md`](BACKUP_GUIDE.md) — backups and write-back into your personal library
