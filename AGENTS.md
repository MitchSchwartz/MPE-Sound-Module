# MPE-Module — agent orientation

*Last updated: 2026-08-10 (America/Toronto)*

**Product:** Raspberry Pi MPE sound module (Surge XT headless + patch browser UI).

**Before looper / Phase 2 work:** [`Documents/DIRECTION.md`](Documents/DIRECTION.md) · [`Documents/DECISIONS.md`](Documents/DECISIONS.md) · OM-Repo [`GROUNDING.md`](https://github.com/opsMachine/OM-Repo/blob/main/internal/projects/mpe-synth-launch/GROUNDING.md)

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
| `mpe record [file] [fps]` | Touch UI screen capture |
| `mpe pull-videos [-o DIR] [--delete-source]` | Download demo videos |
| `mpe restart surge\|touch\|all` | Restart fixed systemd units |
| `mpe looper sl-clips [local\|pi]` | SooperLooper eval: generate 16 fixture WAVs (default: pi) |
| `mpe looper sl-smoke [local\|pi]` | SooperLooper eval: 16-loop load/trigger smoke (default: pi) |
| `mpe looper sl-restart [local\|pi]` | Restart SooperLooper on JACK + wire record path (default: pi) |

**Agent-safe (read-only):** `ping`, `status`, `logs`, `osc-check`, `diagnose`, `sysinfo`, `pull-videos` (skip `--delete-source` for zero writes), `looper sl-clips local` (fixture generation only).

**Writes / restarts:** `restart *`, `record`, `pull-videos --delete-source`, `looper sl-smoke`, `looper sl-restart` (restarts SooperLooper on Pi).

**Do not allowlist for agents:** `scp`/`rsync`, `deploy-all.sh`, `set-audio-profile.sh`, `set-surge-audio.sh`, `set-midi-sync.sh`, poweroff/reboot.

**Raw `ssh` — allowlisted since 2026-08-14, and that is not the same as encouraged.**
This line previously read "do not allowlist raw `ssh`." It was amended because
`ssh mitch@raspberrypi2.local *` **is** now in the local Claude Code allowlist,
and a rule the tooling contradicts is worse than no rule — it is the next stale
claim. Recording what is actually true instead:

| | |
|---|---|
| **Permission** | Broad. The wildcard matches any remote command, including destructive ones |
| **Policy** | Unchanged and narrow. Prefer an `mpe` subcommand every time one exists. `ssh` is for read-only diagnostics and eval/build tasks that have no subcommand |
| **Still forbidden regardless of the grant** | `deploy-all.sh`, `set-audio-profile.sh`, `set-surge-audio.sh`, `set-midi-sync.sh`, poweroff/reboot, and anything that writes to the appliance outside the SooperLooper eval scope below |
| **Still the right reflex** | Used `ssh` twice for the same fixed task? **Propose an `mpe` subcommand** (see below). `mpe rt`, `mpe looper sl-*` and friends all started this way |

The grant is a convenience for the SooperLooper evaluation. **Narrow it when the
eval closes**, alongside deleting the scoped-exception block below — otherwise an
appliance-wide remote-shell grant outlives the reason it was opened.

**Suggest new subcommands:** When you would SSH twice for the same fixed task, **propose a new `mpe` subcommand** in `mpe-cli` (name + behavior + allowlist strings) — do not improvise remote shell. **Editing `mpe-cli` or `~/.config/mpe/mpe.env` requires Mitch approval.**

### Scoped exception — SooperLooper evaluation (opened 2026-08-14, **expires at verdict**)

Building SooperLooper from an upstream tarball still has no `mpe` subcommand
(eval may be discarded). **`mpe looper sl-clips` / `sl-smoke`** wrap fixed repo
scripts only. Mitch-approved 2026-08-14, raw `ssh` remains permitted **for
build/eval tasks without a subcommand**, under these conditions:

| Rule | Why |
|---|---|
| Source tree lives at `~/src/sooperlooper-<version>` — **never under `~/MPE-Module`** | An untracked build tree inside a git checkout is the sweep hazard that cost us files on 2026-08-14. Thousands of files, outside any working tree |
| **Run in place** (`./src/sooperlooper …`). No `make install` during evaluation | Keeps the whole experiment reversible by deleting one directory |
| **`sudo apt install` remains Mitch-only** (step A1) | Unchanged — appliance package install is still a human gate |
| Capture the rollback **before** A1 | The reference Pi went green on Gate C on 2026-08-13. Record what A1 adds so removal is mechanical, not archaeological |
| No changes to systemd units, audio profile, `mpe.env`, or the repo working tree | The experiment adds a process beside a working Phase 1 stack; it does not modify the appliance |
| Results land on a **doc branch** (`docs/sooperlooper-eval`), not `dev` | A measurement without a verdict attached is a claim waiting to go stale |

**Still not delegated to an agent:** the ear tests. B2 (free-form record feel)
and **B10** (free-form vs grid-synced A/B) are Mitch's judgment and cannot be
faked from a terminal. B11/B12 an agent may execute, but "clean seam, no
audible click" is Mitch's call. Split the handoff by *mechanical vs
judgment*, not by session number.

**When the verdict lands, delete this block.** If SooperLooper is adopted,
packaging becomes a real problem (reproducible in CI and in the release
image) and gets solved properly rather than by extending this exception.

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
| **Phase 2 direction + locked decisions** | [`Documents/DIRECTION.md`](Documents/DIRECTION.md) · [`Documents/DECISIONS.md`](Documents/DECISIONS.md) |
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
