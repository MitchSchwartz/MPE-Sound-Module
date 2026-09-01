# Grumpy Dev Code Review — MPE harness & appliance "OS" state

**Scope:** system bring-up, part supervision, lifecycle/state across the Pi, DAC plug/unplug, multiple DACs.
**Trigger:** recent pushes regressed the device; audio output largely broken.
**Date:** 2026-09-01 · **Branch:** dev @ 898b160 · **Cycle:** 1

## Coverage (what I read / did not read)

**Read end to end:** all 26 unit files in `config/`, `config/99-usb-audio.rules`, `scripts/install-units.sh`,
`scripts/jackd-prestart.sh`, `scripts/start-jackd.sh`, `scripts/start-surge-cli.sh`,
`scripts/detect-audio-device.sh`, `scripts/detect-jack-device.sh`, `scripts/restart-audio-graph.sh`,
`scripts/set-surge-audio.sh`, `scripts/surge-watchdog.sh`, `scripts/install-idle-sink.sh`,
`scripts/lib/unload-snd-aloop.sh`, ~500 relevant lines of `scripts/lib/audio-engine.sh`,
`patch_browser/surge_audio.py`, `.github/workflows/test.yml`, `mpe-cli/commands/jack.sh` (partial),
and the last 40 commits.

**Not read:** `patch_browser_ui.py` (63KB), the APC/looper stack, ~40 `measure-*.sh`, `native/`,
`scripts/yolo/`, and most of the 164 test files.

**Test state at review time:** `python3 -m unittest tests.test_audio_engine tests.test_detect_jack_device
tests.test_detect_audio_device tests.test_engine_lifecycle_ownership` -> **104 tests, OK.**
The suite is green and the instrument is silent. That gap is the review.

---

## 1. First Impressions

Professionals work here — that is the diagnosis, not flattery. The commit log is exceptional: `305dc31`
retracts its own previous three commits because the measurement could not distinguish the two outcomes;
`2b7e094` reverts a global setting because it was "a global answer to a one-device fault."
`install-units.sh` refuses to enable a unit whose `ExecStart` does not exist and names the five-day outage
that taught it.

And the device is dead. The discipline is stored in **prose**, not in **code**. Every lesson from the last
month became a comment block instead of a function. `config/mpe-jackd.service` is 92 lines, 60 of them
comment, 44 of those copy-pasted verbatim into `surge-xt-cli.service`.

## 2. Architecture & Structure

**Core defect: device identity has no owner.** Five "which cards are virtual?" lists, four files, all
disagreeing:

| Location | Excludes |
|---|---|
| `detect-jack-device.sh:163` | `Loopback\|Dummy\|vc4hdmi\|UAC2` |
| `audio-engine.sh:444` `should_skip_graph_restart_for_card` | `vc4hdmi*\|UAC2Gadget\|UAC2\|Loopback` |
| `audio-engine.sh:461` `physical_playback_card_present` | `Loopback\|vc4hdmi\|UAC2` |
| `mpe-cli/commands/jack.sh:74` | `Headphones\|vc4hdmi*\|UAC2Gadget\|Loopback` |
| `detect-audio-device.sh:251` (tier 4) | `Default Audio Device\|Dummy` + gadget |

When `snd-dummy` arrived on 2026-08-30, **two of five got updated**. The device broke in the gap.

**Second defect: ALSA truth is derived from a synthesiser.** `detect-audio-device.sh` runs
`surge-xt-cli --list-devices`, greps JUCE display strings, emits a JUCE float index; `detect-jack-device.sh`
chops that string at the last `" on "` and greps `/proc/asound/cards` to recover a card number. A lossy
round-trip through a display format to obtain a fact `/proc/asound/cards` states directly. Broken twice this
week; both fixes were more grep patterns. It also runs a full JUCE binary launch inside a udev `RUN+=`.

**Genuinely good:** the lifecycle *shape* is right — jackd owns the device, Surge is a client, the supervisor
reconciles rather than restarting blindly, the reconcile budget has cooldown/settle/exhaustion states,
`restart-audio-graph.sh` compares by stable card **ID** not `hw:N`, `RuntimeDirectoryPreserve=yes` so
supervisor state outlives the supervised process.

## 3. Code Quality

Naming is good and `mpe_` prefixing is consistent. DRY is violated where it matters (five card lists) and
where it does not (60 duplicated comment lines across two units).

**Dead code:** `start-surge-cli.sh:54` reads `$DEVICE_TIER`, assigned **nowhere in the repo**;
`git log -S 'DEVICE_TIER='` shows the writer died in `5b4f24b`.

**Shell hygiene is inconsistent:** `set -euo pipefail` in `detect-audio-device.sh` / `set-surge-audio.sh`;
`set -uo pipefail` in `detect-jack-device.sh` / `jackd-prestart.sh` / `restart-audio-graph.sh`;
**nothing at all** in `start-surge-cli.sh` and `surge-watchdog.sh` — the two longest-lived processes.
That is why the `DEVICE_TIER` bug is silent instead of loud.

Python (`patch_browser/surge_audio.py`) is clean and typed. One comment in it states something false (4.4).

## 4. Code Smells (Hall of Shame)

### 4.1 [P0] `mpe_physical_playback_card_present` is blind to the idle sink — this is the silent appliance

`scripts/lib/audio-engine.sh:457`:

```bash
# True when a non-virtual playback card is listed in /proc/asound/cards.
mpe_physical_playback_card_present() {
    local cards_file="${MPE_ASOUND_CARDS:-/proc/asound/cards}"
    [ -r "$cards_file" ] || return 1
    grep -E '^[[:space:]]*[0-9]+[[:space:]]*\[' "$cards_file" 2>/dev/null \
        | grep -viE 'Loopback|vc4hdmi|UAC2' \
        | grep -q .
}
```

`Dummy` is absent. `config/modules-load.d/mpe-idle-sink.conf` loads `snd-dummy` at `sysinit.target`, long
before USB enumeration completes, so the Dummy card is **always** present when `mpe-jackd` starts.

Its consumer is the boot gate at `jackd-prestart.sh:42`:

```bash
while ! mpe_physical_playback_card_present; do
    ...
    log "no physical sound card — waiting up to ${WAIT_SECONDS}s"
```

Measured against a synthetic Pi 5 boot card tree:

```
cards-boot-nodac:        PRESENT -> prestart wait loop EXITS IMMEDIATELY (waited=0)
cards-boot-hdmi-only:    PRESENT -> prestart wait loop EXITS IMMEDIATELY (waited=0)
```

**The 15-second wait for USB enumeration is dead.** Cold-boot sequence with a DAC attached:
snd-dummy loads at index 8 -> prestart asks "physical card present?" -> yes (it sees Dummy) -> `waited=0`
-> detection runs before the DAC enumerates -> tiers 1/2 miss -> tier 3 matches `Dummy`
(`detect-audio-device.sh:213`) -> jackd binds `hw:8` -> driver runs, clients attach, systemd green,
`mpe jack status` reports `xruns: 0` -> **snd-dummy is inaudible by construction.**

The same blindness corrupts the failure branch at `jackd-prestart.sh:69`, which reports
`reason=no-card-resolved` when the truth is `no-device` — the exact misdiagnosis `6ca9b5a` was written to
eliminate, reintroduced by the fix for it.

**Fix:** one `mpe_card_is_virtual()` predicate in `audio-engine.sh` covering
`Loopback|Dummy|vc4hdmi*|UAC2*|Headphones`; all five sites call it. Re-assert the wait as a test.

### 4.2 [P0] `DEVICE_TIER` read and never written — UAC2 route selection is dead

`scripts/start-surge-cli.sh:54`:

```bash
source "$SCRIPT_DIR/lib/uac2-lazy-route.sh"
if [ "$DEVICE_TIER" = "0" ]; then
    surge_audio_route_write uac2
    echo "$(date): Surge audio route: uac2 (USB gadget)" >> "$LOG_FILE"
else
    surge_audio_route_write analog
```

`grep -rn DEVICE_TIER scripts/ config/ patch_browser/ tests/` returns that one line. No `set -u` here, so it
expands to `""`, the test is always false, and the appliance **unconditionally writes route `analog`** —
including at tier 0 with the host capturing. The journal line "Surge audio route: uac2" has never printed.

**Fix:** read `TIER=` from `/run/mpe/jack-device` (jackd-prestart already writes it); add `set -uo pipefail`
to this file and `surge-watchdog.sh`.

### 4.3 [P0] Published state cannot distinguish "playing" from "silent"

`engine.state` carries `engine/active/state/reason/looper`; `jack.state` carries
`started/device/period/periods/rate`, where `device` is `hw:8` — an index ALSA reuses. **Neither records the
card ID, the tier, or whether the bound sink can make a sound.**

Bound to snd-dummy: `mpe_surge_on_jack_graph` returns true, the watchdog writes `active=jack state=ok`, the
HUD is green, `mpe engine status` agrees. Byte-identical to a working instrument.
`Documents/DECISIONS.md` names this ("a state that reads the same broken or fine") and `install-units.sh`
quotes it. The doctrine is in comments, not in the state schema.

**Fix:** add `card=` and `tier=` to `jack.state`; publish `state=degraded reason=idle-sink` whenever the bound
card is virtual.

### 4.4 [P0] The 2026-09-01 rollback trap does not defend against the kill it was written for

`scripts/set-surge-audio.sh:135`:

```bash
trap _restore_env_on_death EXIT INT TERM HUP
```

Commit `49bcdc1` states the threat: *"the touch UI calls us through `subprocess.run(..., timeout=...)`, which
KILLS the child."* `subprocess.run(timeout=)` handles expiry via `Popen.kill()` = **SIGKILL**, which is not in
that trap list and cannot be. The child is `sudo` (`patch_browser/surge_audio.py:186`):

```python
result = subprocess.run(
    ["sudo", str(SET_SURGE_AUDIO_SCRIPT), *args],
    capture_output=True, text=True,
    timeout=AUDIO_SWITCH_TIMEOUT_S,
)
```

If sudo exec'd the script, SIGKILL lands on bash and the trap never runs — the untested value stays in
`mpe.env`, the 2026-09-01 failure. If sudo forked a monitor, SIGKILL kills sudo and **orphans the script**,
which keeps restarting the graph while the UI reports "Timed out."

`surge_audio.py:26-32` asserts *"The script now traps its own death and rolls back, so a kill is survivable."*
That comment is false and load-bearing — it justifies treating the 150s timeout as a backstop.

**Fix:** write `/run/mpe/audio-settings.pending` (old + new) *before* touching `mpe.env`, delete on proven
success, reconcile from an `ExecStartPre` on `mpe-jackd`. Survives SIGKILL, power loss, and the orphan case.
Switch the UI to SIGTERM + grace before `kill()`.

### 4.5 [P1] udev forks a JUCE synthesiser on every DAC plug

`99-usb-audio.rules` -> `restart-audio-graph.sh` -> `mpe_graph_restart_is_relevant()` ->
`detect-jack-device.sh` -> `detect-audio-device.sh` -> `timeout 5 surge-xt-cli --list-devices`, inside a udev
`RUN+=` that udev will kill for taking too long, on a device whose scarcest measured resource is CPU.

### 4.6 [P1] Device policy is string-matching against a display format

`detect-jack-device.sh:78` — four chained string amputations to recover a hardware identity
`/sys/class/sound/cardN/id` states directly.

### 4.7 [P1] Tier 1 is a hardcoded product name; multi-DAC is `head -1` of an unordered list

`detect-audio-device.sh:106` hardcodes `grep "Sound Blaster Play! 3"`. The actual interface (Scarlett 4i4)
can only reach tier 2, whose selector is `grep -i "usb" | ... | head -1`. **With two USB DACs attached, which
one you get is whatever JUCE enumerated first.** No `MPE_PREFERRED_DAC` override exists anywhere.

### 4.8 [P2] Comment-as-changelog

`mpe-jackd.service` 65% comment; its 44-line E1 block byte-identical in `surge-xt-cli.service`;
`detect-audio-device.sh` 35% comment. This is *why* 4.1 happened: `305dc31` updated the prose everywhere
`snd-aloop` appeared and missed the two `grep -viE` lists.

### 4.9 [P3] Documentation states something untrue

`docs/USB-AUDIO-HOST.md:168` says the idle sink is "loaded on every deploy." Its only caller is
`bootstrap-pi5-looper.sh`; `deploy-all.sh` does not call it.

### 4.10 [P3] `install-units.sh` enable-state is a hand-maintained list that documents its own trap

## 5. Logic & Business Rules

The state machine is the strongest part of this codebase. `mpe_engine_reconcile_decision`
(cooldown/jackd-settling/failed/restart) is a proper bounded supervisor; `mpe_engine_stuck_failed_maybe_sweep`
handles terminal-state-plus-hardware-returned; the `planned-promote` flag suppresses supervisor interference
during operator changes; the debounce keys on `(card, action)` specifically so unplug->replug cannot be
swallowed, with the asymmetry argument written out.

Edges are where it fails:

- **Boot vs udev race.** On cold boot the `add` event for `pcmC*D*p` can fire before `mpe-jackd` is up.
  `mpe_bound_card_id()` returns non-zero, relevance says "jackd binding unresolved — restarting (fail loud)",
  and `mpe_restart_audio_graph` issues `systemctl restart --no-block` against a unit systemd is concurrently
  starting. With 4.1, boot outcome depends on that interleaving.
- **Relevance cannot see audibility.** Bound to Dummy with a DAC plugged later: if detection is still
  mid-enumeration, `desired` also returns `Dummy`, relevance says "not relevant", exit. snd-dummy should never
  satisfy relevance while any physical card is present.
- **Rollback is best-effort by construction.** `set-surge-audio.sh` mutates a boot-critical file then tries to
  prove the change works. No generation counter, no known-good snapshot, no boot-time validation.
  `_prev_buffer` is read from the file being mutated, so failure compounds across attempts.

## 6. Test Strategy & Execution

164 test files, ~30,000 lines against ~69,000 lines of production code (scripts + patch_browser + native).
104 audio-lifecycle tests pass — 1806 across the whole suite — while the instrument is silent.

[CORRECTED 2026-09-01 after audit: this section originally claimed "more test code than production code".
That was measured wrong — I compared tests/ against scripts/ alone. Production is 2.3x larger.]

**Good:** hermetic and behaviour-driven; `test_detect_jack_device.py` parameterises the card tree and pins the
real 2026-08-30 Pi 5 card list; `02f2898` re-asserted the APC-mini exclusion "so a fix that worked by
loosening the last-resort filter would fail instead of pass."

**Missing — exactly the layer that broke:**
- `mpe_physical_playback_card_present` has **zero tests** (`grep -rn physical_playback_card tests/` -> nothing).
  Four call sites, boot-critical, untested, now wrong.
- **No composition tests.** Every unit tested in isolation; the sequence
  (modules-load -> prestart -> detect -> start-jackd) is not. 4.1 lives entirely in that seam.
- **Nothing asserts audibility.** No test asserts the bound card is not virtual. A test pinning
  `[Dummy] + [Scarlett]` and asserting `JACK_CARD_ID != Dummy` would have caught this pre-push.
- **`DEVICE_TIER` is caught by no test and no lint.** CI runs shellcheck, but SC2154 structurally exempts
  SCREAMING_SNAKE_CASE names, so it was never going to fire at any severity — there is no config knob to
  find. Static analysis cannot see this class of bug in this codebase; `set -u` is the only thing that can.

  [CORRECTED 2026-09-01 after audit: originally said to "check whether it is disabled or the file excluded",
  which would have sent someone hunting for a setting that does not exist.]

Coverage followed convenience, not risk.

## 7. Security & Performance

Security is not the exposure. gitleaks is configured; sudoers grants in `provision-mpe-agent.sh` are scoped to
named units. `set-surge-audio.sh` runs under NOPASSWD sudo and writes `/etc/mpe/mpe.env`, but validates with a
strict allowlist (`is_valid_buffer`/`is_valid_periods`) before any write — correct; keep it that way, since a
free-form value reaching `_update_env_var`'s `sed` becomes an injection into a file systemd sources.

Performance: the no-forks-in-periodic-loops doctrine is honoured well in `surge-watchdog.sh` (batched
`systemctl is-active` pre-filter, `JACK_PROBE_INTERVAL_S` bound with the reasoning). It is then violated
wholesale in the udev path (4.5) — event-driven so it never shows in the steady-state CPU census, but it lands
during a replug, when the system is already busy.

## 8. Developer Experience

Onboarding is a day or two, and the docs are the asset: `AGENTS.md` (20KB), `README.md` (30KB), 45 files in
`docs/`, `docs/measurements/` with raw logs. `install-units.sh --diff` against the live appliance is an
excellent affordance. `mpe-cli` is a well-shaped operator surface.

But the docs have started lying (4.9), and once one entry lies the rest are claims rather than facts.

CI is thin for the risk profile: `ubuntu-latest`, `pip install python-osc`, `unittest discover`, shellcheck,
with good concurrency cancellation — but no ALSA, no systemd, no Pi, so nothing in CI can execute the layer
that keeps breaking. The card-tree parameterisation is the right idea; it needs to cover boot composition.

`patch_browser_ui.py` at 63KB is worth naming on principle.

---

## Verdict

A codebase with an unusually honest engineer and an unusually dishonest state layer. The reasoning quality in
the commit log beats most funded teams; the retraction in `305dc31` is blog-post material. But every lesson
from the last month was written as prose *next to* the code rather than encoded *in* it, producing a system
where four hand-maintained regex lists must agree, do not, and the disagreement is inaudible — literally. The
recent audio work is not sloppy; it is a correct series of fixes to a real Pi 5 problem, delivered into a
structure with no single place to put "which cards are real." The idle sink got added to two of the five places
that needed it. The device is silent because a virtual card passes a check named
`physical_playback_card_present`. Fix the four P0s and the structure they imply — one card-identity predicate,
one published state that knows whether sound is possible, one crash-safe settings write — and this goes from
fragile to genuinely professional.

## Priority Backlog

1. **P0 — Add `Dummy` to the virtual-card exclusion via one shared `mpe_card_is_virtual()` predicate**, not
   five greps. `mpe_physical_playback_card_present` returns true on a Pi 5 with nothing plugged in, killing the
   DAC-enumeration wait and letting jackd bind the inaudible idle sink on every cold boot. Ship the predicate
   and the composition test in one commit.
2. **P0 — Publish audibility in `jack.state`.** Add `card=`/`tier=`; a virtual bound card publishes
   `state=degraded reason=idle-sink`. Until this exists nothing can tell a working appliance from a dead one.
3. **P0 — Make the settings rollback crash-safe.** Write-ahead `/run/mpe/audio-settings.pending` reconciled by
   an `ExecStartPre` on `mpe-jackd`; SIGKILL cannot be trapped. Correct the false comment at
   `surge_audio.py:26`.
4. **P0 — Fix or delete `DEVICE_TIER` at `start-surge-cli.sh:54`**; add `set -uo pipefail` there and in
   `surge-watchdog.sh`. Check why shellcheck SC2154 did not catch it.
5. **P1 — Get `surge-xt-cli` out of device resolution.** Select from `/proc/asound/cards` +
   `/sys/class/sound/`; call Surge only to translate an already-chosen card into a JUCE index. Removes the JUCE
   fork from udev, deletes the string-amputation layer, and gives `MPE_PREFERRED_DAC` / multi-DAC ordering
   somewhere sane to live.
