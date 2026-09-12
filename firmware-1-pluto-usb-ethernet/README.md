# Firmware 1 — pluto-fw v0.38 port (USB + Ethernet)

> **Looking for the build/flash workflow (installing Vivado, opening the
> block diagram, adding HDL, building, flashing via SD card or DFU)?**
> That all lives in the [root README](../README.md) now. This page covers
> what's specific to *this* firmware: exactly what upstream source it's
> built from, what was fixed to match the real board, and how closely the
> result has been verified against it.

This is the board's **factory-default firmware** — the one actually shipped
on the SD card, supporting both USB and Ethernet control. It's also the
only firmware target in this repo.

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
- **Confirmed on real hardware** (2026-09-12): a full `build_all.sh` run's
  output, flashed and booted on a real board, initializes the AD9361
  cleanly and reports `fw_version: 95aad-dirty` / `hw_model: FISH Ball
  PlutoSDR Rev.A (Z7020-AD9361)` over both the serial console login
  banner and `iio_info` — see the root README's
  [verification step](../README.md#7-verify-your-build-is-actually-running).

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
    (Note: this board's device tree unconditionally sets
    `adi,2rx-2tx-mode-enable`, so the `mode` env var's 1r1t/2r2t switch —
    which only takes effect on ADI's official Rev.C model string — is a
    no-op here; **2r2t is always active** regardless of this setting.)
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

## Build system internals

`scripts/build_all.sh` deliberately does **not** just call upstream's own
top-level `Makefile` — it reimplements its steps directly so that Vivado's
own `settings64.sh` (sourced for the HDL/FSBL/packaging steps only) never
leaks its bundled cross-toolchain `PATH` entries into the u-boot/kernel/
buildroot steps, which broke the kernel build the first time this was
tried (see the root README's
[Troubleshooting](../README.md#troubleshooting) section). It does,
however, still replicate one upstream `Makefile` step exactly: writing
`buildroot/board/pluto/VERSIONS` and running Buildroot's `legal-info` to
generate `msd/LICENSE.html`, which `post-build.sh` needs to finish
building the rootfs (that file isn't one of the 5 SD-card outputs, but its
absence aborts the whole Buildroot run before `rootfs.cpio.gz` is ever
produced).

See `scripts/build_all.sh` itself for the exact, current, working sequence
of commands — it's short and directly readable.
