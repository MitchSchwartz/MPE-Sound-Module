#!/usr/bin/env python3
"""Extract scene-A oscillator/FX/filter metadata from a Surge .fxp (embedded XML).

Usage: parse-fxp-metadata.py /path/to/Patch.fxp

Outputs one JSON object on stdout.

Unison: per-unmuted-osc list in ``unison_per_osc`` (never summed). String (9) and
Twist/Plaits (10) have no unison — always 1; their ``param0`` engine indices are in
``osc_engines``. All other types read integer ``param6`` when >= 1 (default 1).

Verified against Quick Select (2026-08-22): types 8/11 use param6 for unison (param0 is
not voice count). Types 12–15 not present in the census set — not inferred here.

Surge .fxp files embed XML then append binary wavetable/sample tail; parse only through
the closing </patch> tag. Oscillator type 0 is Classic (not "off"). Muted mixer slots
(`a_mute_oN` = 1) are excluded from osc_count.
"""
from __future__ import annotations

import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

# String / Twist: param0 is engine selector, not unison (engines have no unison voices).
_ENGINE_PARAM0_TYPES = frozenset({9, 10})

_PARAM_VALUE_RE = re.compile(
    rb'<(?:(?:\w+:)?)(?P<name>[a-zA-Z0-9_]+)\b[^>]*\bvalue="(?P<value>[^"]*)"',
)


def _extract_xml_blob(raw: bytes) -> bytes:
    idx = raw.find(b"<?xml")
    if idx < 0:
        raise ValueError("no XML patch blob in fxp")
    xml = raw[idx:]
    end = xml.find(b"</patch>")
    if end >= 0:
        xml = xml[: end + len(b"</patch>")]
    return xml


def _params_from_regex(xml_bytes: bytes) -> dict[str, str]:
    out: dict[str, str] = {}
    for match in _PARAM_VALUE_RE.finditer(xml_bytes):
        out[match.group("name").decode("ascii")] = match.group("value").decode("ascii")
    return out


def _parse_int(raw: str | None) -> int | None:
    if raw is None:
        return None
    try:
        return int(float(raw))
    except ValueError:
        return None


def _unison_for_osc(osc_type: int, by_name: dict[str, str], slot: int) -> int:
    if osc_type in _ENGINE_PARAM0_TYPES:
        return 1
    voices = _parse_int(by_name.get(f"a_osc{slot}_param6"))
    if voices is None or voices < 1:
        return 1
    return voices


def _metadata_from_param_map(by_name: dict[str, str], path: Path, display_name: str | None) -> dict:
    osc_types: list[int] = []
    unison_per_osc: list[int] = []
    osc_engines: list[int] = []
    for n in (1, 2, 3):
        mute = _parse_int(by_name.get(f"a_mute_o{n}"))
        if mute == 1:
            continue
        osc_type = _parse_int(by_name.get(f"a_osc{n}_type"))
        if osc_type is None:
            continue
        osc_types.append(osc_type)
        unison_per_osc.append(_unison_for_osc(osc_type, by_name, n))
        if osc_type in _ENGINE_PARAM0_TYPES:
            engine = _parse_int(by_name.get(f"a_osc{n}_param0"))
            if engine is not None:
                osc_engines.append(engine)

    fx_slots: list[int] = []
    for slot in (1, 2, 3, 4, 5):
        fx_type = _parse_int(by_name.get(f"fx{slot}_type")) or 0
        if fx_type != 0:
            fx_slots.append(fx_type)

    f1 = _parse_int(by_name.get("a_filter1_type")) or 0
    f2 = _parse_int(by_name.get("a_filter2_type")) or 0
    polylimit = _parse_int(by_name.get("polylimit"))

    name = display_name or path.stem

    out: dict = {
        "name": name,
        "path": str(path),
        "osc_count": len(osc_types),
        "osc_types": osc_types,
        "unison_per_osc": unison_per_osc,
        "fx_count": len(fx_slots),
        "fx_types": fx_slots,
        "filter1_type": f1,
        "filter2_type": f2,
        "patch_polylimit": polylimit,
    }
    if osc_engines:
        out["osc_engines"] = osc_engines
    return out


def parse_fxp_metadata(path: Path) -> dict:
    raw = path.read_bytes()
    xml_bytes = _extract_xml_blob(raw)
    display_name: str | None = None
    by_name: dict[str, str] = {}

    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        by_name = _params_from_regex(xml_bytes)
        if not by_name:
            raise
    else:
        params = root.find("parameters")
        if params is None:
            raise ValueError("patch has no <parameters>")
        for child in params:
            tag = child.tag
            if tag and not tag.startswith("{"):
                val = child.get("value")
                if val is not None:
                    by_name[tag] = val
        meta = root.find("meta")
        if meta is not None and meta.get("name"):
            display_name = meta.get("name")

    if not by_name:
        by_name = _params_from_regex(xml_bytes)
    if not by_name:
        raise ValueError("patch has no readable parameters")

    return _metadata_from_param_map(by_name, path, display_name)


def main() -> None:
    if len(sys.argv) != 2:
        print(f"usage: {sys.argv[0]} PATCH.fxp", file=sys.stderr)
        sys.exit(2)
    path = Path(sys.argv[1])
    if not path.is_file():
        print(f"not found: {path}", file=sys.stderr)
        sys.exit(1)
    try:
        data = parse_fxp_metadata(path)
    except (ET.ParseError, ValueError) as exc:
        print(json.dumps({"name": path.stem, "path": str(path), "error": str(exc)}))
        sys.exit(1)
    print(json.dumps(data, separators=(",", ":")))


if __name__ == "__main__":
    main()
