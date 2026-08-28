# The looper thread — SR&ED account

*Written in response to `PROMPT-looper-agent.md`, by the session that carried out the
seam, multi-clip and control-surface work. Covers 2026-08-25 → 2026-08-28.*

> **Scope and honesty notes.** Where work was routine engineering with no real unknown,
> it is named as such in §6 rather than dressed up. Two items I would not want an
> auditor to discover on their own are flagged inline: the hours for this thread are
> not instrumented (§7), and the final fix is committed but **unverified on hardware**
> (§5). Everything cited below was checked to exist at the path given before it was
> written down.

---

## 1. Loop-wrap continuity outside the audio engine

**Uncertainty.** How to close a recorded loop so playback continues across the join
without a discontinuity, when the recorder (SooperLooper), the synthesiser (Surge XT)
and the control layer (a Python bench over OSC) are separate processes with independent
timing. The specific unknown was not "does it click" but **whether the join could be
repaired from outside the audio engine at all** — whether a control process, which can
only issue commands between audio callbacks, can act with sample accuracy on a boundary
that occurs inside one. Standard practice offers no answer: SooperLooper's OSC surface
documents *what* commands exist, not their latency relative to a loop boundary, and the
question is a property of the running system, not of the API.

**Work performed.** An offline "seam weld" was built first: capture the ring-out tail
after the boundary, merge it into the head of the recorded buffer, and reload the
result. This assumed (a) the tail could be captured close enough to the boundary to
contain the audible decay, and (b) reloading a buffer would not disturb playback. Both
assumptions were eventually tested directly rather than argued about.

Seven hypotheses about the residual artefact were pre-registered and **all seven were
refuted by measurement**: that the merge DSP had changed the audio (output was
byte-identical); that weld timing had changed; that the tail was wrongly skipped
(`skip=0`, the full 73,452-frame tail was applied); that the weld failed to load (live
matched merged exactly); that the click was baked into the buffer (the wrap delta sat at
the 70th percentile of ordinary motion); that JACK buffer 64 vs 128 was responsible
(falsification pre-registered, operator-confirmed null); and that `load_loop` halts
playback.

**Confirming the two premises the narrative records as false — both confirmed, with a
correction to the second.**

1. **`load_loop` does not halt playback.** Measured directly: `loop_pos` ran 0.805 →
   0.813 straight through the call. Confirmed as stated.
2. **The `SEAM_LOAD_LEAD_MS` sweep was not tuning the join.** Confirmed, and I would
   put it more strongly than "measuring the landing error of a subsequent retrigger":
   the sweep was measuring a *different defect that the weld had introduced* — a
   post-weld retrigger landing 4.9 ms early (fixed at `712f012`). The sweep was
   therefore well-behaved and repeatable while being unrelated to the artefact under
   investigation, which is the worst case, because a responsive knob is taken as
   evidence that the right variable has been found.

**Why the native mechanism worked where the offline path could not.** The offline path
carries limits inherent to its own structure, and these were measured rather than
assumed: OSC arm latency of **65–139 ms after the wrap**, meaning the loudest part of
the ring-out was never captured at all; and summed tail energy of **+4.05 dB across the
loop head**, rising to **+18 dB** against a quiet take head (0.0004 RMS) and spanning up
to **87% of loop length**. No amount of tuning removes these — they are consequences of
acting from outside the audio thread. The resolution was a single native `overdub`
command issued **while still recording**, which closes the take and begins overdubbing
at the same sample *inside SooperLooper's own audio thread*. Pop-free, operator-confirmed
on Pi 5, shipped at `117f4cc`.

**A prior refutation that was itself wrong.** This mechanism had been tried on Pi 4 and
rejected. Re-examination traced that failure to a **two-command sequence** (record-off,
then overdub-on) which leaves a gap between the two — not to the engine's behaviour. The
earlier conclusion had been recorded as a property of SooperLooper when it was a property
of how it had been called.

**Also abandoned.** Tail capture and the scratch-loop mechanism it depended on (the
offline pipeline became unreachable from the pad and is pending removal); a
tail-alignment change (`798e376`) reverted within the hour at `c9e7d47` — see §3; and a
`fade_samples` hypothesis raised and dropped on the operator's prior Pi 4 data **before
code was written against it**, which is the cheap version of the same discipline.

**Advancement.** Scoped narrowly to Pi 5, SooperLooper 1.7.9, JACK at the buffer sizes
tested: loop-boundary continuity **cannot** be repaired from a control process outside
the audio engine, and the reason is quantified (65–139 ms arm latency, +4.05 dB head
summing) rather than asserted. The engine's own single-command path does close the take
sample-accurately. Both the working mechanism and the disproved approach are recorded so
the offline path is not re-derived.

**Evidence.** `docs/measurements/PI5-LOOPER-SEAM-WRAP.md` (close-out section); commits
`712f012`, `798e376`, `c9e7d47`, `117f4cc`.

---

## 2. Does the loop engine provide the resources it reports?

**Uncertainty.** The multi-track design assumes the engine supplies as many independent
loop buffers as requested and that the count it reports is that number. SooperLooper
1.7.9 publishes no ceiling on `-l`, and the appliance had run a 16-track configuration
for weeks. It was unknown whether the advertised count is a guarantee, an aspiration, or
unrelated to what the engine will accept commands for — and, critically, **whether a
shortfall is detectable at all** from the control layer.

**Work performed.** A track at the top of the range recorded but would not obey the bar
grid. Four hypotheses were refuted before the answer: grid-sync off-by-one (the source
iterates `range(num_loops)`); OSC burst loss (re-sent alone, 20 ms apart, still ignored);
memory exhaustion (2.6 GB free, reproduces at `-t 10`); last-index reservation (`-l 4`
yields four usable loops). Resolved by a parameter sweep against an **isolated second
engine on port 9971**, writing and reading back every index, so the running instrument
was never perturbed.

**Advancement.** SooperLooper 1.7.9 provides **15 usable loops, indices 0–14**,
independent of `-l` and `-t`. Indices above the ceiling are **phantoms**: they answer
`get` with plausible defaults and silently discard every `set`, so `/ping` and every
read-based health check pass while configuration vanishes. Because the unreliable
component is a third-party dependency, the remedy could not be a corrected instrument —
it is an **acceptance probe** (`check_loops_writable`) that exercises the capability by
writing rather than querying it.

**Knowledge decay as a distinct failure mode.** This ceiling had been discovered once
before and survived only as a string in a shell log line — `16 loops, scratch 14 — loop
15 empty on Pi`. The workaround outlived its explanation, was read as stale copy about a
deleted feature, and was removed, re-exposing the phantom to the player. The constant
and its measurement now travel together in `sl_limits.py`.

**Evidence.** `docs/measurements/sooperlooper-loop-ceiling-2026-08-27.md`;
`scripts/sooperlooper/sl_limits.py`.

---

## 3. Can a control surface be kept alive on a bus it is losing?

**Uncertainty.** The appliance repeatedly presented as "dead" — pads unlit and
unresponsive after a session start, with no error anywhere. It had been attributed to
process state (orphans, duplicate instances, stale code) and "fixed" by restarting, four
or more times. The real unknown was not the cause but something prior to it: **whether
the control layer possessed any reading capable of distinguishing a live surface from a
dead one.** `systemctl is-active`, the absence of journal errors, the startup banner's
device line, and `rtmidi`'s `open_port()` return value are all satisfied equally by a
dead surface. This is why the fault survived repeated diagnosis — every check performed
was structurally incapable of returning a negative.

**Work performed.** The symptom was made measurable *before* it was explained: session
starts were repeated and scored on whether the pads responded, establishing a baseline of
**4 failures in 6 starts**. Quantifying the intermittency was the step that mattered — an
intermittent fault is precisely the kind that single-restart "fixes" appear to resolve.
The kernel ring buffer then supplied the mechanism: `urb status -32` (`-EPIPE`) against
the device's USB address — an **endpoint stall** followed by re-enumeration. The APC MINI
is a 12 Mbit full-speed device two hubs deep, sharing that chain with streaming USB
audio, and the startup sequence blanked all 64 pads in one unpaced burst.

A second, independent path to the same symptom was found alongside it: `systemctl
restart` can SIGKILL the outgoing process and start its replacement within the same
second, before ALSA has released the port, leaving the subscription unmade while the
banner still prints correctly.

**Advancement.** Writes are paced through a queue at a 1.5 ms gap, with the startup blank
drained explicitly (~96 ms) before anything paints over it, and a link-health poll
re-asks the kernel and reopens the port by name. Re-measured: **0 failures in 6 starts**,
no further USB disconnects for the remainder of the session. Separately, and more
durably, the deploy path now interrogates `/proc/asound/seq/clients` for an actual reader
on the port and refuses to report success without one — **a reading that can come back
negative**. The pacing fixes this instance; the reading is what makes the next one
detectable.

**Positive controls and a contradicted expectation.** The 4-of-6 baseline is itself the
positive control for the fix: without a measured failure rate, "0 of 6 after" is
unfalsifiable, since a fault that fires two times in three is easily "fixed" by any
change followed by three lucky starts. The contradicted expectation was the original
diagnosis: the fault was assumed for days to be software state, and is bus contention.

**Evidence.** `scripts/sooperlooper/apc_link.py`, `tests/test_apc_link.py`,
`scripts/sooperlooper/midi_subscription.py` (module docstring records the seventeen-minute
silent failure), `scripts/restart-looper-session.sh`; commit `5aba06a`.

---

## 4. Multi-clip — the composition failure

**Uncertainty.** Whether a multi-clip-per-track model (Ableton Session View semantics)
could be layered over an engine whose loops are flat, independent and stateful, without
the control layer and the engine holding contradictory ideas of what is playing. The
engine has no concept of a "slot"; it has fifteen loops. Any slot model is a fiction
maintained entirely in the control layer, and the unknown was whether that fiction can be
kept consistent with an engine that changes state on its own schedule (quantized to the
bar) and reports state changes only as **edge-triggered updates**.

**Work performed, and what failed.** The first composition attempt failed in a way worth
recording: a matrix that re-decided what a press meant, on top of a footswitch that
already decided what a press meant. Two decision-makers over one buffer produced defects
that were individually fixable and collectively endless. The structure was replaced with
a **router**: the active lane forwards the press to the footswitch verbatim, and the
matrix owns only the cases the footswitch cannot know about (launch, switch,
record-elsewhere, clear, cancel).

Three defects found in operator testing, each a distinct class:

- **A queued switch resolved on a state *change* that never arrives.** PLAYING → PLAYING
  is not a change, so a switch between two playing clips never completed. Fixed by
  polling state rather than waiting on a transition (`6ee8cf5`). Same shape as §3 one
  layer up: a signal that is absent in exactly the case you care about.
- **Two independent causes of clip loss, both silent.** (a) The buffer was `unlink`ed
  before `save_loop` had written it. (b) A take closing into its ring-out overdub was not
  registered as real, so the flush skipped it **without error** and `undo_all` then
  destroyed it. (b) was the operator's own hypothesis, formed from the symptom and
  confirmed in code. Fixed at `1b50f1a`; the save path was made durable (temp `.part` →
  fsync → `os.replace` → fsync directory) so a failed save leaves the previous take
  intact rather than a truncated file.
- **A dual-function control resolved by omission.** The bottom scene button is a scene
  launcher alone and Stop All Clips with Shift; the conflict had been "resolved" by
  leaving it out of the scene column, silencing scene row 0 entirely.

**The instrument failure in this thread was the test suite.** The multi-clip suite passed
while the appliance failed. Cause: every test constructed its starting state by
assignment (`_tracks[0] = Track(...)`) and then performed one gesture — so **the setup
line silently repaired whatever the previous gesture had failed to do**. The suite was
measuring a machine that does not exist. It was rewritten under one rule — state may be
created *only* by gestures, never by assignment — against a fake engine that, like the
real one, emits auto-updates **only on change**. Making the harness less accommodating in
three separate respects (deliver only on change; do not pre-write the WAV; do not wait
for the tail) surfaced a real defect **each time**, which is the positive control for the
rewrite.

**Advancement.** A multi-clip model over a flat-loop engine, with the boundary stated:
the control layer may not re-derive state the engine owns, and any pending intent must be
resolved by polling rather than by waiting for an edge. The test methodology is the more
transferable result — a harness that constructs state by assignment cannot detect a
gesture that fails to construct it.

**Evidence.** `docs/measurements/multi-clip-slot-spike-2026-08-26.md`,
`docs/measurements/multi-clip-p2-composition-failure-2026-08-27.md`,
`tests/test_multiclip_workflow.py`, `scripts/sooperlooper/apc_panel.py`; commits
`0c039e7`, `6ee8cf5`, `1b50f1a`, `a2da7a7`.

---

## 5. Things I am flagging rather than smoothing over

1. **The final fix is unverified on hardware.** `5aba06a` is committed and pushed but was
   never deployed — the appliance went offline first. The 0-of-6 result in §3 was measured
   on the pacing change, which *was* deployed and running; the deploy-path reader check
   was not exercised on the device.
2. **`8c4aee6` shipped a wrong control-surface mapping** and was corrected by `a2da7a7`
   the same day. The row mapping was got wrong three times in one session, each time by
   reasoning from prose that contradicted the note table three lines above it. I record it
   because the remedy is structural (a single source of truth for every note, with the
   rule that a disputed fact is measured on the device again rather than reasoned about)
   and because a claim that shows only clean commits is less credible, not more.
3. **The buffer setting at 64×2 is ear-validated only** — no xrun soak was run. The env
   file says so at the point of use. 64×2 collapsed ALSA/USB on 2026-08-23 for reasons
   never diagnosed, and that non-diagnosis is an open item, not a closed one.
4. **§1's hours are not separable** from the same-day sessions around them.

## 6. Named as routine, and excluded

Deploy scripting and service-unit work; the systemd unit-name correction; git worktree
and stash hygiene; LED colour-table plumbing; test writing for behaviour already
understood; and the ordinary defect fixes that carried no uncertainty (a health probe
rewriting the audio path on a timer, `e111719`). The *waiting* in
`restart-looper-session.sh` is routine; only the **verification** is claimed.

## 7. Time records

None instrumented for this thread. Appliance logs bound some sessions from the outside
(first bench activity, last deploy) but the operator's hands-on time began earlier than
the appliance record shows in at least one case, which is noted in the daily log rather
than reconciled. Hours are the operator's recall, not a measurement, and should be
presented that way.
