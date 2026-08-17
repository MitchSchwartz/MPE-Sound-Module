#!/bin/bash
# DEPRECATED 2026-08-16 — do not use. This script provisions a GitHub PAT onto
# the appliance. The appliance does not need one, and the credential it used to
# install was a liability. It now refuses to run.
#
# See docs/PI-GITHUB-ACCESS.md for what replaced it.
#
# WHY IT WAS REMOVED
#
#   MPE-Sound-Module is PUBLIC. `git pull` over HTTPS needs no credential at
#   all, so the token bought nothing and cost the following:
#
#   - The installed token was a classic PAT with scopes
#     `public_repo, repo:status, repo_deployment`. `public_repo` is WRITE
#     access to every public repository on the account — not read, and not
#     limited to this repo. Classic tokens cannot be scoped per-repository.
#
#   - It lived at /etc/mpe/git-credentials mode 640 root:mitch, i.e. readable
#     by `mitch`. Agent-authored test code runs as `mitch` on the appliance
#     (see docs/racknerd-pi-access-spec.md), so any agent could read it and
#     push to any public repo on the account.
#
#   Capability absent beats capability forbidden: the token was revoked and
#   removed rather than scoped down.
#
# WHAT TO DO INSTEAD
#
#   Public repos (MPE-Sound-Module):
#       Nothing. The HTTPS remote pulls anonymously. Verify with:
#           git -C ~/MPE-Module fetch origin --dry-run
#
#   Private repos (MPE-Library, if it is ever cloned on the Pi — as of
#   2026-08-16 ~/MPE-Library exists but is NOT a git checkout):
#       Use a READ-ONLY DEPLOY KEY, not a PAT. A deploy key is scoped to one
#       repository by construction, is read-only via a checkbox the key holder
#       cannot widen, and carries NO GitHub API access — a PAT can enumerate
#       the repo, read issues and read Actions metadata within its scope.
#
#           ssh-keygen -t ed25519 -C "mpe-pi-readonly-$(date +%Y-%m)" \
#               -f ~/.ssh/mpe_pi_library_ro
#           # GitHub → MPE-Library → Settings → Deploy keys → Add deploy key
#           # Paste the .pub. LEAVE "Allow write access" UNCHECKED.
#           # Then set that repo's remote to the SSH URL.
#
#       Keep it to that one repo. Do not reuse it for MPE-Sound-Module, which
#       needs no credential.
#
# Rotation/cleanup already performed on the appliance (2026-08-16):
#   - /etc/mpe/git-credentials removed; token revoked in GitHub
#   - credential.helper unset (local and global)
#   - push URL disabled:  git remote set-url --push origin DISABLED
#   - ~/.ssh holds only authorized_keys; no private keys, empty known_hosts

set -euo pipefail

cat >&2 <<'MSG'
setup-pi-github-pat.sh: DEPRECATED — refusing to run.

The appliance does not need a GitHub credential. MPE-Sound-Module is public and
pulls anonymously over HTTPS.

This script used to install a classic PAT whose `public_repo` scope granted
WRITE access to every public repo on the account, stored where any code running
as `mitch` could read it. It was revoked and removed on 2026-08-16.

For a private repo on the appliance, use a read-only DEPLOY KEY scoped to that
one repository. See the comments at the top of this file, and
docs/PI-GITHUB-ACCESS.md.
MSG
exit 1
