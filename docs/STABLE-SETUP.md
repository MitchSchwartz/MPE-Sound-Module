# Stable setup (3 commands)

## PC (once)

```bash
cd MPE-Module
cp config/mpe.env.example config/mpe.env   # set PI_USER — required, no default
./scripts/setup-windows-symlinks.sh
```

`config/mpe.env` is gitignored — machine-specific, never committed.

## Pi (once, when online)

Clone both repos under `$HOME` (code + optional private assets repo), push MPE-Module first, then:

```bash
# on Pi
cd ~/MPE-Module && git pull
./scripts/configure-pi-paths.sh --local --force

# from PC
cd MPE-Module
./scripts/setup-pi-symlinks.sh    # needs your assets repo cloned on Pi too
```

## Daily

```bash
cd ../mpe-assets && git add assets/user-data/Patches && git commit
cd ../MPE-Module && ./scripts/deploy-patches.sh
```

(`mpe-assets` = whatever you named your private repo; set `MPE_PERSONAL_REPO` if the path differs.)

## Rules

- **One loader:** `scripts/lib/paths.sh` (PC + Pi)
- **One env file per machine:** `config/mpe.env` (PC), `/etc/mpe/mpe.env` (Pi)
- **Pi runs from git clone only** — no loose copies of scripts in `$HOME`
- **Services** always point at `$MPE_MODULE_REPO/scripts/...`

See also: [PATHS.md](PATHS.md)
