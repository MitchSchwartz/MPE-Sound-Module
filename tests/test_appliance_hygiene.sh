#!/usr/bin/env bash
# apply-appliance-hygiene.sh must not disable a USB audio gadget the appliance
# keeps on purpose.
#
# WHY. 2026-09-07, dry run on the SD image under `sudo`: "would: systemctl
# disable --now usb-audio-gadget.service". The script asked "is the profile
# usb-host?" of an environment sudo had stripped of MPE_AUDIO_PROFILE, and the
# real answer in /etc/mpe/mpe.env was standalone WITH MPE_USB_GADGET_PERSIST=1
# -- the gadget enabled and active by design. The decision now belongs to
# scripts/lib/gadget-persist.sh, the same library the unit asks at boot, fed
# from the env file when the environment is bare. This runs the real script
# in --dry-run against fake tools and reads what it says it would do.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPT="$ROOT/scripts/apply-appliance-hygiene.sh"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

fail() { echo "FAIL: $*" >&2; exit 1; }

mkdir -p "$TMP/bin"
cat >"$TMP/bin/systemctl" <<'EOF'
#!/usr/bin/env bash
# Everything is enabled, so every disable the script wants shows as "would:".
case "$1" in is-enabled) exit 0 ;; esac
exit 0
EOF
cat >"$TMP/bin/aplay" <<'EOF'
#!/usr/bin/env bash
echo "card 4: UAC2Gadget [UAC2_Gadget], device 0: UAC2 PCM [UAC2 PCM]"
EOF
cat >"$TMP/bin/nmcli" <<'EOF'
#!/usr/bin/env bash
# `nmcli -t -f NAME,TYPE con show` -- the SD image's profiles.
if [ "$1" = "-t" ]; then
    printf '%s\n' "Potato 2.4:802-11-wireless" "Wired connection 1:802-3-ethernet" "lo:loopback"
fi
exit 0
EOF
cat >"$TMP/bin/iw" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
chmod +x "$TMP"/bin/*

run() {
    # $1 = env file contents (or "-" for no file); the rest = extra env, as
    # `sudo` would hand it over: no MPE_* variables at all.
    local envfile="$TMP/mpe.env"
    if [ "$1" = "-" ]; then
        rm -f "$envfile"
    else
        printf '%b' "$1" >"$envfile"
    fi
    shift
    env -u MPE_AUDIO_PROFILE -u MPE_USB_GADGET_PERSIST "$@" \
        PATH="$TMP/bin:$PATH" MPE_ENV_FILE="$envfile" \
        bash "$SCRIPT" --dry-run 2>&1
}

DISABLE='would: systemctl disable --now usb-audio-gadget.service'

# 1. The SD image as it is: standalone, persist on. KEPT.
out="$(run 'MPE_AUDIO_PROFILE=standalone\nMPE_USB_GADGET_PERSIST=1\n')"
grep -q 'usb-audio-gadget: kept (profile=standalone persist=1)' <<<"$out" \
    || fail "standalone+persist=1 was not kept:"$'\n'"$out"
grep -qF "$DISABLE" <<<"$out" && fail "standalone+persist=1 would be disabled"

# 2. Persist explicitly off on a standalone box: the one case the gadget goes.
out="$(run 'MPE_AUDIO_PROFILE=standalone\nMPE_USB_GADGET_PERSIST=0\n')"
grep -qF "$DISABLE" <<<"$out" || fail "standalone+persist=0 was not disabled:"$'\n'"$out"

# 3. usb-host routes audio to the host: kept even with persist off.
out="$(run 'MPE_AUDIO_PROFILE=usb-host\nMPE_USB_GADGET_PERSIST=0\n')"
grep -q 'usb-audio-gadget: kept (profile=usb-host persist=0)' <<<"$out" \
    || fail "usb-host was not kept:"$'\n'"$out"

# 4. No env file at all (a bare box, or the file unreadable): fail safe, keep.
out="$(run -)"
grep -q 'usb-audio-gadget: kept' <<<"$out" || fail "no env file did not keep the gadget:"$'\n'"$out"
grep -qF "$DISABLE" <<<"$out" && fail "no env file would disable the gadget"

# 5. The environment wins over the file when it is actually set.
out="$(run 'MPE_AUDIO_PROFILE=standalone\nMPE_USB_GADGET_PERSIST=0\n' MPE_USB_GADGET_PERSIST=1)"
grep -q 'usb-audio-gadget: kept' <<<"$out" || fail "environment persist=1 did not win over the file"

# 6. WiFi powersave is persisted per connection, not only set on the device.
grep -q 'would: nmcli con modify "Potato 2.4" 802-11-wireless.powersave 2' <<<"$out" \
    || fail "WiFi powersave is not persisted on the wireless profile:"$'\n'"$out"
grep -q 'Wired connection 1' <<<"$out" && fail "a wired profile got the WiFi powersave setting"

echo "PASS: apply-appliance-hygiene keeps the gadget the appliance keeps, and persists WiFi powersave"
