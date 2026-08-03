# Surge XT OSC parameter values

*Reference for MPE-Module OSC senders — not a full OSC address list.*

## Wire format (canonical)

Surge’s in-app spec (`OSC Settings → Show OSC Specification`) and
[`oscspecification.html`](https://github.com/surge-synthesizer/surge/blob/main/resources/surge-shared/oscspecification.html) state:

- **All numeric OSC values are floats**, even for bool/int parameters.
- **Float parameters use normalized 0.0–1.0** on the wire: `0` = minimum, `1` = maximum.
- Out-of-range values are clipped.
- Query replies (`/q/param/...`) return the same normalized float, then a human display string (e.g. `−1.00 dB`).

Surge converts with linear mapping (`Parameter::value_to_normalized` / `normalized_to_value` in `Parameter.cpp`):

```text
native = norm * (max − min) + min
norm   = (native − min) / (max − min)
```

**Do not send raw dB, Hz, or percent** unless you have confirmed that specific address uses a different ctrltype (scene amp volume is linear 0..1 — see `PATCH_NORMALIZATION.md`).

## Common ctrltypes (MPE-Module)

| Ctrltype | Native range | Example native → OSC |
|----------|--------------|---------------------|
| `ct_decibel_attenuation` | −48 .. 0 dB | 0 dB → `1.0`; −1 dB → `47/48`; −48 dB → `0.0` |
| `ct_percent_bipolar` | −1 .. 1 | −1 → `0.0`; 0 → `0.5`; 1 → `1.0` |
| `ct_decibel_extra_narrow` | −12 .. 12 dB | 0 dB → `0.5` |
| `ct_freq_audible_deactivatable_hp` | −60 .. 70 | −60 Hz → `0.0` |
| `ct_amplitude` (scene amp) | 0 .. 1 linear | 0.85 → `0.85` |

## Helpers

Python conversions live in [`patch_browser/surge_osc_params.py`](../patch_browser/surge_osc_params.py).

## Output limiter (Conditioner, global FX slot)

Conditioner params (`ConditionerEffect.cpp`):

| OSC suffix | Role | Native intent | OSC helper |
|------------|------|---------------|------------|
| `param5` | Threshold (**input drive**, not ceiling) | −6 dB (`MPE_LIMITER_DRIVE_DB`) | `db_attenuation_to_normalized(-6)` |
| `param6` / `param7` | Attack / release | **+1 = fastest** (higher native = faster, see below) | `bipolar_to_normalized(1)` |
| `param8` | Gain (post-limit output trim = the ceiling) | e.g. −1 dB (`MPE_LIMITER_THRESHOLD_DB`) | `db_attenuation_to_normalized(threshold_db)` |

Appliance ceiling is **`param8` only** — do not drive `/param/global/volume` from the limiter (conflicts with patch volume; raw dB on that path caused ~−54 dBFS silence).

### Attack/release direction (verified against `Conditioner.cpp`)

```cpp
float am = 1.0f + 0.9f * *pd_float[cond_attack];   // native -1..1
float attack = 0.001f * am * am;                    // HIGHER native -> LARGER coeff -> FASTER
```

Native `−1` → `am=0.1` → attack coeff `0.00001` → **~2.3s** time constant (effectively off).
Native `+1` → `am=1.9` → attack coeff `0.00361` → **~6ms** time constant.
Same relationship for release (`0.0001*rm*rm`): native `+1` ≈ 63ms, native `−1` ≈ 22.7s.

### Threshold = input drive, not the ceiling (verified against `Conditioner.cpp`)

```cpp
float pregain = storage->db_to_linear(-*pd_float[cond_threshold]);
...
la = max(1.f, la);   // detector floored at unity (0 dBFS) — inert below it
gain = 1.f / filtered_lamax2;
```

The gain-reduction detector does **nothing** until the (pregain-scaled) signal reaches 0 dBFS. At `threshold = 0 dB` (unity pregain), the detector only reacts once the raw signal is *already* at full scale — combined with attack smoothing being an exponential filter (not instant, even with the 128-sample/~2.9ms lookahead), fast-attack transients can punch several dB over 0 dBFS before gain reduction catches up. Driving `param5` more negative feeds extra gain into the detector so it trips earlier relative to true signal level, trading a bit of always-on gentle compression for real margin against fast transients (e.g. plucky "stab" patches). This is why Surge's own UI groups `Threshold/Attack/Release/Gain` together as **"Limiter"** (`group_label` id 2 in the source) — `Threshold` is meant to be driven, not left at unity.

`param8` (Gain) is the separate final output trim applied *after* limiting — that's the actual ceiling knob (`MPE_LIMITER_THRESHOLD_DB`).
