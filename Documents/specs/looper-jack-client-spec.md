# Looper as JACK callback client — swappable mixer, realtime discipline

> **⚠ SUPERSEDED 2026-08-13** — The Python JACK callback path (Tasks 1–11) is
> **rejected**. Python must not run on the audio thread. See
> [`DECISIONS.md`](../DECISIONS.md) and [`DIRECTION.md`](../DIRECTION.md).
> Keep §A.2 and §B.3/criterion 19 as analysis reference only. Do not execute
> the task table below.

**Issue:** untracked
**Status:** Draft — design only. Implements **Phase 2** of
[`jack-audio-engine-spec.md`](jack-audio-engine-spec.md) (criteria 7, 8, 9, 11).
Phase 1 is on `yolo/jack-audio-engine-phase1` (PR #49), Gate B soaked.
**Created:** 2026-08-12
**Last updated:** 2026-08-12 23:21 (America/Toronto)

**Register:** everything below is a working hypothesis unless labelled
**measured**. Measured facts carry a source — a file path with line numbers, a
git object, or a row from `jack-audio-engine-spec.md` §Evidence. Two of the
load-bearing decisions here (§C kill criterion, §D merge order) are *decisions
taken on incomplete data* and say so.

---

## Problem Statement

Phase 1 made `jackd` the permanent graph server and Surge a JACK client. It also
shipped a knowing regression: the looper is refused whenever the engine is JACK
(spec **D5**, criterion 10), because the looper captures `snd-aloop` with
`arecord` and plays to the DAC with `aplay`, and under JACK there is no loopback
stream to capture (`scripts/lib/engine-guard.sh:4-7`).

Today's looper is five processes and four buffer stages
(`patch_browser/looper_audio_io.py:9` `open_arecord`, `:34` `open_aplay`), costing ~40 ms
round trip across three unsynchronised clocks. Phase 2 makes the looper a JACK
callback client so Surge and the looper are processed in the same graph tick,
which is the entire point of choosing option D
(`docs/AUDIO-ENGINE-FOUNDATION.md` Part 8, *"D contains C, it does not compete
with it"*).

Three things stand between here and there, in increasing order of risk:

1. The mixer is `audioop`-based and returns fresh `bytes` per call
   (`patch_browser/looper_engine.py:226-293` on `yolo/looper-phase0`). A callback
   at one or two periods of latency cannot allocate per period.
2. The looper source is **not on `dev`**. It lives on `yolo/looper-phase0`
   (PR #48, unmerged) and Phase 1 deliberately stripped it.
3. **Nobody has demonstrated that a Python callback holds a 256-frame deadline
   on this hardware.** The governing spec lists this as an assumption that, if
   false, makes Phase 2 unshippable in Python
   (`jack-audio-engine-spec.md:285`).

## Goals

1. The looper records, overdubs and plays with Surge on the graph — no
   `snd-aloop` in the audio path, no `arecord`/`aplay` children (criterion 7).
2. A **swappable mixer interface** with a NumPy backend that is bit-exact
   against the shipped `audioop` path, so the swap is provably behaviour-neutral
   (criterion 8).
3. The realtime callback's allocation is **constant and non-retaining**, and the
   GC tail is **measured and recorded**, not assumed (criterion 9).
4. The D5 guard is deleted and both run together in the default engine
   (criterion 11).
5. A **compiled mix kernel is a drop-in**, not a rewrite, if the measurement
   says Python cannot hold the deadline (`AUDIO-ENGINE-FOUNDATION.md` Part 8,
   *"keep the mix function behind a clean swappable interface"*).

## Non-Goals

- **A compiled mix kernel now.** Deferred until measured — §C states the exact
  number that promotes it from deferred to required.
- **Bit-exactness as the shipping runtime format.** Criterion 8 is a
  *correctness bridge* between two implementations, not a statement that the
  JACK path stays int16. See §A.4.
- **Looping in `usb-host` / `usb-host-session` profiles.** `standalone` only,
  unchanged from Phase 1's Non-Goals.
- **Removing `snd_aloop` from the appliance.** Removing it from the *looper* and
  *Surge start* paths, yes. The module stays installable because patch
  normalization calibration `modprobe`s it
  (`patch_browser/calibration_loopback.py:34-46`). See §B.4.
- **JACK transport integration.** The looper keeps its own bar clock
  (`patch_browser/looper_bar_clock.py`). Spec Open Q2 stays open; sharing a
  sample clock already removes the drift that motivated the question.
- **Latency below 256 frames.** Hardware ceiling, unchanged
  (`jack-audio-engine-spec.md` §Evidence, DAC row).
- **Depending on an automatic ALSA fallback.** ALSA was removed entirely as a
  product audio path (2026-08-13 amendment to `jack-audio-engine-spec.md`).
  Nothing here assumes a fallback exists. See §D.5.

## Acceptance Criteria

Numbering continues `jack-audio-engine-spec.md`. Criteria **7, 8, 9, 11** are
that spec's Phase 2 rows, restated here with the verification made executable.
**Criterion 9 is materially revised** — see §C.1 for why the original cannot
pass.

| # | Criterion | Verification |
|---|-----------|--------------|
| 7 | Looper records and overdubs with Surge on the graph — no `arecord`/`aplay` children, no `snd_aloop` in the audio path | Manual play on Pi. `pgrep -P $(pgrep -f mpe-looper.py)` empty; `lsmod \| grep snd_aloop` empty **while the looper runs and no calibration is in flight** (§B.4); `jack_lsp -c` shows the insert topology of §B.3 |
| 8 | The NumPy int16 backend is **bit-exact** against `audioop` across the fixture set | `mpe test local looper` — `tests/test_looper_mix_parity.py` asserts `numpy.array_equal` (not `assertAlmostEqual`) over every case in `tests/fixtures/mixer_cases.py`, both backends in-process, plus the committed golden `.npz`. Laptop-runnable |
| 8b | The float32 backend agrees with the int16 backend within 1 LSB after quantization | Cross-profile test, same fixture set. Catches a bug present in only one backend |
| 9a | The **mixer** allocates zero bytes | `tracemalloc` delta == 0 across 10 000 `MixWorkspace.mix()` calls. Laptop-runnable |
| 9b | The **callback** allocation is bounded and flat | `tracemalloc` net retained delta between callback 1 000 and callback 100 000 == 0 bytes; per-callback peak identical at both checkpoints. Growth is the failure, not presence (§C.1) |
| 9c | GC is off in the looper process and stays quiet | `gc.isenabled()` False after warm-up; `gc.get_stats()` shows **zero** collections across a 10-minute run. If non-zero, pause distribution recorded and p99 < one period |
| 9d | Memory is flat — proves "GC off" is safe | `VmRSS` at t=60 s and t=600 s differ by < 1 MB |
| 9e | Callback timing recorded, with a stated verdict | `docs/measurements/looper-jack-callback-YYYY-MM-DD.md` holds p50 / p99 / p99.9 / max, xruns, and the §C.3 verdict applied to those numbers |
| 11 | The D5 guard is removed and both run together in the default engine | Boot with `MPE_LOOPER_ENABLED=1` (JACK is the only engine). Looper records. `engine.state` reports `looper=enabled`, never `guarded`. `grep -r LOOPER_GUARD` returns nothing outside CHANGELOG |
| 18 | Surge's direct connection to `system:playback` does not survive a Surge restart | Kill jackd, let D3's supervisor restart Surge, then `jack_lsp -c`: `Surge XT:out_1` connects **only** to `mpe-looper:in_L`. Guards the double-dry-path failure of §B.3 |
| 19 | Looper death does not silence the instrument | `pkill -KILL mpe-looper.py`; Surge reconnects to `system:playback` and audio returns. The insert must fail open |

---

## A. The swappable mixer interface

### A.1 What is being replaced

The shipped mixer is `mix_live_and_loops`
(`patch_browser/looper_engine.py:226-293`, branch `yolo/looper-phase0`). It takes
`bytes`, returns `bytes`, and on the `audioop` path performs a **left fold with
saturation at every step**:

```
loop_sum  = audioop.mul(layer0, 2, per_layer_gain)          # :259-261
for each remaining layer:
    loop_sum = audioop.add(loop_sum,
                           audioop.mul(layer, 2, per_layer_gain), 2)   # :262-267
if effective_live == 0.0: return loop_sum                   # :268-269
return audioop.add(audioop.mul(live, 2, effective_live), loop_sum, 2)  # :270-272
```

Gain policy (`:243-250`), preserved verbatim:

```
headroom       = 1/(live_gain + loop_gain)  if live_gain > 0 and live_gain + loop_gain > 1 else 1.0
effective_live = live_gain * headroom
per_layer_gain = loop_gain * headroom / n
```

### A.2 Exactly what `audioop` does — measured from the C source

The appliance is Debian 13 trixie (`docs/LATENCY-SPIKE.md:36`), whose Python has
no stdlib `audioop` (PEP 594 removed it in 3.13). The looper therefore runs
`audioop-lts` (`requirements.txt` on `yolo/looper-phase0`), which is the
CPython C module repackaged — [`AbstractUmbra/audioop`](https://github.com/AbstractUmbra/audioop),
`audioop/_audioop.c`. **Measured** by reading that source at
`dc1e6078` (symbol references, not line numbers — the file is vendored and
renumbers between releases):

| Fact | Symbol in `_audioop.c` |
|---|---|
| `maxvals[2] = 0x7FFF`, `minvals[2] = -0x8000` | `maxvals[]` / `minvals[]` |
| `fbound(v, min, max)` clamps, then **`floor()`**, then casts | `fbound()` |
| `mul` widens each sample to C `double`, multiplies, applies `fbound` | `audioop_mul_impl()` |
| `add` for `width < 4` is an **integer** add clamped to `[minval, maxval]` — no rounding | `audioop_add_impl()` |
| Length mismatch raises `audioop.error("Lengths should be the same")` | `audioop_add_impl()` |
| Non-multiple-of-width length raises `audioop.error("not a whole number of frames")` | `audioop_check_parameters()` |
| Samples are read **native-endian** via `GETINT16` | `GETRAWSAMPLE` macro |

Three consequences that are where "sample-identical" normally breaks, and each
one is a real trap here:

1. **`mul` rounds toward −∞, not to nearest.** `3 × 0.5 → floor(1.5) = 1`;
   `−3 × 0.5 → floor(−1.5) = −2`. Any NumPy implementation using `np.rint`,
   `np.round`, or Python's `round()` diverges on odd magnitudes.
2. **The fold clips at every pairwise add.** `add(add(a,b),c)` is not
   `clip(a+b+c)`: with `a=b=30000, c=−30000` the fold yields `2767` and the wide
   sum yields `30000`. **The illustrative NumPy snippet in
   `AUDIO-ENGINE-FOUNDATION.md` Part 3 (`acc += …; np.clip(acc)`) is a single
   wide accumulate and is therefore not bit-exact.** It is a good sketch of the
   performance argument and a wrong sketch of the semantics.
3. **`mul` multiplies in `double`.** Multiplying in `float32` would first round
   the gain itself to `float32`, changing the product. The NumPy backend
   multiplies in `float64`; at int16 magnitudes this costs nothing and removes
   the argument.

**The pure-Python fallback in `looper_engine.py:274-293` is not a valid
reference.** It uses `round()` (half-to-even) where `audioop` uses `floor()`, and
accumulates in float with no intermediate clipping. The existing parity test
(`tests/test_looper_engine.py`, `test_mix_live_and_loops_backends_agree_three_layers`)
asserts agreement only to `delta=3`, which is why the divergence never surfaced.
**Criterion 8's reference is `audioop`. Full stop.** The interpreted fallback is
deleted in Task 5, not ported.

### A.3 The interface

New module `patch_browser/looper_mix.py`. No ALSA imports, no JACK imports, no
import from any file that lives only on `yolo/looper-phase0` — this is what makes
§E's independent scope real.

```python
# patch_browser/looper_mix.py
from __future__ import annotations
from typing import Protocol, Sequence, runtime_checkable
import numpy as np
import numpy.typing as npt

Int16Block   = npt.NDArray[np.int16]    # shape (frames, 2), C-contiguous, interleaved L,R
Float32Block = npt.NDArray[np.float32]  # shape (frames, 2), C-contiguous, values nominally [-1.0, 1.0]

MAX_LAYERS   = 8      # APC row 0 is 8 pads (docs/APC-LOOPER-UX.md)
MAX_FRAMES   = 1024   # largest MPE_JACK_BUFFER we accept; workspace is sized for this
S16_SCALE    = 32767.0

class MixShapeError(ValueError):
    """Raised before any write to `out` — `out` is never left half-mixed."""

class MixGains(NamedTuple):
    live: float
    per_layer: float

def mix_gains(live_gain: float, loop_gain: float, layers: int) -> MixGains: ...
    # The `looper_engine.py:243-250` headroom law, factored out so both backends
    # and the parity test share it. Gain math is not what criterion 8 compares.

@runtime_checkable
class MixBackend(Protocol):
    name: str                 # "audioop" | "numpy-int16" | "numpy-float32" | "compiled-<x>"
    dtype: np.dtype           # np.dtype(np.int16) | np.dtype(np.float32)
    allocates_in_mix: bool    # True only for the audioop reference backend

    def mix_into(
        self,
        out: np.ndarray,
        live: np.ndarray,
        layers: Sequence[np.ndarray],
        gains: MixGains,
    ) -> None: ...
```

Concrete backends, all implementing `MixBackend`:

```python
class AudioopBackend:      # reference only; wraps bytes<->ndarray, allocates. Never shipped in the callback.
class NumpyInt16Backend:   # bit-exact vs AudioopBackend. Criterion 8 target.
class NumpyFloat32Backend: # the shipping RT path. Criterion 8b target.
```

And the realtime holder, which is what the callback actually touches:

```python
class MixWorkspace:
    def __init__(self, backend: MixBackend, *, max_frames: int = MAX_FRAMES,
                 max_layers: int = MAX_LAYERS) -> None: ...
        # Allocates every buffer here, once, off the RT thread.

    frames: int                       # active block length; set by set_blocksize(), never in mix()
    def set_blocksize(self, frames: int) -> None: ...   # non-RT; slices, never reallocates

    def layer_view(self, index: int) -> np.ndarray: ...  # preallocated (max_frames, 2) scratch
    def live_view(self) -> np.ndarray: ...
    def out_view(self) -> np.ndarray: ...

    def mix(self, active_layers: int, gains: MixGains) -> None: ...
        # RT path. Unchecked by construction: every shape was validated in __init__
        # and set_blocksize(). No allocation, no branch that can raise.
```

**Contract, stated so a compiled backend can satisfy it unchanged:**

| Concern | Rule |
|---|---|
| Ownership | `out` is caller-owned and preallocated. The backend writes into it and returns `None`. It never allocates, never resizes, never retains a reference past the call. |
| In-place | Always. There is no returning variant. This is the break from `mix_live_and_loops`, which returns `bytes`. |
| Aliasing | `out` may alias `live` (in-place live gain). `out` must **not** alias any element of `layers`; `MixWorkspace` guarantees this by owning all of them. |
| Shape | Every array `(frames, 2)`, C-contiguous, identical `frames`, `dtype == backend.dtype`. `len(layers) <= MAX_LAYERS`. |
| Validation | `mix_into()` validates and raises `MixShapeError` **before** touching `out`. `MixWorkspace.mix()` does not validate — its preconditions were established at construction. Two entry points, one for tests and one for the RT thread. |
| Empty | `layers == []` is legal: `out = clip(live * gains.live)`. `frames == 0` is legal and a no-op. |
| Saturation | int16 backends: `audioop` semantics exactly (§A.2). float32 backend: sum in float32 headroom, single `np.clip(out, -1.0, 1.0)` at the end. |
| Determinism | No RNG, no dependence on `np.seterr` state, no `np.errstate` context inside the RT path. |

**Bit-exact int16 kernel** — the shape the parity test pins:

```python
# per layer, replicating audioop.mul
np.multiply(layer, gain, out=scratch_f64)          # float64, as audioop does
np.floor(scratch_f64, out=scratch_f64)             # round toward -inf, NOT rint
np.clip(scratch_f64, -32768.0, 32767.0, out=scratch_f64)
scaled = scratch_f64.astype(np.int16, copy=False)  # into preallocated int16 scratch

# fold, replicating audioop.add — clip at EVERY pairwise step
np.add(acc_i32, scaled_i32, out=acc_i32)
np.clip(acc_i32, -32768, 32767, out=acc_i32)
```

`np.clip` before the cast, not after: casting out-of-range float64 to int16 is
undefined-ish in NumPy 2 and would be a silent divergence.

### A.4 int16 versus float32 — the tension criterion 8 hides

JACK's wire format is `jack_default_audio_sample_t`, i.e. **float32**:
`Port.get_buffer()` returns `blocksize * ffi.sizeof('float')` bytes and
`get_array()` uses `dtype=np.float32`
([`spatialaudio/jackclient-python`](https://github.com/spatialaudio/jackclient-python) `src/jack.py`).
Keeping the mixer int16 in the callback would add two conversions per period and
would quantize Surge's output to 16 bits inside a graph whose DAC path measured
`S24_3LE` (`jack-audio-engine-spec.md` §Evidence). That is a *regression against
Phase 1*, dressed up as compliance with criterion 8.

**Decision — D-L1.** Criterion 8 is satisfied by the **int16 backend**, which is
the correctness bridge and the format of the loop **store**. The **float32
backend** is the shipping RT path. Both share `mix_gains()`; criterion 8b pins
them to each other within 1 LSB. Read criterion 8 as *"the NumPy mixer's int16
backend is bit-exact with `audioop`"*, not *"the shipping mixer is int16"*.
Rationale for int16 storage: a 4-bar 120 bpm stereo loop is 384 000 frames
(`test_looper_engine.py`, `test_loop_length_frames_four_bars_120bpm`) = 1.5 MB as
int16 against 3.0 MB as float32, times 8 clips.

Conversion lives at the ring boundary, not in the mix, using one named constant
in both directions:

```
capture : int16 = np.rint(np.clip(f32, -1.0, 1.0) * S16_SCALE)
playback: f32   = int16 * (1.0 / S16_SCALE)
```

Symmetric, no DC offset, exact round-trip for every int16 except `-32768`, which
clamps to `-1.0`.

### A.5 The fixture set does not exist

**Measured:** `git ls-tree -r --name-only yolo/looper-phase0` matches no `.wav`,
`.raw`, `.pcm`, `fixture` or `testdata` path. The existing tests build byte
strings inline (`tests/test_looper_engine.py`, `_stereo_frame`). Criterion 8's
*"the existing fixture set"* refers to something that is not in the repository.
**Task 1 creates it.**

`tests/fixtures/mixer_cases.py` — a deterministic generator, plus a committed
golden `tests/fixtures/mixer_golden_audioop.npz` produced once from the
`audioop` backend and checked in, so the parity test survives `audioop-lts` being
removed from `requirements.txt` in Task 5. Required cases, chosen because each
one breaks a plausible NumPy implementation:

| # | Case | Breaks |
|---|---|---|
| 1 | `live_gain == 1.0`, no layers | The `bytes`-identity short-circuit at `looper_engine.py:239-240` |
| 2 | `live_gain == 0.0`, ≥ 1 layer | The early return at `:268-269` |
| 3 | `-32768 × 0.5` | Negative-clamp branch `val < minval + 1.0` |
| 4 | `±3 × 0.5` and other odd magnitudes | `floor` vs `rint` — the classic |
| 5 | Three layers with `a=b=30000, c=-30000` | Fold order and intermediate clipping |
| 6 | Layers that saturate individually before summing | Per-layer `fbound` in `mul` |
| 7 | 0, 1, 3, 6, 8 layers | `per_layer_gain` divisor, `MAX_LAYERS` |
| 8 | All-zero, all-`32767`, all-`-32768` | Boundary arithmetic in `int32` accumulate |
| 9 | Non-power-of-two frame counts (1, 3, 255, 257) | Vectorization tail handling |
| 10 | `live_gain + loop_gain > 1` and `<= 1` | Both `headroom` branches at `:243-249` |

Assertion is `numpy.array_equal`. No `assertAlmostEqual`, no `delta=`.

---

## B. The JACK callback client

### B.1 Binding — `python3-jack-client`, from apt

**Decision — D-L2.** Use the `JACK-Client` binding (import name `jack`),
installed as the Debian package **`python3-jack-client`**.

| Fact | Source |
|---|---|
| `python3-jack-client` **0.5.3-1** is in Debian **trixie** | packages.debian.org/trixie/python3-jack-client |
| Depends `python3-cffi`, `libjack-jackd2-0 (>= 1.9.10+20150825)`; **suggests** `python3-numpy` | Debian source control file, `python-jack-client 0.5.3-1` |
| Upstream version is 0.5.5 | `src/jack.py:28`, `__version__ = '0.5.5'` |
| API used here exists in 0.5.3 | `Client`, `inports`/`outports.register`, `set_process_callback`, `set_xrun_callback`, `set_port_connect_callback`, `activate`, `connect`, `blocksize` — all present in `src/jack.py` |

apt rather than pip because trixie is PEP 668 externally-managed, which this
repo already documents (`docs/BUILD-FROM-ZERO.md:79`). `python3-numpy` comes from
apt for the same reason. `requirements.txt` gains a comment pointing at the apt
packages rather than pip lines that will fail on the appliance.

**Rejected: hand-rolled `ctypes` against `libjack.so.0`.** The binding is a thin
cffi layer over the same symbols, is packaged and versioned, and — the deciding
point — already gets the callback-thread GIL acquisition right, which is
precisely the part a hand-roll gets wrong and the part whose failure mode is
"works on the bench, deadlocks on stage."

### B.2 Ports

Client name `mpe-looper`. Four audio ports:

```
inports.register('in_L'),  inports.register('in_R')
outports.register('out_L'), outports.register('out_R')
```

No JACK MIDI ports — the APC stays on ALSA rawmidi via `python-rtmidi`
(`scripts/mpe-looper.py:85-120`), read from the main thread. Bringing it onto the
graph is not needed to share a sample clock and adds a second binding.

### B.3 Topology — the looper is an insert, not a send

The looper mixes the **live** signal itself:
`clip_matrix.process_period` ends in `mix_live_and_loops(live_pcm, loop_chunks,
live_gain=…, loop_gain=…)` (`patch_browser/clip_matrix.py:202-207`). So Surge's
dry path must go *through* the looper, not alongside it:

```
Surge XT:out_1  →  mpe-looper:in_L        mpe-looper:out_L  →  system:playback_1
Surge XT:out_2  →  mpe-looper:in_R        mpe-looper:out_R  →  system:playback_2
Surge XT:out_{1,2}  ✗  system:playback_{1,2}     ← must be DISCONNECTED
```

Leaving Surge's direct connection in place gives two dry paths one period apart —
comb filtering, not doubling. And **this is not a one-shot at startup**:
`jack-audio-engine-spec.md` §Evidence records that Surge **auto-connects** to
`system:playback` with no `jack_connect`, and D3's supervisor restarts Surge on
jackd death and on promotion to the graph. Every Surge restart re-creates the
connection.

**Decision — D-L3.** Topology is reconciled continuously, not asserted once. The
looper registers `set_port_connect_callback` and `set_port_registration_callback`
and runs a reconciler **on the main thread** (both callbacks arrive on JACK's
non-RT notification thread; the reconciler must not call back into the daemon
from inside them). Criterion 18 tests exactly this.

**Fail-open (criterion 19).** With the looper as an insert, killing it silences
the instrument — strictly worse than Phase 1. On clean shutdown the looper
restores `Surge XT:out_{1,2} → system:playback_{1,2}` before deactivating. On an
unclean death, `surge-watchdog.sh` gains one check: engine is JACK, Surge has no
connection to `system:playback`, and no `mpe-looper` client is registered →
reconnect Surge directly. This reuses the supervisor D3 already built rather than
adding a second one.

### B.4 Retiring `snd-aloop` — criterion 7 needs qualifying

Deleted in Phase 2: `patch_browser/looper_audio_io.py` (the `arecord`/`aplay`
openers), `patch_browser/looper_alsa_stderr.py` (their stderr xrun scraper —
replaced by `set_xrun_callback`), `scripts/looper-audio-route.sh`, and the
loopback tier in `patch_browser/looper_devices.py`.

**But `snd_aloop` cannot be unconditionally absent, and the spec's criterion 7
verification says it must be.** Two consumers remain, both **measured** in the
tree:

| Consumer | Evidence |
|---|---|
| Patch normalization calibration `modprobe`s it and waits for the card | `patch_browser/calibration_loopback.py:34-46` |
| Calibration teardown unloads it when idle | `patch_browser/calibration_teardown.py:27-35` |

Phase 1 already removed it from the Surge start path — `scripts/start-surge-cli.sh:115-119`
sources `lib/unload-snd-aloop.sh`, which unloads only when the refcount is zero.
So **most of criterion 7's `snd-aloop` half is already done**, and the remainder
is deleting the looper's own route. This answers spec **Open Q3** (*"confirm
nothing else depends on it first"*): one thing does — calibration — and it
load/unloads on demand, so nothing blocks. Criterion 7's verification is
qualified accordingly in the table above.

### B.5 Process and threading model

One process. No children (criterion 7 verifies `pgrep -P` is empty).

| Thread | Owner | Work | May allocate |
|---|---|---|---|
| JACK RT | libjack | `process(frames)` — ring reads into workspace, `MixWorkspace.mix()`, write out | Bounded and flat only (§C.1) |
| JACK notification | libjack | `set_xrun_callback`, `set_port_connect_callback`, `set_shutdown_callback` — counters and flags only | Yes (non-RT; upstream docs: *"received in a separated non RT thread"*) |
| Main | us | APC MIDI poll, timing publish to tmpfs, HUD, topology reconcile, 5 s summary | Yes |

**RT ↔ main handoff is lock-free and allocation-free.** No `queue.Queue`, no
`threading.Lock`, no `logging` call anywhere reachable from `process()`.

- **main → RT (transport commands):** a preallocated command object whose fields
  are overwritten and then published by a single attribute store to a sequence
  counter. A CPython attribute store is atomic with respect to the GIL, so the RT
  thread sees either the old or new value, never a tear. The RT side reads the
  counter, and only re-reads the fields when it changed.
- **RT → main (state):** plain integer counters and the preallocated
  `MsHistogram` from `patch_browser/looper_period_debug.py`, which is already
  documented as allocation-free (*"add() is an index + increment"*). Main reads
  them without locking; a torn read costs one stale HUD frame.

**Blocksize changes must not reallocate on the RT thread.** `MixWorkspace` is
built for `MAX_FRAMES = 1024`; `set_blocksize_callback` records the new length
and the next `process()` uses a shorter slice. jackd's period is fixed by
`MPE_JACK_BUFFER` (spec D6), so this is a safety net rather than a feature.

**Realtime scheduling is JACK's job, not ours.** Spec **D4** already establishes
that the client audio thread gets its priority from the server (measured: jackd
70, Surge 65). The looper does **not** wrap itself in `chrt`, and
`MPE_LOOPER_RT_PRIORITY` (added on `yolo/looper-phase0` in `cf32d9c`) is ignored
when the engine is JACK, logged the same way `MPE_SURGE_RT_PRIORITY` is.

---

## C. Realtime discipline — the highest-risk part

### C.1 Criterion 9 as written cannot pass, and the reason is in the binding

**Measured, from `spatialaudio/jackclient-python` `src/jack.py`:**

```python
def get_buffer(self):
    blocksize = self._client.blocksize
    return _ffi.buffer(_lib.jack_port_get_buffer(self._ptr, blocksize),
                       blocksize * _ffi.sizeof('float'))

def get_array(self):
    import numpy as np
    return np.frombuffer(self.get_buffer(), dtype=np.float32)
```

Every `get_buffer()` allocates a fresh cffi buffer object; every `get_array()`
allocates that **plus** an ndarray wrapper. Four ports gives **eight short-lived
Python objects per callback**, as a floor, before any of our code runs. And
caching them across callbacks is explicitly unsafe — the same docstring:

> *"Caching output ports is DEPRECATED in JACK 2.0, due to some new optimization
> (like 'pipelining'). Port buffers have to be retrieved in each callback for
> proper functioning."*

Upstream is equally direct in `set_process_callback`: Python *"is not really
suitable for real-time processing… If you can live with some random audio
drop-outs now and then, feel free to continue using Python!"*

So `tracemalloc` delta == 0 across N callbacks is **not achievable** through this
binding's public buffer API. The criterion as written would either fail forever
or get quietly reinterpreted, and quiet reinterpretation of an acceptance
criterion is how a spec stops meaning anything.

**Decision — D-L4.** Criterion 9 splits into 9a–9e (see the criteria table). The
invariant that actually protects the deadline is **flat, not zero**: constant
per-callback allocation with zero net retention. The mixer, which is *our* code,
is still held to exactly zero (9a).

### C.2 GC policy — disable, don't tune

**Decision — D-L5.** The looper process calls `gc.freeze()` after warm-up and
then `gc.disable()`. Automatic collection never runs on the RT thread.

Rationale: with GC enabled, the question is *"how long is the pause and is it
under 5.333 ms?"* — probabilistic, load-dependent, and hard to bound. With GC
disabled the question becomes *"does memory grow?"* — deterministic, cheap to
measure, and covered by 9d. CPython's refcounting still reclaims every
non-cyclic object immediately, which is all the callback creates if it is written
correctly; flat RSS over ten minutes is the proof that there are no cycles.
`gc.freeze()` first so that if GC is re-enabled for diagnostics, the startup
object graph is not rescanned.

Residual risk, stated rather than hidden: `free()` on the RT thread can still take
a glibc arena lock. Mitigation is `mlockall(MCL_CURRENT|MCL_FUTURE)` plus a
warm-up that touches the arena; jackd's `@audio` limits already grant
`memlock unlimited` (`jack-audio-engine-spec.md` §Security Considerations).
`JACK-Client` does not expose `mlockall`, so it goes through `ctypes` against
libc — **Open Question OQ-3**, because whether it is needed is a measurement, not
a belief.

### C.3 Measurement methodology

**Instrument, do not invent.** `patch_browser/looper_period_debug.py` already
contains `MsHistogram`: fixed-edge, preallocated, `add()` is an index and an
increment, bucket width `budget/16`, 128 buckets. Reuse it. Two `time.perf_counter_ns()`
calls at callback entry and exit, one `hist.add()`.

Reported per run:

| Statistic | How |
|---|---|
| p50 / p99 / **p99.9** / max callback duration | `MsHistogram.percentile()` |
| Looper-attributed xruns | `set_xrun_callback` count, on the non-RT thread |
| Graph load | `client.cpu_load` sampled by the main thread on the 5 s summary |
| GC | `gc.get_stats()` collection counts at t=0 and t=600 s |
| Memory | `VmRSS` from `/proc/self/status` at t=60 s and t=600 s |
| Allocation | `tracemalloc` snapshots at callback 1 000 and 100 000 |

Two caveats that must appear in the recorded result or the number is misleading:

1. `MsHistogram.percentile()` is nearest-rank on the bucket's **upper edge**, so
   every reported percentile is an **upper bound**. Conservative in the right
   direction for a kill test, but it must be labelled.
2. p99.9 needs ≥ 1 000 samples. At 256 frames / 48 kHz there are 187.5 callbacks
   per second, so the minimum honest window is ~5.3 s and the 10-minute run gives
   ~112 500 samples. At budget/16 = 0.333 ms per bucket and 128 buckets, the top
   edge is ~42.7 ms — wide enough that the tail lands in a real bucket rather than
   overflow.

**Fixed run conditions.** Change one of these and the numbers are not comparable:

```
profile          standalone           (Phase 2 targets standalone only)
MPE_JACK_BUFFER  256    MPE_JACK_PERIODS 3    48 kHz, 24-bit
CPU governor     performance
Surge load       the heavy patch + 8-note chord that produced "mild crackle"
                 at 256x3 in jack-audio-engine-spec.md §Evidence
Looper load      4 layers playing, 1 recording for the first 30 s
Duration         10 minutes continuous
```

**Where results land.** `docs/measurements/looper-jack-callback-YYYY-MM-DD.md`,
one file per run, fixed table, plus the verdict from §C.4 applied to those
numbers. Index in `docs/measurements/README.md`. **The directory does not exist
yet** — Task 6 creates it. Spec criterion 9 already names `docs/measurements/`
as the destination, so this is filling in a promise the governing spec made.

### C.4 The kill criterion

Period at 256 frames / 48 kHz = **5.333 ms**. Applied to the §C.3 run:

| Measured | Verdict |
|---|---|
| p99 ≤ **1.07 ms** (20% of period) **and** p99.9 < **2.67 ms** (50%) **and** 0 looper-attributed xruns | **Ship Python at 256.** |
| p99 in (1.07, 2.67] ms, 0 xruns | **Re-run the protocol at 512 frames** (10.67 ms period) and ship there if it passes, logging the latency cost. 512×3 measured 32 ms with 0 xruns and is the config Mitch called *"sounds the best so far"* (§Evidence). |
| p99.9 ≥ **2.67 ms** at 512, **or** any looper-attributed xrun in the window — **while `MixWorkspace.mix()` measured alone is < 0.2 ms** | **Escalate to a compiled mix kernel** behind the same `MixBackend`. The cost is interpreter and binding overhead, not arithmetic, which is exactly what compiling removes. This is the trigger the governing spec's Non-Goals reserve. |
| A compiled kernel behind the same interface still fails p99.9 < one period | **Stop. Phase 1 + an I2S HAT.** The constraint is the Python↔JACK callback boundary, not the mixer. This is the spec's own Falsification Analysis outcome — *"if Phase 2 proves painful, the honest fallback is Phase 1 only + a HAT, not pushing through."* |

**Why 20% and 50%, and not 100% or a guess:**

- The looper is the **second** client in the graph. Surge is first, and §Evidence
  already records *mild crackle on a heavy patch + 8 notes at 256×3* — the cycle
  is near-full **before** the looper exists. A secondary client taking more than
  a fifth of the cycle is spending headroom that is already committed.
- 20% is calibrated against measured work, not intuition. The `audioop` mixer
  costs **0.090 ms per 512-frame period at three layers**
  (`AUDIO-ENGINE-FOUNDATION.md` §Appendix — median of 30 real mixes, on this Pi)
  ≈ **0.045 ms per 256-frame period** ≈ **0.85% of the period**. A 1.07 ms budget
  is roughly **24× the measured arithmetic**. Everything above 0.045 ms is
  interpreter, cffi and GIL. If Python cannot fit in 24× the C cost, tuning will
  not close the gap.
- 50% for the tail rather than 100%: a callback that merely touches the deadline
  is already an xrun in a shared graph, because Surge's cycle time is subtracted
  first. Half a period is the last point at which the overrun is still ours to
  fix instead of audible.

**This threshold is a decision on incomplete data and is meant to be revised
once — after the first run — and never again.** If the first measurement lands
in a band these numbers did not anticipate, revise the table in this file with
the measurement cited, before deciding. That is the discipline spec Open Q1 was
protecting when it declined to guess a number.

---

## D. Branch and dependency strategy

### D.1 Measured state

| Fact | Source |
|---|---|
| `dev` is **not** an ancestor of `yolo/looper-phase0`; merge-base is `da70ee5` | `git merge-base dev yolo/looper-phase0` |
| `yolo/looper-phase0` is **76 ahead / 1 behind** `dev` | `git rev-list --count` both directions |
| PR #48: +7264 / −94 across 69 files, 76 commits, `mergeable_state: clean` | GitHub API |
| PR #48 unchecked gate items: 10-min Pi passthrough @ 512; Surge→Loopback with live signal; one-bar loop mix + latency A/B; repeat @ 256 | PR #48 body |
| PR #48's passed item: *"Pi passthrough 30 s @ 512 — 0 xruns"*, annotated *"silence path; Surge→loopback routing still needed for signal"* | PR #48 body |
| **The D5 guard does not exist on `yolo/looper-phase0`** — `scripts/mpe-looper.py` `main()` has no engine check | `git show yolo/looper-phase0:scripts/mpe-looper.py`, and `scripts/lib/engine-guard.sh:9-12` says so explicitly |
| PR #49 (Phase 1) targets `dev`, `mergeable_state: unstable`, defers criterion 10 to the phase0 merge | GitHub API; `jack-audio-engine-spec.md:81` |

### D.2 Verdict

**PR #48 is a hard prerequisite for looper *integration* (Tasks 5, 7–11). It is
not a prerequisite for the *mixer* (Tasks 1–4, 6).**

`patch_browser/looper_mix.py` and `patch_browser/looper_ring.py` are new files
with new tests. They import nothing that lives only on `yolo/looper-phase0`. The
semantics they must reproduce — the `audioop` fold and the headroom law — are
transcribed exactly into §A.1 and §A.2 of this document, sourced to
`looper_engine.py:226-296` and to `_audioop.c`. The golden fixture is generated
from `audioop` itself, which is a pip/apt package, not a branch.

### D.3 The soak gate is soaking dead code

All four unchecked items on PR #48 exercise `snd-aloop` + `arecord`/`aplay` +
pipes — the architecture criterion 7 **deletes**. Ten minutes of Pi bench time
spent proving that pipeline is stable buys confidence in code that will not exist
after Task 8. That is the wrong thing to ask the repo owner for.

**Recommendation — D-L6.** Merge PR #48 to `dev` on the strength of its unit
suite and the already-passed 30 s zero-xrun run, with the four ALSA-pipeline soak
items **struck from the gate** and replaced by (a) the criterion 10 guard boot
test, which is cheap and has now been deferred twice, and (b) the §C.3 ten-minute
JACK-callback run, which becomes Phase 2's real gate. Merging cannot regress the
default appliance: with the guard in place and the default engine `jack`, the
merged looper is refused at runtime (spec D5).

### D.4 The human ask

Only Mitch can do this. It is one bench session, roughly 20 minutes, and it
replaces four.

> **Order matters: merge PR #49 (Phase 1) first, then #48.** #48's head
> (`811b6cc`) predates Phase 1 and touches `start-surge-cli.sh`,
> `detect-audio-device.sh` and `99-usb-audio.rules`, all of which Phase 1 rewrote.
> Rebasing #48 onto post-#49 `dev` is Task 0 and is agent work, not yours.
>
> **After Task 0 lands the guard on the rebased branch, run this once on the Pi:**
>
> 1. `git fetch && git checkout yolo/looper-phase0 && git pull && ./scripts/configure-pi-paths.sh --local --force`
> 2. Set `MPE_LOOPER_ENABLED=1` in `/etc/mpe/mpe.env`. Reboot.
> 3. `mpe engine status` → expect `looper=guarded`.
> 4. `journalctl -u mpe-looper -n 50` → expect **one** `LOOPER-GUARDED` line and
>    **no restart loop**.
> 5. `mpe status` → `mpe-looper.service` not cycling.
>
> **The question:** did the appliance boot with audio and exactly one guarded
> refusal? **Yes** → merge #48; that closes criterion 10 and criterion 13's full
> badge together. **No** → the guard is wrong, and Phase 2 integration waits while
> it is fixed.
>
> **Strike from PR #48's checklist** (each soaks the pipeline Phase 2 deletes):
> 10-min passthrough @ 512; Surge→Loopback live-signal passthrough; one-bar loop
> mix + latency A/B; repeat @ 256.

### D.5 ALSA removal and the guard message

ALSA was removed entirely as a product audio path (2026-08-13 amendment to
`jack-audio-engine-spec.md`). Effects on this design:

1. **No dependency.** Nothing in §A–§C assumes ALSA or auto-fallback exists. The
   looper is a JACK client; when there is no graph, there is no looper, and that
   is the correct behaviour.
2. **The guard message is final until Phase 2 ships.** `engine-guard.sh` and
   `patch_browser/audio_engine.py` refuse the looper whenever
   `MPE_LOOPER_ENABLED=1`, with no alternate engine to switch to. Criterion 11
   deletes the guard when the callback client lands.
3. **Guard policy is unconditional.** `looper_guard_blocked()` keys on
   `MPE_LOOPER_ENABLED` only (`patch_browser/audio_engine.py:36-38`), not on a
   configured engine — matching the post-amendment world where JACK is the only
   engine.

---

## E. Scope split

**Startable immediately, laptop only, zero dependency on PR #48 or the Pi:**

| Task | Output |
|---|---|
| 1 | Fixture set + golden `.npz` |
| 2 | `NumpyInt16Backend` + bit-exact parity test (criterion 8) |
| 3 | `NumpyFloat32Backend` + `MixWorkspace` + zero-allocation test (criteria 8b, 9a) |
| 4 | `LoopRing` with `read_into` / `write_from` |
| 6 | `docs/measurements/` and its result template |

These are five PRs that can start tonight against `dev`, before #48 moves.

**Blocked on PR #48 merged to `dev`:** Task 5 (rewire `ClipMatrix` /
`LooperSession` onto the workspace, delete the `bytes` mixer and the interpreted
fallback). This is where `clip_matrix.process_period` — which today allocates a
`list`, a fresh `bytes` per playing layer via `read_frames_for_loop`, and a
generator (`clip_matrix.py:159-207`) — becomes allocation-free. That method, not
the mixer, is the dominant per-period allocation today.

**Blocked on Pi hardware in the loop:** Tasks 7–11 — the callback client, the
topology reconciler, criterion 7's `snd-aloop` retirement, the §C.3 measurement
run, and guard removal.

---

## Task Breakdown

The repo has no sibling `*-tasks.md` convention (all four existing files in
`Documents/specs/` embed their phasing), so the breakdown lives here.

One PR each, targeting **`dev`**, branch `yolo/<slug>`, per
[`docs/GIT-WORKFLOW.md`](../../docs/GIT-WORKFLOW.md). Every PR runs
`mpe test local all` before opening. **🔒 = human gate (Mitch only).**

| # | Task | Where | Depends on | Acceptance |
|---|------|-------|-----------|-----------|
| **0** | 🔒 **Rebase `yolo/looper-phase0` onto post-#49 `dev`; add the D5 guard to `mpe-looper.py` `main()`** | Laptop + 🔒 Pi boot test | PR #49 merged | Conflicts resolved in `start-surge-cli.sh`, `detect-audio-device.sh`, `99-usb-audio.rules`. `main()` calls `looper_guard_blocked` / `looper_guard_exit_code` (`patch_browser/audio_engine.py:38-54`). Guard boot test of §D.4 passes → **criterion 10**. Then 🔒 merge #48 |
| **1** | Mixer fixture set + `audioop` golden | Laptop | none | `tests/fixtures/mixer_cases.py` covers all 10 rows of §A.5; `tests/fixtures/mixer_golden_audioop.npz` committed and regenerable by a documented one-liner; a test asserts the golden matches a live `audioop` run when `audioop` is importable, and skips cleanly when it is not |
| **2** | `NumpyInt16Backend` + bit-exact parity | Laptop | 1 | `patch_browser/looper_mix.py` with `MixBackend`, `mix_gains`, `AudioopBackend`, `NumpyInt16Backend`, `MixShapeError`. `tests/test_looper_mix_parity.py` asserts `np.array_equal` on every case. `MixShapeError` raised before any write to `out`. → **criterion 8** |
| **3** | `NumpyFloat32Backend` + `MixWorkspace` + allocation proof | Laptop | 2 | Cross-profile agreement within 1 LSB → **criterion 8b**. `tracemalloc` delta == 0 across 10 000 `MixWorkspace.mix()` calls → **criterion 9a**. `set_blocksize()` never reallocates (assert `ndarray.__array_interface__['data'][0]` is stable) |
| **4** | `LoopRing` — NumPy ring with `read_into` / `write_from` | Laptop | 3 | Semantics match `StereoRingBuffer` including `read_frames_for_loop`'s partial-clip zero-fill (`looper_engine.py:151-179`); `tracemalloc` delta == 0 across 10 000 `read_into` calls; the ring test cases from `tests/test_looper_engine.py` pass against it |
| **5** | Rewire `ClipMatrix` / `LooperSession` onto the workspace; delete the `bytes` mixer | Laptop | 4, **PR #48 merged** | `process_period` takes and fills preallocated arrays; `tracemalloc` delta == 0 across 10 000 calls with 4 layers. `mix_live_and_loops`, `mix_s16_stereo`, `apply_gain_s16_stereo`, `audio_mix_backend` and the interpreted fallback deleted; `audioop-lts` dropped from `requirements.txt`. Existing `tests/test_looper_engine.py` ring + quantize cases still pass |
| **6** | `docs/measurements/` scaffold | Laptop | none | Directory, `README.md` index, and the §C.3 result template with the two percentile caveats pre-written into it |
| **7** | 🔒 Install `python3-jack-client` + `python3-numpy` on the Pi | 🔒 Pi | none | `apt install python3-jack-client python3-numpy`; version recorded in `docs/measurements/README.md`; `BUILD-FROM-ZERO.md` updated. **Human gate: appliance package install** |
| **8** | JACK callback client — ports, callback, topology reconciler | Pi | 5, 6, 7 | `mpe-looper` registers 4 ports; insert topology of §B.3 with Surge disconnected from `system:playback`; no `arecord`/`aplay` children; `looper_audio_io.py`, `looper_alsa_stderr.py`, `looper-audio-route.sh` deleted → **criterion 7**. Topology survives a Surge restart → **criterion 18** |
| **9** | Fail-open on looper death | Pi | 8 | `surge-watchdog.sh` reconnects Surge to `system:playback` when the engine is JACK, Surge is unconnected, and no `mpe-looper` client exists. `pkill -KILL` test → **criterion 19** |
| **10** | GC discipline + callback instrumentation | Pi | 8 | `gc.freeze()` + `gc.disable()` after warm-up; `MsHistogram` wired to callback entry/exit; xrun count from `set_xrun_callback`; `tracemalloc` checkpoints at callback 1 000 / 100 000 → **criteria 9b, 9c, 9d** |
| **11** | 🔒 **The measurement run and the verdict** | 🔒 Pi | 10 | §C.3 protocol executed under the fixed conditions; result written to `docs/measurements/looper-jack-callback-YYYY-MM-DD.md`; §C.4 verdict applied and recorded → **criterion 9e**. **Human gate: this decides whether Phase 2 ships in Python** |
| **12** | Remove the D5 guard | Pi | 11 verdict = ship | `engine-guard.sh` deleted; `LOOPER_GUARD_MESSAGE`, `looper_guard_blocked`, `looper_guard_exit_code` removed; `engine.state` reports `looper=enabled`; HUD `L⛔` badge removed → **criterion 11**. `DECISIONS`-style row in `CHANGELOG.md` |

**Human gates, collected:** Task 0 (merge decision + guard boot test), Task 7
(appliance package install), Task 11 (the ship/escalate/stop verdict), and Task 12
only if Task 11 said ship. Nothing else needs Mitch.

**No schema changes anywhere in Phase 2.** No migrations, no
`/etc/mpe/mpe.env` key additions beyond what Phase 1 shipped.

---

## Security Considerations

- **Data flow:** local audio only. No network surface added. The looper gains no
  new file writes beyond the existing tmpfs timing state
  (`patch_browser/looper_timing_state.py`).
- **Privilege:** the looper stops needing `sudo modprobe snd-aloop`, so Phase 2
  **removes** a privileged call from the looper path
  (`scripts/looper-audio-route.sh` deleted). Calibration keeps its own.
- **Realtime:** the looper's audio thread gets its priority from jackd (D4), not
  from `chrt`. A runaway FIFO thread could starve the touch UI and SSH; priority
  remains a server-assigned constant, never user input.
- **New dependency:** `python3-jack-client` from Debian trixie main, and
  `python3-numpy`. Both distro-packaged, both already dependencies of packages
  the appliance installs. No pip, no PEP 668 override.
- **Input validation:** CLI arguments stay fixed enums so the Cursor allowlist
  boundary holds.
- **Failure modes:** the looper as an insert is a **new single point of failure**
  for all audio. Criterion 19 exists precisely for this and is not optional.
- **RLS:** N/A.

## Assumptions & Constraints

Assumptions whose falsity invalidates the plan, and how each is detected:

| Assumption | If false | Detect by |
|---|---|---|
| A Python callback holds a 256-frame deadline on this Pi | Phase 2 unshippable in Python; the compiled kernel moves from deferred to required | **Task 11**, against the §C.4 table. This is the governing spec's own listed assumption (`jack-audio-engine-spec.md:285`) |
| Surge's JUCE JACK backend tolerates being disconnected from `system:playback` and does not force the reconnection | The insert topology is unbuildable; the looper must become a send and stop mixing live, which changes the gain law | Task 8, `jack_lsp -c` immediately after disconnect and again 60 s later |
| Per-callback allocation is **constant**, not merely small | GC or arena growth eventually produces an audible dropout on a long set | Criteria 9b and 9d, over 10 minutes |
| The `audioop` fold is reproducible bit-exactly in NumPy | Criterion 8 is unmeetable as written and must be re-scoped to a tolerance | **Task 2 — laptop, immediate, no hardware.** This is the cheapest way to falsify the largest piece of §A |
| jackd's 256-frame period leaves room for a second client | The looper only ships at 512, at a real latency cost | Task 11 |

Constraints: Pi 4B Rev 1.5, Debian 13 trixie 13.5, kernel `6.18.34+rpt-rpi-v8`
`SMP PREEMPT` — **not** `PREEMPT_RT` (`docs/LATENCY-SPIKE.md:35-37`). jackd2
1.9.22. USB Full Speed DAC shared with LUMI and APC MINI. `standalone` profile
only.

## Evidence

Measured facts this design rests on, with sources. Everything not in this table
is a working hypothesis.

| Finding | Measurement | Source |
|---|---|---|
| `audioop.mul` rounds toward −∞ after clamping | `fbound()` — clamp, `floor()`, cast | `_audioop.c` `fbound()` @ `dc1e6078` |
| `audioop.add` for width 2 is an integer add with clamp, no rounding | `newval = val1 + val2` then clamp | `_audioop.c` `audioop_add_impl()` |
| int16 saturation bounds | `0x7FFF` / `-0x8000` | `_audioop.c` `maxvals[]` / `minvals[]` |
| `JACK-Client` allocates a cffi buffer + ndarray per `get_array()` | `_ffi.buffer(...)`, `np.frombuffer(...)` | `spatialaudio/jackclient-python` `src/jack.py` |
| Caching port buffers across callbacks is deprecated in JACK 2.0 | `get_buffer()` docstring | same |
| `python3-jack-client` 0.5.3-1 exists in Debian trixie | Depends `python3-cffi`, `libjack-jackd2-0`; suggests `python3-numpy` | packages.debian.org/trixie |
| `audioop` mixer cost, 3 layers, 512-frame period, this Pi | **0.090 ms** (median of 30 real mixes) = 0.8% of a 10.67 ms period | `AUDIO-ENGINE-FOUNDATION.md` §Appendix |
| Graph at 256×3 24-bit | 16 ms, 0 xruns idle; **mild crackle** on heavy patch + 8 notes | `jack-audio-engine-spec.md` §Evidence |
| Graph at 512×3 24-bit | 32 ms, 0 xruns, *"sounds the best so far"* | same |
| Surge auto-connects to `system:playback` with no `jack_connect` | `Surge XT:out_{1,2} → system:playback_{1,2}` | same |
| RT topology under jackd | jackd audio thread FIFO 70, Surge audio thread FIFO 65 | same |
| Calibration `modprobe`s `snd-aloop` and unloads it when idle | `ensure_snd_aloop()` / `unload_snd_aloop_if_idle()` | `calibration_loopback.py:34-46`, `calibration_teardown.py:27-35` |
| Phase 1 already unloads idle `snd-aloop` in the Surge start path | sources `lib/unload-snd-aloop.sh` | `start-surge-cli.sh:115-119` |
| The D5 guard is absent from `yolo/looper-phase0` | no engine check in `mpe-looper.py` `main()` | `git show yolo/looper-phase0:scripts/mpe-looper.py`; stated in `engine-guard.sh:9-12` |
| No mixer fixture files exist on any branch | no `.wav` / `.raw` / `.pcm` / `fixture` path | `git ls-tree -r --name-only yolo/looper-phase0` |
| `yolo/looper-phase0` is 76 ahead / 1 behind `dev` | merge-base `da70ee5` | `git rev-list --count` |

## Open Questions

Resolved here, recorded because the governing spec listed them:

- **Spec Open Q3 — `snd-aloop` retirement.** Answered in §B.4: calibration
  depends on it, load/unloads on demand, so nothing blocks. Criterion 7's `lsmod`
  check is qualified rather than absolute.
- **Spec Open Q2 — JACK transport.** Deferred by choice, not by ignorance: the
  drift that motivated the question is removed by sharing a sample clock. Revisit
  only if a second client needs to sync.

Still open, each with the exact thing that would close it:

| # | Question | Closed by |
|---|---|---|
| OQ-1 | Does JUCE's JACK backend re-assert `Surge XT:out_* → system:playback_*` after we disconnect it, without a Surge restart? If yes, the insert topology needs a different mechanism than a reconciler. | On the Pi with Phase 1 running: `jack_disconnect "Surge XT:out_1" system:playback_1 && sleep 60 && jack_lsp -c \| grep -A1 "Surge XT:out_1"`. **Do this before Task 8** — it is 60 seconds and it can invalidate §B.3. |
| OQ-2 | Is the appliance's Python 3.13 or 3.14? Determines whether `audioop-lts` is the only route and whether the Task 1 golden can be regenerated on the appliance at all. | `mpe sysinfo` on the Pi, or add a `python-version` line to it. Not blocking — the golden is committed. |
| OQ-3 | Is `mlockall` needed, or does jackd's client already do it for us? | Task 11: run the protocol once with and once without, compare p99.9. One variable, one comparison. |
| OQ-4 | Does `jackd -s` (softmode, kept per spec §Technical Notes) mask looper-caused xruns from `set_xrun_callback`, or are they still reported? | Task 10: force an overrun with a deliberate `time.sleep(0.02)` in the callback and confirm the counter increments. If softmode hides them, criterion 9e needs a different xrun source and the §C.4 table's "0 xruns" clause is unverifiable as written. |

## Rollback

- Tasks 1–6 are additive: new files, no call-site changes. Revert is a file
  deletion.
- Task 5 changes `ClipMatrix`; revert restores the `bytes` mixer. `audioop-lts`
  must come back into `requirements.txt` in the same revert.
- Tasks 8–12: `MPE_LOOPER_ENABLED=0` disables the looper without touching the
  graph. Full revert is `git revert` on `dev`; the Pi runs `main` until
  promotion.
- If Task 11's verdict is *stop*, Phase 1 stands on its own and the D5 guard
  stays permanently. That is a shippable end state, not a failure.
