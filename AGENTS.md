# MPE-Module — agent orientation

*Last updated: 2026-08-17 (America/Toronto)*

**Product:** Raspberry Pi MPE sound module (Surge XT headless + patch browser UI).

**Before looper / Phase 2 work:** [`Documents/DIRECTION.md`](Documents/DIRECTION.md) · [`Documents/DECISIONS.md`](Documents/DECISIONS.md) · OM-Repo [`GROUNDING.md`](https://github.com/opsMachine/OM-Repo/blob/main/internal/projects/mpe-synth-launch/GROUNDING.md)

---

## 🔊 Audio output safety — read before making sound

**Headphones or speakers may be connected, and Mitch may be wearing them.** You cannot tell from the appliance. A loud transient into headphones on someone's head causes permanent hearing damage, and can destroy a driver instantly. This is the one failure on this project that cannot be rolled back.

**The rule:**

1. **Use the quietest level that proves the thing.** Verifying that audio is flowing needs far less level than "sounds good." Default to barely audible and raise only if the test genuinely requires it.
2. **Above 50% output, stop and ask.** Explicitly check in with Mitch before any test that exceeds it, and say what you intend to run. Do not infer consent from an earlier "go ahead" — his headphones may be on now and were not before.
3. **Never raise a level to diagnose silence.** If you expect sound and hear none, the cause is almost always routing, a stopped service, or a wrong device — not gain. Turning it up to find out is exactly how the damage happens. Check `mpe-yolo jack-status`, `osc-check`, and the unit states first.
4. **Restore any level you change**, and say in your summary that you changed it.

**There are three gain stages in series.** A level that is safe at one is not safe end to end:

| Stage | Control |
|---|---|
| Surge patch output | OSC `/param/a/amp/volume`, `/param/b/amp/volume` (UDP 53280) |
| Looper | `MPE_SL_LOOP_GAIN`, `MPE_SL_LOOP_GAIN_LAW` |
| Hardware mixer | `MPE_DAC_VOLUME_DB` in `/etc/mpe/mpe.env` → `scripts/set-dac-volume.sh` (`amixer` on Sound Blaster **Speaker**) |

**Hardware output is the Sound Blaster Play! 3** (card index varies by hotplug — scripts detect by name). The playback control is **`Speaker`** — there is no `PCM` control on this card. Scale **0–88 raw**, dB ≈ `(raw − 88) × 0.5`. Appliance default: **`MPE_DAC_VOLUME_DB=-6`** (raw **76**). Previous default was 48 (−20 dB).

Treat the **dB figure as the real number**, not the percentage — `amixer`'s percentage is not perceived loudness. Read or set via:

```bash
./scripts/set-dac-volume.sh --show
./scripts/set-dac-volume.sh   # applies /etc/mpe/mpe.env
```

**Loops sum.** A per-loop gain that is fine alone is not fine with 16 playing at once. Bring level up *after* the loops are running, never before.

**You cannot infer the output device.** Cards 0 (Pi headphone jack), 2 and 3 (HDMI) also exist. Which one reaches Mitch's ears is not visible from the appliance — assume the worst case.

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

## Nerdrack YOLO (Claude Code)

**Runner:** `scripts/yolo/claude-yolo.sh` on nerdrack (`claudeLogin` / `claude-yolo-mpe` SSH alias) — **not** Cursor `agent-yolo.sh`.

| Stage | Where | What |
|---|---|---|
| Spec / Gate A | **Laptop** (sync with Mitch) | Spec `Status: Approved` |
| Mitch gates | **Laptop** | `pi_soak`, `systemd_change`, `audio_profile`, `mpe_env` via `enqueue-yolo-task.sh clear-gate` |
| Enqueue | **Laptop** | `enqueue-yolo-task.sh add` → `approve --id` |
| Build / PR | **Nerdrack** | `YOLO_TASK_ID=… claude-yolo.sh -p "…"` |
| Pi soak / deploy | **Laptop / Mitch** | Pi is LAN-only — nerdrack runs **unit tests only** |

Full setup: [`docs/local-vs-nerdrack-dev.md`](docs/local-vs-nerdrack-dev.md). Queue: `.claude/primitives/yolo-queue.json`.

**Nerdrack must not:** `deploy-all.sh`, audio profile scripts, `mpe restart`, Pi SSH/SCP, merge without independent review.

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

## Pi deploy — appliance only, never a dev workspace

**Hard rule (2026-08-17):** The Pi is a **read-only deploy target**. All commits, branches, stashes, and WIP live on the **laptop** (or nerdrack). Do not SSH in to edit, commit, stash, or create branches on the appliance.

| Pi state | Expected |
|----------|----------|
| Branch | **`main` only** — no local feature/`yolo/*` branches |
| Push | **`origin` push URL = `DISABLED`** — pulls only |
| GitHub auth | **None** — public repo pulls anonymously ([`docs/PI-GITHUB-ACCESS.md`](docs/PI-GITHUB-ACCESS.md)) |
| Working tree | **Clean** — no uncommitted changes, no stashes |

**Deploy from the laptop** — never “finish work on the Pi”:

| Action | Command |
|--------|---------|
| Apply `main` on Pi | `scripts/configure-pi-paths.sh [--force]` (uses `PI_HOST` from `config/mpe.env`) |
| Soak a feature branch | Laptop: merge to `dev`, then `ssh … 'cd ~/MPE-Module && git fetch && git checkout dev && git pull && ./scripts/configure-pi-paths.sh --local --force'` — **return Pi to `main` after soak** |

Repo path on Pi: `~/MPE-Module` (override via `MPE_MODULE_REPO` in `/etc/mpe/mpe.env`).

**Archived Pi-only work:** `archive/yolo-looper-phase0-pi-snapshot` on origin (73 commits from retired `yolo/looper-phase0`; SooperLooper superseded it).

---

## Key docs

| Topic | Doc |
|-------|-----|
| **Code map (function-level, boot/lifecycle)** | [`docs/CODE-MAP.md`](docs/CODE-MAP.md) |
| **Phase 2 direction + locked decisions** | [`Documents/DIRECTION.md`](Documents/DIRECTION.md) · [`Documents/DECISIONS.md`](Documents/DECISIONS.md) |
| Git branches + Pi testing | [`docs/GIT-WORKFLOW.md`](docs/GIT-WORKFLOW.md) |
| Paths / env vars | [`docs/PATHS.md`](docs/PATHS.md) |
| USB desk tether (`usb-host`) | [`docs/USB-AUDIO-HOST.md`](docs/USB-AUDIO-HOST.md) |
| USB multichannel stems (design) | [`docs/USB-MULTICHANNEL-STEMS.md`](docs/USB-MULTICHANNEL-STEMS.md) |
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
