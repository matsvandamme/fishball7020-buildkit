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
layout, not a separate FAT+EXT4 split).

---

## Opening the Vivado project to change the PL HDL

After `./scripts/setup.sh` has run once, the actual Vivado project doesn't
exist yet — the upstream repo only ships the `.tcl` scripts that *create*
it (`src/hdl/projects/pluto/system_project.tcl`, `system_bd.tcl`,
`system_top.v`, `system_constr.xdc`), not a pre-built `.xpr`.
`build_all.sh` creates it the first time it runs. To open it in the GUI
afterwards:

```bash
source ../tools/env-vivado.sh
cd src/hdl/projects/pluto
vivado pluto.xpr
```

In the **Sources** panel: `Design Sources → system_top → system_i` and
click **Open Block Design** to see the graphical canvas.

### The AD9361 signal chain in this project (from `system_bd.tcl`)

This project is *not* the plain "raw samples straight to DMA" architecture
you might expect — Pluto's own reference design already threads channel 0
through Analog Devices' programmable FIR decimator/interpolator, while
channel 1 bypasses filtering entirely:

```
                            AD9361 (physical LVDS pins)
                                    │
                             ┌──────▼───────┐
                             │  axi_ad9361   │
                             └──┬────────▲───┘
      RX ch.0: adc_data_i0/q0 ──┤         ├── TX ch.0: dac_data_i0/q0
      RX ch.1: adc_data_i1/q1 ──┤         ├── TX ch.1: dac_data_i1/q1
                                │         │
                    ┌───────────▼──┐   ┌──┴────────────┐
        ch.0 only:  │rx_fir_       │   │tx_fir_        │  :ch.0 only
     (decimation,   │decimator     │   │interpolator   │  (interpolation,
      8x, 2x taps)  └──────┬───────┘   └───────▲───────┘   2x/8x taps)
                           │                     │
      ch.1 connects  ┌─────▼──────┐       ┌──────┴─────┐  ch.1 connects
      directly, no   │   cpack     │       │  tx_upack  │  directly, no
      filter ────────►(util_cpack2)│       │(util_upack2)◄──── filter
                     └─────┬──────┘       └──────▲─────┘
                           │                       │
                    ┌──────▼──────┐         ┌──────┴──────┐
                    │  adc_dma     │         │  dac_dma     │
                    │ (axi_dmac)   │         │ (axi_dmac)   │
                    └──────────────┘         └──────────────┘
```

**Where to insert custom HDL, depending on what you want:**

- **Channel 1 (`adc_data_i1`/`adc_data_q1` on RX, `dac_data_i1`/`dac_data_q1`
  on TX) has no filter in the path at all** — it's wired directly between
  `axi_ad9361` and `cpack`/`tx_upack`. This is the cleanest insertion point
  if you don't want to touch the existing FIR IP: break the direct
  connection and insert your own block (`ad_connect axi_ad9361/adc_data_i1
  <your_block>/data_in`, etc., mirroring the existing `ad_connect` calls in
  `system_bd.tcl`), then reconnect to `cpack`'s `enable_2`/`fifo_wr_data_2`
  (and `_3` for Q) the same way.
- **Channel 0** already routes through `rx_fir_decimator`/
  `tx_fir_interpolator` (ADI's `util_fir_int` IP, instantiated via
  `ad_add_decimation_filter`/`ad_add_interpolation_filter` in
  `system_bd.tcl`, coefficients loaded from
  `library/util_fir_int/coefile_int.coe`). You can insert before these
  blocks (raw, full-rate samples straight from `axi_ad9361`) or after
  (post-filter samples, right before `cpack`/after `tx_upack`) — or replace
  the filter's `.coe` coefficient file to change its response without
  touching any wiring at all.
- Both channel-0 signal groups run on `axi_ad9361/l_clk` (the AD9361
  interface clock) — match that if you're inserting logic there.

---

## Building everything into the final SD-card files

```bash
./scripts/setup.sh      # once: clone upstream, apply patches/
./scripts/build_all.sh  # every time: full rebuild -> output/
```

`build_all.sh` runs, in order (see the script for the exact commands, or
run them by hand if you want to iterate on just one stage):

1. **HDL → bitstream → hardware platform.** Opens/creates
   `src/hdl/projects/pluto/pluto.xpr`, synthesizes and implements it, then
   exports `system_top.xsa` (the hardware platform, including the
   bitstream, that the FSBL needs).
2. **FSBL.** Scaffolds a fresh Vitis FSBL app from that hardware platform
   and compiles it. (`.metadata`/`system_top`/`fsbl_system` are wiped first
   — Vitis's own workspace registry otherwise refuses to re-scaffold an app
   with the same name.)
3. **U-Boot**, built from `zynq_pluto_defconfig` (already patched to match
   the real board's boot defaults).
4. **Linux kernel**: `uImage`, plus `zynq-pluto-sdr-fishball.dtb` — the
   device tree added by `patches/0002-add-fishball-devicetree.patch`, the
   one verified byte-for-byte identical to the real board's.
5. **Root filesystem**, via Buildroot — `fix_and_retry_buildroot.sh` wraps
   this step and automatically repairs the git-archive hash-drift issue
   described above if it recurs for some other package later.
6. **Packaging**: copies the FSBL, bitstream, and U-Boot ELF into
   `output/`, generates `uEnv.txt` from the freshly-built U-Boot's own
   compiled-in defaults, wraps the rootfs as `uramdisk.image.gz`, and runs
   `bootgen` to produce the final `BOOT.bin`.

Every step after the one you changed re-runs from that point — editing
`src/hdl/projects/pluto/system_top.v` and re-running `build_all.sh` re-uses
the existing Vivado project (incremental synthesis) rather than recreating
it from scratch, then proceeds through FSBL → packaging automatically. If
you only touched the rootfs/kernel/u-boot, the earlier HDL/FSBL steps still
re-run (fast, since Vivado has nothing new to do) — there's no
per-stage skip logic, so a full `build_all.sh` run always regenerates all
five output files.

### Rebuilding after your own changes

- **HDL**: edit `src/hdl/projects/pluto/system_top.v`/`system_constr.xdc`/
  `system_bd.tcl`, then re-run `build_all.sh`.
- **Rootfs/kernel/u-boot**: edit directly under `src/` for quick iteration,
  or turn your change into a new patch under `patches/` and re-run
  `setup.sh` against a fresh `src/` to keep a clean, reproducible change
  history (recommended once a change is actually working).
