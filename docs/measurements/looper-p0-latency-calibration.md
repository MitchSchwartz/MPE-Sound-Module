# P0 — SooperLooper input latency calibration (defining take seam)

**Spec:** `Documents/specs/looper-loop-seam-spec.md` Tier 1 · **Gate:** Mitch ear S2  
**Last updated:** 2026-08-18 (America/Toronto)

**Canon:** [SooperLooper sync/latency](https://sonosaurus.com/sooperlooper/doc_sync.html) · [OSC parameters](https://sonosaurus.com/sooperlooper/doc_osc.html)

---

## Why this comes first

SooperLooper documents the hard-clip symptom explicitly:

> If you set **input latency too high**, you will **lose some of the sound immediately after** you triggered the record function.

That applies to **record stop** as well as record start. Tier 2 (stay Recording until release fades) only helps if the **final** stop boundary is calibrated. No tail-capture or crossfade tuning fixes wrong `input_latency`.

---

## Preconditions

| Check | Command |
|-------|---------|
| Bench + engine up | `mpe looper sl-bench status` · `mpe status` |
| Tier 2 deployed | Bench log on defining close: `recording release into loop until quiet` |
| Seam weld off | `MPE_SL_SEAM_WELD=0` (default) |
| Quiet room / headphones | Normal playing level — see `AGENTS.md` audio safety |

Read current engine values:

```bash
scripts/sooperlooper/dump-loop-levels.py --detail
```

Note `input_latency` on loop 0 (and whether `autoset_latency` is active — see `/etc/mpe/mpe.env`).

---

## Procedure (Mobius / SL method)

SooperLooper’s doc points to the Mobius latency section. Same idea:

1. **Start from autoset or a low baseline.**  
   With `MPE_SL_AUTOSET_LATENCY=1` (default), run `configure-grid-sync.sh` or restart the looper stack so JACK values apply.

2. **One-bar defining take, close on downbeat with release ringing.**  
   Pad-down to close while the note is still audible. Wait for tail capture to finish (LED stops blinking).

3. **Listen at the wrap (N→0).**  
   - **Hard clip / missing release tail** → input latency **too high** → **decrease** `MPE_SL_INPUT_LATENCY` in steps of **~256 samples** (~5 ms @ 48 kHz), or disable autoset and set explicitly.  
   - **Audio before you intended / pre-echo at head** → input latency **too low** → **increase** in steps of **~128–256 samples**.

4. **Repeat** until wrap is continuous enough for S2 pass. SL suggests adding ~10 frames at a time when tweaking manually; use your ear, not math.

5. **Lock the value** in `/etc/mpe/mpe.env`:

   ```bash
   MPE_SL_AUTOSET_LATENCY=0
   MPE_SL_INPUT_LATENCY=<samples>   # e.g. 1024
   ```

6. **Re-apply and restart bench** (not full sl-restart unless engine wedged):

   ```bash
   mpe looper deploy dev
   mpe looper sl-bench restart
   ```

7. **Verify persisted:**

   ```bash
   scripts/sooperlooper/dump-loop-levels.py --detail --json | head -40
   ```

---

## Optional — wrap crossfade

If latency is correct but a thin click remains:

- Raise `MPE_SL_FADE_SAMPLES` (default 256 → try 512) in `mpe.env`, redeploy grid sync / restart bench.
- Re-run the same ear phrase; do **not** change latency and fade in the same pass.

---

## Pass / fail (S2)

| Pass | Fail |
|------|------|
| Release audible through wrap; no obvious chop at stop | Hard clip when tail capture ends |
| `loop_len` grows slightly vs downbeat (Tier 2 expected) | Tail only at loop head or missing |
| Stable on 3 repeats | Worse than baseline |

Record verdict + `input_latency` / `fade_samples` in this file or `DECISIONS.md` when promoted.

---

## Diagnostics during tail capture

Bench log (journal or bench stdout):

- `defining take — recording release into loop until quiet` — Tier 2 active
- `tail capture done (peak<… for 80ms)` — normal close
- `tail capture done (max … no release peak)` — peak meter never saw release; check `in_peak_meter` / input routing

Tail peak subscription uses `in_peak_meter` on loop 0 only while capturing (`sl_bench_listener.py`).

---

## References

- Spec tiers: `Documents/specs/looper-loop-seam-spec.md`
- Apply latency on start: `scripts/sooperlooper/sl_grid_sync.py` → `apply_loop_latency()`
- Dump tool: `scripts/sooperlooper/dump-loop-levels.py --detail`
