#!/usr/bin/env bash
# Installed Surge CLI provenance — sidecar + manifest helpers.
# shellcheck shell=bash

mpe_surge_provenance_sidecar() {
    local cli="${1:-${SURGE_CLI:-$HOME/surge/build/surge_xt_products/surge-xt-cli}}"
    printf '%s/surge-xt-cli.provenance\n' "$(dirname "$cli")"
}

mpe_surge_find_build_artifact() {
    local arch="$1"
    local build_dir="${SURGE_SRC:-$HOME/surge-src}/build-${arch}"
    local candidate
    for candidate in \
        "$build_dir/surge_xt_products/surge-xt-cli" \
        "$build_dir/src/surge-xt/surge-xt_artefacts/Release/CLI/surge-xt-cli"; do
        if [ -x "$candidate" ]; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done
    return 1
}

mpe_surge_write_provenance_sidecar() {
    local cli="$1" arch="$2" artifact="$3" sidecar
    sidecar="$(mpe_surge_provenance_sidecar "$cli")"
    local sha ver commit mcpu
    sha="$(sha256sum "$cli" | awk '{print $1}')"
    ver="$("$cli" --version 2>/dev/null | head -1 || echo unknown)"
    commit="$(printf '%s' "$ver" | awk -F. '{print $NF}')"
    case "$arch" in
        a72) mcpu=cortex-a72 ;;
        a76) mcpu=cortex-a76 ;;
        *) mcpu="" ;;
    esac
    cat >"$sidecar" <<EOF
# surge-xt-cli install provenance — do not edit by hand
arch=${arch}
mcpu=${mcpu}
commit=${commit}
version=${ver}
sha256=${sha}
artifact=${artifact}
installed=$(date -Iseconds)
EOF
}

mpe_surge_read_provenance_field() {
    local field="$1" sidecar="$2"
    [ -f "$sidecar" ] || return 1
    awk -F= -v k="$field" '$1==k {print substr($0, index($0, "=")+1); exit}' "$sidecar"
}

mpe_surge_installed_provenance() {
    local cli="${1:-${SURGE_CLI:-$HOME/surge/build/surge_xt_products/surge-xt-cli}}"
    local sidecar arch mcpu sha ver
    sidecar="$(mpe_surge_provenance_sidecar "$cli")"
    if [ -f "$sidecar" ]; then
        arch="$(mpe_surge_read_provenance_field arch "$sidecar")"
        mcpu="$(mpe_surge_read_provenance_field mcpu "$sidecar")"
        sha="$(mpe_surge_read_provenance_field sha256 "$sidecar")"
        ver="$(mpe_surge_read_provenance_field version "$sidecar")"
        commit="$(mpe_surge_read_provenance_field commit "$sidecar")"
    else
        ver="$("$cli" --version 2>/dev/null | head -1 || echo unknown)"
        commit="$(printf '%s' "$ver" | awk -F. '{print $NF}')"
        sha="$(sha256sum "$cli" 2>/dev/null | awk '{print $1}' || echo unknown)"
        case "$sha" in
            c3680d6b0fa7ce5e710f72b06ed88000c2f010fad870853f1765a5b319dbd091)
                arch=generic
                mcpu=""
                ;;
            556cc6f00a2dd85385e2cf0fe041906f7057dbbcd4e948fbc51780c10355df74)
                arch=a72
                mcpu=cortex-a72
                ;;
            *)
                arch=unknown
                mcpu=""
                ;;
        esac
    fi
    printf 'arch=%s\nmcpu=%s\ncommit=%s\nversion=%s\nsha256=%s\n' \
        "${arch:-unknown}" "${mcpu:-}" "${commit:-unknown}" "${ver:-unknown}" "${sha:-unknown}"
}

mpe_surge_provenance_markdown() {
    local cli="${1:-${SURGE_CLI:-$HOME/surge/build/surge_xt_products/surge-xt-cli}}"
    local sidecar arch mcpu sha ver commit
    sidecar="$(mpe_surge_provenance_sidecar "$cli")"
    # shellcheck disable=SC2034
    eval "$(mpe_surge_installed_provenance "$cli" | sed 's/^/local /')"
    cat <<EOF
| Surge build arch | ${arch:-unknown} |
| Surge \`-mcpu\` | ${mcpu:-(none — generic/stock)} |
| Surge commit | ${commit:-unknown} |
| Surge CLI | ${ver:-unknown} |
| Surge sha256 | \`${sha:-unknown}\` |
| Provenance sidecar | $(if [ -f "$sidecar" ]; then echo yes; else echo "no (sha256 inferred)"; fi) |
EOF
}

mpe_surge_provenance_json() {
    local cli="${1:-${SURGE_CLI:-$HOME/surge/build/surge_xt_products/surge-xt-cli}}"
    local sidecar arch mcpu sha ver commit has_sidecar
    sidecar="$(mpe_surge_provenance_sidecar "$cli")"
    eval "$(mpe_surge_installed_provenance "$cli" | sed 's/^/local /')"
    has_sidecar=false
    [ -f "$sidecar" ] && has_sidecar=true
    printf '{"surge_arch":"%s","surge_mcpu":"%s","surge_commit":"%s","surge_version":"%s","surge_sha256":"%s","surge_provenance_sidecar":%s}\n' \
        "${arch:-unknown}" "${mcpu:-}" "${commit:-unknown}" "${ver:-unknown}" "${sha:-unknown}" "$has_sidecar"
}
