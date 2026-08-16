"""Patch metadata — instrument heuristics and baseline/user index merge."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from patch_browser.json_store import atomic_write_json, read_json_dict

METADATA_VERSION = 1
USER_METADATA_FILE = Path.home() / ".patch_browser_metadata.json"

INSTRUMENT_VOCAB: tuple[str, ...] = (
    "piano",
    "keys",
    "organ",
    "bass",
    "lead",
    "pad",
    "brass",
    "strings",
    "woodwind",
    "pluck",
    "synth",
    "sequencer",
    "percussion",
    "fx",
    "other",
)

# Top-level Surge factory / common folder names → instrument(s)
FOLDER_INSTRUMENT_MAP: dict[str, tuple[str, ...]] = {
    "bass": ("bass",),
    "basses": ("bass",),
    "sub": ("bass",),
    "subs": ("bass",),
    "keys": ("keys",),
    "key": ("keys",),
    "piano": ("piano",),
    "pianos": ("piano",),
    "grand": ("piano",),
    "organ": ("organ",),
    "organs": ("organ",),
    "lead": ("lead",),
    "leads": ("lead",),
    "pad": ("pad",),
    "pads": ("pad",),
    "brass": ("brass",),
    "horn": ("brass",),
    "horns": ("brass",),
    "trumpet": ("brass",),
    "trombone": ("brass",),
    "string": ("strings",),
    "strings": ("strings",),
    "stringed": ("strings",),
    "bowed": ("strings",),
    "violin": ("strings",),
    "cello": ("strings",),
    "guitar": ("pluck",),
    "guitars": ("pluck",),
    "pluck": ("pluck",),
    "plucked": ("pluck",),
    "mallet": ("pluck",),
    "mallets": ("pluck",),
    "marimba": ("pluck",),
    "vibe": ("pluck",),
    "vibes": ("pluck",),
    "bell": ("pluck",),
    "bells": ("pluck",),
    "wind": ("woodwind",),
    "woodwind": ("woodwind",),
    "woodwinds": ("woodwind",),
    "flute": ("woodwind",),
    "flutes": ("woodwind",),
    "reed": ("woodwind",),
    "reeds": ("woodwind",),
    "sax": ("woodwind",),
    "clarinet": ("woodwind",),
    "oboe": ("woodwind",),
    "bassoon": ("woodwind",),
    "synth": ("synth",),
    "synths": ("synth",),
    "analog": ("synth",),
    "digital": ("synth",),
    "wavetable": ("synth",),
    "wt": ("synth",),
    "fm": ("synth",),
    "fx": ("fx",),
    "effect": ("fx",),
    "effects": ("fx",),
    "ambient": ("pad",),
    "atmosphere": ("pad",),
    "atmospheric": ("pad",),
    "drone": ("pad",),
    "texture": ("pad",),
    "template": ("synth",),
    "templates": ("synth",),
    "init": ("synth",),
    "misc": ("other",),
    "other": ("other",),
    "experimental": ("fx",),
    "seq": ("sequencer",),
    "sequence": ("sequencer",),
    "sequencer": ("sequencer",),
    "sequences": ("sequencer",),
    "arpeggio": ("sequencer",),
    "arpeggios": ("sequencer",),
    "basses": ("bass",),
    "plucks": ("pluck",),
    "polysynths": ("synth",),
    "polysynth": ("synth",),
    "winds": ("woodwind",),
    "percussion": ("percussion",),
    "perc": ("percussion",),
    "drum": ("percussion",),
    "drums": ("percussion",),
    "chords": ("keys",),
    "vocoder": ("fx",),
    "mpe": ("synth",),
    "splits": ("other",),
    "tutorials": ("other",),
    "tutorial": ("other",),
    "rhythmic": ("sequencer",),
}

# Generic factory folders — weak signal vs patch name keywords
OPAQUE_FOLDER_KEYS: frozenset[str] = frozenset(
    {
        "template",
        "templates",
        "init",
        "misc",
        "other",
        "experimental",
        "tutorial",
        "tutorials",
        "mpe",
        "splits",
        "seq",
        "sequence",
        "sequencer",
        "sequences",
    }
)

FOLDER_SEGMENT_WEIGHT = 3.0
OPAQUE_FOLDER_WEIGHT = 1.0

# Token matches in patch names (lower weight than folder)
NAME_KEYWORD_MAP: dict[str, tuple[str, ...]] = {
    "piano": ("piano",),
    "grand": ("piano",),
    "grand piano": ("piano",),
    "upright": ("piano",),
    "rhodes": ("keys",),
    "wurl": ("keys",),
    "wurli": ("keys",),
    "clav": ("keys",),
    "clavinet": ("keys",),
    "ep": ("keys",),
    "electric piano": ("keys",),
    "organ": ("organ",),
    "hammond": ("organ",),
    "b3": ("organ",),
    "church": ("organ",),
    "bass": ("bass",),
    "sub": ("bass",),
    "808": ("bass",),
    "909": ("bass",),
    "reese": ("bass",),
    "wobble": ("bass",),
    "lead": ("lead",),
    "solo": ("lead",),
    "pad": ("pad",),
    "string": ("strings",),
    "strings": ("strings",),
    "violin": ("strings",),
    "viola": ("strings",),
    "cello": ("strings",),
    "bowed": ("strings",),
    "brass": ("brass",),
    "trumpet": ("brass",),
    "trombone": ("brass",),
    "horn": ("brass",),
    "flute": ("woodwind",),
    "clarinet": ("woodwind",),
    "sax": ("woodwind",),
    "oboe": ("woodwind",),
    "bassoon": ("woodwind",),
    "duduk": ("woodwind",),
    "woodwind": ("woodwind",),
    "guitar": ("pluck",),
    "pluck": ("pluck",),
    "harpsichord": ("pluck",),
    "marimba": ("pluck",),
    "vibe": ("pluck",),
    "bell": ("pluck",),
    "arp": ("sequencer",),
    "sequencer": ("sequencer",),
    "arpegg": ("sequencer",),
    "arpeggiator": ("sequencer",),
    "kick": ("percussion",),
    "snare": ("percussion",),
    "hat": ("percussion",),
    "hihat": ("percussion",),
    "tom": ("percussion",),
    "clap": ("percussion",),
    "cowbell": ("percussion",),
    "cymbal": ("percussion",),
    "perc": ("percussion",),
    "drum": ("percussion",),
    "taiko": ("percussion",),
    "noise": ("fx",),
    "sweep": ("fx",),
    "riser": ("fx",),
    "impact": ("fx",),
    "hit": ("fx",),
    "fx": ("fx",),
    "synth": ("synth",),
    "analog": ("synth",),
    "ambient": ("pad",),
    "cloud": ("pad",),
    "lush": ("pad",),
    "warm": ("pad",),
}

_TOKEN_SPLIT = re.compile(r"[^a-z0-9]+")

# Rhythm/Rhythms folders hold sequencers unless the patch name is drum-like.
RHYTHM_FOLDER_KEYS: frozenset[str] = frozenset({"rhythm", "rhythms"})

DRUM_NAME_KEYWORDS: frozenset[str] = frozenset(
    {
        "kick",
        "snare",
        "hat",
        "hihat",
        "tom",
        "clap",
        "cowbell",
        "cymbal",
        "perc",
        "drum",
        "taiko",
    }
)


def default_baseline_path() -> Path:
    override = os.environ.get("MPE_PATCH_METADATA_BASELINE", "").strip()
    if override:
        return Path(override)
    repo_root = Path(__file__).resolve().parents[1]
    return repo_root / "data" / "patch_metadata_baseline.json"


def _normalize_token(token: str) -> str:
    return token.strip().lower()


def _tokenize_name(name: str) -> list[str]:
    lowered = name.lower()
    tokens: list[str] = []
    for part in _TOKEN_SPLIT.split(lowered):
        part = part.strip()
        if part:
            tokens.append(part)
    return tokens


def _name_has_drum_keyword(name: str) -> bool:
    """True when patch name suggests a drum/percussion hit (kick, snare, tom, …)."""
    for token in _tokenize_name(name):
        for keyword in DRUM_NAME_KEYWORDS:
            if token == keyword or token.startswith(keyword):
                return True
    return False


def _score_instruments(patch: dict) -> dict[str, float]:
    scores: dict[str, float] = {inst: 0.0 for inst in INSTRUMENT_VOCAB}

    segments: list[str] = []
    category = patch.get("category", "")
    if category:
        segments.append(_normalize_token(category.lstrip("!")))
    for seg in patch.get("folder_segments") or ():
        segments.append(_normalize_token(str(seg)))
    for seg in patch.get("inner_segments") or ():
        segments.append(_normalize_token(str(seg)))

    patch_name = patch.get("name", "")

    for segment in segments:
        key = _normalize_token(segment)
        if key in RHYTHM_FOLDER_KEYS:
            if _name_has_drum_keyword(patch_name):
                scores["percussion"] += FOLDER_SEGMENT_WEIGHT
            else:
                scores["sequencer"] += FOLDER_SEGMENT_WEIGHT
            continue
        weight = (
            OPAQUE_FOLDER_WEIGHT if key in OPAQUE_FOLDER_KEYS else FOLDER_SEGMENT_WEIGHT
        )
        for instrument in FOLDER_INSTRUMENT_MAP.get(key, ()):
            scores[instrument] += weight

    name = patch.get("name", "")
    name_lower = name.lower()
    for phrase, instruments in NAME_KEYWORD_MAP.items():
        if " " in phrase:
            if phrase in name_lower:
                for instrument in instruments:
                    scores[instrument] += 1.5
        else:
            for token in _tokenize_name(name):
                if token == phrase or token.startswith(phrase):
                    for instrument in instruments:
                        scores[instrument] += 1.0

    return scores


def classify_patch_instruments(patch: dict) -> list[str]:
    """
    Return instrument tags sorted by confidence (primary first).

    Always returns at least ``other`` when no signal matches.
    """
    scores = _score_instruments(patch)
    ranked = sorted(
        ((inst, score) for inst, score in scores.items() if score > 0),
        key=lambda item: (-item[1], item[0]),
    )
    if not ranked:
        return ["other"]

    primary_score = ranked[0][1]
    tags = [ranked[0][0]]
    for inst, score in ranked[1:]:
        if score >= primary_score * 0.6 and inst not in tags:
            tags.append(inst)
    return tags[:3]


def path_segments_for_patch(patch: dict) -> list[str]:
    segments: list[str] = []
    category = patch.get("category")
    if category:
        segments.append(str(category).lstrip("!"))
    segments.extend(str(s) for s in patch.get("inner_segments") or ())
    return segments


def metadata_entry_for_patch(patch: dict) -> dict[str, Any]:
    return {
        "name": patch.get("name", ""),
        "path": patch.get("path", ""),
        "path_segments": path_segments_for_patch(patch),
        "instruments": classify_patch_instruments(patch),
        "instrument_user": None,
    }


def build_baseline_document(patches_by_stable_key: dict[str, dict]) -> dict[str, Any]:
    entries = {
        stable_key: metadata_entry_for_patch(patch)
        for stable_key, patch in sorted(patches_by_stable_key.items())
    }
    return {"version": METADATA_VERSION, "patches": entries}


def write_metadata_file(path: Path, document: dict[str, Any]) -> None:
    atomic_write_json(path, document)


def load_metadata_file(path: Path) -> dict[str, dict[str, Any]]:
    raw = read_json_dict(path, label=path.name)
    if int(raw.get("version", 0)) != METADATA_VERSION:
        return {}
    patches = raw.get("patches")
    return patches if isinstance(patches, dict) else {}


class PatchMetadataIndex:
    """Merged baseline + user metadata keyed by stable_key."""

    def __init__(
        self,
        baseline_path: Path | None = None,
        user_path: Path | None = None,
    ) -> None:
        self.baseline_path = baseline_path or default_baseline_path()
        self.user_path = user_path or USER_METADATA_FILE
        self._baseline: dict[str, dict[str, Any]] = {}
        self._user: dict[str, dict[str, Any]] = {}
        self.reload()

    def reload(self) -> None:
        self._baseline = (
            load_metadata_file(self.baseline_path) if self.baseline_path.exists() else {}
        )
        self._user = load_metadata_file(self.user_path) if self.user_path.exists() else {}

    def instruments_for_patch(self, patch: dict) -> list[str]:
        stable_key = patch.get("stable_key")
        if not stable_key:
            return classify_patch_instruments(patch)

        user_row = self._user.get(stable_key)
        if user_row and user_row.get("instrument_user"):
            override = user_row["instrument_user"]
            if isinstance(override, str):
                return [override]
            if isinstance(override, list) and override:
                return [str(x) for x in override]

        baseline_row = self._baseline.get(stable_key)
        if baseline_row and baseline_row.get("instruments"):
            instruments = baseline_row["instruments"]
            if isinstance(instruments, list) and instruments:
                return [str(x) for x in instruments]

        return classify_patch_instruments(patch)

    def enrich_patch(self, patch: dict) -> dict:
        instruments = self.instruments_for_patch(patch)
        patch["instruments"] = instruments
        patch["instrument_primary"] = instruments[0] if instruments else "other"
        return patch

    def enrich_all(self, patches_by_stable_key: dict[str, dict]) -> None:
        for patch in patches_by_stable_key.values():
            self.enrich_patch(patch)

    def set_user_instrument(
        self, stable_key: str, instruments: list[str] | str, *, patch: dict | None = None
    ) -> None:
        if isinstance(instruments, str):
            instrument_user: list[str] | str = instruments
        else:
            instrument_user = [str(x) for x in instruments if x]

        row = dict(self._user.get(stable_key) or {})
        if patch is not None:
            row.setdefault("name", patch.get("name", ""))
            row.setdefault("path", patch.get("path", ""))
            row.setdefault("path_segments", path_segments_for_patch(patch))
        row["instrument_user"] = instrument_user
        self._user[stable_key] = row
        write_metadata_file(
            self.user_path,
            {"version": METADATA_VERSION, "patches": self._user},
        )
