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
    PATH="$CLEAN_PATH" make -C "$SRC_DIR/buildroot" ARCH=arm zynq_pluto_defconfig
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

echo "=== [3/7] Building u-boot ==="
PATH="$TOOLCHAIN_PATH" make -C "$SRC_DIR/u-boot-xlnx" ARCH=arm CROSS_COMPILE=$CROSS_COMPILE zynq_pluto_defconfig
PATH="$TOOLCHAIN_PATH" make -C "$SRC_DIR/u-boot-xlnx" ARCH=arm CROSS_COMPILE=$CROSS_COMPILE UBOOTVERSION="PlutoSDR"

echo "=== [4/7] Building kernel: uImage + fishball device tree ==="
PATH="$TOOLCHAIN_PATH" make -C "$SRC_DIR/linux" ARCH=arm CROSS_COMPILE=$CROSS_COMPILE zynq_pluto_defconfig
PATH="$TOOLCHAIN_PATH" make -C "$SRC_DIR/linux" -j "$(nproc)" ARCH=arm CROSS_COMPILE=$CROSS_COMPILE uImage UIMAGE_LOADADDR=0x8000
PATH="$TOOLCHAIN_PATH" DTC_FLAGS=-@ make -C "$SRC_DIR/linux" -j "$(nproc)" ARCH=arm CROSS_COMPILE=$CROSS_COMPILE zynq-pluto-sdr-fishball.dtb

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
PATH="$CLEAN_PATH" make -C "$SRC_DIR/buildroot" ARCH=arm zynq_pluto_defconfig
PATH="$CLEAN_PATH" make -C "$SRC_DIR/buildroot" legal-info
mkdir -p "$SRC_DIR/build"
(cd "$SRC_DIR" && PATH="$CLEAN_PATH" scripts/legal_info_html.sh "PlutoSDR" "$SRC_DIR/buildroot/board/pluto/VERSIONS")
cp "$SRC_DIR/build/LICENSE.html" "$SRC_DIR/buildroot/board/pluto/msd/LICENSE.html"

PATH="$CLEAN_PATH" "$BUILD_ALL_DIR/fix_and_retry_buildroot.sh" "$SRC_DIR" \
    HOSTCC=gcc-13 HOSTCXX=g++-13 \
    BUSYBOX_CONFIG_FILE="$SRC_DIR/buildroot/board/pluto/busybox-1.25.0.config" all
if [ ! -f "$SRC_DIR/buildroot/output/images/rootfs.cpio.gz" ]; then
    echo "ERROR: buildroot rootfs build failed - see /tmp/buildroot_autoretry_*.log" >&2
    exit 1
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
