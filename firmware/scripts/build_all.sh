#!/bin/bash
# Full build: HDL -> bitstream -> FSBL -> u-boot -> kernel -> rootfs -> SD-card
# file set (BOOT.bin, devicetree.dtb, uEnv.txt, uImage, uramdisk.image.gz).
#
# Run ./setup.sh once first to clone the upstream source and apply patches/.
# Output lands in ../output/.
#
# This build reproduces the board's actual factory-default firmware (USB +
# Ethernet). Verified this session: devicetree.dtb comes out byte-for-byte
# identical to the real working firmware; uEnv.txt and the rootfs file list
# are content-identical (uEnv.txt differs only in U-Boot's internal env
# hash-table dump order, which doesn't affect boot behavior); the kernel's
# uImage and BOOT.bin are extremely close but not byte-identical, because the
# upstream repo's git history has been squashed to a single commit dated
# after this board's firmware was actually built, so a handful of source
# lines have drifted since (not recoverable from the public repo alone).

set -euo pipefail
BUILD_ALL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FW1_DIR="$(dirname "$BUILD_ALL_DIR")"
REPO_ROOT="$(dirname "$FW1_DIR")"
SRC_DIR="$FW1_DIR/src"
OUT_DIR="$FW1_DIR/output"

if [ ! -d "$SRC_DIR" ]; then
    echo "ERROR: $SRC_DIR not found. Run ./setup.sh first." >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Options
# ---------------------------------------------------------------------------
# Stages 3-5 (u-boot, kernel, root filesystem) produce byte-identical output
# when only the HDL has changed, and together they are most of the wall time.
# --hdl-only reuses what is already in src/ and rebuilds just the parts that
# actually depend on the bitstream: HDL -> FSBL -> uEnv.txt -> BOOT.bin.
HDL_ONLY=0
for arg in "$@"; do
    case "$arg" in
        --hdl-only) HDL_ONLY=1 ;;
        -h|--help)
            echo "Usage: $(basename "$0") [--hdl-only]"
            echo "  --hdl-only   rebuild HDL, FSBL and packaging only, reusing the"
            echo "               existing kernel, u-boot and root filesystem."
            exit 0 ;;
        *) echo "ERROR: unknown option '$arg' (try --help)" >&2; exit 1 ;;
    esac
done
[ "$HDL_ONLY" -eq 1 ] && echo "*** --hdl-only: skipping u-boot, kernel and rootfs stages ***"

# ---------------------------------------------------------------------------
# Preflight. This build takes the better part of an hour and the stages that
# need a given tool are spread across all of it - the FSBL stage, for example,
# is ~40 minutes in. Discovering a missing package there is miserable, so
# check everything up front and fail in two seconds instead.
# ---------------------------------------------------------------------------
preflight_fail=0
for c in git make dtc mkimage bison flex python3; do
    command -v "$c" >/dev/null 2>&1 || {
        echo "ERROR: '$c' not found." >&2; preflight_fail=1; }
done
for f in /tools/Xilinx/Vivado/2022.2/bin/vivado \
         /tools/Xilinx/Vitis/2022.2/bin/xsct \
         /tools/Xilinx/Vitis/2022.2/bin/bootgen; do
    [ -x "$f" ] || { echo "ERROR: missing $f (is Vivado/Vitis 2022.2 installed?)" >&2
                     preflight_fail=1; }
done
# The kernel builds GCC plugins (scripts/gcc-plugins), which include gmp.h
# from the compiler's plugin headers. Ask the compiler rather than guessing a
# path - it lives at /usr/include/gmp.h on some distros and under a multiarch
# directory on others. Without it the build dies ~50 minutes in, at stage 4.
if ! echo '#include <gmp.h>' | gcc -E -x c - >/dev/null 2>&1; then
    echo "ERROR: gmp.h not found - the kernel's GCC plugins cannot build." >&2
    echo "       sudo apt install -y libgmp-dev libmpc-dev libmpfr-dev" >&2
    preflight_fail=1
fi

# xsct needs an X display. With a desktop session ($DISPLAY set) it uses that;
# headless - over SSH, in CI, on a server - it falls back to Xvfb, and without
# Xvfb installed it dies at stage 2 with a bare "Xvfb is not available".
if [ -z "${DISPLAY:-}" ] && ! command -v Xvfb >/dev/null 2>&1; then
    echo "ERROR: no \$DISPLAY and Xvfb is not installed." >&2
    echo "       Vitis (xsct) needs one or the other to build the FSBL." >&2
    echo "       Headless/over SSH:  sudo apt install -y xvfb" >&2
    echo "       Or, if a desktop is running on this machine:  export DISPLAY=:0" >&2
    preflight_fail=1
fi
[ "$preflight_fail" -eq 0 ] || { echo "Preflight failed - fix the above and re-run." >&2; exit 1; }

export CROSS_COMPILE=arm-linux-gnueabihf-

# IMPORTANT: Vivado's own settings64.sh (sourced by tools/env-vivado.sh)
# does much more than add Vivado's own bin/ to PATH - it also prepends a
# long list of Xilinx-bundled cross-toolchain directories for every
# architecture Vivado/Vitis knows about (microblaze, arm, aarch32, aarch64,
# armr5, ...). Left in PATH for the rest of the script, this actively
# breaks the kernel build: its gcc-plugin infrastructure ends up loading
# ./scripts/gcc-plugins/arm_ssp_per_task_plugin.so against a mismatched
# libc.so.6 living under one of those Xilinx toolchain directories, failing
# with "GLIBC_2.38 not found" (confirmed by reproducing it with a clean
# kernel tree and manually inspecting PATH/LD_LIBRARY_PATH during
# development - this is Vivado's own settings script doing this, not
# anything this repo's scripts add).
#
# Fix: capture a clean PATH *before* sourcing env-vivado.sh, and use only
# that clean PATH (plus this project's own toolchain dirs) for the u-boot/
# kernel/buildroot steps below, which need none of Vivado's own tools.
# Vivado/Vitis/bootgen are re-added, narrowly, only around the steps that
# actually need them.
CLEAN_PATH="$PATH"
TOOLCHAIN_PATH="$SRC_DIR/buildroot/output/host/bin:$SRC_DIR/buildroot/output/host/sbin:$CLEAN_PATH"

# Apply the board defconfig, then force every source download to go to
# Buildroot's own mirror FIRST.
#
# By default Buildroot tries each package's upstream URL before falling back
# to BR2_BACKUP_SITE. For the many GNU packages (m4, autoconf, automake,
# libtool, ...) that upstream URL is http://ftpmirror.gnu.org, a redirector
# that is regularly slow or unroutable - and every unreachable mirror costs a
# multi-minute TCP timeout before the fallback is even attempted, which can
# stall or fail the build for reasons that have nothing to do with this repo.
# sources.buildroot.net carries the same tarballs and is reliable.
#
# This changes only WHERE sources are fetched from, never WHAT is fetched:
# Buildroot still verifies every download against the recorded .hash file.
buildroot_defconfig() {
    PATH="$CLEAN_PATH" make -C "$SRC_DIR/buildroot" ARCH=arm zynq_pluto_defconfig
    sed -i '/^BR2_PRIMARY_SITE=/d' "$SRC_DIR/buildroot/.config"
    echo 'BR2_PRIMARY_SITE="https://sources.buildroot.net"' >> "$SRC_DIR/buildroot/.config"
    PATH="$CLEAN_PATH" make -C "$SRC_DIR/buildroot" olddefconfig
}

echo "=== [1/7] Building HDL: synth -> impl -> bitstream -> hardware platform ==="
(
    source "$REPO_ROOT/tools/env-vivado.sh"
    cd "$SRC_DIR/hdl/projects/pluto"
    cp "$BUILD_ALL_DIR/build_hdl.tcl" .
    vivado -mode batch -source build_hdl.tcl -journal build_hdl.jou -log build_hdl.log
    echo "    Timing summary:"; grep -A3 "Design Timing Summary" timing.rpt | tail -2 || true
)

echo "=== [1b/7] Building the cross-compilation toolchain (Linaro GCC 7.3-2018.05) ==="
if [ ! -x "$SRC_DIR/buildroot/output/host/bin/arm-linux-gnueabihf-gcc" ]; then
    buildroot_defconfig
    PATH="$CLEAN_PATH" make -C "$SRC_DIR/buildroot" toolchain
else
    echo "    already built, skipping"
fi
[ -x "$SRC_DIR/buildroot/output/host/bin/arm-linux-gnueabihf-gcc" ] || { echo "ERROR: toolchain build failed"; exit 1; }

echo "=== [2/7] Building FSBL ==="
(
    source "$REPO_ROOT/tools/env-vivado.sh"
    export PATH="/tools/Xilinx/Vitis/2022.2/bin:$PATH"
    mkdir -p "$SRC_DIR/hdl/fsbl"
    cd "$SRC_DIR/hdl/fsbl"
    cp "$BUILD_ALL_DIR/gen_fsbl_create.tcl" "$BUILD_ALL_DIR/gen_fsbl_build.tcl" .
    rm -rf system_top fsbl fsbl_system .metadata
    xsct gen_fsbl_create.tcl
    xsct gen_fsbl_build.tcl
    # The freshly-compiled output is at fsbl/Debug/fsbl.elf - the copy under
    # system_top/zynq_fsbl/ is a stale scaffold-time snapshot xsct's own
    # "app build" does not refresh (confirmed by checksum diff).
    make -C fsbl/Debug
)
FSBL_ELF="$SRC_DIR/hdl/fsbl/fsbl/Debug/fsbl.elf"
[ -f "$FSBL_ELF" ] || { echo "ERROR: FSBL build failed"; exit 1; }

if [ "$HDL_ONLY" -eq 1 ]; then
    echo "=== [skipped] u-boot (--hdl-only) ==="
    [ -f "$SRC_DIR/u-boot-xlnx/u-boot" ] || { echo "ERROR: --hdl-only needs a previous full build; $SRC_DIR/u-boot-xlnx/u-boot is missing." >&2; exit 1; }
else
    echo "=== [3/7] Building u-boot ==="
    PATH="$TOOLCHAIN_PATH" make -C "$SRC_DIR/u-boot-xlnx" ARCH=arm CROSS_COMPILE=$CROSS_COMPILE zynq_pluto_defconfig
    PATH="$TOOLCHAIN_PATH" make -C "$SRC_DIR/u-boot-xlnx" ARCH=arm CROSS_COMPILE=$CROSS_COMPILE UBOOTVERSION="PlutoSDR"

fi

if [ "$HDL_ONLY" -eq 1 ]; then
    echo "=== [skipped] kernel (--hdl-only) ==="
    [ -f "$SRC_DIR/linux/arch/arm/boot/uImage" ] || { echo "ERROR: --hdl-only needs a previous full build; $SRC_DIR/linux/arch/arm/boot/uImage is missing." >&2; exit 1; }
else
    echo "=== [4/7] Building kernel: uImage + fishball device tree ==="
    PATH="$TOOLCHAIN_PATH" make -C "$SRC_DIR/linux" ARCH=arm CROSS_COMPILE=$CROSS_COMPILE zynq_pluto_defconfig
    PATH="$TOOLCHAIN_PATH" make -C "$SRC_DIR/linux" -j "$(nproc)" ARCH=arm CROSS_COMPILE=$CROSS_COMPILE uImage UIMAGE_LOADADDR=0x8000
    PATH="$TOOLCHAIN_PATH" DTC_FLAGS=-@ make -C "$SRC_DIR/linux" -j "$(nproc)" ARCH=arm CROSS_COMPILE=$CROSS_COMPILE zynq-pluto-sdr-fishball.dtb

fi

if [ "$HDL_ONLY" -eq 1 ]; then
    echo "=== [skipped] root filesystem (--hdl-only) ==="
    [ -f "$SRC_DIR/buildroot/output/images/rootfs.cpio.gz" ] || { echo "ERROR: --hdl-only needs a previous full build; $SRC_DIR/buildroot/output/images/rootfs.cpio.gz is missing." >&2; exit 1; }
else
    echo "=== [5/7] Building rootfs (auto-retries on git-archive hash drift) ==="
    # Upstream's top-level Makefile (which this script otherwise bypasses, to
    # keep Vivado's PATH pollution away from the u-boot/kernel/buildroot steps -
    # see the big comment above) does three things before "make -C buildroot
    # ... all" that our own direct buildroot invocation was skipping: write
    # buildroot/board/pluto/VERSIONS, run "make -C buildroot legal-info", and
    # turn that into buildroot/board/pluto/msd/LICENSE.html via
    # scripts/legal_info_html.sh. Without msd/LICENSE.html, the board's own
    # post-build.sh fails while generating the (immediately-discarded, and not
    # one of our 5 SD-card output files) boot.vfat MSD image, aborting the
    # whole buildroot run before rootfs.cpio.gz is produced.
    echo device-fw "$(cd "$SRC_DIR" && git describe --abbrev=4 --dirty --always --tags)" > "$SRC_DIR/buildroot/board/pluto/VERSIONS"
    for d in hdl buildroot linux u-boot-xlnx; do
        echo "$d $(cd "$SRC_DIR/$d" && git describe --abbrev=4 --dirty --always --tags)" >> "$SRC_DIR/buildroot/board/pluto/VERSIONS"
    done
    buildroot_defconfig
    # legal-info downloads sources, so it can hit the same git-archive hash drift
    # as the main build - run it through the same auto-repair wrapper rather than
    # letting it kill the build before the wrapper is ever reached.
    PATH="$CLEAN_PATH" "$BUILD_ALL_DIR/fix_and_retry_buildroot.sh" "$SRC_DIR" legal-info
    mkdir -p "$SRC_DIR/build"
    (cd "$SRC_DIR" && PATH="$CLEAN_PATH" scripts/legal_info_html.sh "PlutoSDR" "$SRC_DIR/buildroot/board/pluto/VERSIONS")
    cp "$SRC_DIR/build/LICENSE.html" "$SRC_DIR/buildroot/board/pluto/msd/LICENSE.html"

    # Buildroot's bundled host-m4 doesn't build under GCC >= 14's stricter C
    # defaults, so on a very new distro we have to point HOSTCC at an older
    # compiler. Don't hardcode that: Ubuntu 22.04 (the supported host) ships
    # GCC 11, which builds it fine and does not package gcc-13 at all - forcing
    # HOSTCC=gcc-13 there fails with "Unable to locate package gcc-13".
    HOST_CC_ARGS=()
    gcc_major="$(gcc -dumpversion 2>/dev/null | cut -d. -f1)"
    if [ "${gcc_major:-0}" -ge 14 ]; then
        if command -v gcc-13 >/dev/null 2>&1 && command -v g++-13 >/dev/null 2>&1; then
            echo "    default gcc is $gcc_major (too new for host-m4); using gcc-13 for host tools"
            HOST_CC_ARGS=(HOSTCC=gcc-13 HOSTCXX=g++-13)
        else
            echo "ERROR: your default gcc is $gcc_major, but Buildroot's host-m4 needs GCC <= 13." >&2
            echo "       Install gcc-13 and g++-13, or build on a host with an older default gcc." >&2
            exit 1
        fi
    else
        echo "    default gcc is ${gcc_major:-unknown}; using it for host tools"
    fi

    PATH="$CLEAN_PATH" "$BUILD_ALL_DIR/fix_and_retry_buildroot.sh" "$SRC_DIR" \
        "${HOST_CC_ARGS[@]}" \
        BUSYBOX_CONFIG_FILE="$SRC_DIR/buildroot/board/pluto/busybox-1.25.0.config" all
    if [ ! -f "$SRC_DIR/buildroot/output/images/rootfs.cpio.gz" ]; then
        echo "ERROR: buildroot rootfs build failed - see /tmp/buildroot_autoretry_*.log" >&2
        exit 1
    fi

fi

echo "=== [6/7] Generating uEnv.txt from the freshly-built u-boot ==="
mkdir -p "$OUT_DIR"
PATH="$TOOLCHAIN_PATH" CROSS_COMPILE=$CROSS_COMPILE "$SRC_DIR/scripts/get_default_envs.sh" > "$OUT_DIR/uEnv.txt"

echo "=== [7/7] Packaging SD-card files ==="
(
    source "$REPO_ROOT/tools/env-vivado.sh"
    cd "$OUT_DIR"
    cp "$FSBL_ELF" fsbl.elf
    cp "$SRC_DIR/hdl/projects/pluto/pluto.runs/impl_1/system_top.bit" system_top.bit
    cp "$SRC_DIR/u-boot-xlnx/u-boot" u-boot.elf
    cp "$SRC_DIR/linux/arch/arm/boot/uImage" uImage
    cp "$SRC_DIR/linux/arch/arm/boot/dts/zynq-pluto-sdr-fishball.dtb" devicetree.dtb
    mkimage -A arm -T ramdisk -C gzip -d "$SRC_DIR/buildroot/output/images/rootfs.cpio.gz" uramdisk.image.gz

    cp "$BUILD_ALL_DIR/boot.bif" .
    bootgen -image boot.bif -arch zynq -o BOOT.bin -w
    rm -f fsbl.elf system_top.bit u-boot.elf boot.bif  # intermediate, not needed on the SD card
)

echo "=== Sanity-checking output files ==="
for f in BOOT.bin devicetree.dtb uEnv.txt uImage uramdisk.image.gz; do
    path="$OUT_DIR/$f"
    if [ ! -s "$path" ]; then
        echo "ERROR: $path is missing or empty - a step above silently produced nothing usable." >&2
        exit 1
    fi
done
# BOOT.bin (FSBL + bitstream + U-Boot) should always be multiple MB; a
# truncated file here usually means bootgen failed partway without a
# nonzero exit code.
boot_bin_size=$(stat -c %s "$OUT_DIR/BOOT.bin")
if [ "$boot_bin_size" -lt 1000000 ]; then
    echo "ERROR: $OUT_DIR/BOOT.bin is only $boot_bin_size bytes - expected several MB. bootgen likely failed silently." >&2
    exit 1
fi

echo
echo "=== Done. SD-card files are in: $OUT_DIR ==="
ls -la "$OUT_DIR"
echo
echo "Copy all five files (BOOT.bin, devicetree.dtb, uEnv.txt, uImage,"
echo "uramdisk.image.gz) onto the SD card's single FAT32 partition."
