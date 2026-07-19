# Stable setup (3 commands)

## PC (once)

```bash
cd MPE-Module
cp config/mpe.env.example config/mpe.env   # edit PI_USER if needed
./scripts/setup-windows-symlinks.sh
```

`config/mpe.env` is gitignored — yours has `PI_USER=mitch`.

## Pi (once, when online)

Clone both repos under `$HOME`, push MPE-Module first, then:

```bash
# on Pi
cd ~/MPE-Module && git pull
./scripts/configure-pi-paths.sh --local --force

# from PC
cd MPE-Module
./scripts/setup-pi-symlinks.sh    # needs MPE-Library cloned on Pi too
```

## Daily

```bash
cd ../MPE-Library && git add assets/user-data/Patches && git commit
cd ../MPE-Module && ./scripts/deploy-patches.sh
```

## Rules

- **One loader:** `scripts/lib/paths.sh` (PC + Pi)
- **One env file per machine:** `config/mpe.env` (PC), `/etc/mpe/mpe.env` (Pi)
- **Pi runs from git clone only** — no more `~/scripts/` copies
- **Services** always point at `$MPE_MODULE_REPO/scripts/...`

See also: [PATHS.md](PATHS.md)
