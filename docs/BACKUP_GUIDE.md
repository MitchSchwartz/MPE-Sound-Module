# Backup guide

How the device's patches and settings are backed up, restored and reset. Path
and variable reference: **[PATHS.md](PATHS.md)**. Moving a device onto the git
workflow for the first time: **[LIBRARY-MIGRATION.md](LIBRARY-MIGRATION.md)**.

## What is backed up, and where

Everything goes into your **personal library** (a git checkout, `MPE_PERSONAL_REPO`), so history is the archive.

| What | Where in the library | Moves by |
|------|----------------------|----------|
| Your patches and favorites copies | `assets/user-data/Patches/` | The device's symlink into the checkout, once wired. Until then: `pull-all-from-device.sh`, `sync-from-device.sh`, `backup-quick-select.sh` |
| Per-patch settings and UI prefs (FX, hold, pressure, normalization, instrument tags, favorites index, UI, volume, Surge defaults, looper HUD state) | `settings/` | Copied, not linked ([PATHS.md](PATHS.md)) |
| Device-bound settings (DAC level, brightness, `mpe.env`) | `settings/device/<device-id>/` | Copied |
| Full dated capture of the device (config, calibration backups, drop-ins) | `state/appliance-state/captures/<date>/` | `capture-external-state.sh` + `archive-state-to-assets.sh` |
| Template versions your edits overrode | `.library/kept-template-versions.json` | `mpe-library update` |

Not backed up: runtime-only files (poly state, live pressure, last patch, watchdog and clock scratch, `/run/mpe/*`) and the SSH keys. The looper's saved songs and clips are in the dated capture on the laptop but `library-writeback.sh` leaves them out of the git commit (tens of MB of audio; they wait for LFS, or pass `--with-looper`). The Surge binary is not stored in the template.

One file list drives the capture and the settings copy: [`config/platform/external-state-paths.list`](../config/platform/external-state-paths.list).

## A backup never changes the device

Taking a capture is read-only on the appliance. `capture-external-state.sh` (laptop mode) does not touch the device's git checkout and leaves no files there: the device builds the capture in RAM and streams it back as a tar. If the device's checkout is too old to do that, the script says so and stops; updating the checkout is a deliberate act of its own.

## Weekly write-back (laptop)

```bash
cd MPE-Module
./scripts/library-writeback.sh --device-id <device-id>        # add --push to push
```

That runs, in order: the read-only capture, `library-snapshot-settings.sh` (captured settings into `settings/`, credential scan), `archive-state-to-assets.sh` (capture tree into `state/appliance-state/captures/<date>/`), then one commit that adds `settings/` and `state/` only (without the looper audio). `--no-capture` reuses the newest capture already under `state/`. The script commits nothing else and never forces a push.

The steps can be run alone:

```bash
MPE_CLI_CONFIG=~/.config/mpe/mpe.env ./scripts/provision/capture-external-state.sh
./scripts/library-snapshot-settings.sh --from state/<capture> --device-id <device-id>
./scripts/provision/archive-state-to-assets.sh state/<capture>
```

Patch edits are not in a capture. Once the device is wired to its checkout they sit in that working tree (`git -C <checkout> status` on the device shows them). **On-device auto-commit is not built yet**: until it is, commit and push from the device by hand, or pull the files with `sync-from-device.sh`. Auto-commit is a follow-up, designed to run on events (idle after a change, clean shutdown, user request) and never in a loop.

## Restore to default

`scripts/mpe-library` (needs `MPE_PERSONAL_REPO` or `--repo`, and the `upstream` remote). **Stop the patch browser first** if you will copy the result onto the device: it holds its stores in memory and would write them back.

`settings/` is only as fresh as the last write-back, so `restore-patch` and `factory-reset` take the device's live stores first: pass `--live-home <device home>` and they are copied into `settings/` before the backup commit (so the backup holds what the device has now). `--no-live-snapshot` accepts a stale `settings/` explicitly; with neither flag the command refuses.

| Command | Does |
|---------|------|
| `mpe-library restore-patch <stable_key>` | One patch back to the template's version; clears that patch's user settings (FX, hold, pressure trim, normalization trim, instrument tag). Calibrated baselines stay. |
| `mpe-library restore-stock` | All stock patches and CC0 collections back to the template. Your patches and settings are untouched. |
| `mpe-library factory-reset` | Stock, your patches (back to the template's starter set) and the per-patch settings (back to the template's `defaults/`, else empty). |
| `mpe-library undo <tag>` | Puts back what a restore replaced: only the paths that backup covered. Like every restore it first makes a backup commit, tag and push of those paths, so edits made since the restore are kept. Refuses if tracked files outside those paths have uncommitted changes (`--force` to override; they are never touched either way). |

Every restore mode does this before it changes anything, in this order:

1. a backup commit of what is about to change (its message lists the paths),
2. a tag `pre-restore/<UTC timestamp>` on it,
3. a push of the branch and the tag (never forced).

If the push fails, nothing is restored (pass `--allow-offline` to go ahead anyway; the commit and tag stay local). **Undo:** `mpe-library undo pre-restore/<timestamp>`. Do not use `git checkout <tag> -- .`: it rewrites every file in the checkout, including uncommitted device edits. The tag also survives a lost device because it was pushed.

Then apply the settings to the device: stop the browser **and Surge**, `./scripts/library-snapshot-settings.sh --in --device-id <device-id>`, start them. Every live file that copy replaces is first kept under `<home>/.mpe-library-backups/<timestamp>/`. The copy refuses while either service is running.

## Pulling template updates

```bash
scripts/mpe-library update
```

Fetches `upstream` and merges it into the personal library. **Your edits win every conflict.** Patches (`.fxp`) are binary and git cannot merge them, so a patch changed on both sides keeps your version and the template's version is recorded as a git blob reference in `.library/kept-template-versions.json` (`path`, `upstream_commit`, `blob`). No template copy is written into the tree: any `.fxp` under a scan root would show up in the browser as a duplicate patch. A patch the template withdrew is deleted only if you never modified it. Re-running when nothing is new changes nothing.

`mpe-library absorb <commit>` is for the one-time strip commit the template makes when it drops the personal folders: merging it normally would delete those folders in your library, so absorb it with `merge -s ours` once, before the next update.

## Disaster recovery

Fresh SD card or image, then, from the laptop:

1. Deploy the code and units: [RESTORE.md](RESTORE.md), `scripts/deploy-all.sh`.
2. Re-create the library on the device and wire it ([LIBRARY-MIGRATION.md](LIBRARY-MIGRATION.md)), or push patches with `scripts/deploy-patches.sh` until it is.
3. Apply captured state: `scripts/provision/apply-external-state.sh --state state/<capture>`, or `library-snapshot-settings.sh --in`.

## Troubleshooting

**Cannot reach the device.** `PI_HOST`, `PI_USER` and `SSH_KEY` come from `config/mpe.env` or the environment; there are no built-in defaults for the user.

**Capture refuses: "cannot take a read-only capture".** The device's MPE-Module checkout predates streaming capture. Update it the usual way; the capture script will not do it for you.

**`deploy-patches.sh` refuses, and `deploy-all.sh` skips its patch steps.** The patch roots are links into the library (or could not be checked), and extracting a tarball over a link would detach it. `deploy-all.sh` still deploys code, units and the binary. Update patches through the library (`git pull` in the device's checkout, or `mpe-library update`).

**`setup-pi-symlinks.sh` refuses.** A live directory has files the checkout lacks or different versions of them. Copy those into the checkout, commit, run again. Nothing was changed.

**Push is slow the first time.** The library is about 450 MB; later pushes send only changes.
