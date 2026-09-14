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
- **Confirmed on real hardware** (2026-09-14), current `patches/`: the TX
  safeguard holds across the full cycle — attenuated at boot, the user's gain
  preserved while a stream runs, attenuated *and* synthesiser powered down
  after it stops, and again on a second stream. Over a 50 dB attenuated
  TX→RX loopback, commanded and applied attenuation matched to 0.01 dB at
  every point including 0 dB, so full output is unaffected. The persistent
  serial survives a reboot unchanged while the gadget MAC and interface name
  stay exactly as before, and SDRangel opens and streams from the board on
  both `usb:` and `ip:`. The optional channelizer's filter response was
  measured through the same loopback (flat to the 100 kHz edge, into the
  noise floor by 175 kHz) before it was made opt-in.

## What's in `patches/`

- **`0001-fishball7020-fixes.patch`** — six real fixes:
  - `buildroot/board/pluto/S23udc`: two hardcoded debug leftovers in the
    upstream repo — `fw_version=v0.38` and a literal fake serial number —
    restored to the dynamic runtime lookups the real firmware actually uses.

    **With one addition, because the dynamic lookup finds nothing on this
    board.** It greps `dmesg` for `SPI-NOR-UniqueID`, which the ADI kernel
    prints only for Micron flash; this board carries a Winbond W25Q128, so
    `hw_serial` came out empty. Anything that identifies a Pluto by serial
    then cannot open it — SDRangel lists the board as `PlutoSDR0 TBD` and
    fails with `open serial TBD failed`. The SoC exposes no unique hardware
    id at all (no device-tree `serial-number`, no DNA, no efuse), so the
    script now mints 16 random bytes once and keeps them in
    `/mnt/jffs2/hw_serial`, the board's persistent store.

    The USB gadget MACs are `sha1($serial)`, and a changed MAC renames the
    host's network interface (`enx<mac>`) and breaks any static-IP setup
    bound to it. So the MACs are deliberately still seeded from the
    *original* empty value: interface names and addresses are bit-identical
    to before, and only `hw_serial` and the USB descriptor string change.
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
- **`optional/0003-wbfm-channelizer.patch`** — **not applied by default.**
  Unlike the others this *changes* what the radio does rather than fixing it,
  so it lives in `patches/optional/` and `setup.sh` leaves it alone. Apply it
  by hand with `(cd src && git apply ../patches/optional/0003-wbfm-channelizer.patch)`.
  A worked example of putting
  custom DSP into the AD9361 chain. It adds `ad_fs4_ddc.v` (an Fs/4 frequency
  shifter) ahead of `rx_fir_decimator` and repoints that filter at
  narrow-band FM channel coefficients, turning RX channel 0 into a
  single-station FM channelizer. It touches no device tree, kernel or
  bootloader, so every provenance claim above still holds; drop the patch to
  get the stock wideband datapath back. See
  [docs/wbfm-channelizer.md](../docs/wbfm-channelizer.md).

- **`0004-mute-tx-when-no-dma-stream.patch`** — mutes the AD9361's transmit
  chain whenever no TX DMA buffer is streaming.

  The chip keeps its transmit chain biased for as long as the ENSM is in FDD,
  which it is from power-on, whether or not anything feeds the DAC. When a TX
  buffer is torn down, `cf_axi_dds_buffer_stream.c` only reverts the baseband
  source to the (silent) DDS: the mixer and output stage stay powered, keep
  emitting LO leakage and keep dissipating power. Measured on a real board at
  boot: ENSM `fdd`, TX LO running, and just 10 dB of attenuation.

  The fix hooks the buffer lifecycle the driver already has — `preenable`
  unmutes, `postdisable` mutes — and calls `ad9361_tx_mute()`, ADI's own
  exported helper, which was present in the tree but called from nowhere. It
  caches both channels' attenuation and restores it on unmute, so a chosen TX
  gain survives a stream. A small exported wrapper, `ad9361_tx_lo_powerdown()`,
  also stops the TX synthesiser on mute and restarts it on unmute — attenuation
  is what removes output power, but without this a chain that some application
  powered up would idle with its oscillator running after that application
  closed. Order is kept both ways: signal down before oscillator, oscillator up
  before signal. The IIO core runs `postdisable` on buffer teardown
  even when the application crashed or was killed, which is what makes this a
  guarantee rather than best effort.

  Two details worth knowing. `ad9361_tx_mute()` restores a *cached*
  attenuation, and that cache is only trustworthy once a real mute has filled
  it — so the driver never unmutes something it did not mute (`tx_muted`), and
  it deliberately does **not** mute at probe: at that point the phy has not yet
  applied `adi,tx-attenuation-mdB`, the cache would capture the chip's reset
  value of 89.75 dB, and every later unmute would restore it, leaving the
  transmitter permanently silent (measured: a running stream sat at −89.75 dB
  instead of the requested −20). Quieting the board before the first stream is
  therefore `S21misc`'s job, attenuation only. And the phy is reached through the
  DDS node's existing `clocks` phandle, so **no device tree change is needed**
  and `devicetree.dtb` stays byte-identical.

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
