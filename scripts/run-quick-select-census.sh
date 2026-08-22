#!/bin/bash
# Parse Quick Select .fxp metadata and print a jq summary.
# Usage: ./scripts/run-quick-select-census.sh [QUICK_SELECT_DIR] [OUTPUT_JSONL]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" && pwd)"
QS="${1:-$SCRIPT_DIR/../../MPE-Library/assets/user-data/quick-select/latest/Quick Select}"
OUT="${2:-/tmp/qs-census.jsonl}"
PARSER="$SCRIPT_DIR/parse-fxp-metadata.py"

if [ ! -d "$QS" ]; then
    echo "ERROR: Quick Select dir not found: $QS" >&2
    exit 1
fi

: >"$OUT"
while IFS= read -r f; do
    python3 "$PARSER" "$f" >>"$OUT" || true
done < <(find "$QS" -maxdepth 1 -name '*.fxp' | sort)

exec jq -s '
  . as $all | ($all|map(select(.error==null))) as $ok | ($all|map(select(.error!=null))) as $err |
  {
    total: ($all|length), parsed: ($ok|length), errors: ($err|length),
    error_names: [$err[]|.name],
    osc_count_hist: ($ok|group_by(.osc_count)|map({key:(.[0].osc_count|tostring), value:length})|from_entries),
    by_class: {
      triple_twist_pure: [$ok[]|select(.osc_count>=3 and ([.osc_types[]]|all(.==10)))|.name],
      any_twist_10: [$ok[]|select([.osc_types[]]|any(.==10))|.name],
      any_string_9: [$ok[]|select([.osc_types[]]|any(.==9))|.name],
      triple_fm2_6: [$ok[]|select(.osc_count>=3 and ([.osc_types[]]|all(.==6)))|.name],
      classic0_only: [$ok[]|select(.osc_count>0 and ([.osc_types[]]|all(.==0)))|.name],
      any_classic0: [$ok[]|select([.osc_types[]]|any(.==0))|.name],
      triple_any: [$ok[]|select(.osc_count>=3)|.name],
      filter1_ge10: [$ok[]|select(.filter1_type>=10)|.name]
    },
    osc_type_patch_counts: [
      {type:0, label:"Classic", n:([$ok[]|select([.osc_types[]]|any(.==0))]|length)},
      {type:1, label:"Sine", n:([$ok[]|select([.osc_types[]]|any(.==1))]|length)},
      {type:2, label:"Wavetable", n:([$ok[]|select([.osc_types[]]|any(.==2))]|length)},
      {type:6, label:"FM2", n:([$ok[]|select([.osc_types[]]|any(.==6))]|length)},
      {type:8, label:"Modern", n:([$ok[]|select([.osc_types[]]|any(.==8))]|length)},
      {type:9, label:"String", n:([$ok[]|select([.osc_types[]]|any(.==9))]|length)},
      {type:10, label:"Twist", n:([$ok[]|select([.osc_types[]]|any(.==10))]|length)}
    ],
    filter1_counts: ($ok|group_by(.filter1_type)|map({type:.[0].filter1_type,count:length})|sort_by(-.count))
  }
' "$OUT"
