# V11 — 512×2 and 256×3 at confirm-verified counts

**Queue #1 in `PROGRESS.md`.** Largest direct latency lever in the arc — no rebuild, no
overclock, no thermal risk.

**Expected time:** ~15 min on Pi.

---

## Question

At the voice counts already confirm-verified clean at 1024, do **512×2** (21.3 ms) and
**256×3** (16.0 ms) stay xrun-clean with governor off?

Prior 512 evidence was ramp-derived (screening-grade). This redo uses the confirm harness only.

---

## Cells

Governor off, stock 1800 MHz, condition A. **3 runs × 45 s** per cell via
`measure-latency-run.sh`.

| Patch | Voices | Configs to run |
|---|---|---|
| Crystals | 3 | 512×2, then 256×3 |
| Cloud Horn | 5 | 512×2, then 256×3 |
| Duduk | 3 | 512×2, then 256×3 |

**Pass:** xruns = 0 at the listed voice count for that config.

**Readout:** record `dsp_med` per cell (headroom signal). Do not use ramp ceilings.

---

## Run

```bash
cd ~/MPE-Module
sudo ./scripts/measure-plan-v11.sh
```

Artifacts under `~/plan-v11-YYYYMMDD-HHMMSS/`. Plan log ends with `SENTINEL v11-complete`.

---

## Gates

- **Do not run while another measurement or soak is in flight** (see `PROGRESS.md` standing
  rules).
- **Do not run immediately after a hot compile** if the board is still throttling — check
  `vcgencmd get_throttled` (expect `0x0`) and let temp settle first.
- Restore buffer to whatever Gate 1 / soak profile requires after V11 if you changed the
  live default — script uses `--no-restore-buffer` between cells; final profile is whatever
  the last cell left (256×3). **Revert to 1024×2 before starting an 8 h soak** unless Mitch
  explicitly wants the soak at the V11 winner.

---

## After

Update `PROGRESS.md` confirmed floors table if a config holds. If 512×2 is clean on all three
patches, that becomes the leading candidate for Gate 1 default (still needs soak).
