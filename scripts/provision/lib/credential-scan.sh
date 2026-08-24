#!/usr/bin/env bash
# Shared credential patterns — refuse to archive or ship state trees that match.
_credential_scan_tree() {
    local root="$1"
    if grep -rqiE 'ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|tskey-auth-[A-Za-z0-9-]+|tskey-[A-Za-z0-9-]+|BEGIN (RSA |OPENSSH |EC )?PRIVATE KEY' "$root" 2>/dev/null; then
        echo "ERROR: credential-shaped content under $root — refusing." >&2
        echo "  Patterns: GitHub PAT, Tailscale auth key, PEM private keys." >&2
        exit 1
    fi
    # SSH_KEY= must be a path ($HOME/... or ~/.ssh/...), never inline key material.
    if grep -rE '^SSH_KEY=' "$root" 2>/dev/null | grep -qvE 'SSH_KEY=\$HOME/|SSH_KEY=~/|SSH_KEY=/home/'; then
        echo "ERROR: SSH_KEY= in $root is not a filesystem path — refusing." >&2
        exit 1
    fi
}
