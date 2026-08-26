#!/bin/bash
# Interface guard (2026-08-26) — offline, with a stub amixer.
#
# Guards what nothing else on the appliance could see: a device that accepts the
# USB stream and discards it, while every other reading stays green.

set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FAILED=0
ok()   { echo "  ok   - $1"; }
fail() { echo "  FAIL - $1" >&2; FAILED=1; }
check(){ if [ "$1" = "$2" ]; then ok "$3"; else fail "$3 (got='$1' want='$2')"; fi; }

STUB="$(mktemp -d)"
trap 'rm -rf "$STUB"' EXIT
export PATH="$STUB:$PATH"

# Stub amixer. State lives in files so cset is observable by the test.
cat > "$STUB/amixer" <<'EOF'
#!/bin/bash
S="$STUB_STATE"
shift 2                      # drop "-c N"
case "$1" in
  controls) cat "$S/controls"; exit 0 ;;
  cget)
    name="${2#name=}"; name="${name%\'}"; name="${name#\'}"
    if [ "$name" = "Standalone Switch" ]; then
      echo "  ; type=BOOLEAN,access=rw------,values=1"
      echo "  : values=$(cat "$S/standalone")"
    else
      echo "  ; type=ENUMERATED,access=rw------,values=1,items=15"
      echo "  ; Item #5 'Mix A'"
      echo "  ; Item #11 'PCM 1'"
      echo "  ; Item #12 'PCM 2'"
      echo "  : values=$(cat "$S/${name// /_}" 2>/dev/null || echo 11)"
    fi
    exit 0 ;;
  cset)
    name="${2#name=}"; name="${name%\'}"; name="${name#\'}"
    [ "$name" = "Standalone Switch" ] && echo "$3" > "$S/standalone"
    exit 0 ;;
  sset)
    echo "$3" > "$S/set_$2"
    case "$3" in "PCM 1") echo 11 ;; "PCM 2") echo 12 ;; esac \
      > "$S/Analogue_Output_${2##* }_Playback_Enum"
    exit 0 ;;
esac
exit 0
EOF
chmod +x "$STUB/amixer"
export STUB_STATE="$STUB/state"
mkdir -p "$STUB_STATE"

reset_state() {
    printf 'off\n' > "$STUB_STATE/standalone"
    for n in 01 02 03 04; do
        case "$n" in 01|03) v=11 ;; *) v=12 ;; esac
        echo "$v" > "$STUB_STATE/Analogue_Output_${n}_Playback_Enum"
    done
    rm -f "$STUB_STATE"/set_* 2>/dev/null
    {
        echo "numid=90,iface=MIXER,name='Standalone Switch'"
        for n in 01 02 03 04; do
            echo "numid=2$n,iface=MIXER,name='Analogue Output $n Playback Enum'"
        done
    } > "$STUB_STATE/controls"
}

# shellcheck source=../scripts/lib/interface-guard.sh
source "$ROOT/scripts/lib/interface-guard.sh"

echo "test_interface_guard.sh"

# --- healthy device: no changes ---------------------------------------------
reset_state
OUT="$(mpe_interface_guard 0 2>&1)"
check "$?" "0" "healthy device returns success"
check "$OUT" "" "healthy device is silent (no warning noise on every boot)"

# --- standalone mode is detected and cleared --------------------------------
reset_state; echo on > "$STUB_STATE/standalone"
OUT="$(mpe_interface_guard 0 2>&1)"
check "$(cat "$STUB_STATE/standalone")" "off" "standalone is cleared"
case "$OUT" in *STANDALONE*) ok "standalone is reported loudly" ;;
               *) fail "standalone is reported loudly" ;; esac
case "$OUT" in *"power cycle"*) ok "tells the user a power cycle may be needed" ;;
               *) fail "tells the user a power cycle may be needed" ;; esac

# --- output fed from the hardware mixer is corrected ------------------------
reset_state; echo 5 > "$STUB_STATE/Analogue_Output_01_Playback_Enum"   # 'Mix A'
OUT="$(mpe_interface_guard 0 2>&1)"
check "$(cat "$STUB_STATE/set_Analogue Output 01" 2>/dev/null)" "PCM 1" \
    "output sourced from Mix A is repointed at PCM 1"
case "$OUT" in *"'Mix A'"*) ok "names the wrong source in the warning" ;;
               *) fail "names the wrong source in the warning" ;; esac

# --- opt-out leaves routing alone but still warns ---------------------------
reset_state; echo 5 > "$STUB_STATE/Analogue_Output_01_Playback_Enum"
OUT="$(MPE_INTERFACE_FORCE_PCM=0 mpe_interface_guard 0 2>&1)"
if [ -f "$STUB_STATE/set_Analogue Output 01" ]; then
    fail "MPE_INTERFACE_FORCE_PCM=0 leaves routing untouched"
else
    ok "MPE_INTERFACE_FORCE_PCM=0 leaves routing untouched"
fi
case "$OUT" in *WARNING*) ok "opt-out still warns (never silent)" ;;
               *) fail "opt-out still warns (never silent)" ;; esac

# --- full kill switch --------------------------------------------------------
reset_state; echo on > "$STUB_STATE/standalone"
OUT="$(MPE_INTERFACE_GUARD=0 mpe_interface_guard 0 2>&1)"
check "$(cat "$STUB_STATE/standalone")" "on" "MPE_INTERFACE_GUARD=0 does nothing"

# --- a device without these controls is skipped, not failed ------------------
reset_state; : > "$STUB_STATE/controls"
mpe_interface_guard 0 >/dev/null 2>&1
check "$?" "0" "interface without the matrix is skipped cleanly"

# --- never fails the caller ---------------------------------------------------
# jackd must start even if the guard cannot do its job; no instrument at all is
# worse than a misconfigured one.
reset_state
mpe_interface_guard "" >/dev/null 2>&1
check "$?" "0" "empty card argument returns success"

if [ "$FAILED" -ne 0 ]; then echo "FAILED test_interface_guard.sh" >&2; exit 1; fi
echo "OK test_interface_guard.sh"
