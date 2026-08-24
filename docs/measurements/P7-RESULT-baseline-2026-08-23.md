# P7 — overclock diagnostic (partial)

*Baseline completed: 2026-08-23 14:53–15:12 (America/Toronto)*  
*Pi: `raspberrypi2.local` · git `1c165b9` · artifacts `~/plan-p7-20260823-145330`*

---

## Status

| phase | state |
|---|---|
| Baseline @ **1800 MHz** | **DONE** — all runs `throttled=0x0`, 0 xruns |
| Overclock @ **2000 MHz** | **config applied, reboot pending** — `arm_freq=2000` in `/boot/firmware/config.txt`; backup `config.txt.bak-p7-20260823` |

Poly governor **off** for P7 (one variable). Re-enable after P7 completes (G2 shipping state).

---

## Pre-registration (from artifact)

- Clock gain 1800→2000: **+11.1%**
- If compute-bound: expect **`dsp_p99` ~−10%** per patch vs baseline spread
- Falsifier: within baseline spread → clock not binding
- Alarm: drop ≫11% → comparison broken

---

## Baseline @ 1800 MHz (1024×2, confirm 45 s × 3 runs)

| patch | voices | run | xruns | dsp_p99 | dsp_max |
|---|---:|---|---:|---:|---:|
| Crystals | 3 | 1 | 0 | 5.595 | 5.664 |
| Crystals | 3 | 2 | 0 | 5.637 | 5.796 |
| Crystals | 3 | 3 | 0 | 5.639 | 5.795 |
| Cloud Horn | 5 | 1 | 0 | 7.642 | 7.776 |
| Cloud Horn | 5 | 2 | 0 | 7.627 | 7.793 |
| Cloud Horn | 5 | 3 | 0 | 7.665 | 7.975 |
| Duduk | 3 | 1 | 0 | 5.581 | 5.696 |
| Duduk | 3 | 2 | 0 | 5.626 | 5.740 |
| Duduk | 3 | 3 | 0 | 5.605 | 5.691 |

Run-to-run spread (dsp_p99): Crystals ~0.04 pp, Cloud Horn ~0.04 pp, Duduk ~0.05 pp.

**Note:** Confirm-harness `dsp_p99`/`dsp_max` are JACK client-load readings at verified-clean floors — not the reference-suite `dsp_median` scale (~38–57%). P7 compares **before/after at the same harness**, which is the valid clock-scaling question.

---

## Finish P7 (after reboot)

Pi has `arm_freq=2000` staged. After reboot:

```bash
# on Pi
cd ~/MPE-Module
sudo ./scripts/measure-plan-p7.sh --phase oc \
  --artifact-dir /home/mitch/plan-p7-20260823-145330
# script reverts config.txt and requires a second reboot back to 1800
```

Then verify `sudo ./scripts/pi-overclock-config.sh status` (~1800, `throttled=0x0`) and re-enable governor for V12:

```bash
sudo systemctl enable --now surge-poly-governor.service
```

---

## Close-out hook

When OC phase completes, update `PI4-CLOSEOUT-2026-08-23.md` §2 H6 from **NOT RUN** to outcome and merge into `P7-RESULT-2026-08-23.md` (full).
