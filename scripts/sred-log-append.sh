#!/usr/bin/env bash
# Append one row to docs/SRED-DAILY-LOG.md (SR&ED daily capture).
# See .claude/skills/sred-daily-capture/SKILL.md
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG="$REPO_ROOT/docs/SRED-DAILY-LOG.md"
MARKER='| date | session | phase (§4) | hands-on | meas | instrument | review | cat | g5 | anchor | note |'

date= session= phase= hands_on= meas=- instrument=- review=- cat=- g5= anchor= note=

usage() {
  echo "Usage: scripts/sred-log-append.sh --date YYYY-MM-DD --session SPAN --phase PHASE --note TEXT"
  echo "Optional: --hands-on --meas --instrument --review --cat --g5 --anchor"
  exit 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --date) date="$2"; shift 2 ;;
    --session) session="$2"; shift 2 ;;
    --phase) phase="$2"; shift 2 ;;
    --hands-on) hands_on="$2"; shift 2 ;;
    --meas) meas="$2"; shift 2 ;;
    --instrument) instrument="$2"; shift 2 ;;
    --review) review="$2"; shift 2 ;;
    --cat) cat="$2"; shift 2 ;;
    --g5) g5="$2"; shift 2 ;;
    --anchor) anchor="$2"; shift 2 ;;
    --note) note="$2"; shift 2 ;;
    -h|--help) usage ;;
    *) echo "Unknown arg: $1" >&2; usage ;;
  esac
done

[[ -n "$date" && -n "$session" && -n "$phase" && -n "$note" ]] || usage
[[ -f "$LOG" ]] || { echo "Missing $LOG" >&2; exit 1; }

esc() { printf '%s' "$1" | tr '\n' ' ' | sed 's/|/\\|/g'; }
session_e=$(esc "$session")
phase_e=$(esc "$phase")
hands_on_e=$(esc "${hands_on:-?}")
note_e=$(esc "$note")
anchor_e=$(esc "${anchor:--}")
g5_e="${g5:-admissibility-pending}"

row="| $date | $session_e | $phase_e | $hands_on_e | $meas | $instrument | $review | $cat | $g5_e | $anchor_e | $note_e |"

tmp=$(mktemp)
found_header=0
inserted=0
while IFS= read -r line || [[ -n "$line" ]]; do
  if [[ "$inserted" -eq 0 && "$line" == "---" && "$found_header" -eq 1 ]]; then
    printf '%s\n' "$row" >>"$tmp"
    inserted=1
  fi
  if [[ "$line" == "$MARKER" ]]; then
    found_header=1
  fi
  printf '%s\n' "$line" >>"$tmp"
done <"$LOG"

if [[ "$found_header" -eq 0 ]]; then
  rm -f "$tmp"
  echo "Could not find log table header in $LOG" >&2
  exit 1
fi
if [[ "$inserted" -eq 0 ]]; then
  rm -f "$tmp"
  echo "Could not find log section footer (---) in $LOG" >&2
  exit 1
fi

mv "$tmp" "$LOG"
echo "Appended SR&ED daily row: $date $session_e"
