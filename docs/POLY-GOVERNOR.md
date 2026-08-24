# Dynamic voice limit (poly governor)

*Last updated: 2026-08-23 (America/Toronto)*

Surge's polyphony ceiling is not fixed at boot. A background service (`surge-poly-governor.service`) watches real-time DSP load and moves Surge's voice limit up and down so dense playing stays inside JACK's deadline. Surge's built-in softkill (`uber_release`) steals voices when the limit bites — there is no MIDI panic.

**Toggle:** Touch UI → System settings → **Sound → Audio…** → **Dynamic voice limit** (default on).  
**Disable entirely:** turn off in settings, or `MPE_POLY_GOVERNOR=0` in `/etc/mpe/mpe.env`.

---

## What it measures (v2)

With **`MPE_POLY_GOVERNOR_V2=1`** (default on current builds):

| Meter | Source | Used when |
|---|---|---|
| **Jack deadline load** | `mpe-peak-meter` → `dsp_percent` in `/run/mpe/meter.state` | `MPE_POLY_GOVERNOR_METER=jack` (Pi 5 tune path) |
| **Process CPU** | `/proc` sample for `surge-xt-cli` | Fallback / legacy threshold mode |

The v2 controller prefers **jack deadline stress** — how close Surge is to missing its audio callback — not raw CPU percent alone. That matches what you hear as crackle vs a clean voice steal.

---

## Control modes

Set in `/etc/mpe/mpe.env` via **`MPE_POLY_LIMIT_MODE`**:

### `always_on` (Pi 5 ear tune)

A **continuous curve** maps normalized jack load → voice limit between a rest top and **`MPE_POLY_FLOOR`**.

| Knob | Role |
|---|---|
| `MPE_POLY_JACK_BASELINE` | Platform idle load (%); stress = meter − baseline |
| `MPE_POLY_MIN_HEADROOM` | At zero stress, limit = ceiling − headroom (when rest cap off) |
| `MPE_POLY_REST_CAP` | **Pre-engaged cap** — fixed rest limit from boot (experiment); overrides headroom when > 0 |
| `MPE_POLY_LIMIT_HARD` | Load % where curve reaches floor |
| `MPE_POLY_RAMP_APPLY` | While load is rising, OSC limit tracks the curve immediately (no deferral) |
| `MPE_POLY_RISE_*` | Rate-of-rise bias — lead the curve on fast attacks |
| `MPE_POLY_LIMIT_MAX_STEP_DOWN` / `MPE_POLY_LIMIT_STEP_INTERVAL_S` | Rate-limit OSC steps so steals are gradual |
| `MPE_POLY_STEP_UP=0` | Hold engaged cap — no recovery bumps (pairs with rest cap experiment) |

**Static bounds** (still applied on patch load): `MPE_POLY_CEILING`, `MPE_POLY_FLOOR`, emergency floor at very high load / xruns (`MPE_POLY_EMERGENCY`, `MPE_POLY_EMERGENCY_XRUN_ONLY`).

### `progressive` (threshold-gated A/B)

Separate soft/hard load thresholds; limit steps down in bands. See `MPE_POLY_LIMIT_SOFT_START` / `MPE_POLY_LIMIT_HARD` in `config/mpe.env.example`.

---

## Static vs dynamic limit

On every patch load the browser:

1. Rewrites the patch for **Reuse Single** (same-key restrikes reuse a voice).
2. Sends Surge OSC **`/param/global/polyphony_limit`** to the configured **ceiling**.

The governor then **lowers** that limit under load and **raises** it when headroom returns (unless `MPE_POLY_STEP_UP=0`). The CPU meter in the touch header reflects engine headroom; orange/red is semantic load, not “governor off.”

---

## Platform notes

| Platform | Status |
|---|---|
| **Pi 4** | G2 recalibration closed 2026-08-23 — governor thresholds aligned to jack meter; see [`docs/measurements/PI4-CLOSEOUT-2026-08-23.md`](measurements/PI4-CLOSEOUT-2026-08-23.md) |
| **Pi 5** | Ear tune **paused** — best tune 97/3/7 + ramp apply; step-attack crackle open; pre-engaged cap @ 48 under test. Cooler + 27 W PSU gate before promotion. See [`docs/measurements/poly-governor-v2-always-on-pi5-2026-08-23.md`](measurements/poly-governor-v2-always-on-pi5-2026-08-23.md) |

Do not copy Pi 5 experimental env wholesale to Pi 4 player images until Gate B closes on stable hardware.

---

## Service and debugging

```bash
systemctl status surge-poly-governor
journalctl -u surge-poly-governor -n 30 --no-pager
cat /run/mpe/meter.state    # dsp_percent, xruns
python3 scripts/manual/test-poly-governor-osc.py   # OSC smoke (laptop or Pi)
```

**Spec:** [`Documents/specs/poly-governor-v2-progressive-spec.md`](../Documents/specs/poly-governor-v2-progressive-spec.md)  
**Env reference:** [`config/mpe.env.example`](../config/mpe.env.example) (search `MPE_POLY_`)
