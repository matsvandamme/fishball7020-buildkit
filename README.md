> **This is not the official Analog Devices / OpenSourceSDRLab repository.**
> The Fishball7020 is an ADALM-PLUTO-derivative board that ships with no
> published, editable firmware source of its own — this repository is an
> independent, reverse-engineered reconstruction of that firmware, built and
> verified to be as close to bit-perfect as is possible from public sources.
> See [How this repo came to exist](#how-this-repo-came-to-exist).

# Fishball7020 FPGA Devkit

<p align="center">
  <img src="https://img.shields.io/badge/board-Zynq%20XC7Z020%20%2B%20AD9361-blue" alt="Board: Zynq XC7Z020 + AD9361">
  <img src="https://img.shields.io/badge/toolchain-Vivado%2FVitis%202022.2-orange" alt="Toolchain: Vivado/Vitis 2022.2">
  <img src="https://img.shields.io/badge/host%20OS-Ubuntu%2022.04%20LTS-e95420" alt="Host OS: Ubuntu 22.04 LTS">
  <img src="https://img.shields.io/badge/license-MIT%20%2B%20GPL%20(mixed)-lightgrey" alt="License: MIT + GPL (mixed)">
  <a href="../../actions/workflows/verify-patches.yml"><img src="https://github.com/matsvandamme/fishball7020-fpga-devkit/actions/workflows/verify-patches.yml/badge.svg" alt="Verify patches CI status"></a>
</p>

<p align="center"><img src="docs/img/board.jpg" alt="Fishball7020 / PlutoSky SDR board — Zynq XC7Z020 with AD9361, 4x SMA connectors, Ethernet and USB" width="480"></p>

Build your own custom FPGA/HDL firmware for the **"7020-SDR"** — a
Zynq XC7Z020-CLG400 + AD9361 software-defined radio board with dual TX/RX
(hence "Fishball7020"), also distributed as **"PlutoSky"** by
OpenSourceSDRLab.

> **This repo targets one exact board:** the one sold on AliExpress as
> [**"7020-SDR" (XC7Z020 + AD9361, dual TX/RX)**](https://nl.aliexpress.com/item/1005012055627197.html).
> Other Zynq/AD936x SDR boards — including the original ADALM-PLUTO
> (XC7Z010) — use different pin constraints and won't work with the HDL
> project or device tree built here without changes.

This repo takes you from a stock, unmodified board all the way to **your own
FPGA logic running inside it**: install Vivado, open the real block design,
add your HDL next to the AD9361 datapath, rebuild every layer of the
firmware (bitstream → FSBL → U-Boot → kernel → root filesystem), and flash
it back onto the board — via SD card or over USB (DFU), no disassembly
required either way.

| | |
|---|---|
| **Board** | Zynq-7020 (XC7Z020-CLG400) + AD9361, 2×2 MIMO RF front end |
| **Toolchain** | Xilinx Vivado/Vitis **2022.2** (free WebPACK license — no purchase needed) |
| **Host OS** | **Ubuntu 22.04 LTS** — the version Vivado/Vitis 2022.2 officially supports |
| **Firmware base** | Linux 5.15, U-Boot, Buildroot — a Zynq-7020 port of ADI's `plutosdr-fw` |
| **Verified against real hardware** | `devicetree.dtb` byte-identical; kernel, bootloader, rootfs content-identical — see [Provenance](#how-this-repo-came-to-exist) |

## Table of contents

- [Before you start: back up your stock firmware](#before-you-start-back-up-your-stock-firmware)
- [Prerequisites](#prerequisites)
- [Repository layout](#repository-layout)
- [1. Install Vivado/Vitis 2022.2](#1-install-vivadovitis-2022-2)
- [2. Get the firmware source](#2-get-the-firmware-source)
- [3. Open the block diagram](#3-open-the-block-diagram)
- [4. Add your own HDL](#4-add-your-own-hdl)
- [5. Build the firmware](#5-build-the-firmware)
- [6. Flash the board](#6-flash-the-board)
  - [Option A — SD card](#option-a--sd-card-always-works)
  - [Option B — DFU over USB](#option-b--dfu-over-usb-no-disassembly)
- [7. Verify your build is actually running](#7-verify-your-build-is-actually-running)
- [Troubleshooting](#troubleshooting)
- [How this repo came to exist](#how-this-repo-came-to-exist)
- [Vendor resources](#vendor-resources)
- [License](#license)

## Before you start: back up your stock firmware

This devkit replaces the FPGA bitstream, bootloader, kernel, and root
filesystem on your board — a bad build (or a bad flash) can leave it
unable to boot. Before you touch anything:

1. **Image the SD card your board actually shipped with**, file-for-file,
   onto your computer (just copy the 5 files off the FAT32 partition —
   `BOOT.bin`, `devicetree.dtb`, `uEnv.txt`, `uImage`, `uramdisk.image.gz`
   — to a folder you'll keep). That's your known-good fallback: if a
   custom build doesn't boot, re-copying these 5 original files back onto
   the card restores exactly the factory state.
2. If you only have one SD card, **buy a second one** before
   experimenting — microSD cards are cheap, and it means you're never in
   a position where your only fallback and your only test card are the
   same physical object.
3. **DFU (see step 6B) is not a rescue path.** If a bad `BOOT.bin` won't
   boot, U-Boot never starts DFU mode either, so pushing new files over
   USB isn't possible — an SD card swap back to the stock files (or a
   known-good build) is the only way back at that point.

## Prerequisites

**Hardware:**
- A Fishball7020 / PlutoSky board, a micro-USB cable, and a microSD card
  (any size — the image is small) with a USB card reader, **or** just the
  USB cable if you'll flash via DFU.

**Software** (Ubuntu 22.04 LTS; install before step 1):

```bash
sudo apt update
sudo apt install -y git build-essential bison flex libssl-dev \
    device-tree-compiler u-boot-tools dfu-util screen \
    gcc-13 g++-13 python3
```

- `gcc-13`/`g++-13` alongside your system's default GCC — one legacy
  Buildroot host tool (`host-m4`) doesn't build under GCC ≥14's stricter C
  defaults.
- `device-tree-compiler` (`dtc`) and `u-boot-tools` (`mkimage`) are used to
  build the device tree and the ramdisk image.
- `dfu-util` and `screen` are only needed if you'll flash/debug over USB
  (steps 6B/7) rather than by copying files to an SD card.

## Repository layout

```
fishball7020-sdr-firmware/
├── README.md                            ← you are here: the full build/flash workflow
├── LICENSE                              multiple licenses apply — see below
│
├── tools/
│   ├── env-vivado.sh                    ← source this before any vivado/xsct/bootgen command
│   └── legacy-libs/libs/                vendored libtinfo5/libncurses5/libssl1.1 (see below)
│
└── firmware/       the only firmware target — factory-default USB+Ethernet build
    ├── README.md                       deep technical reference: exact patch list, provenance,
    │                                   byte-for-byte comparison results against real hardware
    ├── patches/
    │   ├── 0001-fishball7020-fixes.patch        6 real fixes (see firmware README for details)
    │   └── 0002-add-fishball-devicetree.patch   the board's actual device tree, as source
    ├── scripts/
    │   ├── setup.sh                    (run once) clones upstream source into src/, applies patches/
    │   ├── build_all.sh                (run every time) full build → output/
    │   ├── build_hdl.tcl               Vivado batch script: synth → impl → export hardware platform
    │   ├── gen_fsbl_create.tcl         Vitis/xsct: scaffold the FSBL app from the hardware platform
    │   ├── gen_fsbl_build.tcl          Vitis/xsct: compile the FSBL app
    │   ├── fix_and_retry_buildroot.sh  auto-repairs a known Buildroot git-archive hash-drift issue
    │   └── boot.bif                    bootgen recipe: FSBL + bitstream + U-Boot → BOOT.bin
    ├── src/                            ← created by setup.sh, NOT committed to git (see .gitignore)
    │   │                                 the actual upstream source tree you'll edit HDL/kernel/etc in:
    │   ├── hdl/projects/pluto/          ← the Vivado project lives here (pluto.xpr, once built)
    │   │   ├── system_bd.tcl            block-design source (what you're editing in step 4)
    │   │   ├── system_top.v             top-level HDL wrapper
    │   │   └── system_constr.xdc        pin constraints
    │   ├── linux/                       Linux 5.15 kernel source
    │   ├── u-boot-xlnx/                 U-Boot source
    │   └── buildroot/                   Buildroot tree that builds the root filesystem
    └── output/                          ← build_all.sh writes the 5 final SD-card files here:
        ├── BOOT.bin                     FSBL + bitstream + U-Boot (changes whenever HDL changes)
        ├── devicetree.dtb
        ├── uEnv.txt
        ├── uImage                       the Linux kernel
        └── uramdisk.image.gz            the root filesystem
```

## 1. Install Vivado/Vitis 2022.2

The board's Zynq-7020 is fully covered by Xilinx's **free WebPACK
license** — no purchase or license file needed.

1. Create a free account at [xilinx.com](https://www.xilinx.com) (now AMD)
   and go to the [2022.2 downloads page](https://www.xilinx.com/support/download/index.html/content/xilinx/en/downloadNav/vivado-design-tools/2022-2.html).
2. Download the **Vitis** unified installer for Linux (not just Vivado —
   you need Vitis too, for the FSBL build in step 5). It's a single
   self-extracting `.bin` file.
3. Run it:
   ```bash
   chmod +x Xilinx_Unified_2022.2_*.bin
   ./Xilinx_Unified_2022.2_*.bin
   ```
4. In the installer GUI:
   - Choose **"Vitis"** as the product (this includes Vivado Design Suite,
     the Vitis IDE, and `xsct`).
   - Under device families, you only need **Zynq-7000** — deselecting
     everything else brings the download from ~130 GB down to ~30 GB.
   - **Keep the default install path**, `/tools/Xilinx` — this repo's
     `tools/env-vivado.sh` points there. If you install elsewhere, edit
     the two paths at the top of that file to match.
5. No license step is needed — WebPACK devices (which includes the
   XC7Z020) are auto-licensed.

**Why `tools/env-vivado.sh` exists:** Vivado 2022.2's bundled binaries are
linked against `libtinfo.so.5`, `libncurses.so.5`, and
`libssl.so.1.1`/`libcrypto.so.1.1` — legacy compatibility libraries not
present in a default Ubuntu 22.04 install. This script prepends
locally-vendored copies of exactly those libraries to `LD_LIBRARY_PATH`
before sourcing Vivado's own `settings64.sh`, without touching anything
system-wide. From here on, **always run `source tools/env-vivado.sh`
instead of Vivado's own `settings64.sh`**, in any shell where you'll run
`vivado`, `xsct`, or `bootgen` by hand.

## 2. Get the firmware source

```bash
cd firmware
./scripts/setup.sh
```

This clones the upstream source (a Zynq-7020 port of Analog Devices'
`plutosdr-fw`) into `src/` and applies this repo's `patches/` on top —
six real fixes plus the board's actual device tree (see the
[firmware README](firmware/README.md) for exactly
what each patch does and why). `src/` is gitignored and only exists on
your machine; re-run `setup.sh` any time you want a clean slate.

## 3. Open the block diagram

```bash
source ../tools/env-vivado.sh          # from firmware/
cd src/hdl/projects/pluto
vivado pluto.xpr
```

The first time, this project doesn't exist yet — only the `.tcl` scripts
that generate it (`system_project.tcl`, `system_bd.tcl`). Run
`./scripts/build_all.sh` once first (step 5) to create `pluto.xpr`, *then*
open it with the command above for subsequent edits.

Once Vivado's GUI is open: in the **Sources** panel, expand
**Design Sources → system_top → system_i** and click **Open Block Design**
to see the graphical canvas.

## 4. Add your own HDL

This project is not a bare "samples straight to DMA" design — Pluto's
reference architecture already threads channel 0 through Analog Devices'
programmable FIR decimator/interpolator, while channel 1 bypasses
filtering entirely:

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
        ch.0 only:  │rx_fir_       │   │tx_fir_        │  ch.0 only:
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
                     ▲ YOU ARE HERE — insert custom logic between
                     axi_ad9361 and cpack/tx_upack (channel 1),
                     or before/after the FIR blocks (channel 0)
```

**Where to insert your logic, depending on what you want:**

- **Channel 1** (`adc_data_i1`/`adc_data_q1` on RX, `dac_data_i1`/
  `dac_data_q1` on TX) **has no filter in the path at all** — it's wired
  directly between `axi_ad9361` and `cpack`/`tx_upack`. This is the
  cleanest insertion point if you don't want to touch existing IP: break
  the direct connection in the block design and insert your own block
  (mirroring the existing `ad_connect axi_ad9361/adc_data_i1
  <your_block>/data_in` calls in `system_bd.tcl`), then reconnect to
  `cpack`'s `enable_2`/`fifo_wr_data_2` (and `_3` for Q).
- **Channel 0** already routes through `rx_fir_decimator`/
  `tx_fir_interpolator` (ADI's `util_fir_int` IP, instantiated via
  `ad_add_decimation_filter`/`ad_add_interpolation_filter` in
  `system_bd.tcl`, coefficients from `library/util_fir_int/coefile_int.coe`).
  Insert before these blocks (raw, full-rate samples) or after (post-filter,
  right before `cpack`/after `tx_upack`) — or just replace the `.coe`
  coefficient file to change the filter's response without touching any
  wiring.
- Both channel-0 signal groups run on `axi_ad9361/l_clk` (the AD9361
  interface clock) — match that clock domain for anything you insert there.
- You can edit the block design graphically (drag in your own IP, wire it
  up, right-click → **Create HDL Wrapper**), or edit `system_bd.tcl`
  directly. See below for why the second one is what actually counts.

### Making your block-diagram changes permanent

**This trips up almost everyone the first time.** If you edit the block
design in the Vivado GUI, your changes are saved into
`src/hdl/projects/pluto/pluto.xpr` — and that entire `src/` directory is
**gitignored and regenerated by `setup.sh`**. So your work:

- *does* survive re-running `build_all.sh` (it opens the existing project),
- but is **lost forever** the moment anyone deletes the project or runs a
  clean `setup.sh`,
- and is **invisible** to anyone who clones this repo.

`system_bd.tcl` is the real source of truth. To promote a GUI change into it:

**1. Export what the GUI built**, as a reference. In Vivado's Tcl console
with the block design open:

```tcl
write_bd_tcl -force ~/bd_export.tcl
```

Don't paste this over `system_bd.tcl` — it's machine-generated, verbose, and
uses raw `create_bd_cell`/`connect_bd_net` instead of ADI's helper procs. Use
it to check the exact IP properties Vivado recorded, so you don't miss one
you set in a dialog.

**2. Hand-port the change into `src/hdl/projects/pluto/system_bd.tcl`**,
using the same `ad_ip_instance` / `ad_connect` style as the surrounding code.

**3. Prove the TCL reproduces it.** This is the step people skip, and it's
the one that catches a forgotten property. Deleting the project is also
*required* for your edit to take effect at all — `build_hdl.tcl` only
re-runs `system_project.tcl` when `pluto.xpr` is absent, otherwise it just
re-synthesises the old design and silently ignores you:

```bash
cd src/hdl/projects/pluto
rm -rf pluto.xpr pluto.cache pluto.gen pluto.hw \
       pluto.ip_user_files pluto.runs pluto.srcs pluto.sim
cd ../../../.. && ./scripts/build_all.sh
```

**4. Capture it as a patch** so it survives a fresh `setup.sh` and can be
committed:

```bash
cd src
# modified files
git diff hdl/projects/pluto/system_bd.tcl > ../patches/0003-my-change.patch
# any NEW file (e.g. a .coe) must be staged first to appear in the diff
git add -N hdl/library/util_fir_int/my_coefficients.coe
git diff hdl/library/util_fir_int/my_coefficients.coe >> ../patches/0003-my-change.patch
```

Commit that patch file. Now a clean clone + `setup.sh` reproduces your
design on any machine — which is the only meaningful definition of
"permanent" here.

In short: **use the GUI to explore and to visually verify, but treat
`system_bd.tcl` as the thing you actually edit and commit.**

## 5. Build the firmware

```bash
./scripts/build_all.sh
```

This single script runs every stage, in order, and leaves the final files
in `output/`:

| Stage | What it does |
|---|---|
| 1. HDL | Synthesizes and implements `pluto.xpr`, exports the hardware platform (bitstream included) |
| 1b. Toolchain | Builds Buildroot's own Linaro GCC 7.3 cross-compiler (once; skipped on later runs) |
| 2. FSBL | Scaffolds and compiles a fresh Vitis FSBL app from the hardware platform |
| 3. U-Boot | Built from `zynq_pluto_defconfig` (patched to match the real board's boot defaults) |
| 4. Kernel | Builds `uImage` + `zynq-pluto-sdr-fishball.dtb` (the device tree from `patches/0002`) |
| 5. Root filesystem | Buildroot; auto-retries through a known git-archive hash-drift issue |
| 6. `uEnv.txt` | Generated fresh from the just-built U-Boot's own compiled-in defaults |
| 7. Packaging | `bootgen` combines FSBL + bitstream + U-Boot into `BOOT.bin`; wraps the rootfs |

A full run takes roughly 45–90 minutes depending on your machine (HDL
synthesis/implementation and the Buildroot rootfs are the two long
stages). Every step re-runs on every invocation — there's no per-stage
skip logic — so editing `system_top.v` and re-running `build_all.sh` is
all you need to do after an HDL change; it reuses Vivado's incremental
synthesis under the hood, so only what actually changed gets rebuilt.

When it finishes, you'll have exactly these five files in `output/`:
`BOOT.bin`, `devicetree.dtb`, `uEnv.txt`, `uImage`, `uramdisk.image.gz`.

## 6. Flash the board

### Option A — SD card (always works)

Format a microSD card as a single FAT32 partition, then copy all five
files from `output/` onto it:

```bash
cp firmware/output/{BOOT.bin,devicetree.dtb,uEnv.txt,uImage,uramdisk.image.gz} /path/to/sd-card/
```

Eject it, insert it into the board, and power-cycle. This is the only
option that can update **everything**, including the FPGA bitstream, and
it's the one to use whenever you've changed HDL.

### Option B — DFU over USB (no disassembly)

The board's U-Boot has USB DFU built in, letting you push new files onto
the SD card's FAT partition over the same micro-USB cable you use for
normal operation — no card removal needed. This path can update
`uImage`, `devicetree.dtb`, and `uramdisk.image.gz`, but it **cannot**
update `BOOT.bin` (there's no DFU target for the FPGA bitstream/FSBL/
U-Boot on this board) — for any HDL change, use Option A instead. DFU is
ideal for iterating on the kernel or rootfs without touching the SD card.

1. Connect the board over USB and open a serial console (see
   [step 7](#7-verify-your-build-is-actually-running) for how). Power-cycle
   the board and **press any key within 3 seconds** to stop autoboot at
   the `Zynq>` prompt.
2. Enter DFU mode:
   ```
   Zynq> run dfu_mmc
   ```
   The board is now waiting for USB DFU transfers (nothing more appears on
   the console).
3. From your host:
   ```bash
   dfu-util -l   # confirms you can see uImage / devicetree.dtb / uramdisk.image.gz
   cd firmware/output
   dfu-util -D uImage             -a uImage
   dfu-util -D devicetree.dtb     -a devicetree.dtb
   dfu-util -D uramdisk.image.gz  -a uramdisk.image.gz
   ```
4. Back on the serial console, press **Ctrl+C** to exit the DFU wait loop,
   then reboot into your new files:
   ```
   Zynq> reset
   ```

## 7. Verify your build is actually running

**Which USB port is which:** this board exposes two completely different
USB connections that are easy to mix up:

| Port | Enumerates as | What it's for |
|---|---|---|
| The board's own USB-OTG port | `0456:b673` (Analog Devices/ADALM-PLUTO), `/dev/ttyACM0` | Normal operation: network-over-USB (`192.168.2.1`), the board's own USB console |
| The debug header (if populated) | FTDI `0403:6010` dual UART, `/dev/ttyUSB0` **and** `/dev/ttyUSB1` | JTAG-over-UART + a second UART console — only relevant if you're debugging at the FSBL/U-Boot level before the OTG port is even up |

For everyday use, connect to the board's normal USB port:

```bash
screen /dev/ttyACM0 115200
```
(Press Enter for a login prompt: `root`, no password. To exit `screen`
cleanly: `Ctrl-A` then `k`, then `y`.)

If you're on the debug header instead, try `/dev/ttyUSB1` first, then
`/dev/ttyUSB0` if that one's silent or garbled — which channel carries
the console vs. JTAG depends on the header wiring.

Then confirm your build, not stock/vendor firmware, is running:

```
cat /opt/VERSIONS
```

This should print a `device-fw <git-hash>` line plus one per component
(`hdl`, `buildroot`, `linux`, `u-boot-xlnx`), generated fresh by your
`build_all.sh` run. The *original* upstream firmware hardcodes
`fw_version=v0.38` — if you see anything other than that literal string
(a real git hash, e.g. `95aad-dirty`), you're provably running your own
build, not vendor-stock firmware. The same value is visible from a host
running `iio_info` as the `fw_version` context attribute, and
`hw_model` there should read
`FISH Ball PlutoSDR Rev.A (Z7020-AD9361)`, matching this board's device
tree.

## Troubleshooting

- **`vivado`/`xsct`/`bootgen` fail to start, or complain about missing
  shared libraries** — you sourced Vivado's own `settings64.sh` instead
  of `tools/env-vivado.sh`. Always use the latter.
- **The kernel build fails with a `GLIBC_2.xx not found` error inside a
  `gcc-plugins` step** — this happens if you source `env-vivado.sh` in the
  *same* shell you then use to build the kernel by hand; Vivado's own
  `settings64.sh` injects a long list of Xilinx cross-toolchain
  directories into `PATH` that conflict with the kernel's own toolchain.
  `build_all.sh` already isolates this correctly (Vivado is only sourced
  inside scoped subshells); if you're running kernel `make` commands
  manually, do it in a fresh shell that has never sourced
  `env-vivado.sh`.
- **U-Boot/kernel builds fail with `unrecognized -march target: armv5` or
  otherwise pick up your system's own GCC** — Buildroot's own
  cross-compiler (stage 1b) hasn't been built yet; re-run
  `build_all.sh` (it builds it automatically) rather than invoking `make`
  in `u-boot-xlnx`/`linux` directly before that's done.
- **Buildroot fails with `has wrong sha256 hash`** for some package —
  this is a known, harmless git-archive repackaging drift for a handful
  of pinned upstream commits (the commit hash itself is still the real
  content guarantee). `build_all.sh` calls
  `scripts/fix_and_retry_buildroot.sh`, which detects and repairs this
  automatically; if it still fails, check
  `/tmp/buildroot_autoretry_*.log` for a different underlying cause.
- **`dfu-util -l` shows nothing** — you didn't stop autoboot in time,
  or `run dfu_mmc` wasn't accepted; try again and press a key
  immediately after power-on.
- **You moved or renamed the checkout, and now Buildroot fails with
  `cp: cannot stat '<old path>/...'`** — Buildroot's `output/` tree is
  **not relocatable**. Autotools bakes absolute paths into thousands of
  generated files (`config.status`, `Makefile`, `libtool`, `*.la`), so a
  rename leaves stale references pointing at the old location. The build
  may get surprisingly far before something (often a package's
  `legal-info` step copying its patches) trips over one. Fix it by
  discarding the stale build state — the download cache is unaffected, so
  nothing is re-downloaded:
  ```bash
  rm -rf firmware/src/buildroot/output
  ./scripts/build_all.sh
  ```
  This also rebuilds the cross-toolchain, so expect the full build time.

Still stuck? [Open an issue](../../issues/new/choose) — pick the build
failure or hardware mismatch template, they ask for exactly the details
(stage, tool versions, logs) that actually speed up debugging a build
system like this one. See also [CONTRIBUTING.md](CONTRIBUTING.md) if
you'd like to fix something yourself.

## How this repo came to exist

The Fishball7020/PlutoSky board ships with no published, editable
firmware source of its own. This firmware was reverse-engineered and
rebuilt from scratch, starting from the public upstream fork
[`Xiaozhang-code-cloud/Fish-Wan-plutosdr-fw-7020-SDR`](https://github.com/Xiaozhang-code-cloud/Fish-Wan-plutosdr-fw-7020-SDR),
cross-referenced against:

- The board's real schematic, to verify the HDL project's pin constraints
  by hand before trusting it — several other candidate projects turned out
  to target *different*, similarly-named boards despite compiling
  successfully.
- A byte-for-byte checksum comparison against
  [`OpenSourceSDRLab/PlutoSky_7020_AD936X_SDR`](https://github.com/OpenSourceSDRLab/PlutoSky_7020_AD936X_SDR),
  which confirmed that repo as the genuine vendor source for this
  firmware's prebuilt binaries (though not its editable HDL/kernel source,
  which was never published there — only prebuilt artifacts).
- An extracted `IKCONFIG`/embedded kernel `.config` and kernel version
  banner pulled directly out of the real working firmware's compiled kernel
  image, used to prove this rebuild's kernel configuration is provably
  identical to the original, not just "close."

The result, verified file-by-file against a real working unit:
`devicetree.dtb` builds byte-for-byte identical; `uEnv.txt` and the root
filesystem file list are content-identical; the kernel and bootloader are
within a few hundred bytes of identical (the repo's git history was
squashed to a single commit *after* this board's firmware was actually
built, so a handful of source lines have drifted since — not recoverable
from public sources alone). See the
[firmware README](firmware/README.md) for the exact
patch list, including two genuine upstream bugs (hardcoded debug
leftovers) found and fixed along the way.

## Vendor resources

Material published by the board's own distributor. Useful as primary
reference, but note that none of it includes editable HDL sources — which
is the gap this repository exists to fill.

- [**PlutoSky R1 — OpenSourceSDRLab blog**](https://blog.opensourcesdrlab.com/archives/PlutoSky-R1)
  — the vendor's own write-up of this board.
- [**Vendor file archive**](https://workupload.com/archive/kc2v7ryVZZ)
  — accompanying files distributed with the board.
- [`OpenSourceSDRLab/PlutoSky_7020_AD936X_SDR`](https://github.com/OpenSourceSDRLab/PlutoSky_7020_AD936X_SDR)
  — the vendor's GitHub repo, confirmed by checksum as the genuine source
  of the prebuilt factory firmware binaries.

## License

This repository contains several kinds of content under different
licenses — see [`LICENSE`](LICENSE) for the full breakdown. In short:
this repo's own scripts, patches, and documentation are MIT-licensed;
the cloned upstream source (Linux/U-Boot/Buildroot, fetched fresh by
`setup.sh`, never committed here) remains GPL-licensed; Xilinx
Vivado/Vitis and any AMD IP are proprietary and licensed separately by
AMD/Xilinx.
