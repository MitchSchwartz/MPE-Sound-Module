#!/usr/bin/env python3
"""Extract scene-A oscillator/FX/filter metadata from a Surge .fxp (embedded XML).

Usage: parse-fxp-metadata.py /path/to/Patch.fxp

Outputs one JSON object on stdout. Unison is best-effort: Modern/Wavetable osc types
use integer param0 as voice count; other types use integer param6 when present.
"""
from __future__ import annotations

import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

# Surge osc types where param0 (type=0) is typically unison voice count.
_UNISON_PARAM0_TYPES = frozenset({8, 9, 10, 11, 12, 13, 14, 15})


def _extract_xml_blob(raw: bytes) -> bytes:
    idx = raw.find(b"<?xml")
    if idx < 0:
        raise ValueError("no XML patch blob in fxp")
    return raw[idx:]


def _int_val(elem: ET.Element | None) -> int | None:
    if elem is None:
        return None
    raw = elem.get("value")
    if raw is None:
        return None
    try:
        return int(float(raw))
    except ValueError:
        return None


def _float_val(elem: ET.Element | None) -> float | None:
    if elem is None:
        return None
    raw = elem.get("value")
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def parse_fxp_metadata(path: Path) -> dict:
    raw = path.read_bytes()
    xml_bytes = _extract_xml_blob(raw)
    root = ET.fromstring(xml_bytes)
    params = root.find("parameters")
    if params is None:
        raise ValueError("patch has no <parameters>")

    by_name: dict[str, ET.Element] = {}
    for child in params:
        tag = child.tag
        if tag and not tag.startswith("{"):
            by_name[tag] = child

    osc_types: list[int] = []
    unison_voices = 0
    for n in (1, 2, 3):
        type_elem = by_name.get(f"a_osc{n}_type")
        osc_type = _int_val(type_elem) or 0
        if osc_type == 0:
            continue
        osc_types.append(osc_type)
        p0 = by_name.get(f"a_osc{n}_param0")
        p6 = by_name.get(f"a_osc{n}_param6")
        if osc_type in _UNISON_PARAM0_TYPES:
            unison_voices += _int_val(p0) or 0
        else:
            unison_voices += _int_val(p6) or 0

    fx_slots: list[int] = []
    for slot in (1, 2, 3, 4, 5):
        elem = by_name.get(f"fx{slot}_type")
        fx_type = _int_val(elem) or 0
        if fx_type != 0:
            fx_slots.append(fx_type)

    f1 = _int_val(by_name.get("a_filter1_type")) or 0
    f2 = _int_val(by_name.get("a_filter2_type")) or 0
    polylimit = _int_val(by_name.get("polylimit"))

    name = path.stem
    meta = root.find("meta")
    if meta is not None and meta.get("name"):
        name = meta.get("name") or name

    return {
        "name": name,
        "path": str(path),
        "osc_count": len(osc_types),
        "osc_types": osc_types,
        "unison_voices": unison_voices,
        "fx_count": len(fx_slots),
        "fx_types": fx_slots,
        "filter1_type": f1,
        "filter2_type": f2,
        "patch_polylimit": polylimit,
    }


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
