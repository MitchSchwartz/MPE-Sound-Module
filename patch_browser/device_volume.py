"""Hardware output level of the bound DAC — the one place that decides it.

The touch Vol fader used to trim Surge in software and nothing else. The DAC's
own level was a second, invisible stage: `set-dac-volume.sh` knew the Sound
Blaster by name and skipped every other card, so the FiiO KA1 came up at
whatever ALSA's restore rule last saved for it. Measured 2026-09-13:
/var/lib/alsa/asound.state held KA1 raw 87 (-20 dB), which alsamixer shows as
41% — the level Mitch found at a jam with no way to raise it.

Two modes, decided per bound card:

  device  exactly ONE playback-only volume element that is writable and carries
          a dB range. The fader IS that element; the stage gain carries a fixed
          DEVICE_MODE_SURGE_TRIM_DB (-9 dB) so nothing stacks and the graph
          keeps headroom. KA1 (PCM) and Sound Blaster (Speaker) qualify.
  trim    anything else — no element, no dB, or more than one candidate. The
          Scarlett 4i4 exposes four Line volumes behind a hardware knob, and
          choosing one would be a guess. The fader trims the stage gain
          (patch_normalization.stage_db).

Ambiguity goes to trim on purpose: the fader must show a value read from the
device, and a device we cannot read unambiguously cannot give us that.

Levels are remembered per USB model (usb:VID:PID) in dB. A model never seen
before starts at MPE_DAC_VOLUME_DB, never above DEFAULT_DB (-30 dB).

The fader position is ALSA's perceptual mapping (volume_mapping.c) — the same
percentage alsamixer and `amixer -M` show, so the numbers agree across tools.
"""

from __future__ import annotations

import json
import math
import os
import re
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path

DEVICE_MODE = "device"
TRIM_MODE = "trim"

# The stage gain's fixed cut while the DAC is the level stage. The only place it is set.
#
# Normalization (patch_normalization.py) calibrates each patch to -18 LUFS with
# a -3 dBTP peak cap on single test notes. Live chords with pressure stack well
# past that, and the FX host adds peaks of its own (phaser up to +5.4 dB) before
# clamping at +-1.0. In trim mode the fader's attenuation gave that headroom by
# accident; from 2026-09-13 (a51a975) device mode put Surge at unity, and Mitch's
# hand-calibrated favourites started reaching 0 dBFS. The DAC has the range to
# make this up in analogue: the KA1 sat at -41 dB of -63.5..0.
#
# -6 dB from 2026-10-11 (#156); -9 dB from 2026-10-13. With seven effects at
# +1 dB makeup (#169), all seven on peaked at -0.5 dBFS on the self-test's worst
# signal from a -9 dBFS input, and live chords with pressure run hotter than the
# calibration's test notes. The extra 3 dB is that margin.
#
# Deliberately not compensated on upgrade: raising the stored DAC level would
# make the box jump louder at the next boot, the dangerous direction (AGENTS.md
# "Audio output safety"). It comes up quieter; the fader brings it back.
DEVICE_MODE_SURGE_TRIM_DB = -9.0

# Until 2026-10-19 the -9 dB went to Surge's amp/volume as 10^(-9/20), which
# Surge reads as a cube law: about -27 dB. The stage applies -9, so every stored
# DAC level is cut once by the difference (`load_levels`), and device mode comes
# up exactly as loud as before at the same fader.
STAGE_LAW_DAC_CUT_DB = 3.0 * DEVICE_MODE_SURGE_TRIM_DB - DEVICE_MODE_SURGE_TRIM_DB
# Marks a levels file as already cut. `save_levels` always writes it.
STAGE_LAW_KEY = "_stage_law"
STAGE_LAW = "fx-stage-v1"

# A model never seen before starts here, or lower if MPE_DAC_VOLUME_DB says so:
# -12 until the stage, less the cut above. An env file written before the
# stage still says -12, which would start a new card 18 dB louder than it did.
DEFAULT_DB = -30.0
STATE_FILE = Path.home() / ".patch_browser_device_volume.json"
MPE_ENV_PATH = Path("/etc/mpe/mpe.env")
ASOUND_ROOT = Path("/proc/asound")
AMIXER_TIMEOUT_S = 3.0
# How often the UI checks jack.state for a new graph start. A file read, no fork.
REBIND_POLL_S = 1.0

# volume_mapping.c: ranges at or below this are mapped linearly in raw steps.
MAX_LINEAR_DB_SCALE = 24.0

_NUMID_RE = re.compile(r"^numid=(\d+),iface=MIXER,name='([^']*)'")
_TYPE_RE = re.compile(r"type=INTEGER,access=([^,]+),values=(\d+),min=(-?\d+),max=(-?\d+)")
_VALUES_RE = re.compile(r"^\s*:\s*values=([-\d,]+)\s*$")
_DB_RE = re.compile(r"dBminmax-min=(-?[\d.]+)dB,max=(-?[\d.]+)dB")


@dataclass(frozen=True)
class VolumeElement:
    numid: int
    name: str
    raw_min: int
    raw_max: int
    db_min: float
    db_max: float
    raw: int

    def raw_to_db(self, raw: int) -> float:
        span = self.raw_max - self.raw_min
        if span <= 0:
            return self.db_max
        return self.db_min + (raw - self.raw_min) * (self.db_max - self.db_min) / span

    def db_to_raw(self, db: float) -> int:
        span_db = self.db_max - self.db_min
        if span_db <= 0:
            return self.raw_max
        frac = (min(self.db_max, max(self.db_min, db)) - self.db_min) / span_db
        return round(self.raw_min + frac * (self.raw_max - self.raw_min))

    def position_from_db(self, db: float) -> float:
        """ALSA perceptual mapping, dB -> 0..1 (what alsamixer displays)."""
        db = min(self.db_max, max(self.db_min, db))
        if self.db_max - self.db_min <= MAX_LINEAR_DB_SCALE:
            return (db - self.db_min) / (self.db_max - self.db_min)
        norm = 10 ** ((db - self.db_max) / 60.0)
        min_norm = 10 ** ((self.db_min - self.db_max) / 60.0)
        return (norm - min_norm) / (1.0 - min_norm)

    def db_from_position(self, position: float) -> float:
        position = min(1.0, max(0.0, position))
        if self.db_max - self.db_min <= MAX_LINEAR_DB_SCALE:
            return self.db_min + position * (self.db_max - self.db_min)
        min_norm = 10 ** ((self.db_min - self.db_max) / 60.0)
        norm = position * (1.0 - min_norm) + min_norm
        return self.db_max + 60.0 * math.log10(norm)


def parse_contents(text: str) -> tuple[VolumeElement, ...]:
    """Playback-only, writable, dB-carrying volume elements from `amixer contents`.

    A "<base> Playback Volume" whose base also has a "<base> Capture Volume" is
    a monitor path, not the output (Sound Blaster `Mic`), and is excluded.
    """
    blocks: list[dict] = []
    current: dict | None = None
    for line in text.splitlines():
        if line.startswith("numid="):
            # Every numid line ends the previous block. Matching only MIXER
            # headers let the KA1's trailing iface=PCM channel map (access r--)
            # overwrite its volume element, which then read as not writable.
            head = _NUMID_RE.match(line)
            current = {"numid": int(head.group(1)), "name": head.group(2)} if head else None
            if current is not None:
                blocks.append(current)
            continue
        if current is None:
            continue
        typ = _TYPE_RE.search(line)
        if typ:
            current["access"] = typ.group(1)
            current["raw_min"] = int(typ.group(3))
            current["raw_max"] = int(typ.group(4))
            continue
        vals = _VALUES_RE.match(line)
        if vals:
            current["values"] = [int(v) for v in vals.group(1).split(",") if v]
            continue
        db = _DB_RE.search(line)
        if db:
            current["db_min"] = float(db.group(1))
            current["db_max"] = float(db.group(2))

    capture_bases = {
        b["name"][: -len(" Capture Volume")]
        for b in blocks if b["name"].endswith(" Capture Volume")
    }
    elements = []
    for b in blocks:
        name = b["name"]
        if not name.endswith(" Playback Volume"):
            continue
        if name[: -len(" Playback Volume")] in capture_bases:
            continue
        if not b.get("access", "").startswith("rw"):
            continue
        if "db_min" not in b or not b.get("values") or "raw_min" not in b:
            continue
        elements.append(VolumeElement(
            numid=b["numid"], name=name,
            raw_min=b["raw_min"], raw_max=b["raw_max"],
            db_min=b["db_min"], db_max=b["db_max"],
            raw=max(b["values"]),
        ))
    return tuple(elements)


def choose_element(elements: tuple[VolumeElement, ...]) -> VolumeElement | None:
    """Exactly one candidate, or None (trim mode)."""
    return elements[0] if len(elements) == 1 else None


def bound_card_index(jack_state: dict[str, str]) -> str | None:
    device = str(jack_state.get("device", "")).strip()
    if not device.startswith("hw:"):
        return None
    index = device[3:].split(",", 1)[0].strip()
    return index if index.isdigit() else None


def card_model_key(index: str, root: Path = ASOUND_ROOT) -> str | None:
    try:
        usbid = (root / f"card{index}" / "usbid").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return f"usb:{usbid}" if usbid else None


def resolve_binding(
    jack_state: dict[str, str], *, runner=None, asound_root: Path = ASOUND_ROOT
) -> tuple[str | None, str | None, VolumeElement | None]:
    """(card, model key, element) for a graph start. Reads only; never writes.

    The one place the device/trim decision is made. `DeviceVolume.rebind` uses
    it, and so does any tool that has to know the mode without taking the
    fader over (scripts/load-patch-osc.py).
    """
    run = runner or _run_amixer
    card = bound_card_index(jack_state)
    key = card_model_key(card, asound_root) if card else None
    if card is None or key is None:
        return card, key, None
    text = run(["-c", card, "contents"])
    return card, key, choose_element(parse_contents(text or ""))


def stage_volume_args(*, device_active: bool, level: float) -> tuple[float, float]:
    """(fader, headroom_db) for PatchLoader.set_volume. The one rule for the stage's share.

    Device mode: the DAC is the level stage, the stage sits at full fader with the
    fixed DEVICE_MODE_SURGE_TRIM_DB headroom. Trim mode: the fader trims the stage.
    """
    from patch_browser.touch_ui_constants import VOLUME_MAX

    if device_active:
        return VOLUME_MAX, DEVICE_MODE_SURGE_TRIM_DB
    return float(level), 0.0


def apply_stage_volume(loader, *, device_active: bool, level: float):
    """Send the stage's share of the level through PatchLoader.set_volume, the UI's own path."""
    fader, headroom_db = stage_volume_args(device_active=device_active, level=level)
    return loader.set_volume(fader, headroom_db=headroom_db)


def default_db(env_path: Path = MPE_ENV_PATH) -> float:
    raw = os.environ.get("MPE_DAC_VOLUME_DB", "")
    if not raw and env_path.is_file():
        try:
            for line in env_path.read_text(encoding="utf-8").splitlines():
                if line.strip().startswith("MPE_DAC_VOLUME_DB="):
                    raw = line.partition("=")[2].strip().strip("\"'")
        except OSError:
            raw = ""
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_DB
    return min(value, DEFAULT_DB) if math.isfinite(value) else DEFAULT_DB


def load_levels(path: Path = STATE_FILE) -> dict[str, float]:
    """Stored levels by model. A file from before the stage is cut by
    STAGE_LAW_DAC_CUT_DB and rewritten, once (never raised)."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    levels = {}
    for key, value in data.items():
        if key == STAGE_LAW_KEY or isinstance(value, bool):
            continue
        try:
            levels[str(key)] = float(value)
        except (TypeError, ValueError):
            continue
    if data.get(STAGE_LAW_KEY) != STAGE_LAW:
        levels = {k: round(v + STAGE_LAW_DAC_CUT_DB, 2) for k, v in levels.items()}
        if levels:
            print(f"device volume: stored levels cut {STAGE_LAW_DAC_CUT_DB:+.0f} dB "
                  "for the stage gain (once)")
        save_levels(levels, path)
    return levels


def save_levels(levels: dict[str, float], path: Path = STATE_FILE) -> None:
    payload: dict[str, object] = {str(k): v for k, v in levels.items()}
    payload[STAGE_LAW_KEY] = STAGE_LAW
    try:
        path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    except OSError as exc:
        print(f"Warning: could not persist device volume ({exc})")


def _run_amixer(args: list[str]) -> str | None:
    try:
        result = subprocess.run(
            ["amixer", *args], capture_output=True, text=True, timeout=AMIXER_TIMEOUT_S,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout if result.returncode == 0 else None


class DeviceVolume:
    """Binds the fader to the bound card's hardware level, or declines (trim).

    `rebind` forks amixer only when jack.state names a different graph start, so
    it is safe to call from the UI loop. Writes during a drag are coalesced onto
    one worker thread: only the newest position is ever applied.
    """

    def __init__(self, *, runner=_run_amixer, state_file: Path = STATE_FILE,
                 asound_root: Path = ASOUND_ROOT, env_path: Path = MPE_ENV_PATH) -> None:
        self._runner = runner
        self._state_file = state_file
        self._asound_root = asound_root
        self._env_path = env_path
        self._binding: tuple[str, str] | None = None
        self.card: str | None = None
        self.key: str | None = None
        self.element: VolumeElement | None = None
        self._db: float | None = None
        self._pending: tuple[int, float] | None = None
        self._written_raw: int | None = None
        self._wake = threading.Condition()
        self._worker: threading.Thread | None = None

    @property
    def mode(self) -> str:
        return DEVICE_MODE if self.element is not None else TRIM_MODE

    @property
    def active(self) -> bool:
        return self.element is not None

    def rebind(self, jack_state: dict[str, str]) -> bool:
        """Re-resolve after a graph (re)start. True when the binding changed."""
        binding = (str(jack_state.get("started", "")), str(jack_state.get("device", "")))
        if binding == self._binding:
            return False
        self._binding = binding
        self.card, self.key, element = resolve_binding(
            jack_state, runner=self._runner, asound_root=self._asound_root
        )
        self.element = None
        self._db = None
        self._written_raw = None
        if element is None:
            return True
        self.element = element
        target = load_levels(self._state_file).get(self.key, default_db(self._env_path))
        self._write_now(element.db_to_raw(target))
        return True

    def _write_now(self, raw: int) -> None:
        if self.element is None or self.card is None:
            return
        out = self._runner(["-c", self.card, "cset", f"numid={self.element.numid}", str(raw)])
        if out is None:
            # Read back what the device actually holds rather than claim the write.
            text = self._runner(["-c", self.card, "contents"]) or ""
            for el in parse_contents(text):
                if el.numid == self.element.numid:
                    raw = el.raw
                    break
            else:
                raw = self.element.raw
        self._written_raw = raw
        self._db = self.element.raw_to_db(raw)

    def db(self) -> float | None:
        return self._db

    def position(self) -> float | None:
        if self.element is None or self._db is None:
            return None
        return self.element.position_from_db(self._db)

    def set_position(self, position: float, *, persist: bool = True) -> None:
        if self.element is None:
            return
        raw = self.element.db_to_raw(self.element.db_from_position(position))
        self._db = self.element.raw_to_db(raw)
        if persist and self.key:
            levels = load_levels(self._state_file)
            levels[self.key] = round(self._db, 2)
            save_levels(levels, self._state_file)
        if raw == self._written_raw:
            return
        with self._wake:
            self._pending = (raw, self._db)
            self._wake.notify()
        if self._worker is None or not self._worker.is_alive():
            self._worker = threading.Thread(target=self._drain, daemon=True, name="DeviceVolume")
            self._worker.start()

    def _drain(self) -> None:
        while True:
            with self._wake:
                if self._pending is None:
                    self._wake.wait(timeout=5.0)
                    if self._pending is None:
                        return
                raw, _ = self._pending
                self._pending = None
            if raw != self._written_raw:
                self._write_now(raw)

    def flush(self, timeout: float = 2.0) -> None:
        """Wait for queued writes (tests, shutdown)."""
        worker = self._worker
        if worker is not None:
            with self._wake:
                self._wake.notify()
            deadline = timeout
            while worker.is_alive() and self._pending is not None and deadline > 0:
                worker.join(0.05)
                deadline -= 0.05

    def default_position(self) -> float | None:
        """Where Reset lands: the new-device default, never full scale."""
        if self.element is None:
            return None
        return self.element.position_from_db(default_db(self._env_path))

    def format_db(self) -> str:
        if self._db is None:
            return "?"
        if abs(self._db) < 0.05:
            return "0"
        return f"{self._db:.0f}"
