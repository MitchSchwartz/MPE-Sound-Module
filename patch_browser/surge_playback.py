"""Surge XT playback policy — Reuse Single patch prep and poly limit OSC."""

from __future__ import annotations

import hashlib
import os
import re
import socket
import struct
import xml.etree.ElementTree as ET
from pathlib import Path

from patch_browser.json_store import atomic_write_json, read_json_dict

POLY_LIMIT_OSC = "/param/global/polyphony_limit"
POLY_MIN = 2
POLY_MAX = 64

ONE_VOICE_PER_KEY = 1
NEW_VOICE_EVERY_NOTEON = 0

POLY_STATE_FILE = Path.home() / ".patch_browser_poly_state.json"
REUSE_SINGLE_CACHE_DIR = Path("/tmp/mpe-reuse-single")

DEFAULT_POLY_CEILING = 12
DEFAULT_POLY_FLOOR = 4
DEFAULT_POLY_EMERGENCY = 3
DEFAULT_GOVERNOR_LOAD_HEADROOM = 3


def reuse_single_enabled() -> bool:
    return os.environ.get("MPE_REUSE_SINGLE", "1").strip().lower() not in ("0", "false", "no", "off")


def poly_ceiling() -> int:
    raw = os.environ.get("MPE_POLY_CEILING", str(DEFAULT_POLY_CEILING)).strip()
    try:
        return clamp_poly_limit(int(raw))
    except ValueError:
        return DEFAULT_POLY_CEILING


def poly_floor() -> int:
    raw = os.environ.get("MPE_POLY_FLOOR", str(DEFAULT_POLY_FLOOR)).strip()
    try:
        return clamp_poly_limit(int(raw))
    except ValueError:
        return DEFAULT_POLY_FLOOR


def poly_emergency() -> int:
    """Minimum poly during CPU emergency (below normal floor)."""
    raw = os.environ.get("MPE_POLY_EMERGENCY", str(DEFAULT_POLY_EMERGENCY)).strip()
    try:
        value = clamp_poly_limit(int(raw))
    except ValueError:
        value = DEFAULT_POLY_EMERGENCY
    return min(value, poly_floor())


def governor_load_headroom() -> int:
    raw = os.environ.get("MPE_POLY_GOVERNOR_HEADROOM", str(DEFAULT_GOVERNOR_LOAD_HEADROOM)).strip()
    try:
        return max(0, int(raw))
    except ValueError:
        return DEFAULT_GOVERNOR_LOAD_HEADROOM


def clamp_poly_limit(value: int, *, minimum: int = POLY_MIN, maximum: int = POLY_MAX) -> int:
    return max(minimum, min(maximum, int(value)))


def resolve_patch_file(patch_path: Path) -> Path | None:
    """Return readable .fxp path (accepts path with or without extension)."""
    path = Path(patch_path)
    if path.is_file():
        return path
    for suffix in (".fxp", ".FXP"):
        candidate = path.with_suffix(suffix)
        if candidate.is_file():
            return candidate
    return None


def _patch_is_xml(data: bytes) -> bool:
    stripped = data.lstrip()
    return stripped.startswith(b"<?xml") or stripped.startswith(b"<")


def _ensure_nonparamconfig(root: ET.Element) -> ET.Element:
    node = root.find("nonparamconfig")
    if node is None:
        node = ET.SubElement(root, "nonparamconfig")
    return node


def patch_xml_reuse_single(xml_text: str) -> str:
    """Set polyVoiceRepeatedKeyMode to ONE_VOICE_PER_KEY for scenes 0 and 1."""
    root = ET.fromstring(xml_text)
    npc = _ensure_nonparamconfig(root)
    for scene in (0, 1):
        tag = f"polyVoiceRepeatedKeyMode_{scene}"
        el = npc.find(tag)
        if el is None:
            el = ET.SubElement(npc, tag)
        el.set("v", str(ONE_VOICE_PER_KEY))
    return ET.tostring(root, encoding="unicode")


def ensure_reuse_single_patch(patch_path: Path | str) -> Path:
    """Return path to load — cached temp copy with Reuse Single when XML patch."""
    if not reuse_single_enabled():
        return Path(patch_path)

    source = resolve_patch_file(Path(patch_path))
    if source is None:
        return Path(patch_path)

    try:
        raw = source.read_bytes()
    except OSError:
        return source

    if not _patch_is_xml(raw):
        return source

    try:
        patched = patch_xml_reuse_single(raw.decode("utf-8", errors="strict"))
    except (ET.ParseError, UnicodeDecodeError):
        return source

    digest = hashlib.sha256(patched.encode("utf-8")).hexdigest()[:16]
    cache_dir = REUSE_SINGLE_CACHE_DIR
    cache_dir.mkdir(parents=True, exist_ok=True)
    cached = cache_dir / f"{source.stem}-{digest}.fxp"
    if cached.is_file():
        try:
            if cached.read_text(encoding="utf-8") == patched:
                return cached
        except OSError:
            pass

    try:
        cached.write_text(patched, encoding="utf-8")
    except OSError:
        return source
    return cached


def parse_polylimit_query(data: bytes) -> int | None:
    """Parse Surge /q/param/global/polyphony_limit reply as voice count."""
    if len(data) < 8 or data[0] != 0x2F:
        return None
    try:
        from pythonosc.osc_message import OscMessage

        for param in OscMessage(data).params:
            if isinstance(param, str):
                match = re.search(r"(\d+)", param)
                if match:
                    return clamp_poly_limit(int(match.group(1)))
                continue
            if isinstance(param, (int, float)):
                value = int(round(float(param)))
                if POLY_MIN <= value <= POLY_MAX:
                    return value
    except Exception:
        pass

    match = re.search(rb"(\d+)", data)
    if match:
        try:
            return clamp_poly_limit(int(match.group(1)))
        except ValueError:
            return None
    return None


def query_polylimit(
    osc_client,
    *,
    osc_host: str = "127.0.0.1",
    osc_out_port: int = 53270,
    timeout_s: float = 0.08,
) -> int | None:
    if osc_client is None:
        return None
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind((osc_host, osc_out_port))
        sock.settimeout(timeout_s)
        for query in (f"/q{POLY_LIMIT_OSC}", "/q/param/global/polyphony_limit"):
            osc_client.send_message(query, [])
            try:
                data, _addr = sock.recvfrom(4096)
            except socket.timeout:
                continue
            limit = parse_polylimit_query(data)
            if limit is not None:
                return limit
    except OSError:
        return None
    finally:
        sock.close()
    return None


def send_polylimit(osc_client, voice_count: int) -> bool:
    if osc_client is None:
        return False
    try:
        osc_client.send_message(POLY_LIMIT_OSC, float(clamp_poly_limit(voice_count)))
        return True
    except Exception as exc:
        print(f"Error setting poly limit via OSC: {exc}")
        return False


def write_poly_state(
    *,
    patch_name: str,
    native_poly: int,
    ceiling_poly: int,
    effective_poly: int,
    reuse_single: bool,
) -> None:
    atomic_write_json(
        POLY_STATE_FILE,
        {
            "patch": patch_name,
            "native_poly": native_poly,
            "ceiling_poly": ceiling_poly,
            "effective_poly": effective_poly,
            "reuse_single": reuse_single,
        },
    )


def read_poly_state() -> dict:
    return read_json_dict(POLY_STATE_FILE)


def effective_poly_after_load(native_poly: int | None, *, ceiling: int | None = None) -> int:
    native = clamp_poly_limit(native_poly if native_poly is not None else DEFAULT_POLY_CEILING)
    cap = ceiling if ceiling is not None else poly_ceiling()
    return min(native, clamp_poly_limit(cap))


def effective_poly_on_load(
    native_poly: int | None,
    *,
    ceiling: int | None = None,
    governor_active: bool = False,
) -> int:
    """Poly limit after load — conservative headroom when dynamic governor is enabled."""
    effective = effective_poly_after_load(native_poly, ceiling=ceiling)
    if governor_active:
        effective = max(poly_floor(), effective - governor_load_headroom())
    return effective
