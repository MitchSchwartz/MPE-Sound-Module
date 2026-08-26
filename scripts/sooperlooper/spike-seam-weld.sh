#!/usr/bin/env bash
# Manual Tier 3 seam-weld spike on the Pi (looper-loop-seam-spec.md P2).
#
# Procedure:
#   1. mpe rt check && mpe looper sl-bench status
#   2. Record defining take on loop 0; release notes; pad-down to close
#   3. Watch bench log for scratch record + seam-weld save/merge/load
#   4. Ear: wrap should include release without loop-length growth or head overdub
#
# Requires MPE_SL_SEAM_WELD=1 (default in sl_seam_weld.py) and scratch loop 15 idle.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

echo "=== Tier 3 seam weld spike ==="
echo "Repo: ${REPO_ROOT}"
echo "Scratch loop: ${MPE_SL_SCRATCH_LOOP:-14} (Pi 5: must not be 15 — empty save_loop)"
echo "Merge samples: ${MPE_SL_SEAM_MERGE_SAMPLES:-2048}"
echo
echo "Preconditions:"
echo "  - Loop 15 must be empty (last track in 16-loop grid)"
echo "  - Bench log should show: scratch tail record → seam merge queued → seam-weld: done"
echo
echo "Disable Tier 3 (Tier 1 only): MPE_SL_SEAM_WELD=0 in /etc/mpe/mpe.env"
echo "Offline merge unit tests: mpe test (tests/test_seam_merge.py)"
