# MPE-Module — agent orientation

*Last updated: 2026-08-10 (America/Toronto)*

**Product:** Raspberry Pi MPE sound module (Surge XT headless + patch browser UI).

---

## Pi CLI (`mpe`)

**Use the global `mpe` CLI** (separate [`mpe-cli`](https://github.com/MitchSchwartz/mpe-cli) repo) for laptop → Pi operations. Do not run raw `ssh`, `scp`, or `rsync` when a subcommand exists — Cursor allowlists fixed `mpe` entrypoints instead of open SSH.

Install once: clone `mpe-cli`, run `./install.sh`, edit `~/.config/mpe/mpe.env` (`PI_HOST`, `PI_USER`, `SSH_KEY`). **Do not embed the CLI in this repo.**

| Command | Purpose |
|---------|---------|
| `mpe ping` | Connectivity check |
| `mpe status` | Service active/enabled summary |
| `mpe logs surge\|touch\|watchdog [-n N]` | Recent logs (max 200 lines) |
| `mpe osc-check` | Surge OSC ports + process |
| `mpe diagnose` | Full read-only Pi diagnostics |
| `mpe sysinfo` | Board, kernel/preempt, EEPROM, CPU governor, Surge RT limits, buffer latency |
| `mpe test` | Run unit tests on laptop (`LOCAL_MPE_MODULE` or default clone) |
| `mpe test pi` | Run unit tests on the appliance via SSH |
| `mpe test list` | Show named test suites (`apc`, `looper`, `midi`, …) |
| `mpe test <suite>` | Run one suite on laptop (enum — see `mpe test list`) |
| `mpe test pi <suite>` | Run one suite on the appliance |
| `mpe midi-list` | Read-only USB + ALSA MIDI port snapshot |
| `mpe record [file] [fps]` | Touch UI screen capture |
| `mpe pull-videos [-o DIR] [--delete-source]` | Download demo videos |
| `mpe restart surge\|touch\|looper\|all` | Restart fixed systemd units |
| `mpe looper deploy [branch]` | Git pull on Pi + restart `mpe-looper.service`, and the patch browser UI unit when the pull changed `patch_browser/` (default: `yolo/looper-phase0`) |
| `mpe looper restart` | Restart `mpe-looper.service` only |

**Looper deploy (yolo branch):** after pushing looper code, run **`mpe looper deploy`** — do not wait for Mitch and do not use raw `ssh` or `./scripts/looper-deploy.sh` from the laptop. Latency budget: **512 Surge + 512 looper** (~1024 samples); do not recommend 1024+1024.

**The looper spans two processes.** `mpe-looper.service` runs the transport and writes `/dev/shm/mpe_looper_timing.json`; the **patch browser** unit (`touch-patch-browser.service`, or `patch-browser.service` on OLED builds) reads that file and draws the HUD. A `git pull` cannot change a running Python process, so **anything under `patch_browser/` stays inert until that unit restarts** — `mpe looper deploy` now handles this, but if you change HUD or UI code by any other route, restart the browser (`mpe restart touch`) before judging the result. Several HUD fixes were mistakenly read as ineffective because only the looper had been restarted.

**Unit tests:** always **`mpe test`** (or `mpe test pi looper`, etc.) — not `cd … && python3 -m unittest`. The CLI picks the repo, runs fixed suite enums from `mpe-cli/lib/test_suites.sh`, and matches the Cursor allowlist prefix. Suites: `mpe test list`.

**Agent-safe (read-only):** `ping`, `status`, `logs`, `osc-check`, `diagnose`, `sysinfo`, `test`, `midi-list`, `pull-videos` (skip `--delete-source` for zero writes).

**Writes / restarts:** `restart *`, `looper deploy|restart`, `record`, `pull-videos --delete-source`.

**Do not allowlist for agents:** raw `ssh`/`scp`/`rsync`, `./scripts/*.sh` for deploy/audio/profile (use `mpe` subcommands), `scripts/manual/*` hardware smokes unless a dedicated `mpe` wrapper exists, `deploy-all.sh`, `set-audio-profile.sh`, `set-surge-audio.sh`, `set-midi-sync.sh`, poweroff/reboot.

**Suggest new subcommands:** When you would SSH twice for the same fixed task, **propose a new `mpe` subcommand** in `mpe-cli` (name + behavior + allowlist strings) — do not improvise remote shell. **Editing `mpe-cli` or `~/.config/mpe/mpe.env` requires Mitch approval.**

Pattern: [OM-Repo `Docs/appliance-cli-pattern.md`](https://github.com/opsMachine/OM-Repo/blob/main/Docs/appliance-cli-pattern.md) · [`COMMANDS.md`](COMMANDS.md)

---

## Git workflow (read first)

**Canonical doc:** [`docs/GIT-WORKFLOW.md`](docs/GIT-WORKFLOW.md)

Hard rules for agents:

1. **Feature work → `dev`.** Do not merge to **`main`** until Mitch confirms Pi soak on `dev` (or explicitly says "merge to main" / "promote").
2. **Never push to `dev` and `main` in the same testing pass.** That bypasses integration testing and confuses what the Pi is running.
3. **Test on the Pi by checking out the branch** (`dev`, `yolo/*`, or PR head) — not by promoting to `main` first.
4. **Stable appliance:** Pi clone on **`main`**. After promotion, switch the Pi back to `main` and run `configure-pi-paths.sh --local --force`.
5. **Appliance env** (`/etc/mpe/mpe.env`) persists across branch switches — audio profile, UI mode, etc. are not wiped by git checkout.

---

## Pi deploy

| Action | Command (on Pi) |
|--------|------------------|
| Apply branch | `git fetch && git checkout <branch> && git pull && ./scripts/configure-pi-paths.sh --local --force` |
| Remote from PC | `scripts/configure-pi-paths.sh [--force]` (uses `PI_HOST` from `config/mpe.env`) |

Repo path on Pi: `~/MPE-Module` (override via `MPE_MODULE_REPO` in `/etc/mpe/mpe.env`).

---

## Key docs

| Topic | Doc |
|-------|-----|
| Git branches + Pi testing | [`docs/GIT-WORKFLOW.md`](docs/GIT-WORKFLOW.md) |
| Paths / env vars | [`docs/PATHS.md`](docs/PATHS.md) |
| USB desk tether (`usb-host`) | [`docs/USB-AUDIO-HOST.md`](docs/USB-AUDIO-HOST.md) |
| USB session record (`usb-host-session`) | [`docs/USB-SESSION-RECORD.md`](docs/USB-SESSION-RECORD.md) |
| Touch UI demo screen record | [`docs/TOUCH_PATCH_BROWSER.md`](docs/TOUCH_PATCH_BROWSER.md) · `mpe record` (mpe-cli) |
| Touch UI | [`docs/TOUCH_PATCH_BROWSER.md`](docs/TOUCH_PATCH_BROWSER.md) |
| Boot recovery | [`docs/PI-BOOT-RECOVERY.md`](docs/PI-BOOT-RECOVERY.md) |

---

## Tests

```bash
python3 -m unittest discover -s tests -q
```

Run before opening PRs to `dev`.
