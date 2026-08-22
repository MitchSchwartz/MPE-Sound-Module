# Session handoff — V8/V9 + gates (2026-08-22)

**Branch:** `docs/v8-patch-capacity` · [PR #95](https://github.com/MitchSchwartz/MPE-Sound-Module/pull/95)

## Mitch gates (2026-08-22 AM)

| gate | decision |
|---|---|
| **1 — 1024×2 ship** | **Yes, after one overnight soak** — `measure-soak-instrument.sh` (Cloud Horn @ 5, 8 h). Looper stays 1024×3/D. |
| **2 — poly governor** | **No** — fade + steal order first; fix `CPU_HIGH_THRESHOLD=50` (below ~58.9% @1024 baseline). |
| **3 — percussive metric** | **Defer** — rate (notes/sec), not voice count. Attenborough separate. |
| **V10-b ramp fix** | **Proceed** — implemented; validate on Pi. |

**×2 evidence uncontaminated:** V9-b/d/c1 use `measure-latency-run` only, never ramp.

## Agent status

- [x] V10-b `_xruns_delta` rewrite (blind abort, per-second, lead-in)
- [x] `measure-soak-instrument.sh` for Gate 1 soak
- [x] `measure-v10b-validate.sh` (Closed Hat @ 15 must be non-zero)
- [x] Pi: V10-b validate **PASS** (Closed Hat @15 → 266 xruns)
- [ ] Pi: overnight soak running (`~/instrument-soak-1024x2.log`, finish ~08:00 Toronto)

## Pi

- Profile: **1024×2**, softmode 1, governor OFF
- Soak log default: `~/instrument-soak-1024x2.log`

*Last updated: 2026-08-22 (America/Toronto)*
