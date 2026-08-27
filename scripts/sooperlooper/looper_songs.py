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
MANIFEST_VERSION = 2
#: v1 manifests stay loadable forever (Gate A). Each v1 loop becomes slot 0 of
#: its track. Overwrite-Save upgrades a song to v2; nothing rewrites in place.
MANIFEST_VERSION_V1 = 1
NUM_SLOTS = 8
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


# Durability. This is an appliance people switch off at the wall, so "Saved"
# has to mean the song survives that — not that it reached the page cache.
#
# Measured in SP1 (2026-08-26): SooperLooper's save_loop returned a ~1.5 MB WAV
# in 2.1 ms. That is ~700 MB/s on a Class-10 SD card, i.e. nothing touched the
# card at all. SL never fsyncs, and neither did we. The file existing on disk —
# which is all _save_loop_blocking waits for — proves only that the kernel
# accepted the write.
#
# So every file the save path produces is fsynced, and so is the directory that
# names it: without the directory fsync the rename of the manifest into place
# can be lost even though the manifest's own contents are durable, which would
# leave the WAVs on the card and no song pointing at them.
#
# Cost is paid once per save gesture, not per loop write, and it is real — tens
# of ms on this card. That is the price of the toast being true. Set
# MPE_LOOPER_FSYNC=0 to skip it (tests, or a session on a machine where the
# save target is not the appliance's own card).
FSYNC_ENABLED = os.environ.get("MPE_LOOPER_FSYNC", "1") != "0"


def _fsync_file(path: Path) -> None:
    """Flush one file's contents to the storage device.

    Opened read-only on purpose: the writer here is SooperLooper, in another
    process. fsync(2) on an O_RDONLY descriptor still flushes the inode's dirty
    pages on Linux, and this way the save path never holds a writable handle to
    a file SL may still be touching.
    """
    if not FSYNC_ENABLED:
        return
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _fsync_dir(path: Path) -> None:
    """Flush a directory entry, so creates and renames inside it are durable."""
    if not FSYNC_ENABLED:
        return
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass  # some filesystems refuse fsync on a directory; nothing to do
    finally:
        os.close(fd)


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
    """v1 layout: one WAV per loop index. **Kept, not changed** — the v1 reader
    depends on this exact name and v1 songs must load forever."""
    root = songs_dir or SONGS_DIR
    return root / f"{slug}_{loop:02d}.wav"


def wav_path_v2(
    slug: str, track: int, slot: int, *, songs_dir: Path | None = None
) -> Path:
    """v2 layout: one WAV per (track, slot) cell."""
    root = songs_dir or SONGS_DIR
    return root / f"{slug}_t{track:02d}_s{slot}.wav"


@dataclass(frozen=True)
class SlotEntry:
    """One saved cell. ``file`` is a bare filename, resolved against the song dir."""

    file: str
    len_s: float = 0.0
    sl_state: int = SL_STATE_OFF


@dataclass(frozen=True)
class TrackEntry:
    track: int
    slots: tuple[SlotEntry | None, ...]
    active_slot: int | None = None
    wet: float = 1.0

    def occupied(self) -> list[int]:
        return [i for i, s in enumerate(self.slots) if s is not None]


@dataclass(frozen=True)
class SongManifest:
    """A song, normalised. v1 and v2 both parse into this shape.

    Having one shape is the point: `load_song` must not branch on version, or
    the v1 path quietly rots the first time the v2 path changes.
    """

    version: int
    name: str
    slug: str
    bpm: float
    grid_active: bool
    tracks: tuple[TrackEntry, ...]
    saved_at: str = ""

    def track(self, index: int) -> TrackEntry | None:
        for entry in self.tracks:
            if entry.track == index:
                return entry
        return None


def _coerce_slot(raw: object) -> SlotEntry | None:
    if not isinstance(raw, dict):
        return None
    name = str(raw.get("file") or "")
    if not name:
        return None
    try:
        return SlotEntry(
            file=name,
            len_s=float(raw.get("len_s") or 0.0),
            sl_state=int(raw.get("sl_state", SL_STATE_OFF)),
        )
    except (TypeError, ValueError):
        return None


def parse_manifest(raw: dict, *, slug: str = "") -> SongManifest | None:
    """Normalise a v1 or v2 manifest. None when it is not usable at all.

    Tolerant of junk *inside* a manifest — a single unreadable entry drops that
    cell rather than the song — because a song with four good clips and one bad
    one should still load the four.
    """
    if not isinstance(raw, dict):
        return None
    version = int(raw.get("version") or MANIFEST_VERSION_V1)
    name = str(raw.get("name") or slug)
    got_slug = str(raw.get("slug") or slug)
    bpm = float(raw.get("bpm") or 0.0)
    grid_active = bool(raw.get("grid_active")) and bpm > 0.0

    tracks: list[TrackEntry] = []
    if version >= 2:
        for entry in raw.get("tracks") or []:
            if not isinstance(entry, dict):
                continue
            try:
                index = int(entry.get("track", -1))
            except (TypeError, ValueError):
                continue
            if not 0 <= index < NUM_LOOPS:
                continue
            slots = [_coerce_slot(s) for s in (entry.get("slots") or [])]
            slots = (slots + [None] * NUM_SLOTS)[:NUM_SLOTS]
            active = entry.get("active_slot")
            active = int(active) if isinstance(active, (int, float)) else None
            if active is not None and not (0 <= active < NUM_SLOTS):
                active = None
            if active is not None and slots[active] is None:
                active = None
            if not any(s is not None for s in slots):
                continue
            try:
                wet = float(entry.get("wet", 1.0))
            except (TypeError, ValueError):
                wet = 1.0
            tracks.append(
                TrackEntry(track=index, slots=tuple(slots), active_slot=active, wet=wet)
            )
    else:
        # v1: one loop = one clip. It becomes slot 0, and it is active because
        # in v1 there was nothing else it could be.
        for entry in raw.get("loops") or []:
            if not isinstance(entry, dict):
                continue
            try:
                index = int(entry.get("i", -1))
            except (TypeError, ValueError):
                continue
            if not 0 <= index < NUM_LOOPS:
                continue
            slot = _coerce_slot(entry)
            if slot is None:
                continue
            try:
                wet = float(entry.get("wet", 1.0))
            except (TypeError, ValueError):
                wet = 1.0
            slots = [slot] + [None] * (NUM_SLOTS - 1)
            tracks.append(
                TrackEntry(track=index, slots=tuple(slots), active_slot=0, wet=wet)
            )

    if not tracks:
        return None
    return SongManifest(
        version=version,
        name=name,
        slug=got_slug,
        bpm=bpm,
        grid_active=grid_active,
        tracks=tuple(tracks),
        saved_at=str(raw.get("saved_at") or ""),
    )


def build_manifest_v2(
    *,
    name: str,
    slug: str,
    bpm: float,
    grid_active: bool,
    tracks: list[TrackEntry],
    saved_at: str,
) -> dict:
    """The JSON payload. Pure — no disk, no clock."""
    return {
        "version": MANIFEST_VERSION,
        "name": name.strip(),
        "slug": slug,
        "saved_at": saved_at,
        "bpm": float(bpm),
        "grid_active": bool(grid_active),
        "tracks": [
            {
                "track": t.track,
                "wet": float(t.wet),
                "active_slot": t.active_slot,
                "slots": [
                    None
                    if s is None
                    else {
                        "file": s.file,
                        "len_s": float(s.len_s),
                        "sl_state": int(s.sl_state),
                    }
                    for s in t.slots
                ],
            }
            for t in tracks
        ],
    }


def verify_slot_files(
    manifest: SongManifest, *, songs_dir: Path
) -> list[str]:
    """Spec save-path step 3. Returns a problem per bad cell; empty means good.

    Not optional, and not paranoia. Save only writes the WAV for the *active*
    slot; every other referenced file is expected to be on disk already from an
    earlier swap-flush — an invariant maintained by different code at a
    different time. If a flush was missed, the manifest points at a stale or
    absent take and **the save looks exactly the same either way.** One stat per
    cell buys the difference between a save that worked and one that lied.
    """
    problems: list[str] = []
    for entry in manifest.tracks:
        for index, slot in enumerate(entry.slots):
            if slot is None:
                continue
            path = songs_dir / slot.file
            if not path.is_file():
                problems.append(
                    f"track {entry.track} slot {index}: missing {slot.file}"
                )
            elif path.stat().st_size < MIN_LOOP_WAV_BYTES:
                problems.append(
                    f"track {entry.track} slot {index}: {slot.file} is "
                    f"{path.stat().st_size} B, under {MIN_LOOP_WAV_BYTES}"
                )
    return problems


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
    active_slots: dict[int, int] | None = None,
) -> SongResult:
    """``active_slots`` maps track -> the slot its buffer currently holds.

    The matrix lives in the bench, not here, so it has to be handed in. Absent,
    every track saves to slot 0 — which is exactly today's one-clip-per-track
    behaviour, expressed in the v2 shape.
    """
    active_slots = active_slots or {}
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

    loops_meta: list[TrackEntry] = []
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
        # v2 layout: the name carries (track, slot). Using the v1 name would
        # collide the moment a track holds two clips, and the collision would
        # be silent — the second save would simply overwrite the first.
        slot_index = active_slots.get(loop, 0)
        wav = wav_path_v2(slug, loop, slot_index, songs_dir=root)
        if not _save_loop_blocking(send, loop, wav):
            return SongResult(ok=False, message=f"save_loop failed for track {loop + 1}")
        if wav.stat().st_size < MIN_LOOP_WAV_BYTES:
            wav.unlink(missing_ok=True)
            continue
        # Durable before the manifest can reference it — see FSYNC_ENABLED.
        _fsync_file(wav)
        slots: list[SlotEntry | None] = [None] * NUM_SLOTS
        slots[slot_index] = SlotEntry(
            file=wav.name, len_s=float(loop_len), sl_state=int(state)
        )
        loops_meta.append(
            TrackEntry(
                track=loop,
                slots=tuple(slots),
                active_slot=slot_index,
                wet=float(wet) if wet is not None else 1.0,
            )
        )

    if not loops_meta:
        return SongResult(ok=False, message="Nothing to save — no loops with audio")

    payload = build_manifest_v2(
        name=name,
        slug=slug,
        bpm=float(tempo) if tempo is not None else 0.0,
        grid_active=grid_active,
        tracks=loops_meta,
        saved_at=datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
    )

    # Step 3 — verify every referenced file BEFORE the manifest exists, so a
    # song that cannot be reloaded is never written. Fails loudly, naming the
    # cell, rather than leaving a manifest that points at nothing.
    parsed = parse_manifest(payload, slug=slug)
    problems = verify_slot_files(parsed, songs_dir=root) if parsed else ["unparseable"]
    if problems:
        return SongResult(
            ok=False,
            message="Save aborted — " + "; ".join(problems[:3]),
        )

    # Write, flush, rename, flush the directory. The rename is atomic, so a
    # power cut either leaves the previous manifest or the new one — never a
    # half-written file. The two fsyncs are what make that guarantee reach the
    # card rather than stopping at the page cache.
    tmp = manifest.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    _fsync_file(tmp)
    tmp.replace(manifest)
    _fsync_dir(root)

    if overwrite:
        keep = {
            slot.file
            for entry in loops_meta
            for slot in entry.slots
            if slot is not None
        }
        pruned = False
        for path in root.glob(f"{slug}_*.wav"):
            if path.name not in keep:
                path.unlink(missing_ok=True)
                pruned = True
        if pruned:
            _fsync_dir(root)

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

    song = parse_manifest(raw, slug=slug)
    if song is None:
        return SongResult(ok=False, message="Song has no loops")

    bpm = song.bpm
    grid_active = song.grid_active
    if grid_active:
        establish_grid_clock(send, bpm)
        set_grid_active(send, num_loops=num_loops, active=True)
    else:
        set_grid_active(send, num_loops=num_loops, active=False)

    # Lazy load (Gate A): SooperLooper has one buffer per track, so only the
    # ACTIVE slot is loaded. Inactive occupied slots stay on disk as manifest
    # paths until something launches them — SP2 measured that swap at 6.8 ms
    # p95 against a 2000 ms bar, so there is nothing to gain by preloading and
    # nowhere to put it if there were.
    loaded = 0
    for entry in song.tracks:
        loop = entry.track
        if loop < 0 or loop >= num_loops or loop == scratch_loop:
            continue
        if entry.active_slot is None:
            continue
        slot = entry.slots[entry.active_slot]
        if slot is None:
            continue
        wav = root / slot.file
        if not wav.is_file():
            # v1 songs whose WAV was renamed out from under the manifest.
            wav = wav_path(slug, loop, songs_dir=root)
        if not wav.is_file():
            continue
        probe.send(f"/sl/{loop}/load_loop", [str(wav), "", ""])
        time.sleep(0.05)
        probe.send(f"/sl/{loop}/set", ["wet", float(entry.wet)])
        if slot.sl_state in ACTIVE_PLAY:
            probe.send(f"/sl/{loop}/hit", "pause_off")
            probe.send(f"/sl/{loop}/hit", "trigger")
        elif slot.sl_state in (SL_STATE_MUTE, SL_STATE_PAUSED):
            probe.send(f"/sl/{loop}/hit", "mute_on")
        loaded += 1

    if loaded == 0:
        return SongResult(ok=False, message="No loop files loaded")

    return SongResult(ok=True, message=f"Loaded '{song.name}'")


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
