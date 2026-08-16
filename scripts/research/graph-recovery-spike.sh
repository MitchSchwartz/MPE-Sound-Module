#!/bin/bash
# Tier-3 research: jackd/Surge recovery paths (run on Pi only).
# Non-destructive intent: restores original rate/buffer at end.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
# shellcheck source=scripts/lib/paths.sh
source "$REPO_ROOT/scripts/lib/paths.sh"
# shellcheck source=scripts/lib/audio-engine.sh
source "$REPO_ROOT/scripts/lib/audio-engine.sh"

ENV_FILE="/etc/mpe/mpe.env"
LOG="/tmp/mpe-graph-recovery-spike-$(date +%Y%m%d-%H%M%S).log"
exec > >(tee -a "$LOG") 2>&1

section() { echo ""; echo "========== $* =========="; }

now_ms() { date +%s%3N; }

surge_on_graph() {
    mpe_surge_on_jack_graph
}

alsa_playback_format() {
    local f
    for f in /proc/asound/card*/pcm0p/sub0/hw_params; do
        if [ -r "$f" ] && grep -q 'format:' "$f" 2>/dev/null; then
            grep '^format:' "$f" | head -1
            grep '^rate:' "$f" | head -1
            grep '^buffer_size:' "$f" | head -1
            return 0
        fi
    done
    echo "format: (no open playback hw_params)"
}

jack_server_info() {
    if command -v jack_bufsize >/dev/null 2>&1 && pgrep -x jackd >/dev/null; then
        echo "jack_bufsize: $(jack_bufsize 2>/dev/null || echo '?')"
    fi
    if command -v jack_samplerate >/dev/null 2>&1 && pgrep -x jackd >/dev/null; then
        echo "jack_samplerate: $(jack_samplerate 2>/dev/null || echo '?')"
    fi
}

poll_surge_reconnect() {
    local label="$1"
    local timeout_s="${2:-30}"
    local start end elapsed t0
    t0="$(now_ms)"
    start=$(date +%s)
    end=$((start + timeout_s))
    while [ "$(date +%s)" -lt "$end" ]; do
        if surge_on_graph; then
            elapsed=$(( $(now_ms) - t0 ))
            echo "${label}: surge_on_graph=yes elapsed_ms=${elapsed}"
            return 0
        fi
        sleep 0.25
    done
    elapsed=$(( $(now_ms) - t0 ))
    echo "${label}: surge_on_graph=no elapsed_ms=${elapsed} TIMEOUT"
    return 1
}

wait_jack_ready() {
    local timeout_s="${1:-20}"
    local t0 waited
    t0="$(now_ms)"
    waited=0
    while [ "$waited" -lt "$timeout_s" ]; do
        if mpe_jack_server_ready; then
            echo "jack_ready elapsed_ms=$(( $(now_ms) - t0 ))"
            return 0
        fi
        sleep 0.25
        waited=$((waited + 1))
    done
    echo "jack_ready TIMEOUT elapsed_ms=$(( $(now_ms) - t0 ))"
    return 1
}

read_env_var() {
    local key="$1"
    grep -E "^${key}=" "$ENV_FILE" 2>/dev/null | tail -1 | cut -d= -f2- || true
}

ORIG_RATE="$(read_env_var MPE_SURGE_SAMPLE_RATE)"
ORIG_BUFFER="$(read_env_var MPE_JACK_BUFFER)"
ORIG_SURGE_BUFFER="$(read_env_var MPE_SURGE_BUFFER_SIZE)"
[ -z "$ORIG_RATE" ] && ORIG_RATE=48000
[ -z "$ORIG_BUFFER" ] && ORIG_BUFFER=256
[ -z "$ORIG_SURGE_BUFFER" ] && ORIG_SURGE_BUFFER="$ORIG_BUFFER"

section "BASELINE $(date -Iseconds)"
echo "env: rate=$ORIG_RATE jack_buffer=$ORIG_BUFFER surge_buffer=$ORIG_SURGE_BUFFER profile=${MPE_AUDIO_PROFILE:-?}"
echo "surge_on_graph: $(surge_on_graph && echo yes || echo no)"
alsa_playback_format
jack_server_info
jack_lsp 2>/dev/null | grep -i surge || echo "(no surge in jack_lsp)"

# --- Test B: live jack_bufsize (no jackd restart) ---
section "TEST B: jack_bufsize live resize (no jackd restart)"
if command -v jack_bufsize >/dev/null 2>&1 && pgrep -x jackd >/dev/null; then
    cur="$(jack_bufsize 2>/dev/null || echo 0)"
    alt=512
    [ "$cur" = "512" ] && alt=256
    echo "current_buf=$cur trying alt=$alt"
    echo "before resize:"
    alsa_playback_format
    t0="$(now_ms)"
    jack_bufsize "$alt" 2>&1 || true
    echo "jack_bufsize cmd elapsed_ms=$(( $(now_ms) - t0 ))"
    sleep 0.5
    echo "after resize to $alt:"
    alsa_playback_format
    jack_server_info
    echo "restoring buffer via jackd restart..."
    sudo -n systemctl restart mpe-jackd.service
    wait_jack_ready 20 || true
    poll_surge_reconnect "B-restore-watchdog" 25 || true
else
    echo "SKIP: jack_bufsize or jackd unavailable"
fi

# --- Test A: jackd restart only (Surge left running) ---
section "TEST A: jackd restart only — does Surge reconnect without Surge restart?"
surge_pid_before="$(pgrep -f surge-xt-cli | head -1 || true)"
echo "surge_pid_before=$surge_pid_before"
t0="$(now_ms)"
sudo -n systemctl restart --no-block mpe-jackd.service
echo "jackd restart issued elapsed_ms=$(( $(now_ms) - t0 ))"
wait_jack_ready 20 || true
if poll_surge_reconnect "A-jackd-only" 30; then
    surge_pid_after="$(pgrep -f surge-xt-cli | head -1 || true)"
    echo "surge_pid_after=$surge_pid_after same_process=$([ "$surge_pid_before" = "$surge_pid_after" ] && echo yes || echo no)"
else
    echo "Surge did NOT reconnect within 30s — watchdog promote path required"
    surge_pid_after="$(pgrep -f surge-xt-cli | head -1 || true)"
    echo "surge_pid_after=$surge_pid_after (watchdog may restart soon)"
    sleep 8
    poll_surge_reconnect "A-after-watchdog-wait" 20 || true
fi

# --- Test D: jackd restart + immediate Surge restart (planned promote) ---
section "TEST D: jackd restart + sync Surge restart (planned promote path)"
surge_pid_before="$(pgrep -f surge-xt-cli | head -1 || true)"
t0="$(now_ms)"
sudo -n systemctl restart --no-block mpe-jackd.service
wait_jack_ready 20 || true
t_jack=$(( $(now_ms) - t0 ))
sudo -n systemctl restart surge-xt-cli.service
t_total=$(( $(now_ms) - t0 ))
poll_surge_reconnect "D-sync-promote" 15 || true
surge_pid_after="$(pgrep -f surge-xt-cli | head -1 || true)"
echo "jack_ready_ms=$t_jack total_to_surge_on_graph_ms=$t_total surge_pid changed=$([ "$surge_pid_before" != "$surge_pid_after" ] && echo yes || echo no)"
alsa_playback_format

# --- Test C: production set-surge-audio sample-rate toggle ---
section "TEST C: set-surge-audio sample-rate toggle (watchdog path)"
toggle_rate=44100
[ "$ORIG_RATE" = "44100" ] && toggle_rate=48000
echo "toggling $ORIG_RATE -> $toggle_rate via set-surge-audio.sh"
t0="$(now_ms)"
sudo -n "$REPO_ROOT/scripts/set-surge-audio.sh" --sample-rate "$toggle_rate"
echo "set-surge-audio returned elapsed_ms=$(( $(now_ms) - t0 )) (async jackd — still measuring recovery)"
t_start="$t0"
if poll_surge_reconnect "C-watchdog-path" 35; then
    echo "C total silence window estimate_ms=$(( $(now_ms) - t_start ))"
else
    echo "C FAILED to recover within 35s"
fi
alsa_playback_format
jack_server_info

section "RESTORE original rate=$ORIG_RATE"
t0="$(now_ms)"
sudo -n "$REPO_ROOT/scripts/set-surge-audio.sh" --sample-rate "$ORIG_RATE"
poll_surge_reconnect "restore-rate" 35 || true
echo "restore elapsed_ms=$(( $(now_ms) - t0 ))"
alsa_playback_format

section "DONE log=$LOG"
