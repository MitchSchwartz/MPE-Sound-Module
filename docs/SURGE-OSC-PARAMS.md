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
| `param5` | Threshold (pregain) | 0 dB unity | `db_attenuation_to_normalized(0)` |
| `param6` / `param7` | Attack / release | −1 = fast | `bipolar_to_normalized(-1)` |
| `param8` | Gain (post trim) | ceiling e.g. −1 dB | `db_attenuation_to_normalized(threshold_db)` |

Appliance ceiling is **`param8` only** — do not drive `/param/global/volume` from the limiter (conflicts with patch volume; raw dB on that path caused ~−54 dBFS silence).
