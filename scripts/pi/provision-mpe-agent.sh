#!/usr/bin/env bash
# Provision `mpe-agent`: a capable, unprivileged appliance user for remote agents.
#
#   sudo ./scripts/pi/provision-mpe-agent.sh <racknerd-pubkey-file>
#
# GOAL (Mitch, 2026-08-16): the agent should be able to do what he can do from
# the laptop — run tests, drive Surge and sooperlooper, start and stop appliance
# services, read logs. NOT: become root on the Pi, or attack the home network
# from it.
#
# WHAT MAKES THE NETWORK CONSTRAINT REAL
#
#   Earlier analysis dismissed egress filtering on the Pi, correctly, because
#   `mitch` has NOPASSWD: ALL and can flush any ruleset. That objection does not
#   apply to `mpe-agent`, which has no sudo at all except the narrow systemctl
#   list below. An owner-matched nftables rule cannot be removed by the user it
#   targets, so for THIS user it is enforcement rather than decoration.
#
# WHAT "NOT ROOT" HONESTLY MEANS HERE
#
#   mpe-agent gets no sudo beyond specific `systemctl` verbs on named appliance
#   units, its own checkout, and no write access to mitch's home or the repo the
#   appliance executes from. The direct escalation paths are closed.
#
#   It is not a hard boundary. `mitch` has NOPASSWD: ALL, so any path that ends
#   in code running as mitch ends at root. That is why mpe-agent gets its OWN
#   checkout and cannot write /home/mitch/MPE-Module — eleven systemd units
#   ExecStart out of that directory, two of them as root. See the access spec's
#   §Decision C: the appliance is expendable, the LAN is what is protected.

set -euo pipefail

AGENT_USER=mpe-agent
AGENT_HOME="/home/$AGENT_USER"
REPO_URL="https://github.com/MitchSchwartz/MPE-Sound-Module.git"
RACKNERD_TS_IP="100.80.219.21"
LAN_CIDR="192.168.0.0/16"

[ "$(id -u)" -eq 0 ] || { echo "ERROR: run as root." >&2; exit 1; }
PUBKEY_FILE="${1:-}"
[ -r "$PUBKEY_FILE" ] || { echo "ERROR: pass a readable pubkey file." >&2; exit 1; }
PUBKEY="$(cat "$PUBKEY_FILE")"

echo "== user =="
if ! id "$AGENT_USER" >/dev/null 2>&1; then
    useradd --create-home --shell /bin/bash \
        --comment "Remote agent — capable, unprivileged, LAN-blocked" "$AGENT_USER"
    echo "  created $AGENT_USER"
else
    echo "  $AGENT_USER exists"
fi
passwd -l "$AGENT_USER" >/dev/null 2>&1 || true
# audio: JACK/ALSA, the whole point. systemd-journal: read logs. No sudo group.
usermod -aG audio,systemd-journal "$AGENT_USER"
echo "  groups: $(id -nG "$AGENT_USER")"

echo "== realtime limits (JACK clients need these) =="
cat >/etc/security/limits.d/95-mpe-agent.conf <<LIM
$AGENT_USER   -  rtprio      95
$AGENT_USER   -  memlock     unlimited
$AGENT_USER   -  nice        -19
LIM
echo "  /etc/security/limits.d/95-mpe-agent.conf"

echo "== own checkout (NOT mitch's — that directory is a root-escalation path) =="
if [ ! -d "$AGENT_HOME/MPE-Module/.git" ]; then
    sudo -u "$AGENT_USER" git clone -q --branch dev "$REPO_URL" "$AGENT_HOME/MPE-Module"
    echo "  cloned dev"
else
    sudo -u "$AGENT_USER" git -C "$AGENT_HOME/MPE-Module" fetch -q origin || true
    echo "  checkout present"
fi

echo "== narrow sudo: named units, named verbs, nothing else =="
# mpe-bench is here so the agent can free the APC + sooperlooper OSC port before
# a hardware test. The alternative asked for was a `sudo kill` rule; sudo-kill
# can signal ANY process including root ones, so it is a far wider grant than
# one more named unit. Starting mpe-bench runs mitch's checkout, not the
# agent's — the agent runs its own branch's bench directly as mpe-agent.
UNITS="mpe-jackd surge-xt-cli mpe-looper surge-watchdog surge-poly-governor midi-clock-in midi-clock-out mpe-pressure-remap mpe-bench"
{
    echo "# Remote agent: restart/start/stop/status of appliance units ONLY."
    echo "# Deliberately NOT /bin/systemctl wholesale — that is equivalent to root,"
    echo "# because systemctl can run arbitrary units and edit unit files."
    for verb in start stop restart status is-active; do
        for u in $UNITS; do
            echo "$AGENT_USER ALL=(root) NOPASSWD: /usr/bin/systemctl $verb $u.service"
        done
    done
} >/etc/sudoers.d/mpe-agent
chmod 0440 /etc/sudoers.d/mpe-agent
visudo -cf /etc/sudoers.d/mpe-agent >/dev/null && echo "  sudoers valid ($(wc -l </etc/sudoers.d/mpe-agent) rules)"

echo "== SSH access from Racknerd (real shell — the agent needs to work) =="
install -d -m 0700 -o "$AGENT_USER" -g "$AGENT_USER" "$AGENT_HOME/.ssh"
printf 'from="%s",no-agent-forwarding,no-port-forwarding,no-X11-forwarding %s\n' \
    "$RACKNERD_TS_IP" "$PUBKEY" > "$AGENT_HOME/.ssh/authorized_keys"
chown "$AGENT_USER:$AGENT_USER" "$AGENT_HOME/.ssh/authorized_keys"
chmod 0600 "$AGENT_HOME/.ssh/authorized_keys"
echo "  authorized_keys pinned to $RACKNERD_TS_IP, no forwarding"

echo "== egress lockdown: mpe-agent cannot reach the LAN =="
AGENT_UID="$(id -u "$AGENT_USER")"
cat >/etc/nftables-mpe-agent.nft <<NFT
#!/usr/sbin/nft -f
# Owner-matched egress policy for the remote agent user.
# Separate table so Tailscale's own tables are untouched.
table inet mpe_agent {
    chain output {
        type filter hook output priority filter; policy accept;

        # Loopback stays open: Surge OSC (53280), sooperlooper (9951), JACK.
        oifname "lo" accept

        # Tailscale CGNAT range — how results get back to Racknerd.
        ip daddr 100.64.0.0/10 accept

        # Everything private is denied to this user. This is the constraint
        # that matters: no scanning, no pivoting, no touching the router.
        skuid $AGENT_UID ip daddr $LAN_CIDR drop
        skuid $AGENT_UID ip daddr 10.0.0.0/8 drop
        skuid $AGENT_UID ip daddr 172.16.0.0/12 drop
        skuid $AGENT_UID ip daddr 169.254.0.0/16 drop
    }
}
NFT
nft -f /etc/nftables-mpe-agent.nft
echo "  ruleset loaded"

cat >/etc/systemd/system/mpe-agent-egress.service <<'UNIT'
[Unit]
Description=Egress lockdown for the mpe-agent user (LAN unreachable)
After=network-pre.target nftables.service
Wants=network-pre.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/sbin/nft -f /etc/nftables-mpe-agent.nft

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now mpe-agent-egress.service >/dev/null 2>&1
echo "  mpe-agent-egress.service enabled (survives reboot)"

echo ""
echo "Done. Verify with: ./scripts/pi/verify-mpe-agent.sh"
