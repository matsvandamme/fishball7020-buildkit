# Source this instead of settings64.sh directly:
#   source tools/env-vivado.sh
#
# A default Ubuntu 22.04 install does not ship libtinfo5/libncurses5/libssl1.1, which
# Vivado 2022.2's bundled binaries require at runtime. This prepends
# locally-extracted copies of those libraries to LD_LIBRARY_PATH so
# Vivado can find them without touching the rest of the OS.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export LD_LIBRARY_PATH="$SCRIPT_DIR/legacy-libs/libs${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
source /tools/Xilinx/Vivado/2022.2/settings64.sh
export PATH="$PATH:/tools/Xilinx/Vitis/2022.2/bin"
