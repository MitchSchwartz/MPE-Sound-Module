# Grumpy Dev Code Review — Audio / OS-State Spine, **CYCLE 2**

*Date: 2026-09-01 · Branch: `fix/card-identity-and-audible-state` (uncommitted, cut from `dev` @ 898b160)*
*Reviewer: cycle-2 pass. I did not write this code and I did not write the cycle-1 review.*
*Also in scope: uncommitted `mpe-cli/commands/jack.sh`.*

## Coverage — what I read, what I ran, what I did not

**Read end to end:** the whole diff; `scripts/lib/audio-engine.sh` (state + card-identity +
supervisor regions), `scripts/lib/audio-settings-pending.sh`, `scripts/reconcile-audio-settings.sh`,
`scripts/start-surge-cli.sh`, `scripts/surge-watchdog.sh`, `scripts/start-jackd.sh`,
`scripts/jackd-prestart.sh`, `scripts/set-surge-audio.sh`, `scripts/lib/paths.sh`,
`lib/uac2-lazy-route.sh`, `lib/profile-switch-flag.sh`, `lib/unload-snd-aloop.sh`,
`scripts/detect-jack-device.sh`, `scripts/detect-audio-device.sh` (filter sites),
`config/mpe-jackd.service`, `config/surge-xt-cli.service`, `config/surge-watchdog.service`,
`scripts/install-units.sh` (exec guard), `patch_browser/surge_audio.py`,
`patch_browser/audio_engine.py`, `patch_browser/surge_monitor.py`,
`patch_browser/session_snapshot.py` (jack.state read), all four touched/new test files,
`mpe-cli/commands/jack.sh`, and both cycle-1 documents.

**Executed, not just read** (results inline below):

| # | Probe | Why |
|---|---|---|
| A | Ran `start-surge-cli.sh` end to end under `set -uo pipefail` in a hermetic stub sandbox — **both** the no-jackd failure path and the jackd-up success path (fake `jackd` visible to `pgrep -x`, stub `jack_lsp`/`surge-xt-cli`/`systemctl`/`sudo`) | the brief's highest-risk item: sourcing cleanly proves nothing |
| B | Ran `surge-watchdog.sh` for multiple loop ticks under `set -uo pipefail`, including the ≥30 s looper-reconcile branch and the surge-failed branch | same |
| C | Read-only-`mpe.env` reconcile | can it brick / lie? |
| D | Two concurrent settings changes, then SIGKILL the first | can it roll back a *bad* value as "known good"? |
| E | Planted decoy re-implementations of the card list in `scripts/` and ran `SingleSourceOfTruthTests` | is the guard real? |
| F | `mpe_card_is_virtual` against 12 boundary card ids | prefix-glob correctness |
| G | `sed -n 's/^ExecStart=//p'` against a unit with `ExecStartPre=` | does `install-units.sh` cover the new pre-hook? |
| H | Full suite: `python3 -m unittest discover -s tests -q` | **Ran 1847 tests · OK (skipped=3)** — matches the claim; +41 over the 1806 baseline |

**Not read:** `patch_browser_ui.py`, the APC/looper stack, `native/`, `scripts/yolo/`,
`measure-*.sh`, and the ~160 test files unrelated to this spine.

---

## 1. First Impressions (The Gut Check)

This is a **good** fix pass. The diagnosis in cycle 1 was right, the fixes land on the right
mechanisms, and the new tests are the best in the repo — they carry negative controls *and* a
premise check that proves the damage exists before testing the cure
(`test_sigkill_mid_change_leaves_the_untested_value_behind`). That is AGENTS.md Rule −1
actually honoured in code rather than quoted in a comment, and it is rare.

The `set -u` change I was told to attack hardest is the part I'd worry about least. I ran both
scripts, not just sourced them, on success and failure paths, and nothing died. The libraries
were already `${x:-}`-disciplined and the one bare `${MPE_MODULE_REPO}` read in the watchdog's
30 s branch is unconditionally assigned by `paths.sh`. Verdict on that one: **safe.**

What is wrong is the shape of the pass, not its craft. Two of the four P0s were fixed at the
**producer** and not at the **consumer**, and one boot-critical unit gained a mandatory
`ExecStartPre` that nothing validates.

The headline finding: **the touch HUD still says "Audio ready" when the graph is bound to the
idle sink.** `jack.state` now knows. `engine.state` now says `reason=idle-sink`. And
`patch_browser/audio_engine.py:126` returns `"Audio restored", "Audio ready"` on
`state == "ok" and active == "jack"` **before it ever looks at `reason`**. The player looks at
the HUD. The one surface that had to change did not. Cycle 1's own words — *"Until this exists
nothing can tell a working appliance from a dead one"* — are still true at the only place it
matters.

## 2. Architecture & Structure

**The card-identity consolidation is real but oversold.** The claim is "one predicate replacing
6 divergent regex lists." What actually happened:

| Site | Before | After |
|---|---|---|
| `audio-engine.sh` `mpe_physical_playback_card_present` | own `grep -viE` | ✅ calls `mpe_card_is_virtual` |
| `audio-engine.sh` `mpe_should_skip_graph_restart_for_card` | own `case` | ✅ delegates (with a semantic change — §5) |
| `detect-jack-device.sh` tier 2 | own `grep -viE` | ✅ `_drop_virtual_records` |
| `detect-jack-device.sh` `_playable_records` | own `grep -viE` | ✅ `_drop_virtual_records` |
| **`detect-audio-device.sh` `GADGET_GREP` + tier 2 (`:147`) + tier 4 (`:250-251`)** | own lists | ❌ **untouched — not in the diff at all** |
| **`mpe-cli/commands/jack.sh` `_jack_pick_dac`** | own list | ❌ still its own list; `Dummy` added by hand, "keep in step" comment |

So: four consolidated, two left, and the two left are guarded by a comment. That is a genuine
improvement and it is not "one list."

Worse, cycle 1's table *under*-counted. It cited `detect-audio-device.sh:251` (tier 4). But
**tier 2 at `detect-audio-device.sh:147` — the selector that actually picks the DAC —** excludes
only `$GADGET_GREP`; it has no `Loopback`, no `Dummy`, no `vc4hdmi` exclusion whatsoever:

```bash
# scripts/detect-audio-device.sh:145-148
    grep -i "usb" | \
    grep -viE "$GADGET_GREP" | \
    ...
    head -1
```

It survives on the coincidence that JUCE's display string for `Dummy` does not contain "usb".
That is not a design, it is a near miss.

**A new cross-file dependency was added without comment on its blast radius.**
`detect-jack-device.sh` now sources `lib/audio-engine.sh` (~1,100 lines). That file runs in the
udev `RUN+=` path (`99-usb-audio.rules` → `restart-audio-graph.sh` → `mpe_graph_restart_is_relevant`
→ `detect-jack-device.sh`), which is already the CPU-doctrine violation cycle 1 flagged (4.5).
Sourcing bash is cheap next to a JUCE fork, so this is not a cost problem — but the contract
problem is real: `detect-jack-device.sh`'s **stdout is parsed** by `jackd-prestart.sh`
(`grep '^JACK_DEVICE='`). Any future top-level `echo` in `audio-engine.sh` now corrupts device
resolution at boot. Nothing pins that. A one-line test asserting `detect-jack-device.sh`'s stdout
contains only `KEY=value` lines would close it permanently.

**Genuinely improved:** `mpe_physical_playback_card_present` no longer greps whole lines. It
parses the card **id** out of the brackets and asks the predicate:

```bash
    done < <(sed -n 's/^[[:space:]]*[0-9]\+[[:space:]]*\[\([^]]*\)\].*/\1/p' \
                 "$cards_file" 2>/dev/null | sed 's/[[:space:]]*$//')
```

That kills a whole bug class (a *description* substring laundering a card past the filter) and,
as a free side effect, removes a latent `set -o pipefail` landmine: the old
`grep | grep -viE | grep -q .` under `jackd-prestart.sh`'s `set -uo pipefail` could return
non-zero from an upstream SIGPIPE when `grep -q` short-circuited. Nobody claimed credit for that.
I will.

## 3. Code Quality

**`set -uo pipefail` on the two boot-path scripts: I could not break it.** Probe A and B.

- `start-surge-cli.sh`, no jackd → clean `state=failed reason=no-server`, exit 1, trap-free, no
  unbound variable.
- `start-surge-cli.sh`, jackd up → clean `state=ok active=jack device=0.0`, `surge.state` and
  `engine.state` both written.
- `surge-watchdog.sh` → survives multiple ticks including `_reconcile_looper_units_if_needed`
  (the one bare `${MPE_MODULE_REPO}` read) and the `is-failed` branch.
- Every sourced library (`paths.sh`, `audio-engine.sh`, `uac2-lazy-route.sh`,
  `profile-switch-flag.sh`, `unload-snd-aloop.sh`) reads through `${x:-}` or an assignment that
  `paths.sh` guarantees. `HOME` is bare in `paths.sh` but both units declare `User=`, so systemd
  supplies it.
- `pipefail` is only consequential where a pipeline's status is consumed; there is no
  `if …|…; then` or trailing `| head`/`| grep -q` whose status is read in either script's
  reachable path.

The `DEVICE_TIER` fix is correct and minimal — it reads `TIER=` out of the file
`jackd-prestart.sh` already writes, and initialises the variable so `set -u` cannot fire on the
missing-file path.

**Two comment defects, in a repo whose stated disease is exactly this.**

`mpe_jack_bound_card()` was inserted *between* a comment and the function it documents:

```bash
# Epoch seconds when jackd last started, or 0 when unknown. Written by
# start-jackd.sh rather than parsed out of systemctl so it is testable and works
# for a hand-started server too.
# What the graph is actually bound to, and whether the player can hear it.
mpe_jack_bound_card() {
```

The first three lines now describe `mpe_jack_start_epoch`, which is 25 lines further down.

And in `audio-settings-pending.sh`:

```bash
    # fsync the directory so the marker is durable before mpe.env is touched --
    mv -f "$tmp" "$file" 2>/dev/null || { …; return 1; }
    sync 2>/dev/null || true
```

`sync(1)` is not a directory fsync. The effect is stronger, not weaker, so this is harmless —
but the comment names a mechanism the code does not use.

**Docs went stale in three new places while one old lie was fixed.** `docs/USB-AUDIO-HOST.md:168`
(cycle-1 4.9) and `install-idle-sink.sh`'s `index=7` (the audit's find) are both correctly fixed.
Meanwhile the `jack.state` schema gained three fields and:

- `docs/CODE-MAP.md:100` — still `device, period, periods, rate, started`
- `docs/CODE-MAP.md:512` — still shows the 4-argument `mpe_jack_state_write` call
- `docs/PATHS.md:55` — same stale field list
- `Documents/specs/session-control-plane-spec.md:65` — same

Net zero on false-assertion debt.

## 4. Code Smells (The Hall of Shame)

### 4.1 🔴 The audibility signal never reaches the player. `state=ok` still renders "Audio ready".

`patch_browser/audio_engine.py:126`:

```python
    state = engine.get("state") or ""
    reason = engine.get("reason") or ""
    active = engine.get("active") or ""

    if state == "ok" and active == "jack":
        return "Audio restored", "Audio ready", 2.0
```

`reason` is read on the line above and then *not consulted* on the `ok` path. Every downstream
`reason` branch (`no-device`, `no-card-resolved`, `no-server`, `supervisor-exhausted`,
`promote-planned`, …) lives under `state == "failed"` or `state == "recovering"`.
`surge_monitor.py:171` likewise only inspects `reason` when `state == "failed"`.

So on a Dummy-bound rig the watchdog dutifully publishes `state=ok active=jack reason=idle-sink`,
and the HUD says **"Audio ready."** Grep confirms nothing else consumes it either:

```
grep -rn "audible\|idle-sink" patch_browser/ scripts/ ../mpe-cli/
  -> only definitions inside scripts/lib/audio-engine.sh. Zero consumers.
```

The only new operator-visible signal in the whole pass is one journal line at
`start-jackd.sh:44`, which nobody at a gig is reading. (`mpe engine status` does dump
`reason=idle-sink` because `mpe_cli_render_engine_state_kv` prints every key — that is luck, not
design.)

This is the same failure shape the pass exists to eliminate, one layer up. Cycle 1 wrote the fix
as "add `card=`/`tier=` to `jack.state`" and the audit called it *"the most structurally
important finding"* — and neither traced a single consumer.

**Fix (small):** in `audio_switch_progress_message`, before the `ok` branch:

```python
    if state == "ok" and active == "jack":
        if reason == "idle-sink" or (jack.get("audible") or "") == "no":
            return "No DAC — idle sink, nothing audible", "No DAC — silent", 8.0
        return "Audio restored", "Audio ready", 2.0
```

and add the corresponding assertion to `tests/test_card_identity.py`, since today no test in the
repo asserts what the *player* sees.

### 4.2 🔴 A mandatory, unvalidated `ExecStartPre` on the one unit that makes sound

`config/mpe-jackd.service`:

```ini
ExecStartPre=+@MPE_MODULE_REPO@/scripts/reconcile-audio-settings.sh
```

No `-` prefix. If that path does not resolve to an executable file, systemd fails the command,
`ExecStart` never runs, and — with `Restart=always`, `RestartSec=3`, `StartLimitIntervalSec=0` —
mpe-jackd thrashes forever without ever starting. Total silence, no `jackd`, no Surge client.

Three independent facts make this reachable rather than theoretical:

1. **`ConditionPathExists=` guards only `start-jackd.sh`**, not the pre-hook.
2. **`install-units.sh`'s existence guard is blind to it.** Probe G:
   ```
   $ printf 'ExecStartPre=+/nonexistent/reconcile.sh\nExecStart=/bin/true\n' > u.svc
   $ sed -n 's/^ExecStart=//p' u.svc | head -1
   /bin/true          # the ExecStartPre line is never examined
   ```
   The guard whose comment says *"That is how mpe-looper.service skipped every boot for five days
   unnoticed"* does not cover the line this pass added.
3. **AGENTS.md's own soak workflow walks straight into it.** *"Soak a feature branch … **return
   Pi to `main` after soak**"*. Units live in `/etc/systemd/system` and persist across
   `git checkout`; `scripts/reconcile-audio-settings.sh` does not exist on `main`. And neither
   `configure-pi-paths.sh` nor `deploy-all.sh` calls `install-units.sh` — I grepped; unit
   installation is a separate manual step. Soak this branch, switch back to `main`, and the
   appliance does not make sound until someone re-runs `install-units.sh`.

**Fix:** `ExecStartPre=-+@MPE_MODULE_REPO@/scripts/reconcile-audio-settings.sh` (a missing
reconciler must never outrank having an audio graph — the script's own header says exactly this),
**and** widen `install-units.sh`'s loop to `sed -n 's/^ExecStartPre*=//p'` over *all* matching
lines.

**Note the test actively blocks the fix.**
`tests/test_audio_settings_pending.py::test_jackd_unit_runs_the_reconciler_as_root_before_device_selection`
asserts `lines[0].startswith("ExecStartPre=+")`. Adding the `-` makes that test fail. Relax it to
`"reconcile-audio-settings.sh" in lines[0] and "+" in lines[0]`.

### 4.3 🟡 The reconciler can restore an *untested* value — the compounding failure was moved, not fixed

`set-surge-audio.sh`'s own comment claims the design ends this:

> *"Worse, it compounds: `_prev_buffer` is read from the same file, so once a killed run has left
> a bad value behind, the NEXT run adopts it as 'previous'. Two failed attempts and the last
> known-good setting is gone."*

There is no lock, and `mpe_pending_write` unconditionally overwrites. Probe D, reproduced:

```
  marker after A: restore:MPE_JACK_BUFFER=128     <- the real known-good
  marker after B: restore:MPE_JACK_BUFFER=64      <- B recorded A's UNTESTED value
  reconcile-audio-settings: WARNING a settings change did not complete — restoring …
  audio-settings: restored MPE_JACK_BUFFER=64
  RESULT env: MPE_JACK_BUFFER=64                   <- 128 is gone
```

Also unhandled, same family: a run that **succeeds** and is killed before `mpe_pending_clear`
(the graph came up, the UI's 150 s timeout fired anyway) leaves a stale marker, and the next
graph start silently reverts a setting that was proven good.

**Fix:** wrap the whole of `set-surge-audio.sh` in `flock "$(mpe_pending_file).lock"`, and refuse
to start a change while `mpe_pending_status` reports `inflight`. That is ~4 lines and it closes
both cases.

### 4.4 🟡 `mpe_pending_reconcile` logs "restored" whether or not it restored — Rule −1, in the recovery path

```bash
        install -m 0644 "$tmp" "$env_file" && restored=1
        rm -f "$tmp"
        echo "audio-settings: restored ${key}=${value} (settings change did not complete)" >&2
```

The `echo` is outside the `&&`. Probe C, with `mpe.env` not replaceable:

```
install: Failed to remove existing file '…/mpe.env'. Error: PermissionDenied
audio-settings: restored MPE_JACK_BUFFER=128 (settings change did not complete)   <- false
reconcile exit=0
env now: MPE_JACK_BUFFER=64                                                        <- unchanged
```

A journal line that reads identically whether the recovery worked or not, on the code path that
only runs when the appliance is already broken. That is the exact shape
`docs/measurements/MEASUREMENT-DISCIPLINE.md` Rule −1 names.

Second half of the same defect: `mpe_pending_clear` runs **unconditionally** after the loop, and
`reconcile-audio-settings.sh` does `mpe_pending_reconcile || true`. A restore that failed for any
reason destroys the only record of the known-good values and reports success to systemd.

**Fix:** move the `echo` inside the `&&`; on `restored != 1`, keep the marker and log
`WARNING: could not restore …` instead.

### 4.5 🟡 `SingleSourceOfTruthTests` misses the two most likely re-implementations

The heuristic is: non-comment line, contains `grep`, and mentions ≥2 of
`Loopback|vc4hdmi|UAC2|Dummy`. Probe E planted two decoys under `scripts/`:

```bash
# __probe_case.sh  — a copy in the SAME shape as the canonical predicate
_probe_is_virtual() {
    case "${1:-}" in
        Loopback* | Dummy* | vc4hdmi* | UAC2* ) return 0 ;;
        *) return 1 ;;
    esac
}
# __probe_multiline.sh — the same grep chain, one filter per line
    grep -viE 'Loopback' \
      | grep -viE 'Dummy' \
      | grep -viE 'vc4hdmi'
```

Result: **`OK`.** Both pass. Only the single-line four-token grep (`__probe_split.sh`) was caught.

So the guard misses (a) a `case`-glob copy — which is what the canonical implementation looks
like, and therefore the most likely thing anyone copies; (b) any line-wrapped grep chain;
(c) `awk`/`sed`; (d) a list hoisted into a variable; (e) everything outside `scripts/**/*.sh`,
which is where the two *actually surviving* lists live (`detect-audio-device.sh` is inside the
tree but its lines carry only one token each; `mpe-cli` is another repo).

**Fix:** invert it. Instead of pattern-matching for offenders, assert the predicate has exactly
one definition (`grep -c '^mpe_card_is_virtual()' == 1` across the repo) and count *occurrences*
of each virtual-card token per file with a small explicit allowlist of the sites that legitimately
mention them (`audio-engine.sh`, `detect-audio-device.sh`'s `GADGET_GREP`, the test fixtures).
A guard that can be defeated by re-indenting is decoration.

### 4.6 🟡 `mpe_card_is_virtual`'s prefix globs are wider than the facts require

```bash
        Loopback* | Dummy* | vc4hdmi* | vc4-hdmi* | UAC2* ) return 0 ;;
```

Probe F:

```
  UAC20          -> VIRTUAL      UACDemoV10     -> REAL
  UAC2Audio      -> VIRTUAL      Play3          -> REAL
  DummyPlug      -> VIRTUAL      Scarlett4i4    -> REAL
  LoopbackPro    -> VIRTUAL      Headphones     -> REAL
  vc4hdmi0       -> VIRTUAL      loopback       -> REAL  (case-sensitive)
```

The facts: `snd-dummy` registers id **`Dummy`** exactly. The Linux UAC2 gadget registers
**`UAC2Gadget`** exactly (`UAC2` is the second form the old code matched). Only `vc4hdmi*`
genuinely needs a prefix (`vc4hdmi0`/`vc4hdmi1`), and `Loopback*` is defensible for multi-instance
`snd-aloop`. `Dummy*` and `UAC2*` are gratuitous widening in a predicate whose false-positive
consequence is *the appliance refuses to make sound through a working DAC*.

ALSA derives a USB card's id from the sanitised USB product string. Generic UAC2-firmware boards
do ship product strings in the `UAC2…` family, and such an id would be excluded from the boot
gate, from `detect-jack-device.sh` tier 2, **and** from `_playable_records` tier 4 simultaneously —
the appliance would burn the 15 s wait, fall through to tier 3 / `Dummy`, and go silent, now
correctly reporting `audible=no` for entirely the wrong reason.

Neither of Mitch's known interfaces collides (`Play3`, Scarlett), so this is a latent trap rather
than a live bug — but it is the kind that surfaces at a gig with a borrowed DAC.

**Fix:** `Dummy | UAC2 | UAC2Gadget | Loopback* | vc4hdmi* | vc4-hdmi*`. And add the boundary
cases to `CardIsVirtualTests.test_real_cards_are_not_virtual` — today that list is
`("Play3", "USB", "Scarlett4i4", "Headphones", "bcm2835")`, none of which is anywhere near a
boundary.

### 4.7 🟡 `mpe_should_skip_graph_restart_for_card` silently changed meaning

```bash
 mpe_should_skip_graph_restart_for_card() {
-    case "$card_id" in
-        vc4hdmi* | UAC2Gadget | UAC2 | Loopback) return 0 ;;
+    mpe_card_is_virtual "${1:-}"
 }
```

That function answers *"which udev events must not restart the production graph"* (spec D2) — a
different question from *"can this card be heard."* The pass argues this exact distinction
in-code to justify keeping `mpe-cli`'s `_jack_pick_dac` separate ("it answers a different
question — 'which card is the PREFERRED DAC'"), then collapses D2's list without making the
argument.

Concretely the set gained `Dummy*` and `vc4-hdmi*` and widened `Loopback`/`UAC2` from exact to
prefix. I believe the outcome is benign-to-better — skipping a graph restart when the idle sink
appears is right — but it is an unremarked behaviour change on the udev path, and
`GraphRestartDenylistTests` only asserts the new answers, so it can't tell you the meaning moved.
Either state the D2-equals-audibility argument in the comment, or give D2 its own thin wrapper.

### 4.8 🟢 `test_commit_happens_only_after_the_graph_is_proven` got looser

```python
-        commit = self.src.index("_env_committed=true\n\necho -n \"Applied\"")
+        applied = self.src.index('echo -n "Applied"')
+        commit = self.src.rindex("_env_committed=true", 0, applied)
```

The stated rationale is honest and correct — the old literal broke because `mpe_pending_clear`
was inserted between the two, which is formatting, not semantics. But `rindex` now finds *any*
`_env_committed=true` before `"Applied"`, and the rollback-path assignment (inside the
`if ! mpe_promote_surge_planned` block) also satisfies `commit > promote`. Delete the success-path
assignment entirely and this test still passes. So does
`test_crash_marker_is_cleared_on_the_success_path`, which reuses the same anchor.

**Fix:** anchor on the unique comment instead — `commit = self.src.index("# Proven: the graph came up")`.
Not a Rule −1 violation; a precision loss worth one line to undo.

### 4.9 🟢 A duplicate dressed as a negative control

`ReconcileTests.test_negative_control_without_reconcile_the_bad_value_survives` and
`test_sigkill_mid_change_leaves_the_untested_value_behind` run the identical simulation and make
the identical assertion. Two names, one piece of evidence. The premise check is the valuable one;
the "negative control" adds nothing, and calling it a negative control overstates what the file
proves.

### 4.10 🟢 Orphaned comment / wrong mechanism name

`mpe_jack_bound_card()` inherited `mpe_jack_start_epoch`'s doc block (§3); `mpe_pending_write`'s
comment says "fsync the directory" and calls `sync`. Both cosmetic, both the exact prose-drift
disease cycle 1 named in §1.

### 4.11 🟢 Boot cost, stated for the record

`jackd-prestart.sh`'s wait now actually runs. Any boot with no real DAC attached — a legitimate
`usb-host` idle configuration — now pays the full `MPE_JACK_DEVICE_WAIT_S` (15 s) before
detection. That is correct behaviour and the whole point of the fix; it is also a boot-time
regression for that configuration that nobody wrote down.

## 5. Logic & Business Rules

**The reconcile state machine is right, and I want to say so plainly.** The
`none | inflight | stale` classification keyed on `(boot_id, pid)` is the correct decomposition,
and the `inflight` branch is what makes the whole thing work: `set-surge-audio.sh` restarts
mpe-jackd to validate a change, which runs the reconciler, which must not undo the change it is
validating. It doesn't. `test_reconcile_leaves_an_inflight_change_alone` pins exactly that, and it
is the test I would most have expected a fix pass to forget.

Better still, and unremarked in the code: `mpe-jackd` carries `Restart=always`, `RestartSec=3`,
`StartLimitIntervalSec=0`. So a SIGKILLed settings change that leaves an unstartable period
self-heals in ~3 s — jackd fails, systemd restarts it, `ExecStartPre` now sees `stale`, restores,
jackd comes up. The design does not actually need the reboot it was scoped around. Worth stating
in the header, because it is the strongest argument for this approach over the trap.

**Residual holes in that machine**, in severity order: the missing lock (4.3), the
unconditional-clear-on-failed-restore (4.4), and PID reuse within a single boot. The last is
genuinely low: `kill -0` on a recycled pid would classify a dead change as `inflight` and skip the
restore forever, but pid reuse inside one uptime on a Pi with `pid_max=32768` and a handful of
processes is remote. I would not spend anything on it; I mention it only because the file's header
claims `(boot_id, pid)` makes the classification unambiguous, and it doesn't quite.

**A gap between producer and producer.** `start-surge-cli.sh` publishes
`mpe_engine_state_write … "$ENGINE_REASON"` where `ENGINE_REASON` is `""` on success — it never
calls `mpe_engine_sink_reason`. So after every Surge start, `engine.state` reads
`state=ok reason=` for up to one watchdog probe interval (≤10 s), even on a Dummy-bound graph,
before the watchdog corrects it. Given 4.1 the HUD is wrong either way; once 4.1 is fixed, this
becomes the residual hole. One-line fix: use `"$(mpe_engine_sink_reason)"` there too.

**`audible=unknown` is the right default and is tested.** `mpe_jack_bound_is_audible` requires an
explicit `yes`, so an unknown card gates the warning *on*, not off. That is the correct direction
of failure and `test_unknown_card_is_not_assumed_audible` pins it. There is exactly one 4-argument
`mpe_jack_state_write` caller left in the repo (`tests/test_audio_engine.py:1190-1191`) and it
asserts nothing about `audible`, so no production path publishes `audible=unknown` today.

## 6. Test Strategy & Execution

**Full suite: 1847 tests, OK (skipped=3).** Baseline 1806 → +41. Claim verified.

**The new tests are the best work in this pass.** `test_card_identity.py` pairs each behavioural
assertion with a negative control that restores the pre-fix predicate and asserts the test would
have failed. `test_audio_settings_pending.py` opens with a premise check that reproduces the
2026-09-01 damage before testing the cure, and pins the `inflight` non-interference property.
Both files lead with a dated regression narrative. This is what AGENTS.md asks for and rarely gets.

**Were the two modified tests weakened?** Honest answer: `test_surge_audio.py` was **strengthened**
— the `subprocess.run` → `Popen` mock migration was forced by the fix, the assertions carried over
intact, and three new tests were added, two of which (`test_timeout_asks_politely_before_killing`,
`test_kill_is_the_last_resort_not_the_first`) assert the ordering that is the entire point of the
change. `test_set_surge_audio_rollback.py` gained two real tests and lost a little anchor precision
in one (4.8). Net: not weakened. No Rule −1 violation.

**What the new tests do not cover** — and these are the gaps that matter, given §4:

| Gap | Why it matters |
|---|---|
| Nothing asserts what the **player sees**. No test touches `audio_switch_progress_message` with `reason=idle-sink`. | This is why 4.1 shipped. The whole P0 was "the appliance reads the same broken or fine", and the reading nobody tested is the HUD. |
| Two concurrent settings changes | 4.3, reproduced in 30 s of shell |
| A restore that fails (unwritable / full disk) | 4.4 — the false "restored" line and the marker destruction |
| Success-then-killed-late (stale marker on a proven-good value) | reverts a good setting at the next graph start |
| `reconcile-audio-settings.sh` missing / non-executable | 4.2 — the brick |
| `mpe_card_is_virtual` boundary ids | 4.6 — the real-DAC list has no near-boundary member |
| `detect-jack-device.sh` stdout contract after it started sourcing `audio-engine.sh` | §2 |

`SingleSourceOfTruthTests` is analysed in 4.5: it is a real check of one narrow shape, and
trivially bypassed by the two shapes most likely to occur.

## 7. Security & Performance

Nothing new. `set-surge-audio.sh`'s allowlist (`is_valid_buffer`/`is_valid_periods`/
`is_valid_sample_rate`) still gates every value before `_update_env_var`, and the pass did not
loosen it. The new `ExecStartPre=+` runs as root by design and is documented as such; the script
it runs takes no arguments and reads only root-owned files.

One thing to keep an eye on rather than fix: `mpe_pending_reconcile` interpolates the restored
value straight into a `sed` replacement (`sed "s/^${key}=.*/${key}=${value}/"`), identically to
the pre-existing `_update_env_var`. The values reaching it come from `/etc/mpe/mpe.env` itself, so
a hand-edited env file containing `/` or `&` in one of the three keys would corrupt the rewrite.
Same exposure as before this pass, root-only, not worth a change on its own — worth doing if
anyone touches `_update_env_var` anyway.

Performance: `TERMINATE_GRACE_S = 10.0` extends the worst-case UI block from 150 s to 160 s.
Sourcing `audio-engine.sh` into `detect-jack-device.sh` adds a few ms to the udev path that
already forks a JUCE synthesiser (cycle-1 4.5, still open). Neither registers.

## 8. Developer Experience

The new code documents itself well — the header of `audio-settings-pending.sh` is a model of
"why this shape and not the obvious one," with the measured incident, the mechanism, and the
rejected alternative (`/run` is tmpfs) all in eight lines. Someone picking this up cold would
understand it.

Against that: the schema changed and four docs did not (§3), a boot-critical unit gained a
dependency that `install-units.sh` cannot see (4.2), and the branch that is currently the only
place `reconcile-audio-settings.sh` exists is one `git checkout main` away from a silent
appliance. Per AGENTS.md's own deploy rules, **push before deploying and re-run
`install-units.sh` on any branch switch** — but the better answer is to make the unit tolerant
(4.2) so the rule doesn't have to hold.

---

## Verdict

The diagnosis was right and the craft is good: the card-identity predicate is correct at the
boundaries I could reach, the `set -u` change I was told to distrust survived being executed
rather than read, the crash-safe marker is the right mechanism for an untrappable kill, and the
new tests carry negative controls and a premise check that most teams never write. **No cycle-1
finding was fixed wrongly.** But the pass stopped at the producer twice. `jack.state` now knows
whether the instrument can be heard, and the touch HUD — the only surface the player looks at —
still prints "Audio ready" for a Dummy-bound graph, because `audio_engine.py` returns on
`state == "ok"` before it reads `reason`. That is cycle-1's P0 #2, unfixed at the point of use,
and it is the same reads-the-same-either-way disease one layer up. Alongside it, the fix for P0 #3
put a hard, unvalidated `ExecStartPre` on `mpe-jackd`, on an appliance whose documented soak
workflow checks the script out and then checks it back away. Fix those two and the three-line
follow-ups in 4.3/4.4, and this pass is genuinely finished; ship it as-is and the appliance can
still lie to its owner, just for a new reason.

## Priority Backlog

1. **🔴 Consume the audibility signal at the HUD.** `patch_browser/audio_engine.py:126` — check
   `reason == "idle-sink"` (or `jack["audible"] != "yes"`) *before* returning "Audio ready", and
   add the test that asserts what the player sees. Without this, cycle-1 P0 #2 is not fixed.
2. **🔴 Make the new `ExecStartPre` unable to brick `mpe-jackd`.** Use
   `ExecStartPre=-+…/reconcile-audio-settings.sh`; widen `install-units.sh`'s existence guard to
   `ExecStartPre` lines; relax
   `test_jackd_unit_runs_the_reconciler_as_root_before_device_selection`, which currently pins the
   hazard.
3. **🟡 Serialise settings changes.** `flock` around `set-surge-audio.sh` and refuse to start while
   `mpe_pending_status` is `inflight` — otherwise the second change records the first's untested
   value as "known good" (reproduced) and the compounding failure the header claims to have
   solved is still live.
4. **🟡 Stop the reconciler reporting a restore it did not perform.** Move the `echo` inside
   `install … &&`; keep the marker and log a WARNING when `restored != 1` instead of clearing it.
5. **🟡 Tighten `mpe_card_is_virtual` and its guard.** Exact-match `Dummy`, `UAC2`, `UAC2Gadget`;
   keep prefixes only for `vc4hdmi*`/`vc4-hdmi*`/`Loopback*`; add boundary ids to the real-card
   test. Rewrite `SingleSourceOfTruthTests` as a definition-count + allowlisted-occurrence check —
   a `case`-glob copy and a line-wrapped grep chain both pass it today (verified), and
   `detect-audio-device.sh` tier 2 still has no `Dummy`/`Loopback` exclusion at all.
