# Paths and configuration

Machine-specific paths are **not hardcoded** in scripts. Defaults assume:

- **PC:** `MPE-Module` and a private **assets repo** cloned as **siblings**
- **Pi:** same two repos under `$HOME`, Surge build under `$HOME/surge`

Override via `config/mpe.env` (copy from [`config/mpe.env.example`](../config/mpe.env.example)) or environment variables.

## Repo layout (relative)

```
parent/                    # e.g. ~/GitHub
├── MPE-Module/            # this repo — code, docs, scripts
└── mpe-assets/            # private — your choice of folder/repo name
    └── assets/
        ├── user-data/Patches/
        ├── patches/
        └── binaries/
```

Deploy/sync scripts resolve `../mpe-assets`, `../MPE-Library`, or `../MPE-Personal` automatically (legacy names still work).

## Environment variables

| Variable | Default | Used on |
|----------|---------|---------|
| `MPE_PERSONAL_REPO` | sibling assets repo (see paths.sh) | PC deploy/sync |
| `SURGE_XT_DIR` | `$HOME/Documents/Surge XT` | PC symlink setup |
| `PI_HOST` | set in `config/mpe.env` | PC → Pi SSH |
| `PI_USER` | **none — required** | PC → Pi SSH. No safe default: Raspberry Pi Imager makes you set a custom username per device, so this must be set in `config/mpe.env`. Scripts error clearly if it's unset. |
| `SSH_KEY` | `$HOME/.ssh/id_ed25519` or path in `mpe.env` | PC → Pi SSH |
| `PI_MPE_MODULE` | `$HOME/MPE-Module` on Pi | Pi clone path override |
| `PI_MPE_PERSONAL` | assets repo path on Pi | Pi clone path override |
| `MPE_MODULE_REPO` | script location / `$HOME/MPE-Module` | Pi runtime |
| `MPE_PERSONAL_REPO` | assets repo on Pi | Pi runtime |
| `MPE_SURGE_ROOT` | `$HOME/surge` | Pi runtime |
| `MPE_FAVORITES_NAME` | `!Quick Access` | **Patch browser UI** — quick-access folder under `~/Documents/Surge XT/Patches/`; **use leading `!`** to pin first. Curate on PC and deploy. See [`docs/PATCH_BROWSER_UI.md`](PATCH_BROWSER_UI.md). |
| `MPE_UI_MODE` | `oled` | **Which patch browser boots:** `oled` (encoder/OLED) or `touch` (SmartiPi). Applied by `configure-pi-paths.sh`. |
| `MPE_JACK_BUFFER` | `256` | JACK period in frames (server-side; distinct from `MPE_SURGE_BUFFER_SIZE`). Valid: 32–2048 (see `audio-engine.sh`). |
| `MPE_JACK_PERIODS` | `3` | JACK periods per buffer (server-side). Valid: 2, 3, 4. |

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

Units declare `RuntimeDirectory=mpe` + `RuntimeDirectoryPreserve=yes` on `surge-xt-cli`, `mpe-jackd`, and `surge-watchdog` so sibling restarts do not wipe shared state.

Touch HUD reads `engine.state` via `patch_browser/audio_engine.py`.

Full list: [`config/mpe.env.example`](../config/mpe.env.example).

## Pi setup — reconfigure paths

When you first set up (or move) the Pi, **verify or reconfigure** where repos and Surge paths live:

1. **Clone both repos** where you want them (default: `$HOME/MPE-Module` + assets repo beside it).
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
4. **Point Surge patch dirs at your assets repo** (symlinks or copies):
   ```bash
   # From PC — set PI_MPE_PERSONAL if not in default location
   export PI_MPE_PERSONAL=/your/path/mpe-assets   # optional
   ./scripts/setup-pi-symlinks.sh
   ```
5. **Restart services** after any path change:
   ```bash
   ssh $PI_USER@$PI_HOST 'sudo systemctl daemon-reload && sudo systemctl restart surge-xt-cli patch-browser'
   ```

If you previously had everything under one repo with patches in `MPE-Module/assets/`, symlinks on the Pi must be **recreated** to target your assets repo's `assets/` tree instead.

## PC quick start

```bash
cd MPE-Module
cp config/mpe.env.example config/mpe.env   # optional — edit PI_USER, PI_HOST, paths
./scripts/setup-windows-symlinks.sh
```

## Related docs

- [`assets/README.md`](../assets/README.md) — where patches live
- [`docs/PATCH-EDITING-WORKFLOW.md`](PATCH-EDITING-WORKFLOW.md) — edit → commit → deploy
- [`docs/BACKUP_GUIDE.md`](BACKUP_GUIDE.md) — pull/sync backups into your assets repo
