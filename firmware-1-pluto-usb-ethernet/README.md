# Firmware 1 — pluto-fw v0.38 port (USB + Ethernet)

This is the board's **factory-default firmware** — the one actually shipped
on the SD card, supporting both USB and Ethernet control.

Upstream source: [`Xiaozhang-code-cloud/Fish-Wan-plutosdr-fw-7020-SDR`](https://github.com/Xiaozhang-code-cloud/Fish-Wan-plutosdr-fw-7020-SDR)
(a monolithic fork of Analog Devices' `plutosdr-fw`, retargeted from the
stock ADALM-PLUTO's XC7Z010-CLG225 to this board's XC7Z020-CLG400, with the
matching AD9361 pin constraints for this board's actual PCB layout).

## Verified against the real board

Every fix in `patches/` was derived by building this exact source and
diffing the result, file-by-file, against a genuine working `SD Card
Firmware/` dump pulled from a real unit:

- **`devicetree.dtb` builds byte-for-byte identical** to the real one.
- **`uEnv.txt`** is content-identical (every variable and value matches);
  the only remaining difference is the *order* U-Boot's internal
  environment hash table dumps variables in, which has no effect on boot
  behavior (variables are looked up by name, never position).
- **The root filesystem's file list is identical** to the real one.
- **`uImage`** builds with an identical kernel `.config` and an identical
  build banner, but is not byte-identical: the upstream repo's git history
  has been squashed to a single commit dated after this board's firmware was
  actually built, so a small number of kernel source lines have drifted
  since — not recoverable from the public repo alone.
- **`BOOT.bin`** inherits the above plus normal Vivado place-and-route
  non-determinism in the bitstream.
- A handful of remaining rootfs file-size differences (a random password
  salt, a build-path-dependent GDB helper script, and a version-string
  format that depends on git submodules vs. this repo's flattened layout)
  are inherent/cosmetic, not bugs.

## What's in `patches/`

- **`0001-fishball7020-fixes.patch`** — six real fixes:
  - `buildroot/board/pluto/S23udc`: two hardcoded debug leftovers in the
    upstream repo — `fw_version=v0.38` and a literal fake serial number —
    restored to the dynamic runtime lookups the real firmware actually uses.
  - `buildroot/configs/zynq_pluto_defconfig`: enables the `iperf` package
    (present on the real board, missing from a stock build) and sets
    `CONFIG_BOOTDELAY=3` to match.
  - `u-boot-xlnx/configs/zynq_pluto_defconfig` /
    `u-boot-xlnx/include/configs/zynq-common.h`: default env values
    (`maxcpus=2`, `mode=1r1t`), a GPIO pin number used for board-revision
    detection (`10`→`14`), and a hex-formatting inconsistency
    (`0x0E00000`→`0xE00000`) — all matched against the real board's dump.
  - `buildroot/package/libiio/libiio.hash` and
    `buildroot/package/ad936x_ref_cal/ad936x_ref_cal.hash`: buildroot's own
    git-archive re-packaging of these exact pinned upstream commits
    produces a different tar.gz byte stream on modern git/tar versions than
    whatever originally computed the recorded hash (the commit hash itself
    is the real content guarantee) — `scripts/fix_and_retry_buildroot.sh`
    handles this automatically for *any* future package hit by the same
    drift, not just these two.
- **`0002-add-fishball-devicetree.patch`** — adds
  `linux/arch/arm/boot/dts/zynq-pluto-sdr-fishball.dts`. None of the three
  stock device-tree variants in the upstream repo (base/revb/revc) matched
  the real board's device tree exactly (each had at least one different
  node), so this file is the real board's own `devicetree.dtb`, decompiled
  back to source with `dtc` and confirmed to recompile byte-for-byte
  identical to the original through the actual kernel build path.

## Usage

```bash
./scripts/setup.sh      # clone upstream + apply patches (once)
./scripts/build_all.sh  # build everything, output/ gets the 5 SD-card files
```

Requires: Vivado 2022.2 (see `../tools/env-vivado.sh` for the Ubuntu 26.04+
compat-library workaround), `xsct`/Vitis 2022.2, `bootgen`, `dtc`,
`mkimage`, `bison`, `flex`, and `gcc-13`/`g++-13` alongside your default gcc
(needed for one legacy buildroot host tool, `host-m4`, which doesn't build
under GCC ≥14's stricter C defaults).

Output (`output/`): `BOOT.bin`, `devicetree.dtb`, `uEnv.txt`, `uImage`,
`uramdisk.image.gz` — copy all five onto the SD card's single FAT32
partition (this firmware uses Pluto's classic single-partition ramdisk-root
layout, not the two-partition FAT+EXT4 layout used by Firmware 2).

## Rebuilding after your own changes

- Editing HDL: modify `src/hdl/projects/pluto/*.v`/`*.xdc`, then re-run
  `build_all.sh` (it detects the existing project and re-synthesizes rather
  than recreating from scratch).
- Editing the rootfs/kernel/u-boot: edit under `src/`, or add a new patch to
  `patches/` and re-run `setup.sh` against a fresh `src/` for a clean,
  reproducible change history.
