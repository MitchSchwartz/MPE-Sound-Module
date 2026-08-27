"""Save and load looper songs — flat directory of JSON manifest + per-loop WAVs."""

from __future__ import annotations

import json
import os
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from sl_grid_sync import apply_grid_sync, establish_grid_clock, set_grid_active
from sl_loop_states import ACTIVE_PLAY, SL_STATE_MUTE, SL_STATE_OFF, SL_STATE_PAUSED

SL_HOST = os.environ.get("MPE_SL_OSC_HOST", "127.0.0.1")
SL_PORT = int(os.environ.get("MPE_SL_OSC_PORT", "9951"))
LISTEN_PORT = int(os.environ.get("MPE_SL_SONGS_PORT", "9955"))
NUM_LOOPS = int(os.environ.get("MPE_SL_LOOPS", "16"))
# No loop is reserved any more. Loop 14 was the seam-weld scratch buffer; that
# pipeline is gone (see SRED-EVIDENCE §3 U11), so all 16 loops are musical.
# Set MPE_SL_SCRATCH_LOOP to reserve one again if some future feature needs it.
SCRATCH = int(os.environ.get("MPE_SL_SCRATCH_LOOP", "-1"))
MIN_LOOP_WAV_BYTES = int(os.environ.get("MPE_LOOPER_MIN_LOOP_WAV_BYTES", "512"))
SAVE_POLL_S = float(os.environ.get("MPE_LOOPER_SAVE_POLL_S", "0.05"))
SAVE_TIMEOUT_S = float(os.environ.get("MPE_LOOPER_SAVE_TIMEOUT_S", "8.0"))
MANIFEST_VERSION = 1
SONGS_DIR = Path(
    os.environ.get("MPE_LOOPER_SONGS_DIR", str(Path.home() / ".mpe" / "looper-songs"))
)
MIN_LOOP_LEN_S = float(os.environ.get("MPE_LOOPER_SONG_MIN_LEN_S", "0.05"))


@dataclass(frozen=True)
class SongSummary:
    name: str
    slug: str
    saved_at: str
    mtime: float


@dataclass
class SongResult:
    ok: bool
    message: str


def slugify(name: str) -> str:
    base = name.strip().lower()
    base = re.sub(r"[^a-z0-9]+", "-", base)
    base = base.strip("-")
    return base or "song"


def musical_loop_indices(*, num_loops: int = NUM_LOOPS, scratch_loop: int = SCRATCH) -> list[int]:
    if scratch_loop < 0:
        return list(range(num_loops))
    return [i for i in range(num_loops) if i != scratch_loop]


def _save_loop_blocking(send, loop: int, path: Path) -> bool:
    """Ask SL to write a loop to disk and wait for the file to appear.

    SooperLooper's save_loop is fire-and-forget over OSC — there is no reply to
    wait on, so the file landing on disk is the only completion signal.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    send(f"/sl/{loop}/save_loop", [str(path), "", "", "", ""])
    deadline = time.monotonic() + SAVE_TIMEOUT_S
    while time.monotonic() < deadline:
        if path.exists() and path.stat().st_size > 64:
            return True
        time.sleep(SAVE_POLL_S)
    return False


def manifest_path(slug: str, *, songs_dir: Path | None = None) -> Path:
    root = songs_dir or SONGS_DIR
    return root / f"{slug}.json"


def wav_path(slug: str, loop: int, *, songs_dir: Path | None = None) -> Path:
    root = songs_dir or SONGS_DIR
    return root / f"{slug}_{loop:02d}.wav"


class LooperSongProbe:
    """Short-lived OSC client for save/load from the touch UI or CLI."""

    def __init__(self) -> None:
        self.last: dict[str, float] = {}
        self._server = None
        self._listen_host = "127.0.0.1"
        self._listen_port = LISTEN_PORT
        self.client = None

    def _on(self, _addr, *args) -> None:
        if len(args) >= 3:
            self.last[str(args[1])] = float(args[2])

    def start(self) -> LooperSongProbe:
        from pythonosc import dispatcher as osc_dispatcher
        from pythonosc import osc_server, udp_client

        disp = osc_dispatcher.Dispatcher()
        disp.set_default_handler(self._on)
        # Ephemeral port — fixed LISTEN_PORT collides when save/load probes overlap.
        self._listen_host = "127.0.0.1"
        self._server = osc_server.ThreadingOSCUDPServer((self._listen_host, 0), disp)
        self._listen_port = int(self._server.server_address[1])
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        self.client = udp_client.SimpleUDPClient(SL_HOST, SL_PORT)
        return self

    def close(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

    def get(self, ctrl: str, loop: int = 0, timeout: float = 1.5):
        self.last.pop(ctrl, None)
        path = "/get" if loop < 0 else f"/sl/{loop}/get"
        reply = f"{self._listen_host}:{self._listen_port}"
        self.client.send_message(path, [ctrl, reply, "/r"])
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if ctrl in self.last:
                return self.last[ctrl]
            time.sleep(0.05)
        return None

    def send(self, path: str, args) -> None:
        if isinstance(args, (list, tuple)):
            self.client.send_message(path, list(args))
        else:
            self.client.send_message(path, [args])


def list_songs(*, songs_dir: Path | None = None) -> list[SongSummary]:
    root = songs_dir or SONGS_DIR
    if not root.is_dir():
        return []
    out: list[SongSummary] = []
    for path in sorted(root.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(raw, dict):
            continue
        slug = path.stem
        out.append(
            SongSummary(
                name=str(raw.get("name") or slug),
                slug=slug,
                saved_at=str(raw.get("saved_at") or ""),
                mtime=path.stat().st_mtime,
            )
        )
    return out


def session_has_content(
    probe: LooperSongProbe,
    *,
    num_loops: int = NUM_LOOPS,
    scratch_loop: int = SCRATCH,
) -> bool:
    for loop in musical_loop_indices(num_loops=num_loops, scratch_loop=scratch_loop):
        state = probe.get("state", loop)
        loop_len = probe.get("loop_len", loop)
        if state is None:
            continue
        if int(state) == SL_STATE_OFF:
            continue
        if loop_len is not None and float(loop_len) > MIN_LOOP_LEN_S:
            return True
    return False


def stop_playback(probe: LooperSongProbe) -> None:
    probe.send("/sl/-1/set", ["mute_quantized", 0.0])
    probe.send("/sl/-1/hit", "mute_on")
    probe.send("/sl/-1/hit", "pause_on")
    probe.send("/sl/-1/set", ["mute_quantized", 1.0])
    tempo = probe.get("tempo", -1)
    if tempo is not None and float(tempo) > 0:
        probe.send("/set", ["tempo", float(tempo)])


def clear_all_loops(
    probe: LooperSongProbe,
    *,
    num_loops: int = NUM_LOOPS,
) -> None:
    for loop in range(num_loops):
        probe.send(f"/sl/{loop}/hit", "pause_on")
        probe.send(f"/sl/{loop}/hit", "undo_all")


def _send_fn(probe: LooperSongProbe) -> Callable[[str, list], None]:
    def _send(path: str, args: list) -> None:
        probe.send(path, args)

    return _send


def save_song(
    probe: LooperSongProbe,
    name: str,
    *,
    overwrite: bool = False,
    num_loops: int = NUM_LOOPS,
    scratch_loop: int = SCRATCH,
    songs_dir: Path | None = None,
) -> SongResult:
    slug = slugify(name)
    root = songs_dir or SONGS_DIR
    manifest = manifest_path(slug, songs_dir=root)
    if manifest.exists() and not overwrite:
        return SongResult(ok=False, message=f"Song '{name}' already exists")

    if probe.get("state", 0) is None:
        return SongResult(ok=False, message="Looper engine not responding")

    stop_playback(probe)
    time.sleep(0.15)

    tempo = probe.get("tempo", -1)
    grid_active = tempo is not None and float(tempo) >= 20.0

    loops_meta: list[dict] = []
    root.mkdir(parents=True, exist_ok=True)

    send = _send_fn(probe)
    for loop in musical_loop_indices(num_loops=num_loops, scratch_loop=scratch_loop):
        state = probe.get("state", loop)
        loop_len = probe.get("loop_len", loop)
        wet = probe.get("wet", loop)
        if state is None or int(state) == SL_STATE_OFF:
            continue
        if loop_len is None or float(loop_len) <= MIN_LOOP_LEN_S:
            continue
        wav = wav_path(slug, loop, songs_dir=root)
        if not _save_loop_blocking(send, loop, wav):
            return SongResult(ok=False, message=f"save_loop failed for track {loop + 1}")
        if wav.stat().st_size < MIN_LOOP_WAV_BYTES:
            wav.unlink(missing_ok=True)
            continue
        loops_meta.append(
            {
                "i": loop,
                "file": wav.name,
                "len_s": float(loop_len),
                "sl_state": int(state),
                "wet": float(wet) if wet is not None else 1.0,
            }
        )

    if not loops_meta:
        return SongResult(ok=False, message="Nothing to save — no loops with audio")

    payload = {
        "version": MANIFEST_VERSION,
        "name": name.strip(),
        "slug": slug,
        "saved_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "bpm": float(tempo) if tempo is not None else 0.0,
        "grid_active": grid_active,
        "loops": loops_meta,
    }
    tmp = manifest.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(manifest)

    if overwrite:
        for path in root.glob(f"{slug}_*.wav"):
            if path.name not in {entry["file"] for entry in loops_meta}:
                path.unlink(missing_ok=True)

    return SongResult(ok=True, message=f"Saved '{name.strip()}'")


def load_song(
    probe: LooperSongProbe,
    slug: str,
    *,
    num_loops: int = NUM_LOOPS,
    scratch_loop: int = SCRATCH,
    songs_dir: Path | None = None,
) -> SongResult:
    root = songs_dir or SONGS_DIR
    manifest = manifest_path(slug, songs_dir=root)
    if not manifest.is_file():
        return SongResult(ok=False, message=f"Song '{slug}' not found")
    try:
        raw = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return SongResult(ok=False, message=f"Bad manifest: {exc}")

    if probe.get("state", 0) is None:
        return SongResult(ok=False, message="Looper engine not responding")

    stop_playback(probe)
    clear_all_loops(probe, num_loops=num_loops)
    time.sleep(0.2)

    send = _send_fn(probe)
    apply_grid_sync(send, num_loops=num_loops)

    loops = raw.get("loops") or []
    if not isinstance(loops, list) or not loops:
        return SongResult(ok=False, message="Song has no loops")

    bpm = float(raw.get("bpm") or 0.0)
    grid_active = bool(raw.get("grid_active")) and bpm > 0.0
    if grid_active:
        establish_grid_clock(send, bpm)
        set_grid_active(send, num_loops=num_loops, active=True)
    else:
        set_grid_active(send, num_loops=num_loops, active=False)

    loaded = 0
    for entry in loops:
        if not isinstance(entry, dict):
            continue
        loop = int(entry.get("i", -1))
        if loop < 0 or loop >= num_loops or loop == scratch_loop:
            continue
        fname = str(entry.get("file") or "")
        wav = root / fname
        if not wav.is_file():
            wav = wav_path(slug, loop, songs_dir=root)
        if not wav.is_file():
            continue
        probe.send(f"/sl/{loop}/load_loop", [str(wav), "", ""])
        time.sleep(0.05)
        wet = entry.get("wet")
        if wet is not None:
            probe.send(f"/sl/{loop}/set", ["wet", float(wet)])
        sl_state = int(entry.get("sl_state", SL_STATE_OFF))
        if sl_state in ACTIVE_PLAY:
            probe.send(f"/sl/{loop}/hit", "pause_off")
            probe.send(f"/sl/{loop}/hit", "trigger")
        elif sl_state in (SL_STATE_MUTE, SL_STATE_PAUSED):
            probe.send(f"/sl/{loop}/hit", "mute_on")
        loaded += 1

    if loaded == 0:
        return SongResult(ok=False, message="No loop files loaded")

    display = str(raw.get("name") or slug)
    return SongResult(ok=True, message=f"Loaded '{display}'")


def run_with_probe(fn) -> SongResult:
    try:
        import pythonosc  # noqa: F401
    except ImportError as exc:
        return SongResult(ok=False, message=f"pythonosc missing: {exc}")
    probe = LooperSongProbe()
    try:
        probe.start()
    except OSError as exc:
        return SongResult(ok=False, message=f"Looper OSC unavailable: {exc}")
    try:
        raw = fn(probe)
    except Exception as exc:
        return SongResult(ok=False, message=str(exc))
    finally:
        probe.close()
    if isinstance(raw, SongResult):
        return raw
    if isinstance(raw, bool):
        return SongResult(ok=raw, message="")
    return SongResult(ok=True, message=str(raw))
