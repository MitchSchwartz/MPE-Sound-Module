"""Per-channel peak levels from a WAV file.

Split out of check-audio-path.sh so the decision — "did signal arrive?" — is
testable without an interface, a tone generator, or a person to listen.
"""

from __future__ import annotations

import array
import sys
import wave

# A silent digital channel is exactly zero, so any real threshold only has to
# clear the capture noise floor. Measured on a Scarlett 4i4: idle analogue
# inputs sit around 0.006% FS, and a loopback of host audio is orders of
# magnitude above that.
SIGNAL_THRESHOLD_FS = 0.001  # 0.1% of full scale

_WIDTH_TO_TYPECODE = {1: "b", 2: "h", 4: "i"}


def peaks(path: str) -> list[int]:
    """Absolute peak per channel, in raw sample units."""
    with wave.open(path) as w:
        channels = w.getnchannels()
        width = w.getsampwidth()
        raw = w.readframes(w.getnframes())
    code = _WIDTH_TO_TYPECODE.get(width)
    if code is None:
        raise ValueError(f"unsupported sample width: {width}")
    samples = array.array(code)
    samples.frombytes(raw[: len(raw) - (len(raw) % (width * channels))])
    out = [0] * channels
    for i, v in enumerate(samples):
        c = i % channels
        a = -v if v < 0 else v
        if a > out[c]:
            out[c] = a
    return out


def peaks_fs(path: str) -> list[float]:
    """Peak per channel as a fraction of full scale (0.0 … 1.0)."""
    with wave.open(path) as w:
        width = w.getsampwidth()
    full = float(1 << (width * 8 - 1))
    return [p / full for p in peaks(path)]


def has_signal(level_fs: float, threshold: float = SIGNAL_THRESHOLD_FS) -> bool:
    return level_fs >= threshold


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: wav_peaks.py FILE [channels-to-check...]", file=sys.stderr)
        return 2
    path = argv[1]
    wanted = [int(a) for a in argv[2:]] or None
    levels = peaks_fs(path)
    failed = 0
    for idx, fs in enumerate(levels, start=1):
        if wanted is not None and idx not in wanted:
            continue
        ok = has_signal(fs)
        if not ok:
            failed += 1
        print(f"  ch{idx}: {fs * 100:8.4f}% FS  {'SIGNAL' if ok else 'SILENT'}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
