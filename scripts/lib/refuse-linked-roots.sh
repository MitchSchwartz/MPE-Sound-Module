#!/usr/bin/env bash
# Refuse a tarball patch deploy when it could detach the library. Piped to the device
# by deploy-patches.sh / deploy-all.sh before anything is extracted.
#
# Exit 0: all three patch roots are known and none is a symlink.
# Exit 1: a root is a link into the personal library (extracting a tarball over a link
#         makes GNU tar replace it with a real directory).
# Exit 2: cannot tell (a root path is unknown, e.g. paths.sh failed to load): fail closed.
res="${MPE_SURGE_RESOURCES:-}"
docs="${MPE_SURGE_DOCS:-}"
if [ -z "$res" ] || [ -z "$docs" ]; then
    echo "ERROR: cannot tell where the patch roots are (MPE_SURGE_RESOURCES / MPE_SURGE_DOCS unset); refusing." >&2
    exit 2
fi
bad=0
for r in "$res/patches_factory" "$res/patches_3rdparty" "$docs/Patches"; do
    if [ -L "$r" ]; then
        echo "ERROR: $r is a link into the personal library; a tarball deploy would detach it." >&2
        bad=1
    fi
done
if [ "$bad" = 1 ]; then
    echo "  Update patches through the library instead (git pull in the device's checkout," >&2
    echo "  or scripts/mpe-library update). See docs/LIBRARY-MIGRATION.md." >&2
    exit 1
fi
exit 0
