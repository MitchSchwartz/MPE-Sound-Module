#!/bin/bash
# Offline checks for Sound Blaster Speaker raw ↔ dB mapping (dac-volume.sh).

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=../scripts/lib/dac-volume.sh
source "$ROOT/scripts/lib/dac-volume.sh"

assert_eq() {
    local got="$1" want="$2" label="$3"
    if [ "$got" != "$want" ]; then
        echo "FAIL $label: got=$got want=$want" >&2
        exit 1
    fi
}

assert_eq "$(dac_speaker_raw_from_db -12)" "64" "raw at -12 dB (appliance default)"
assert_eq "$(dac_speaker_raw_from_db -6)" "76" "raw at -6 dB"
assert_eq "$(dac_speaker_raw_from_db -20)" "48" "raw at -20 dB"
assert_eq "$(dac_speaker_raw_from_db 0)" "88" "raw at 0 dB"
assert_eq "$(dac_speaker_db_from_raw 64)" "-12.00" "dB at raw 64"
assert_eq "$(dac_speaker_db_from_raw 76)" "-6.00" "dB at raw 76"
assert_eq "$(dac_speaker_db_from_raw 48)" "-20.00" "dB at raw 48"

echo "OK test_dac_volume.sh"
