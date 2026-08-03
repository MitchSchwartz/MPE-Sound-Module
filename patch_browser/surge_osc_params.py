"""Surge XT OSC parameter value encoding (normalized 0..1 on the wire).

Canon: Surge resources/surge-shared/oscspecification.html — all float OSC
values are sent as 0.0..1.0 where 0 = param minimum and 1 = param maximum.
Query replies use the same normalized float (display string is separate).

Surge maps normalized ↔ native via Parameter::normalized_to_value /
value_to_normalized (linear per ctrltype min/max in Parameter.cpp).
"""

from __future__ import annotations


def linear_to_normalized(value: float, min_val: float, max_val: float) -> float:
    """Map a native parameter value into Surge OSC float 0..1."""
    if max_val == min_val:
        return 0.0
    return max(0.0, min(1.0, (float(value) - min_val) / (max_val - min_val)))


def normalized_to_linear(norm: float, min_val: float, max_val: float) -> float:
    """Map Surge OSC float 0..1 back to native parameter value."""
    n = max(0.0, min(1.0, float(norm)))
    return n * (max_val - min_val) + min_val


# ct_decibel_attenuation / ct_decibel_attenuation_clipper (Parameter.cpp)
DB_ATTENUATION_MIN = -48.0
DB_ATTENUATION_MAX = 0.0


def db_attenuation_to_normalized(db: float) -> float:
    """ct_decibel_attenuation: native −48..0 dB → OSC 0..1."""
    return linear_to_normalized(db, DB_ATTENUATION_MIN, DB_ATTENUATION_MAX)


def normalized_to_db_attenuation(norm: float) -> float:
    return normalized_to_linear(norm, DB_ATTENUATION_MIN, DB_ATTENUATION_MAX)


# ct_percent_bipolar
BIPOLAR_MIN = -1.0
BIPOLAR_MAX = 1.0


def bipolar_to_normalized(value: float) -> float:
    """ct_percent_bipolar: native −1..1 → OSC 0..1."""
    return linear_to_normalized(value, BIPOLAR_MIN, BIPOLAR_MAX)


def normalized_to_bipolar(norm: float) -> float:
    return normalized_to_linear(norm, BIPOLAR_MIN, BIPOLAR_MAX)


# ct_decibel_extra_narrow / ct_decibel_extra_narrow_deactivatable
DB_EXTRA_NARROW_MIN = -12.0
DB_EXTRA_NARROW_MAX = 12.0


def db_extra_narrow_to_normalized(db: float) -> float:
    return linear_to_normalized(db, DB_EXTRA_NARROW_MIN, DB_EXTRA_NARROW_MAX)


# ct_freq_audible_deactivatable_hp (Conditioner Side Low Cut)
FREQ_AUDIBLE_HP_MIN = -60.0
FREQ_AUDIBLE_HP_MAX = 70.0


def freq_audible_hp_to_normalized(value: float) -> float:
    return linear_to_normalized(value, FREQ_AUDIBLE_HP_MIN, FREQ_AUDIBLE_HP_MAX)
