# Dynamic voice limit (poly governor)

*Last updated: 2026-08-25 (America/Toronto)*

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

## Buffer dependence and meter saturation

*Found 2026-08-25 while ear-tuning the Pi 5 at `64 x 2`. Unresolved — this section records
the finding, not a fix.*

### The finding

At `MPE_JACK_BUFFER=64` / `MPE_JACK_PERIODS=2`, the governor's meter has **no usable
dynamic range**. Across ten minutes of live playing, `raw` took exactly two values:

```
raw=100.0
raw=99.9
```

With `MPE_POLY_JACK_BASELINE=97`, that makes `normalized = max(0, raw - 97)` a **constant
3**. The entire continuous curve — `REST_CAP`, `LIMIT_HARD`, rise bias — was being fed a
constant, and therefore did nothing.

What remained was a panic reflex: `reason=emergency` on an xrun, limit slammed to the
floor, then a slow crawl back at one voice per `LIMIT_RECOVER_HOLD_S` (5 s). Observed
live, `effective_poly=5` on a patch whose `native_poly=8`.

**Paired with the then-current `MPE_POLY_STEP_UP=0`, recovery never happened at all** — a
single xrun anywhere in a session pinned the limit at the emergency floor until the next
patch load. That is the mechanism behind the long-standing "needs reload" symptom.

### Two classes of buffer dependence

Both classes move with buffer size, but for **different reasons** — worth separating,
because they need different fixes.

| Class | Knobs | Why it moves | Fixable by scaling? |
|---|---|---|---|
| **Load thresholds** (%) | `JACK_BASELINE`, `LIMIT_HARD`, `emergency`, `spike`, `high`, `warm`, `low` | The **meter changes meaning** — fixed per-callback cost is paid 750x/s at 64 frames vs 47x/s at 1024 | **No** — see below |
| **Voice counts** | `CEILING`, `FLOOR`, `REST_CAP`, `EMERGENCY` | **Capacity changes** — at larger buffers more of the budget is DSP rather than overhead | Yes, but must be re-derived per platform *and* buffer |
| **Timing** | `STEP_INTERVAL_S`, `LIMIT_RECOVER_HOLD_S`, `high_hold`, `poll` | Does not move — these are perceptual, and a human does not care about period size | N/A — leave alone |

### Why rescaling the thresholds is not the fix

If the meter only ever reports 99.9-100.0, moving `JACK_BASELINE` to 99.5 buys half a point
of range. That is not a tune.

Whether the meter is genuinely saturating or honestly reporting ~100% of a 1.33 ms budget,
the operational consequence is identical: **there is no signal to control on**, and the
proportional controller degenerates into an xrun-triggered reflex.

The fix direction is an instrument with headroom in this regime — measured callback
**slack** rather than a percentage that pins. For scale: V1 recorded max callback lateness
of **917 us against a 10.7 ms deadline**; the same 917 us is **69% of budget at 64 frames**.
That is a meter that resolves where `dsp_percent` does not.

### Rule -1 exposure

**Nothing detects that the tune is stale.** Change `MPE_JACK_BUFFER` and every load
threshold silently becomes wrong — the service keeps running, keeps logging plausible
numbers, and quietly stops governing. A correctly-tuned governor and a decalibrated one are
indistinguishable from the outside.

Minimum viable fix, independent of any meter redesign:

1. **Derive `JACK_BASELINE` at startup** from a brief idle sample rather than reading a
   static env value.
2. **Assert** when the configured value is far from measured, and say so loudly.

Then buffer size can change and the zero point follows it.

### Gotcha: `EMERGENCY` clamps to `FLOOR`

`MPE_POLY_EMERGENCY` cannot exceed `MPE_POLY_FLOOR`. Setting `MPE_POLY_EMERGENCY=16`
against `MPE_POLY_FLOOR=4` silently yields `emergency_poly=4` in the startup line — no
warning. **`MPE_POLY_FLOOR` is the effective knob for panic depth**, because everything at
the bottom of the range clamps to it.

Always confirm against the service startup log rather than the env file:

```bash
journalctl -u surge-poly-governor.service --since '-30s' --no-pager | grep 'startup'
```

### The limit is lowered but not enforced

*Found 2026-08-25, live, on patch "Dark Strange" at `64 x 2`. This is the more consequential
half of the finding above.*

**Symptom.** Under rapid repeated notes, **new notes go silent while already-sounding notes
continue**. Volume swells back over tens of seconds. Strongly patch-dependent — not
reproducible on faster-releasing patches.

**That signature is not voice stealing.** Stealing kills *old* notes to free a slot for a new
one. Silent-new / sustained-old is the opposite, and it identifies **allocation denial**.

**Mechanism**, from this project's own instrumentation
([`poly-governor-instrumentation-2026-08-21.md`](measurements/poly-governor-instrumentation-2026-08-21.md) §143-147):

> `setParameter01()` for `polyphony_limit` updates storage; **`softkillVoice()` is not called**.
> Lowering `/param/global/polyphony_limit` via OSC **does not immediately cull** sounding voices.

So when the governor drops the limit from 45 to 16, the 45 sounding voices **keep playing and
keep costing CPU**. The new limit applies only to *allocation*. The result is a dead zone in
which every new note is silent until enough existing tails decay below the limit.

**Consequence — the governor's central action does not do what it claims.** Under xrun
pressure it lowers a limit that reduces no load, while producing a conspicuous musical
artifact. Load is unchanged; only the player is affected.

This also explains the patch dependence: only patches whose release tails outlive the recovery
window can hold voices long enough for the dead zone to bite.

**Two coherent fixes** (neither is a knob; not attempted):

1. **Cull on step-down** — actually invoke the softkill so lowering the limit enforces itself.
   Converts denial into stealing: still audible, but the conventional and expected behavior.
2. **Do not step down while voices are sounding** — only lower at low voice counts, so the
   limit can never trap the player.

**Refuted remedy.** Raising `MPE_POLY_FLOOR` to 32 to shorten the dead zone (shallower cut =
fewer tails to wait out) **produced crackle on Cloud Horn** — the protective range was consumed
and there is no spare headroom at `64 x 2`. Reverted to 4. The dead zone and the protection
trade directly against each other, and at this buffer size there is no setting that buys both.

**Practical mitigation today:** larger buffer. At `128 x 2` xruns are rare, so emergency drops
are rare, so the dead zone rarely triggers *and* the floor can stay protective.

### Pi 5 ear tune as of 2026-08-25

Live values after this session, at `64 x 2`. **Ear-tuned, not derived** — and sitting on
top of the saturated meter described above, so treat them as a comfort setting rather than
a calibration.

| Knob | Was | Now | Why |
|---|---|---|---|
| `MPE_POLY_REST_CAP` | 40 | **48** | Resting limit felt tight on rich patches |
| `MPE_POLY_STEP_UP` | 0 | **1** | Enables recovery at all — fixes the "needs reload" symptom |
| `MPE_POLY_FLOOR` | 4 | 4 *(unchanged)* | Tried 16, then 32; **32 caused crackle on Cloud Horn** — reverted |
| `MPE_POLY_EMERGENCY` | 3 (default) | 3 *(unchanged)* | Clamps to floor; reverted with it |

**Net kept:** only `REST_CAP` and `STEP_UP`. The floor experiment was run and **refuted** the
same session — see the refuted remedy above. `STEP_UP=1` is the substantive fix: it restores
recovery at all, which is what made a single xrun pin the limit until patch reload.

Backup of the pre-session values: `/etc/mpe/mpe.env.bak-20260825-160932` on `raspberrypi5`.

### Open questions

1. Is `dsp_percent` saturating, or honestly reporting ~100% of a 1.33 ms budget? These need
   different fixes and are not currently distinguishable from the log.
2. What is idle `dsp_percent` at 1024 / 512 / 256 / 128 / 64 on Pi 5? One table would make
   the baseline derivable instead of guessed.
3. Should voice-count knobs live in `config/platform/` per (platform, buffer) rather than a
   single `/etc/mpe/mpe.env` value?
4. Does the touch-header meter agree with the governor's meter? They disagreed visibly this
   session — UI green while the governor read 100 (see O4 instrument split).

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
