#!/bin/bash
# Verify Pi-side USB-host audio passthrough setup and print host capture hints.
#
# Run on the Pi (usb-host profile). Does not require REAPER or a connected laptop
# capture test — checks profile, gadget bind, Surge tier-0 device, and PCM state.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" && pwd)"
# shellcheck source=lib/paths.sh
source "$SCRIPT_DIR/lib/paths.sh"

PASS=0
WARN=0
FAIL=0

ok() { echo "  OK   $*"; PASS=$((PASS + 1)); }
warn() { echo "  WARN $*"; WARN=$((WARN + 1)); }
fail() { echo "  FAIL $*"; FAIL=$((FAIL + 1)); }

section() {
    echo ""
    echo "== $* =="
}

section "Profile"
PROFILE="${MPE_AUDIO_PROFILE:-standalone}"
echo "MPE_AUDIO_PROFILE=$PROFILE"
if [ "$PROFILE" = "usb-host" ]; then
    ok "usb-host profile active"
else
    warn "not usb-host (standalone uses Sound Blaster analog — gadget checks skipped)"
fi

section "USB gadget"
if [ "$PROFILE" = "usb-host" ]; then
    if "$SCRIPT_DIR/setup-usb-audio-gadget.sh" status >/dev/null 2>&1; then
        ok "configfs UAC2 gadget bound"
        "$SCRIPT_DIR/setup-usb-audio-gadget.sh" status 2>&1 | sed 's/^/       /'
    else
        fail "gadget not bound — run: sudo systemctl start usb-audio-gadget.service"
    fi
else
    echo "  (skipped — profile is $PROFILE)"
fi

section "ALSA playback (Pi → host)"
if command -v aplay >/dev/null 2>&1; then
    aplay -l 2>/dev/null | sed 's/^/  /' || fail "aplay -l failed"
    if aplay -l 2>/dev/null | grep -qiE 'UAC2|USB Audio Passthrough|MPE Sound Module'; then
        ok "gadget playback card visible to ALSA"
    elif [ "$PROFILE" = "usb-host" ]; then
        fail "no UAC2/gadget card in aplay -l"
    fi
else
    warn "aplay not installed"
fi

section "Surge device detection (Tier 0)"
if [ -f "$SURGE_CLI" ]; then
    if DETECT_OUT="$("$SCRIPT_DIR/detect-audio-device.sh" "$SURGE_CLI" 2>/dev/null)"; then
        echo "$DETECT_OUT" | sed 's/^/  /'
        TIER="$(echo "$DETECT_OUT" | sed -n 's/^TIER=//p')"
        if [ "$PROFILE" = "usb-host" ] && [ "$TIER" = "0" ]; then
            ok "Surge selects Tier 0 gadget device"
        elif [ "$PROFILE" = "usb-host" ]; then
            fail "expected Tier 0 in usb-host profile (got TIER=${TIER:-?})"
        else
            ok "detect-audio-device succeeded (profile=$PROFILE)"
        fi
    else
        fail "detect-audio-device.sh failed"
    fi
else
    fail "Surge CLI missing at $SURGE_CLI"
fi

section "Surge service"
if systemctl is-active --quiet surge-xt-cli.service 2>/dev/null; then
    ok "surge-xt-cli.service active"
else
    fail "surge-xt-cli.service not active"
fi

section "Host-side capture hints (run on laptop when Pi is tethered)"
cat <<'EOF'
  The Pi UAC2 gadget exposes **playback on the Pi** = **capture/input on the host**.
  Select the host **input/recording** device (Passthrough), NOT host playback/output.

  Linux host — prefer hardware device for capture (plughw often records silence):
    arecord -l
    # Note card N for "USB Audio Passthrough" / "MPE Sound Module"
    arecord -D hw:N,0 -f S16_LE -r 48000 -c 2 -d 5 /tmp/mpe-host-capture.wav
    # Peak on tone test ~26267 expected; plughw:N,0 may be silent

  PulseAudio/PipeWire: pavucontrol → Recording tab → select gadget input.

  Open issue: host capture may stay silent while Surge is playing user patches
  (tone/generator tests can work). See docs/USB-AUDIO-PASSTHROUGH-SPIKE.md.

  speaker-test on Pi without a host capturing the UAC2 stream may show I/O error -5 — expected.
EOF

section "Summary"
echo "  pass=$PASS warn=$WARN fail=$FAIL"
if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
exit 0
