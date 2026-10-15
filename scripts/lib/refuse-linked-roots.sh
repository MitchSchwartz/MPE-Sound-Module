#!/usr/bin/env bash
# Exit 1 if any Surge patch root is a symlink (i.e. wired to the personal library).
# Piped to the device by the tar-based deploy scripts: extracting a tarball over a
# linked root makes GNU tar replace the link with a real directory (detaching it
# from the library), and `mv third-party patches_3rdparty` would nest the patches
# inside the library's scan root. Needs MPE_SURGE_RESOURCES and MPE_SURGE_DOCS.
bad=0
for r in "$MPE_SURGE_RESOURCES/patches_factory" "$MPE_SURGE_RESOURCES/patches_3rdparty" "$MPE_SURGE_DOCS/Patches"; do
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
