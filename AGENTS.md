# MPE-Module — agent orientation

*Last updated: 2026-08-22 (America/Toronto)*

**Product:** Raspberry Pi MPE sound module (Surge XT headless + patch browser UI).

**Before looper / Phase 2 work:** [`Documents/DIRECTION.md`](Documents/DIRECTION.md) · [`Documents/DECISIONS.md`](Documents/DECISIONS.md) · OM-Repo [`GROUNDING.md`](https://github.com/opsMachine/OM-Repo/blob/main/internal/projects/mpe-synth-launch/GROUNDING.md)

**Before adding any polling loop, watchdog tick, or timer:** CPU is the scarcest resource
on this appliance — a `python3` fork is ~400 ms on the Pi, so once every 5 s is **9% of a
core, forever**. Compute cost × cadence and put it in the PR. Rules and measured constants:
[`Documents/DECISIONS.md`](Documents/DECISIONS.md) § *2026-08-18 — CPU is the scarcest resource*.

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

**Hardware output is the Sound Blaster Play! 3** (card index varies by hotplug — scripts detect by name). The playback control is **`Speaker`** — there is no `PCM` control on this card. Scale **0–88 raw**, dB ≈ `(raw − 88) × 0.5`. Appliance default: **`MPE_DAC_VOLUME_DB=-12`** (raw **64**). Previous defaults: 76 (−6 dB), 48 (−20 dB).

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
| **CPU budget / no forks in loops** | [`Documents/DECISIONS.md`](Documents/DECISIONS.md) § *2026-08-18* |

---

## Tests

```bash
python3 -m unittest discover -s tests -q
```

Run before opening PRs to `dev`.

### Never ask Mitch to run a test you could have run yourself

If a check does not require his hands or his ears, run it. Do not stage it, describe
it, or ask him to trigger it — run it, read the result, and report the number.

He is needed for exactly three things:

- **playing the instrument** — feel, per-patch behaviour, anything musical (B10, criterion 44);
- **hearing it** — crackle, timbre, whether a change sounds right;
- **decisions** — enabling a unit by default, accepting a tradeoff, scope.

Everything else is yours: unit tests, xrun runs, CPU sampling, DSP load, latency
harnesses, crash-recovery timing, snapshot cost. MIDI input is **not** a reason to
involve him — `scripts/midi-load.py` drives Surge, and a virtual ALSA port connected to
the bench's input drives pads (see `docs/measurements/archive/looper-midi-osc-latency-2026-08-19.md`).

### Self-test the instrument before it costs him anything

> **Doctrine, 2026-08-22 — nine occurrences, one root cause.** Every instrument here returns
> its value and its failure *through the same channel*, so a broken instrument is
> indistinguishable from a working one at the reading site and blindness arrives as a
> **result**. Required in every harness: **no in-band failures** (no `|| x=0`, no `unknown`,
> no continue-on-error — halt the cell); **a positive control** (force a known answer, assert
> the reading matches); **a negative control** (break it deliberately, assert the harness
> halts); **physics assertions** rejecting impossible results in-harness. Run
> **`./scripts/instrument-conformance.sh`** (≤ 15 min, offline) before any Pi measurement or
> shipping claim. Full rule: `docs/measurements/MEASUREMENT-DISCIPLINE.md` **Rule -1**. **Do not
> weaken an assertion to make a test pass.**

On 2026-08-19 Mitch tapped pads **382 times across two sessions** and both produced zero
samples, because the harness was hooking a code path pads never touch. Four separate
measurements in that work order exited cleanly having recorded nothing (nine instances
project-wide — see MEASUREMENT-DISCIPLINE Rule −1):

| instrument | failure | looked like |
|---|---|---|
| `xrun-corr.sh` | writes to `~/xrun-corr.out`, not stdout | 12 runs, exit 0, empty file |
| `set-surge-audio.sh` | fails without `sudo`, then continues | a run labelled 512 executed at 1024 |
| latency tap v1 | hooked `_send`; footswitches use the raw client | 267 presses, `n=0` |
| latency tap v2 | paired only with `/hit`; most gestures emit none | 115 presses, `n=0` |

Same shape every time: **the failure is indistinguishable from the success.** So before
any measurement involves him:

1. **Drive it synthetically first** and confirm it produces non-zero output.
2. **Assert the instrument is the one you think it is** — `grep` the deployed file for
   the fix, print the real `jackd` command line, check the sample count. A deploy step
   that prints nothing has not necessarily succeeded.
3. **Count what you discarded.** A measurement that silently drops its input reports the
   same thing whether it worked or not.

A remote command that returns no output is not evidence that it ran.

### And a working instrument can still be the wrong one

The rule above catches an instrument that returns **nothing**. On 2026-08-21 we hit the
other half: instruments that return **confident, plausible numbers that do not mean what we
assumed.** Both would have passed every check in the previous section.

| instrument | produced | actually meant |
|---|---|---|
| `mpe-xrun-probe` xrun count | non-zero every run, self-tests clean | an **event count with no magnitude**, conflating ALSA underruns with JACK graph overruns — see `docs/measurements/xrun-counter-audit-2026-08-21.md` |
| proposed 10-20 Hz fill poller | a smooth, legible trace | **sub-Nyquist** against period rates of 47/94/188 Hz — a confident trace of nothing |

So two more checks, before an instrument informs any decision:

4. **Audit what it actually counts**, in writing, dated. One-time cost per instrument.
   Ask: *what reading would this produce if it were broken?* If that matches a healthy
   reading, it is not an instrument.
5. **Check its resolution against the signal.** A sampler below the Nyquist rate of the
   thing it measures yields an authoritative-looking trace with the answer removed.
6. **Ask what the shortest useful version of the test is**, and justify anything longer in
   writing. Size windows from the **expected event rate**, not convention — ~30 events takes
   ~1 s at 2776/min but ~4 hours at 0.13/min. When the shortest useful version comes out
   implausibly long, **the metric is wrong for the question**, not a reason to run a soak.

**Before designing any measurement, invoke the `measurement-design` skill**
(`.claude/skills/measurement-design/SKILL.md`). It carries the checklist, the audited
instrument facts, and the rules for writing a measurement prompt for another agent — use it
before opening a Pi window, before handing a prompt to an agent, and when interpreting
results.

**Full doctrine: [`docs/measurements/MEASUREMENT-DISCIPLINE.md`](docs/measurements/MEASUREMENT-DISCIPLINE.md)**
— cheap-check-first ordering, per-cell pre-registration (prediction **and** falsifier written
before the run), claim classes and minimum n, and the requirement that the harness stamp
**actual** state into every result rather than intended state. Read it before designing a
measurement.

---

## SR&ED daily labour capture (mandatory going forward)

**Canon lives in OM-Repo**, not this repo: [`internal/projects/mpe-synth-launch/sred/`](../OM-Repo/internal/projects/mpe-synth-launch/sred/README.md)

Historical labour through 2026-08-22 lives in [`SRED-EFFORT-LOG.md`](../OM-Repo/internal/projects/mpe-synth-launch/sred/SRED-EFFORT-LOG.md) (G1 reconstruction). **Do not backfill that way again.**

| when | do |
|---|---|
| End of investigation / measurement / instrument session | Invoke OM-Repo **`sred-daily-capture`** ([`.claude/skills/sred-daily-capture/SKILL.md`](../OM-Repo/.claude/skills/sred-daily-capture/SKILL.md)) |
| Append row | [`SRED-DAILY-LOG.md`](../OM-Repo/internal/projects/mpe-synth-launch/sred/SRED-DAILY-LOG.md) via [`sred/scripts/sred-log-append.sh`](../OM-Repo/internal/projects/mpe-synth-launch/sred/scripts/sred-log-append.sh) |
| Phase names | [`SRED-EVIDENCE-2026.md`](../OM-Repo/internal/projects/mpe-synth-launch/sred/SRED-EVIDENCE-2026.md) §4 only |
| Hours | Mitch's ranges only — never invent; use `?` if session ends without answer |

**Log instrumentation work explicitly** (build / discover wrong / derive rule — see effort log §Instrumentation). Unattended soaks are normal; note monitoring fraction and mechanism references in the daily row.

**Pair with `measurement-design`:** conditions before the Pi run; **labour after the session** (OM-Repo skill).
