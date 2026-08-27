"""How many loops SooperLooper 1.7.9 actually gives you.

**Fifteen. Indices 0..14.** Not whatever you pass to `-l`.

Measured 2026-08-27 on the Pi 5 against the arm64 1.7.9 build, by writing a
control and reading it back on every index:

    -l 8  -t 40     all 8 usable
    -l 16 -t 10     [15] ignores writes
    -l 16 -t 40     [15] ignores writes
    -l 17 -t 40     [15, 16] ignore writes
    -l 20 -t 40     [15..19] ignore writes

`-t` makes no difference and neither does free memory (2.6 GB available during
the sweep). Asking for more than 15 just buys more phantoms.

A phantom index is the dangerous part. It is not absent — absent would be
honest, and index 16 with `-l 16` does time out. A phantom **answers `get` with
plausible defaults and silently discards every `set`**. So:

  * `/ping` reports the loop count you asked for, not the count you got.
  * Reading state works, so any health check built on reads passes.
  * `apply_grid_sync` sent quantize/sync to loop 15 like every other loop and
    the engine dropped them, leaving one track unquantized among fifteen
    quantized ones — a pad that records and stops unlike all its neighbours.

This cost a morning on 2026-08-27. It had been discovered once before and the
only record was a string in a bootstrap log line — "16 loops, scratch 14 —
loop 15 empty on Pi" — which read as stale copy about a deleted feature, so it
was deleted. The workaround (reserve loop 14 as "scratch") outlived the
explanation, and removing the workaround handed the phantom straight to the
player as track 15.

Hence this module: the constant and the reason travel together, and
`sl-health.py` proves the limit by writing rather than trusting `/ping`.
"""

from __future__ import annotations

import os

# Hard engine limit. Raising this does not create loops — it creates phantoms.
MAX_USABLE_LOOPS = 15

# The default everything should use. MPE_SL_LOOPS may lower it (measurement
# runs use fewer); it is clamped rather than allowed to exceed the engine.
DEFAULT_NUM_LOOPS = MAX_USABLE_LOOPS


def resolve_num_loops(env_value: str | None = None) -> int:
    """The usable loop count, clamped to what the engine can actually provide.

    Silently exceeding the limit is what produced a phantom track, so a request
    for more is clamped here rather than passed through to `-l`.
    """
    raw = env_value if env_value is not None else os.environ.get("MPE_SL_LOOPS", "")
    raw = (raw or "").strip()
    if not raw:
        return DEFAULT_NUM_LOOPS
    try:
        want = int(raw)
    except ValueError:
        return DEFAULT_NUM_LOOPS
    if want < 1:
        return 1
    return min(want, MAX_USABLE_LOOPS)
