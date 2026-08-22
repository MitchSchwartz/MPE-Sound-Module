# Patch normalization v2 — calibration integrity + level UX

**Issue:** [MPE-Sound-Module #5](https://github.com/MitchSchwartz/MPE-Sound-Module/issues/5) (follow-on)
**Status:** Implemented (pending Pi soak)
**Created:** 2026-08-17
**Last updated:** 2026-08-18 (America/Toronto)

**Related:** [`docs/PATCH_NORMALIZATION.md`](../../docs/PATCH_NORMALIZATION.md) · [`docs/OUTPUT-METER.md`](../../docs/OUTPUT-METER.md) · [`docs/TOUCH_PATCH_BROWSER.md`](../../docs/TOUCH_PATCH_BROWSER.md) §Mixer faders

---

## Problem statement

Per-patch normalization was meant to make Quick Select patches **musically level at load** — integrated LUFS near −18, true peak near −3 dBTP on a fixed MPE calibration gesture — with the touch **Norm** fader as per-patch trim and **Vol** as global post-norm attenuation.

On the live appliance (2026-08-17) the system **does not deliver that contract**:

| Symptom | Operator impact |
|---|---|
| Some patches **blare** (OUT meter hot); others stay **very quiet** | Set is unusable without constant manual trim |
| **FM Acoustic Piano** exceeds the −3 dBFS design ceiling in real play | Norm fader at **−12 dB floor** is insufficient; operator must lower **global Vol**, ducking everything else |
| OUT meter **−48…0 dBFS** scale feels useless | Musically useful range compressed into top of **Vol** travel (IEC console law) |
| Raising **DAC** output (−6 dB default, now **−12 dB** interim) made spread **more obvious** | Hardware trim is post-graph; it cannot fix bad cal |

### Measured evidence (Pi, `~/.patch_browser_normalization.json`, 2026-08-17)

| Finding | Count / example |
|---|---|
| Patches with stored `true_peak_dbtp` **above −3** (pre-gain capture) | **150** |
| Patches with stored peak **above 0 dBTP** at measure time | **52** |
| Duplicate cal rows for same logical patch (path keys) | e.g. `thirdparty:…/FM Acoustic Piano 1` **+2.62 dB** vs `user:Quick Select/…/FM Acoustic Piano 1` **−1.76 dB** |
| Calibration gesture | **Single note** (not poly/chord) — strike + sustain anchors, separate captures |

**Ruled out:** calibration does **not** play multiple notes simultaneously. Loudness mismatch is **gesture vs performance** (velocity, pressure, polyphony at play time), **loopback vs Sound Blaster path**, **weak post-gain verify**, and **Norm trim range**.

### Interim mitigation (shipped 2026-08-17)

**`MPE_DAC_VOLUME_DB=-12`** (Speaker raw **64**) is the new repo and fresh-install default — middle ground between −6 (too hot for current cal) and −20 (previous conservative default). Pi `/etc/mpe/mpe.env` updated same session. This is **headroom at the DAC**, not a calibration fix.

---

## Goals

1. **Trustworthy cal** — saved `gain_db` implies **verified post-gain peak ≤ −3 dBTP** on the **live listening path** for the calibration gesture.
2. **Playable spread** — Quick Select set: typical patch, norm on, Vol at 0 dB trim, OUT peak **−12…−3 dBFS** on the same gesture replayed by ear (no patch > −3 without user override).
3. **Per-patch trim that works** — Norm fader can tame outliers (e.g. piano) **without** global Vol; floor deep enough for worst case.
4. **Controls match the meter** — fader travel and OUT meter scale aligned so adjustment is predictable.

## Non-goals

- Live dynamic compression / limiter in the RT graph (static gain only; no JACK LADSPA in v2).
- Re-calibrating the full Surge library in one shot (Quick Select scope first).
- Binding **Vol** to `amixer` (DAC stays separate — see OUTPUT-METER.md).

---

## Decisions (proposed)

| ID | Decision | Rationale |
|---|---|---|
| **D1** | **Cal capture on live Sound Blaster path** (or loopback + documented **path offset** applied at save) | Loopback measured 4–14 dB hotter than DAC path on same patches (PATCH_NORMALIZATION.md 2026-08-01). |
| **D2** | **Reject save** unless closed-loop verify reports **post-gain true peak ≤ −3.0 dBTP** (±0.2 tolerance) | 150 entries prove current save path skips this. |
| **D3** | **One storage key per patch identity** — merge path aliases; stem fallback only when unambiguous | Duplicate keys caused ±4 dB for FM Piano. |
| **D4** | **Norm fader = offset from calibrated `gain_db`**, not absolute replacement | Absolute −12…+24 dB forces global Vol when cal baseline is wrong. Proposed: **`user_trim_db`** added to calibrated gain, clamped **−24…+12**. |
| **D5** | Extend Norm display/range to **−24 dB** minimum trim | FM Piano needed more than −12 dB cut from cal baseline. |
| **D6** | **Dynamic patches** (piano, velocity-hot FM): optional **fortissimo verify** — second capture at vel 127 / full pressure; gain = min(strike, sustain, fortissimo) | Single-note cal underestimates peak for performance dynamics. |
| **D7** | OUT meter scale **−24…0 dBFS** (or mark **−3** target line on current scale) | −48 floor wastes bar; norm targets −3. |
| **D8** | **Vol fader:** offer **linear-in-dB** mode (env `MPE_VOL_FADER_LAW=linear\|console`, default **console** until soak) | IEC law fixes cliff but compresses useful range; operator feedback 2026-08-17. |

---

## Acceptance criteria

### Phase 1 — Cal integrity (blocking)

| # | Criterion | Test type |
|---|---|---|
| 1 | After cal, **post-gain** peak ≤ −3.0 dBTP on verify capture; else patch **skipped** (not saved) | Unit + integration (`test_calibration_integrity.py` extended) |
| 2 | Cal script exits non-zero if >10% of batch skipped for peak fail (operator-visible summary) | Integration |
| 3 | **`mpe cal verify`** (or `calibrate-patch-normalization.py --verify-only`) replays gesture on **loaded patch** and reports pass/fail vs −3 | Manual + script |
| 4 | Quick Select **re-cal** (`--favorites-only --force`) on Pi: **0** saved rows with post-verify peak > −3 | Manual (Pi soak) |
| 5 | FM Acoustic Piano 1 (Quick Select): norm on, Vol 0 dB, cal gesture replay — OUT peak **≤ −3 dBFS** | Manual |

### Phase 2 — Storage + runtime trim

| # | Criterion | Test type |
|---|---|---|
| 6 | Duplicate path keys for same `.fxp` resolve to **one** entry (migration script + test) | Unit |
| 7 | Norm fader adjusts **`user_trim_db`** relative to calibrated gain; double-tap clears trim | Unit + manual |
| 8 | Effective gain clamp: **`calibrated + user_trim` ≥ −24 dB** trim, peak at runtime still bounded by Surge `amp/volume` ≤ 1.5 linear when norm off cap applies | Unit |
| 9 | FM Piano: operator can tame hot play **using Norm only** (no Vol move) — target OUT **−12 dBFS** on their test phrase | Manual |

### Phase 3 — UX alignment

| # | Criterion | Test type |
|---|---|---|
| 10 | OUT meter default scale **−24…0** (or −3 marker documented in UI) | Manual |
| 11 | Vol fader law selectable; **linear** mode has ≤3 dB step at 50% travel (regression test on `volume_fader_trim_to_db`) | Unit |
| 12 | PATCH_NORMALIZATION.md updated; stale buffer rows corrected | Review |

---

## Implementation notes

### Calibration pipeline changes

```
load patch → strike capture → sustain capture → compute gain_db (dual anchor, min)
  → apply gain → closed-loop verify (existing, enforce hard fail)
  → optional fortissimo capture (D6)
  → save only if post-gain peak ≤ SAFE_PEAK_DBTP
```

- Tighten `finalize_gain_with_closed_loop`: **do not save** on verify fail (today may save with bad `true_peak_dbtp` from pre-gain measure).
- Store **`post_gain_peak_dbtp`** and **`cal_route`** (`loopback` | `soundblaster`) per entry.

### Runtime (`patch_loader.py`)

- `_patch_gain_linear` from `calibrated_gain_db + user_trim_db` (D4).
- Keep `volume_fader_to_amp_linear` stacking **after** norm baseline.

### Migration

- One-time: merge normalization JSON keys by resolved `stable_key`; keep **newest `calibrated_at`**; log conflicts for operator review.
- Pi backup before migration: existing `patch_normalization.pi-backup-*` pattern.

### DAC default

Already **−12 dB** in `scripts/lib/dac-volume.sh`, `configure-pi-paths.sh`, `config/mpe.env.example`. No further change in v2 unless soak says otherwise.

---

## Security considerations

- **Data flow:** local JSON + OSC to Surge only; no network.
- **Cal script:** stops production audio services (existing); must not leave appliance silent without `calibration_teardown` restore.
- **Auth:** N/A — physical appliance.

---

## Falsification

If after Phase 1 re-cal **spread is unchanged** and verify passes on all Quick Select patches, hypothesis shifts to **runtime stack** (Vol taper, duplicate load keys, wrong sidecar lookup) — profile `_send_combined_volume` and sidecar key resolution before adding a limiter.

If **−24 dB Norm trim** still cannot tame piano without Vol, add **category-based cal** (keyboard gesture set) rather than raising DAC attenuation.

---

## Open questions

1. **Target integrated LUFS** — keep −18 or lower to −20 for headroom?
2. **Full library re-cal** — schedule after Quick Select soak, or incremental only?
3. **Vol law default** — switch to linear after A/B on Pi, or env-only?

---

## Rollback

- **DAC:** set `MPE_DAC_VOLUME_DB=-20` (or −6) in `/etc/mpe/mpe.env`; `set-dac-volume.sh`.
- **Cal v2 JSON schema:** keep `user_trim_db` optional; absent = v1 behaviour.
- **Revert spec-only commit** — no runtime change until Phase 1 lands.
