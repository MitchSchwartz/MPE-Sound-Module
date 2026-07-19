# Paths and configuration

Machine-specific paths are **not hardcoded** in scripts. Defaults assume:

- **PC:** `MPE-Module` and `MPE-Library` cloned as **siblings**
- **Pi:** same two repos under `$HOME`, Surge build under `$HOME/surge`

Override via `config/mpe.env` (copy from [`config/mpe.env.example`](../config/mpe.env.example)) or environment variables.

## Repo layout (relative)

```
parent/                    # e.g. ~/GitHub
├── MPE-Module/            # this repo — code, docs, scripts
└── MPE-Library/          # private — assets/
    └── assets/
        ├── user-data/Patches/
        ├── patches/
        └── binaries/
```

Deploy/sync scripts in `MPE-Module/scripts/` resolve `../MPE-Library` automatically.

## Environment variables

| Variable | Default | Used on |
|----------|---------|---------|
| `MPE_PERSONAL_REPO` | `../MPE-Library` | PC deploy/sync |
| `SURGE_XT_DIR` | `$HOME/Documents/Surge XT` | PC symlink setup |
| `PI_HOST` | `surge.local` | PC → Pi SSH |
| `PI_USER` | **none — required** | PC → Pi SSH. No safe default: Raspberry Pi Imager makes you set a custom username per device, so this must be set in `config/mpe.env`. Scripts error clearly if it's unset. |
| `SSH_KEY` | `$HOME/.ssh/surge_pi_key` | PC → Pi SSH |
| `PI_MPE_MODULE` | `$HOME/MPE-Module` on Pi | Pi clone path override |
| `PI_MPE_PERSONAL` | `$HOME/MPE-Library` on Pi | Pi clone path override |
| `MPE_MODULE_REPO` | script location / `$HOME/MPE-Module` | Pi runtime |
| `MPE_PERSONAL_REPO` | `$HOME/MPE-Library` | Pi runtime |
| `MPE_SURGE_ROOT` | `$HOME/surge` | Pi runtime |
| `MPE_FAVORITES_NAME` | `!Quick Access` | **Patch browser UI** — folder under `~/Documents/Surge XT/Patches/`; **use leading `!`** to pin first. On-device 2s-hold copy target. See [`docs/PATCH_BROWSER_UI.md`](PATCH_BROWSER_UI.md). |

Full list: [`config/mpe.env.example`](../config/mpe.env.example).

## Pi setup — reconfigure paths

When you first set up (or move) the Pi, **verify or reconfigure** where repos and Surge paths live:

1. **Clone both repos** where you want them (default: `$HOME/MPE-Module`, `$HOME/MPE-Library`).
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
4. **Point Surge patch dirs at MPE-Library** (symlinks or copies):
   ```bash
   # From PC — set PI_MPE_PERSONAL if not in $HOME/MPE-Library
   export PI_MPE_PERSONAL=/your/path/MPE-Library   # optional
   ./scripts/setup-pi-symlinks.sh
   ```
5. **Restart services** after any path change:
   ```bash
   ssh $PI_USER@$PI_HOST 'sudo systemctl daemon-reload && sudo systemctl restart surge-xt-cli patch-browser'
   ```

If you previously had everything under one repo with patches in `MPE-Module/assets/`, symlinks on the Pi must be **recreated** to target `MPE-Library/assets/` instead.

## PC quick start

```bash
cd MPE-Module
cp config/mpe.env.example config/mpe.env   # optional — edit PI_USER, paths
./scripts/setup-windows-symlinks.sh
```

## Related docs

- [`assets/README.md`](../assets/README.md) — where patches live
- [`docs/PATCH-EDITING-WORKFLOW.md`](PATCH-EDITING-WORKFLOW.md) — edit → commit → deploy
- [`docs/BACKUP_GUIDE.md`](BACKUP_GUIDE.md) — pull/sync backups into MPE-Library
