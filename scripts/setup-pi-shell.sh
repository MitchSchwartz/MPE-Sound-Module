#!/usr/bin/env bash
# setup-pi-shell.sh — zsh + Oh My Zsh (shared) + zoxide on the MPE Pi.
#
# Interactive login only — does not touch systemd units, /etc/mpe/mpe.env, or
# running Surge/looper/touch services (they use explicit ExecStart paths).
#
# From laptop:
#   ssh mitch@raspberrypi2.local 'sudo bash -s' < scripts/setup-pi-shell.sh
# Or on Pi:
#   sudo bash ~/MPE-Module/scripts/setup-pi-shell.sh
set -euo pipefail

OMZ_DIR=/usr/local/share/oh-my-zsh
SKEL_ZSHRC=/etc/skel/.zshrc
GLOBAL_SNIPPET=/etc/zsh/omz-zoxide.rc
PI_USER="${PI_USER:-mitch}"

install_custom_omz_plugin() {
  local name="$1" url="$2"
  local dest="${OMZ_DIR}/custom/plugins/${name}"
  if [[ -d "${dest}/.git" ]]; then
    echo "  custom plugin ${name}: already installed"
    return 0
  fi
  echo "  custom plugin ${name}: clone"
  git clone --depth=1 "$url" "$dest"
}

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "FAIL: run as root (sudo) on the Pi" >&2
  exit 1
fi

echo "== apt: zsh, zoxide, git =="
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y zsh zoxide git

if [[ ! -d "$OMZ_DIR" ]]; then
  echo "== install Oh My Zsh (shared) =="
  git clone --depth=1 https://github.com/ohmyzsh/ohmyzsh.git "$OMZ_DIR"
  chmod -R a+rX "$OMZ_DIR"
  chmod -R go-w "$OMZ_DIR"
else
  echo "== Oh My Zsh already at $OMZ_DIR =="
fi

echo "== Oh My Zsh custom plugins =="
mkdir -p "${OMZ_DIR}/custom/plugins"
install_custom_omz_plugin zsh-autosuggestions https://github.com/zsh-users/zsh-autosuggestions
install_custom_omz_plugin zsh-syntax-highlighting https://github.com/zsh-users/zsh-syntax-highlighting
chmod -R a+rX "${OMZ_DIR}/custom"
chmod -R go-w "${OMZ_DIR}/custom" 2>/dev/null || true

mkdir -p /etc/zsh
cat >"$GLOBAL_SNIPPET" <<'EOF'
# Shared Oh My Zsh + zoxide (installed by setup-pi-shell.sh)
export ZSH=/usr/local/share/oh-my-zsh
ZSH_THEME=robbyrussell
# syntax-highlighting must stay last in plugins=(...)
plugins=(git colored-man-pages history-substring-search zsh-autosuggestions zsh-syntax-highlighting)
source "$ZSH/oh-my-zsh.sh"
eval "$(zoxide init zsh)"

# MPE Pi shortcuts (zoxide learns paths after a few cds)
alias zmpe='z ~/MPE-Module'
alias mrepo='z ~/MPE-Module && git status -sb'
EOF
chmod 644 "$GLOBAL_SNIPPET"

cat >"$SKEL_ZSHRC" <<'EOF'
# Default interactive zsh for new users (MPE Pi)
[[ -f /etc/zsh/omz-zoxide.rc ]] && source /etc/zsh/omz-zoxide.rc

export PATH="$HOME/.local/bin:$PATH"
[[ -d "$HOME/bin" ]] && export PATH="$HOME/bin:$PATH"
EOF
chmod 644 "$SKEL_ZSHRC"

write_user_zshrc() {
  local home="$1"
  local user
  user="$(stat -c '%U' "$home")"
  local zshrc="${home}/.zshrc"
  local zprofile="${home}/.zprofile"

  if [[ ! -f "$zshrc" ]] || ! grep -q 'omz-zoxide.rc' "$zshrc" 2>/dev/null; then
    cat >"$zshrc" <<EOF
# MPE Pi interactive zsh — setup-pi-shell.sh
[[ -f /etc/zsh/omz-zoxide.rc ]] && source /etc/zsh/omz-zoxide.rc

export PATH="\$HOME/.local/bin:\$PATH"
[[ -d "\$HOME/bin" ]] && export PATH="\$HOME/bin:\$PATH"
EOF
    chown "${user}:${user}" "$zshrc"
    chmod 644 "$zshrc"
    echo "  wrote $zshrc"
  else
    echo "  skip $zshrc (already configured)"
  fi

  if [[ ! -f "$zprofile" ]]; then
    cat >"$zprofile" <<'EOF'
# Login shell PATH (MPE Pi)
export PATH="$HOME/.local/bin:$PATH"
EOF
    chown "${user}:${user}" "$zprofile"
    chmod 644 "$zprofile"
    echo "  wrote $zprofile"
  fi
}

echo "== per-user zshrc =="
if getent passwd "$PI_USER" >/dev/null; then
  write_user_zshrc "$(getent passwd "$PI_USER" | cut -d: -f6)"
else
  echo "  WARN: user ${PI_USER} not found" >&2
fi

echo "== default login shell -> zsh (${PI_USER} only) =="
ZSH_BIN="$(command -v zsh)"
grep -qxF "$ZSH_BIN" /etc/shells 2>/dev/null || echo "$ZSH_BIN" >>/etc/shells
if getent passwd "$PI_USER" >/dev/null; then
  chsh -s "$ZSH_BIN" "$PI_USER"
  echo "  ${PI_USER} -> ${ZSH_BIN}"
fi

echo ""
echo "OK: zsh $(zsh --version | head -1)"
echo "OK: zoxide $(zoxide --version)"
echo "OK: Oh My Zsh -> $OMZ_DIR"
echo "OK: plugins -> git, colored-man-pages, history-substring-search, zsh-autosuggestions, zsh-syntax-highlighting"
echo "Live stack unchanged — systemd units still use explicit script ExecStart paths."
echo "Re-login (or: exec zsh -l) to pick up the new shell."
