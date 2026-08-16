#!/usr/bin/env bash
# Reclassify Rhythm/Rhythms folder rows in patch_metadata_baseline.json.
# Sequencer unless patch name looks like a drum hit (kick, snare, tom, …).
set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
baseline="${1:-$repo_root/data/patch_metadata_baseline.json}"

if [[ ! -f "$baseline" ]]; then
  echo "Baseline not found: $baseline" >&2
  exit 1
fi

if command -v jq >/dev/null 2>&1; then
  tmp="$(mktemp)"
  jq '
    def drum_name:
      (.name | ascii_downcase) as $n
      | ($n | test("(^|[^a-z0-9])(kick|snare|tom|hat|hihat|clap|cowbell|cymbal|perc|drum|taiko)([^a-z0-9]|$)"));

    def in_rhythm_folder:
      (.path_segments // [] | map(ascii_downcase) | any(. == "rhythm" or . == "rhythms"));

    .patches |= with_entries(
      if (.value | in_rhythm_folder) then
        .value.instruments = (if (.value | drum_name) then ["percussion"] else ["sequencer"] end)
      else
        .
      end
    )
  ' "$baseline" >"$tmp"
  mv "$tmp" "$baseline"
else
  python3 - "$baseline" <<'PY'
import json
import re
import sys
from pathlib import Path

path = Path(sys.argv[1])
data = json.loads(path.read_text())
drum_re = re.compile(
    r"(^|[^a-z0-9])(kick|snare|tom|hat|hihat|clap|cowbell|cymbal|perc|drum|taiko)([^a-z0-9]|$)"
)

def in_rhythm_folder(segments):
    return any(str(s).lower() in {"rhythm", "rhythms"} for s in (segments or []))

def drum_name(name: str) -> bool:
    return bool(drum_re.search(name.lower()))

changed = 0
for row in data.get("patches", {}).values():
    if not in_rhythm_folder(row.get("path_segments")):
        continue
    target = "percussion" if drum_name(row.get("name", "")) else "sequencer"
    if row.get("instruments") != [target]:
        row["instruments"] = [target]
        changed += 1

path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
print(f"Updated {changed} rhythm-folder row(s) in {path}")
PY
fi

if command -v jq >/dev/null 2>&1; then
  jq '[.patches[] | select(.path_segments | map(ascii_downcase) | any(. == "rhythm" or . == "rhythms")) | .instruments[0]] | group_by(.) | map({(.[0]): length}) | add' "$baseline"
fi
