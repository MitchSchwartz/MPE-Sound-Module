#!/usr/bin/env bash
# Install surge-xt-cli from ~/surge-src/build-{arch}/ to runtime path + write provenance sidecar.
# Usage: ./scripts/install-surge-from-build.sh --arch {a72|a76|generic}

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ARCH=""

usage() {
    echo "Usage: $0 --arch {a72|a76|generic}" >&2
    exit 1
}

while [ $# -gt 0 ]; do
    case "$1" in
        --arch) ARCH="${2:?}"; shift 2 ;;
        -h|--help) usage ;;
        *) echo "Unknown arg: $1" >&2; usage ;;
    esac
done

[ -n "$ARCH" ] || usage
case "$ARCH" in
    a72|a76|generic) ;;
    *) echo "ERROR: unknown arch: $ARCH" >&2; exit 1 ;;
esac

# shellcheck source=lib/paths.sh
source "$ROOT/scripts/lib/paths.sh"
# shellcheck source=lib/surge-build-provenance.sh
source "$ROOT/scripts/lib/surge-build-provenance.sh"

artifact="$(mpe_surge_find_build_artifact "$ARCH")" || {
    echo "ERROR: no built artifact for arch=$ARCH under ${SURGE_SRC:-$HOME/surge-src}/build-${ARCH}/" >&2
    echo "Run: ./scripts/build-surge.sh --arch $ARCH" >&2
    exit 1
}

dest_dir="$(dirname "$SURGE_CLI")"
mkdir -p "$dest_dir"
install -m 755 "$artifact" "$SURGE_CLI"
mpe_surge_write_provenance_sidecar "$SURGE_CLI" "$ARCH" "$artifact"

{
    echo "Installed $SURGE_CLI from $artifact"
    echo "PROVENANCE $(mpe_surge_installed_provenance "$SURGE_CLI" | tr '\n' ' ')"
    "$SURGE_CLI" --version 2>&1 || true
} 
