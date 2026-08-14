#!/usr/bin/env bash
# Generate 16 stereo 48 kHz WAV test loops (distinct sine per slot) for SooperLooper smoke tests.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
OUT_DIR="${1:-${REPO_ROOT}/tests/fixtures/sooperlooper-loops}"

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "generate-test-clips: ffmpeg required" >&2
  exit 1
fi

mkdir -p "${OUT_DIR}"
DUR="${MPE_SL_TEST_CLIP_SEC:-2}"

for i in $(seq 0 15); do
  freq=$((220 + i * 55))
  out="${OUT_DIR}/loop$(printf '%02d' "${i}").wav"
  ffmpeg -y -loglevel error \
    -f lavfi -i "sine=frequency=${freq}:duration=${DUR}" \
    -ar 48000 -ac 2 "${out}"
  echo "  ${out} (${freq} Hz, ${DUR}s)"
done

echo "generate-test-clips: 16 clips in ${OUT_DIR}"
