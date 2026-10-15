# Wiring a device to the personal library

Runbook for moving a device from "Surge-owned real directories" to "symlinks into a personal library checkout". Each step is idempotent and reversible, none restarts anything, and the device keeps playing throughout because the browser and Surge keep using the directories they already have open. Background and variables: [PATHS.md](PATHS.md). Backups: [BACKUP_GUIDE.md](BACKUP_GUIDE.md).

Steps marked **laptop** need no device access. Steps marked **device write** change the appliance and need its owner.

## Before anything

- **Laptop:** take a fresh capture and commit it ([BACKUP_GUIDE.md](BACKUP_GUIDE.md), `library-writeback.sh`). It must include the FX store and the device volume file; both are in the capture list.
- **Laptop:** clone the personal library beside `MPE-Module`, add the template as `upstream`, copy the device's live user patches into `assets/user-data/Patches` and commit + push. `setup-pi-symlinks.sh` refuses to link while the device holds a patch the checkout lacks, so this is checked for you, but do it first.

## On the device

All device commands are `scripts/library-migrate-device.sh <step>`, run on the device. Environment (not committed): `MPE_LIBRARY_PERSONAL_URL`, `MPE_LIBRARY_UPSTREAM_URL`.

| # | Step | Command | Reverse | Needs |
|---|------|---------|---------|-------|
| 3 | Credential for the personal repo on the device | arranged by the owner (read/write deploy key, force-push and deletion blocked on `main`) | remove the key | **device write**, human |
| 3 | Clone beside the old copy; add `upstream` | `clone --dir <new-dir>` | delete `<new-dir>` | **device write** (disk, ~450 MB) |
| 4a | Record patch counts and settings hits | `snapshot` | delete `~/.mpe-library-migration/before.json` | **device write** (one small file) |
| 4b | Dry run: checks only | `link --dir <new-dir> --dry-run` | nothing to undo | read-only |
| 4c | Point `MPE_PERSONAL_REPO` at the clone, then link | `link --dir <new-dir>` | `rollback` | **device write** (`/etc/mpe/mpe.env`, three links) |
| 4d | Compare against the snapshot | `verify` | read-only | |
| 4e | Restart Surge and the browser so they read through the links | by hand, when the player is not mid-song | n/a | **device write**, interrupts sound |
| 5 | Soak | | `rollback`, restart again | |
| 6 | Delete the old copy and the `*.backup.<ts>` dirs | `cleanup --old-copy <old-dir>` lists; add `--yes` to delete (refused unless the library checkout has nothing uncommitted under `assets/` and no commits missing from `origin/main`) | **not reversible** (the library repo is the archive) | **device write** |

What `link` does, in order: runs `setup-pi-symlinks.sh --local --dry-run` (a refusal stops everything, nothing changed), rewrites only `MPE_PERSONAL_REPO` in `/etc/mpe/mpe.env` (old file kept as `mpe.env.pre-library.<ts>`), makes the three links keeping their names (`patches_factory`, `patches_3rdparty`, `Patches`; the existing directories become `*.backup.<ts>`), then runs `verify`; if verify fails, `link` rolls itself back and exits non-zero. `link` records exactly what it changed (the backups it created and the env copy) in `~/.mpe-library-migration/link-manifest`. `rollback` undoes exactly those records, newest first, and restores the original `MPE_PERSONAL_REPO` value from the oldest env backup. Re-running either is harmless.

`verify` compares, per root, the patch count, and per store (FX, hold, pressure, normalization, metadata), how many keys land on a real patch. It exits non-zero if any count went down; a renamed link shows up as zero hits.

## Later, on the laptop

- Template: one delete-only commit removing the personal folders (`assets/appliance-state`, `assets/user-data` apart from the starter set) and the engine binary, plus `defaults/`.
- Personal library: `scripts/mpe-library absorb <strip-commit>` once, then `scripts/mpe-library update`.
- Before any second user: replace the template with a fresh repo with clean history (decided; not a history rewrite).

## Not done yet

- **On-device auto-commit** of patches and settings (event-triggered `mpe-library-commit` script and unit). Design: idle timer armed by the browser after a change, clean shutdown, user request; checks read-only state first; explicit paths only, never `git add -A` over the tree; never forced.
- Looper songs and clips in the repo through LFS.
- Starter favorites in the template, and the CC0 collections as a fourth scan root on the device (a browser change; would add patches to the list).
