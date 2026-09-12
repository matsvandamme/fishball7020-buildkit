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
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FW1_DIR="$(dirname "$SCRIPT_DIR")"
REPO_ROOT="$(dirname "$FW1_DIR")"
SRC_DIR="$FW1_DIR/src"
OUT_DIR="$FW1_DIR/output"

if [ ! -d "$SRC_DIR" ]; then
    echo "ERROR: $SRC_DIR not found. Run ./setup.sh first." >&2
    exit 1
fi

echo "=== [1/7] Sourcing Vivado 2022.2 environment ==="
source "$REPO_ROOT/tools/env-vivado.sh"
export CROSS_COMPILE=arm-linux-gnueabihf-
export PATH="$SRC_DIR/buildroot/output/host/bin:$SRC_DIR/buildroot/output/host/sbin:/tools/Xilinx/Vitis/2022.2/bin:$PATH"

echo "=== [2/7] Building HDL: synth -> impl -> bitstream -> hardware platform ==="
cd "$SRC_DIR/hdl/projects/pluto"
cp "$SCRIPT_DIR/build_hdl.tcl" .
vivado -mode batch -source build_hdl.tcl -journal build_hdl.jou -log build_hdl.log
echo "    Timing summary:"; grep -A3 "Design Timing Summary" timing.rpt | tail -2 || true

echo "=== [3/7] Building FSBL ==="
mkdir -p "$SRC_DIR/hdl/fsbl"
cd "$SRC_DIR/hdl/fsbl"
cp "$SCRIPT_DIR/gen_fsbl_create.tcl" "$SCRIPT_DIR/gen_fsbl_build.tcl" .
rm -rf system_top fsbl fsbl_system .metadata
xsct gen_fsbl_create.tcl
xsct gen_fsbl_build.tcl
# The freshly-compiled output is at fsbl/Debug/fsbl.elf - the copy under
# system_top/zynq_fsbl/ is a stale scaffold-time snapshot xsct's own "app
# build" does not refresh (confirmed by checksum diff during development).
make -C fsbl/Debug
FSBL_ELF="$SRC_DIR/hdl/fsbl/fsbl/Debug/fsbl.elf"
[ -f "$FSBL_ELF" ] || { echo "ERROR: FSBL build failed"; exit 1; }

echo "=== [4/7] Building u-boot ==="
cd "$SRC_DIR"
make -C u-boot-xlnx ARCH=arm CROSS_COMPILE=$CROSS_COMPILE zynq_pluto_defconfig
make -C u-boot-xlnx ARCH=arm CROSS_COMPILE=$CROSS_COMPILE UBOOTVERSION="PlutoSDR"

echo "=== [5/7] Building kernel: uImage + fishball device tree ==="
make -C linux ARCH=arm CROSS_COMPILE=$CROSS_COMPILE zynq_pluto_defconfig
make -C linux -j "$(nproc)" ARCH=arm CROSS_COMPILE=$CROSS_COMPILE uImage UIMAGE_LOADADDR=0x8000
DTC_FLAGS=-@ make -C linux -j "$(nproc)" ARCH=arm CROSS_COMPILE=$CROSS_COMPILE zynq-pluto-sdr-fishball.dtb

echo "=== [6/7] Building rootfs (auto-retries on git-archive hash drift) ==="
"$SCRIPT_DIR/fix_and_retry_buildroot.sh" "$SRC_DIR" \
    HOSTCC=gcc-13 HOSTCXX=g++-13 \
    BUSYBOX_CONFIG_FILE="$SRC_DIR/buildroot/board/pluto/busybox-1.25.0.config" all
if [ ! -f "$SRC_DIR/buildroot/output/images/rootfs.cpio.gz" ]; then
    echo "ERROR: buildroot rootfs build failed - see /tmp/buildroot_autoretry_*.log" >&2
    exit 1
fi

echo "=== [7/7] Packaging SD-card files ==="
mkdir -p "$OUT_DIR"
cd "$OUT_DIR"
cp "$FSBL_ELF" fsbl.elf
cp "$SRC_DIR/hdl/projects/pluto/pluto.runs/impl_1/system_top.bit" system_top.bit
cp "$SRC_DIR/u-boot-xlnx/u-boot" u-boot.elf
cp "$SRC_DIR/linux/arch/arm/boot/uImage" uImage
cp "$SRC_DIR/linux/arch/arm/boot/dts/zynq-pluto-sdr-fishball.dtb" devicetree.dtb
CROSS_COMPILE=$CROSS_COMPILE "$SRC_DIR/scripts/get_default_envs.sh" > uEnv.txt
mkimage -A arm -T ramdisk -C gzip -d "$SRC_DIR/buildroot/output/images/rootfs.cpio.gz" uramdisk.image.gz

cp "$SCRIPT_DIR/boot.bif" .
bootgen -image boot.bif -arch zynq -o BOOT.bin -w
rm -f fsbl.elf system_top.bit u-boot.elf boot.bif  # intermediate, not needed on the SD card

echo
echo "=== Done. SD-card files are in: $OUT_DIR ==="
ls -la "$OUT_DIR"
echo
echo "Copy all five files (BOOT.bin, devicetree.dtb, uEnv.txt, uImage,"
echo "uramdisk.image.gz) onto the SD card's single FAT32 partition."
