# Pi 5 predictions — pre-registered before first boot

*Platform: predictions authored on Pi 4B control · committed 2026-08-23 (America/Toronto)*

Per [`PI5-TRANSITION-PLAN.md`](../PI5-TRANSITION-PLAN.md) §5. **Pi 5 actual** column stays blank until Suite 1 runs. Significance floor from A4: **1.7% max run-to-run spread** on Pi 4.

| quantity | Pi 4 measured | Pi 5 predicted | basis | Pi 5 actual |
|---|---|---|---|---|
| fixed per-callback cost `a` | 0.13 ms | ~0.10 ms | clock 1800→2400 MHz (+33%); mostly compute-bound on Pi 4 | |
| Crystals dsp_med @ 512×2 @3v | pass1 ~58.9% (A2) | ~35–45% | clock × ~1.3–1.5 A76 IPC vs A72; osc-bound | |
| Cloud Horn dsp_med @ 512×2 @5v | pass1 ~62% (A2) | ~40–50% | same; osc-bound, higher voice count | |
| Duduk dsp_med @ 512×2 @3v | pass1 ~59% (A2) | ~45–55% | filter-bound — **may scale less** than Crystals; P7 informs | |
| lowest clean buffer (instrument) | 1024×2 @ verified floors | 512×2 or 256×3 | if compute wall clears, spend headroom on periods | |
| xrun event class | 100% JACK graph overrun (W1) | unchanged | stack property, not board-specific | |
| reference-suite spread tolerance | max 1.70% (A4) | same suite script | noise floor for “did Pi 5 help?” | |
| B2 soak rate @ 1024×2 Cloud Horn @5 | 2.06/min (991/8 h) | ≤1.0/min if compute-bound | instrument-only; no looper | |

**Osc vs filter fork:** if Crystals cells improve &gt;2× and Duduk cells improve &lt;1.5×, optimisation work splits (osc path vs filter path).

**Scoring:** after Pi 5 Suite 1, fill **Pi 5 actual** and mark hit/miss per row. Do not edit predictions retroactively.
