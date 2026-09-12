#!/bin/bash
# Full build: HDL -> bitstream -> FSBL (with a build earmark) -> BOOT.BIN -> SD-card file set.
# Run after making your custom HDL changes in ../hdl/projects/fmcomms2/zc706/
#
# Usage: ./build_all.sh
# Output lands in ../output/ (the four files to copy onto the SD card).

set -euo pipefail
BUILD_ALL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE="$(dirname "$BUILD_ALL_DIR")"
REPO_ROOT="$(dirname "$BASE")"
HDL_PROJ="$BASE/hdl/projects/fmcomms2/zc706"
FSBL_DIR="$BASE/hdl/fsbl"
FW_DIR="$BASE/firmware"
OUT_DIR="$BASE/output"

echo "=== [1/5] Sourcing Vivado 2022.2 environment ==="
source "$REPO_ROOT/tools/env-vivado.sh"

echo "=== [2/5] Building HDL: synth -> impl -> bitstream -> hardware platform ==="
cd "$HDL_PROJ"
vivado -mode batch -source build.tcl -journal build.jou -log build.log
echo "    Timing summary:"
grep -A3 "Design Timing Summary" timing.rpt | tail -2 || true
echo "    NOTE: check timing.rpt for any failing paths before flashing."

echo "=== [3/5] Scaffolding FSBL from the fresh hardware platform ==="
cd "$FSBL_DIR"
# .metadata is the Eclipse/Vitis workspace registry - it remembers the "fsbl" app
# name even after its own directories are deleted, so it must be wiped too or
# `app create` fails with "A project with given name already exists".
rm -rf system_top fsbl fsbl_system .metadata
xsct gen_fsbl_create.tcl

HOOKS_C="$FSBL_DIR/fsbl/src/fsbl_hooks.c"
if [ ! -f "$HOOKS_C" ]; then
    echo "ERROR: FSBL scaffold failed, $HOOKS_C not found. Check $FSBL_DIR/gen_fsbl_create.log"
    exit 1
fi

echo "=== [4/5] Injecting a build earmark and compiling the FSBL ==="
BUILD_TAG="FISHBALL7020-$(date -u +%Y%m%d-%H%M%S)"
echo "    Build tag: $BUILD_TAG"
python3 "$BUILD_ALL_DIR/patch_fsbl_earmark.py" "$HOOKS_C" "$BUILD_TAG"

xsct gen_fsbl_build.tcl

# xsct's own "Build Finished" completion message is not a reliable signal that
# fsbl.elf on disk actually reflects the current source - observed twice in
# testing: the build tag was missing from fsbl.elf immediately after xsct
# returned (even after a 10s poll), yet was present moments later with no
# further action taken. Root cause not fully isolated (suspected async
# Eclipse/CDT job scheduling inside xsct), so route around it entirely: invoke
# the real underlying `make` xsct itself generated, synchronously, as the
# actual final build step. This has a real, deterministic exit code and is a
# fast no-op if xsct's build was already current.
make -C "$FSBL_DIR/fsbl/Debug"

# NOTE: the actual freshly-compiled output lives at fsbl/Debug/fsbl.elf.
# system_top/zynq_fsbl/fsbl.elf (and the copy under system_top/export/.../boot/)
# are stale scaffold-time copies that `app build` does NOT refresh - verified
# by checksum/strings diff in this session. Using them silently packages an
# old FSBL binary. Always take the ELF from fsbl/Debug/.
FSBL_ELF="$FSBL_DIR/fsbl/Debug/fsbl.elf"
if [ ! -f "$FSBL_ELF" ]; then
    echo "ERROR: FSBL build failed, $FSBL_ELF not found. Check $FSBL_DIR/gen_fsbl_build.log"
    exit 1
fi
# Root cause of an earlier false failure here, fully reproduced and fixed:
# `if strings "$FSBL_ELF" | grep -qF "$TAG"; then` is broken under `set -o
# pipefail` (active via this script's `set -euo pipefail`) even when grep
# genuinely finds the tag. `grep -q` exits the instant it finds a match,
# which can SIGPIPE the still-writing `strings` process; pipefail then
# reports that SIGPIPE as the pipeline's exit status instead of grep's own
# success. Fix: capture strings' output fully into a variable first (no
# concurrent reader/writer, so no SIGPIPE is possible), then grep that.
FSBL_STRINGS="$(strings "$FSBL_ELF")"
if ! grep -qF "$BUILD_TAG" <<< "$FSBL_STRINGS"; then
    echo "ERROR: built FSBL does not contain the expected build tag - refusing to package a stale/wrong binary."
    exit 1
fi

echo "=== [5/5] Packaging BOOT.BIN ==="
mkdir -p "$OUT_DIR"
cd "$OUT_DIR"
cp "$FSBL_ELF" fsbl.elf
cp "$HDL_PROJ/fmcomms2_zc706.runs/impl_1/system_top.bit" system_top.bit

# u-boot and the device tree are unchanged from the working Firmware 2 package.
# bootgen strips the ELF envelope when it stores a partition, so the raw dump
# in firmware/uboot-raw.bin is bare machine code, not a valid ELF - wrap it
# back into a minimal ELF at its original load/exec address (0x04000000)
# before bootgen will accept it again.
arm-none-eabi-objcopy -I binary -O elf32-littlearm -B arm "$FW_DIR/uboot-raw.bin" uboot_blob.o
arm-none-eabi-ld -T "$FW_DIR/uboot_wrap.ld" uboot_blob.o -o u-boot.elf
rm -f uboot_blob.o

cp "$FW_DIR/system.dtb" system.dtb
cp "$FW_DIR/boot.bif" boot.bif

bootgen -image boot.bif -arch zynq -o BOOT.BIN -w

# Assemble the final SD-card set alongside BOOT.BIN
cp "$FW_DIR/boot.scr" .
cp "$FW_DIR/image.ub" .
cp "$FW_DIR/rootfs.tar.gz" .
rm -f fsbl.elf system_top.bit u-boot.elf system.dtb boot.bif  # intermediate, not needed on the SD card

echo "$BUILD_TAG" > BUILD_TAG.txt

echo
echo "=== Done. SD-card files are in: $OUT_DIR ==="
ls -la "$OUT_DIR"
echo
echo "Copy BOOT.BIN, boot.scr, image.ub to the SD card's FAT32 partition."
echo "Extract rootfs.tar.gz onto the SD card's EXT4 partition."
echo
echo "To confirm this exact build landed on the device: connect a serial console"
echo "at 115200 baud (via the board's JTAG-USB port) and power-cycle. Right after"
echo "the standard Xilinx FSBL banner, before u-boot starts, you should see:"
echo
echo "    *** BUILD EARMARK: $BUILD_TAG ***"
echo
echo "(also saved to $OUT_DIR/BUILD_TAG.txt for later reference)"
