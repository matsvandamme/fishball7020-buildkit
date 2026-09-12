#!/bin/bash
# Clones the upstream firmware source and applies this repo's fixes on top.
# Run once before the first build_all.sh (or after deleting src/ to start clean).
#
# The upstream repo is vendored as a flattened monorepo (hdl/buildroot/linux/
# u-boot-xlnx all committed directly, no git submodules) - many hundreds of MB
# of Linux kernel / U-Boot / buildroot source, which is why it's cloned fresh
# here rather than committed into this repo.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FW1_DIR="$(dirname "$SCRIPT_DIR")"
SRC_DIR="$FW1_DIR/src"

UPSTREAM_URL="https://github.com/Xiaozhang-code-cloud/Fish-Wan-plutosdr-fw-7020-SDR.git"

if [ -d "$SRC_DIR/.git" ]; then
    echo "=== $SRC_DIR already exists - skipping clone. Delete it first for a clean setup. ==="
else
    echo "=== Cloning $UPSTREAM_URL ==="
    git clone "$UPSTREAM_URL" "$SRC_DIR"
fi

cd "$SRC_DIR"
echo "=== Applying patches ==="
for p in "$FW1_DIR"/patches/*.patch; do
    echo "  -> $(basename "$p")"
    git apply --check "$p" 2>/dev/null && git apply "$p" || {
        echo "     already applied or conflicts - skipping"
    }
done

echo
echo "=== Done. Source tree ready at $SRC_DIR ==="
echo "Next: ./scripts/build_all.sh"
