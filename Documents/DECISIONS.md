# Engineering decisions log

Dated rows agents and humans can trust over stale specs. Full research lives in
OM-Repo [`internal/projects/mpe-synth-launch/research/`](https://github.com/opsMachine/OM-Repo/tree/main/internal/projects/mpe-synth-launch/research).
Orientation canon: OM-Repo [`GROUNDING.md`](https://github.com/opsMachine/OM-Repo/blob/main/internal/projects/mpe-synth-launch/GROUNDING.md).

---

## 2026-08-13 — No Python on the JACK audio thread

**Decision:** Python must not run on the realtime audio path. No
`python3-jack-client` callback, no NumPy mixer in-process with JACK, no
`arecord`/`aplay`/`snd-aloop` pipeline (Phase 0 — also retired).

**Python stays for:** touch UI, OSC/MIDI control, file I/O, systemd wrappers,
`pedal-to-osc.py`, calibration — everything that is *not* mixing audio every
graph period.

**Rationale:** [`recipe-answer-01.md`](https://github.com/opsMachine/OM-Repo/blob/main/internal/projects/mpe-synth-launch/research/recipe-answer-01.md)
— shipped audio products keep interpreted code off the audio thread; GIL contention
with the touchscreen UI is the binding risk; pre-committed rule: **no outcome
where Python-in-callback wins.** Mitch confirmed 2026-08-13.

**Supersedes:** `Documents/specs/looper-jack-client-spec.md` Tasks 1–11 (Python
JACK client path). Keep §A.2 (`audioop` analysis) and §B.3/criterion 19
(fail-open topology) as reference only.

**Next:** Evaluate Phase 2 options that respect M3 — see [`DIRECTION.md`](DIRECTION.md).

---

## 2026-08-13 — Looper is a product requirement

**Decision:** The looper is core to the instrument (not an exploration). Phase 1
without a looper is incomplete product, not a shippable v1.

**Rationale:** Mitch confirmed 2026-08-13 ("seemingly" → affirmed). Matches
`LOOPER-PLAN.md` product framing and `product-narrative.md`.

**Does not decide:** which looper implementation — that is gated on the
SooperLooper Pi test and the audio-input question (see GROUNDING §6.3).

---

## 2026-08-13 — Phase 2 path: evaluate before building

**Decision:** Do **not** start Phase 2 implementation code until the
**SooperLooper Pi test** completes (~4 h timebox,
[`looper-vetting.md` §7](https://github.com/opsMachine/OM-Repo/blob/main/internal/projects/mpe-synth-launch/research/looper-vetting.md)).

**During the test:** prove B1 (`dry=0`), B7 (xruns beside Surge), B10 (20-min play).
Try mic/guitar on the interface for ~10 min — audio input in/out is decided by
playing in the same session, not as a separate prerequisite.

**Dropped:** play `yolo/looper-phase0` on the Pi — branch predates JACK; checkout
downgrades to deleted ALSA stack (repriced after PR #50).

**Pre-committed rule** (from recipe analysis, not yet executed):

| SooperLooper fits? | GIL experiment tail | Choose |
|---|---|---|
| Yes | (irrelevant) | **Adopt SooperLooper** — Python never touches audio |
| No | ugly | **Build compiled JACK client** (C/Rust) |
| No | flat | **Build compiled** — simplest kernel, not Python |

**Supersedes:** `Documents/HANDOVER-mixer-looper-2026-08-13.md` TL;DR (Tasks 1–4,
whole #48 merge). Handover retained for Phase 1 landing notes only.
