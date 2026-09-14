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
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-GPL--2.0-lightgrey" alt="License: GPL-2.0"></a>
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

> **The same board goes by several names.** All of these refer to the hardware
> this devkit targets — an XC7Z020-CLG400 + AD9361, 2×2, four SMAs:
>
> | Name | Where you'll see it |
> |---|---|
> | **7020-SDR** | the AliExpress listing title |
> | **PlutoSky**, **PlutoSky R1** | OpenSourceSDRLab's [shop write-up](https://blog.opensourcesdrlab.com/archives/PlutoSky-R1) |
> | **PlutoSky_7020_AD936X_SDR**, "AD9361/AD9363 Development Board" | OpenSourceSDRLab's [GitHub repo](https://github.com/OpenSourceSDRLab/PlutoSky_7020_AD936X_SDR) |
> | **FISH Ball PlutoSDR Rev.A (Z7020-AD9361)** | what the board itself reports as `hw_model` |
> | **Fish-Wan** | the upstream firmware fork this devkit builds from |
> | **Fishball7020** | this repository |
>
> Names on listings drift; the board's own report doesn't. **The definitive
> check** — from any machine with `libiio-utils`, no login needed:
>
> ```bash
> iio_attr -S
> #  1: 192.168.2.1 (FISH Ball PlutoSDR Rev.A (Z7020-AD9361)), serial=... [ip:pluto.local]
> ```
>
> If yours says `FISH Ball PlutoSDR Rev.A (Z7020-AD9361)`, this devkit fits.
> If it says `Z7010` or `AD9363`, or a different Rev, it does not.

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

## What you get

- **Agent Skills** in [`.claude/skills/`](.claude/skills/fishball7020-firmware/SKILL.md) —
  if you use Claude Code, the workflow rules below (flash via SD only, delete
  the Vivado project before an HDL change, the PA power budget, check
  `/mnt/jffs2` first) load automatically when you work in this repo. Written to
  the [Agent Skills spec](https://agentskills.io/specification); harmless if
  you don't use an agent.
- **A firmware build you can trust** — verified against a real unit:
  `devicetree.dtb` comes out byte-for-byte identical, the rootfs and
  bootloader environment content-identical.
- **One command builds every layer** — bitstream → FSBL → U-Boot →
  Linux 5.15 → Buildroot root filesystem → `BOOT.bin`.
- **The real ADI block design, editable** — open it in Vivado and put your
  own HDL directly into the AD9361 datapath.
- **Three ways onto the board** — SD card, DFU over USB, or JTAG for a
  seconds-long iteration loop instead of a full rebuild.
- **The transmitter is off unless you are transmitting** — stock firmware
  leaves the AD9361's TX chain biased from power-on, radiating LO leakage with
  nothing in the DAC. This build mutes it and powers its synthesiser down
  whenever no TX buffer is streaming.
  See [Transmitter safety](#transmitter-safety).
- **Your changes are reproducible** — they live in `patches/`, so a clean
  clone rebuilds them on any machine.
- **Free toolchain** — the XC7Z020 is covered by Vivado's no-cost WebPACK
  licence. No purchase, no licence file.
- **The traps are already handled** — Vivado `PATH` pollution breaking the
  kernel build, Buildroot mirror timeouts, host GCC version drift. Each one
  cost a debugging session; none of them will cost you one.

## Quick start

Assumes Vivado/Vitis 2022.2 is installed ([step 1](#1-install-vivadovitis-20222)
if not — it's the only slow part).

```bash
# run from: wherever you want the devkit to live (e.g. ~)
git clone https://github.com/matsvandamme/fishball7020-fpga-devkit.git
cd fishball7020-fpga-devkit/firmware

./scripts/setup.sh        # clone upstream source + apply patches  (~5 min)
./scripts/build_all.sh    # build everything                    (45-90 min)
```

You should end up with exactly five files:

```
$ ls output/
BOOT.bin  devicetree.dtb  uEnv.txt  uImage  uramdisk.image.gz
```

Copy all five onto a FAT32 SD card, insert it, and power on. If nothing
happens, check the `BOOT` DIP switch is in SD mode (`0 0`) — see
[Boot modes](#boot-modes-boot-dip-switch). Then jump to
[step 4](#4-add-your-own-hdl) to start changing the FPGA logic.

New to FPGAs or embedded Linux? **[How it works](docs/how-it-works.md)**
explains what those five files are and what happens between power-on and a
login prompt — no prior knowledge assumed.

> **Back up first.** Before flashing anything, copy the five files already
> on your board's SD card somewhere safe — that's your one-click way back if
> a build misbehaves. No backup? See
> [recovery](#if-things-go-wrong-recovering-the-factory-firmware).

## Table of contents

- [What you get](#what-you-get) · [Quick start](#quick-start)
- [Boot modes (DIP switch)](#boot-modes-boot-dip-switch) · [Requirements](#requirements)
- **Walkthrough** — [1. Install Vivado](#1-install-vivadovitis-20222) ·
  [2. Get the source](#2-get-the-firmware-source) ·
  [3. Open the block diagram](#3-open-the-block-diagram) ·
  [4. Add your own HDL](#4-add-your-own-hdl) ·
  [5. Build](#5-build-the-firmware) ·
  [6. Flash](#6-flash-the-board) ·
  [7. Verify](#7-verify-your-build-is-actually-running)
- [Repository layout](#repository-layout)
- [How it works](docs/how-it-works.md) — the boot chain explained from scratch
- [The stock block design: IPs, wiring, and what you can change](docs/block-design.md)
- [Worked example: an FM channelizer in the FPGA](docs/wbfm-channelizer.md)
- [Transmitter safety](#transmitter-safety) — TX is muted when nothing is being sent
- [Measured performance](docs/measured-performance.md) — what one board actually does, and what the numbers do not mean
- [Simulating your HDL first](#simulating-your-hdl-first) — one second instead of twenty minutes
- [Is the board healthy?](#is-the-board-healthy) — a self-test that measures, cable optional
- [Controlling the USER LED](docs/user-led.md) — for custom projects
- [Troubleshooting](#troubleshooting)
- [How this repo came to exist](#how-this-repo-came-to-exist) ·
  [The end-to-end test](#the-end-to-end-test) ·
  [Vendor resources](#vendor-resources) · [License](#license)

## Boot modes (BOOT DIP switch)

The board picks where to boot from using a two-position DIP switch marked
**`BOOT`**, next to the `RST` button between the `USB2.0` and `DEBUG` ports.
Boards ship set to **SD card (`0 0`)**, which is what the devkit needs — so
normally there is nothing to change. If a freshly flashed card appears to do
nothing, check this switch first.

<table>
<tr>
<td align="center"><img src="docs/img/boot-sd-00.jpg" alt="BOOT switch set to 0 0 for SD card boot" width="250"><br><b>SD card — <code>0 0</code></b><br><sub>factory default, used by the devkit</sub></td>
<td align="center"><img src="docs/img/boot-qspi-10.jpg" alt="BOOT switch set to 1 0 for QSPI flash boot" width="250"><br><b>QSPI flash — <code>1 0</code></b><br><sub>boots from the onboard flash</sub></td>
<td align="center"><img src="docs/img/boot-jtag-11.jpg" alt="BOOT switch set to 1 1 for JTAG mode" width="250"><br><b>JTAG — <code>1 1</code></b><br><sub>debugging and flashing</sub></td>
</tr>
</table>

<sub>Switch photographs from the distributor's
<a href="https://blog.opensourcesdrlab.com/archives/PlutoSky-R1">PlutoSky R1 write-up</a>.</sub>

| Mode | SW1 | SW2 | What it does |
|---|---|---|---|
| **SD card** | `0` (GND) | `0` (GND) | Boots `BOOT.bin` from the microSD card — **factory default, and what the devkit needs** |
| **QSPI flash** | `1` (VCC3V3) | `0` (GND) | Boots from the onboard 16 MiB flash chip instead |
| **JTAG** | `1` (VCC3V3) | `1` (VCC3V3) | For debugging and flashing over JTAG |

`1` means the slider is pushed toward the **`ON`** marking on the switch
body; `0` means the opposite side.

> **Always change it with the board powered off.** The boot mode is sampled
> only at power-on, so flipping it on a running board does nothing until the
> next power cycle — and hot-switching signal pins is a bad habit regardless.

Notes:

- **SD-card boot never writes to the QSPI flash**, so whatever is on that
  chip is unaffected by anything the devkit does.
- **JTAG mode is for the [Option C](#option-c--jtag-temporary-but-the-fastest-hdl-loop)
  workflow**, not for normal running.
- The distributor's write-up lists QSPI as the default; boards observed in
  practice ship in SD mode. Either way the switch is the thing to check, not
  the documentation.

### LEDs

Three indicators sit between the two USB ports:

| LED | Meaning |
|---|---|
| `PWR` | Power present |
| `DONE` | FPGA configured successfully — the same DONE signal Vivado reports as `End of startup status: HIGH` |
| `USER` | Driven by Linux (PS GPIO). Blinks by default via the kernel's heartbeat trigger — [how to control it](docs/user-led.md) |

Source: the distributor's own
[PlutoSky R1 write-up](https://blog.opensourcesdrlab.com/archives/PlutoSky-R1),
which has photographs of each switch position.

## Requirements

**Hardware:**
- A Fishball7020 / PlutoSky board, a micro-USB cable, and a microSD card
  (any size — the image is small) with a USB card reader, **or** just the
  USB cable if you'll flash via DFU.

**Software** (Ubuntu 22.04 LTS; install before step 1):

```bash
# run on your HOST, from anywhere
sudo apt update
sudo apt install -y git build-essential bison flex libssl-dev \
    device-tree-compiler u-boot-tools dfu-util screen python3 xvfb \
    libgmp-dev libmpc-dev libmpfr-dev
```

- **No extra GCC needed on 22.04.** Jammy's default GCC 11 builds
  everything. One legacy Buildroot host tool (`host-m4`) fails under
  GCC ≥ 14's stricter C defaults, so *only* on a much newer distro do you
  also need `gcc-13`/`g++-13` alongside the default compiler. `build_all.sh`
  detects your GCC version and picks automatically — it never forces
  `gcc-13` on a host that doesn't need it (and jammy doesn't even package
  it).
- `device-tree-compiler` (`dtc`) and `u-boot-tools` (`mkimage`) are used to
  build the device tree and the ramdisk image.
- `dfu-util` and `screen` are only needed if you'll flash/debug over USB
  (steps 6B/7) rather than by copying files to an SD card.
- **`libgmp-dev`/`libmpc-dev`/`libmpfr-dev`** are needed by the kernel's
  GCC-plugin build (`scripts/gcc-plugins`), which `#include <gmp.h>`. Miss
  them and the build fails at stage 4 with `fatal error: gmp.h: No such file
  or directory`.
- **`xvfb` matters if you build headless** — over SSH, in CI, or on a box
  with no desktop. Vitis (`xsct`) needs an X display to build the FSBL: it
  uses `$DISPLAY` if one is set, and otherwise falls back to Xvfb. Without
  either, the build dies at stage 2 with a bare
  `ERROR: Xvfb is not available on the system`. `build_all.sh` now checks
  for this up front rather than letting you discover it 40 minutes in.

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
   # run from: wherever you downloaded the installer (e.g. ~/Downloads)
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
# run from: wherever you want the devkit to live (e.g. ~)
git clone https://github.com/matsvandamme/fishball7020-fpga-devkit.git
cd fishball7020-fpga-devkit/firmware
./scripts/setup.sh
```

> **Where to run things:** from here on, every command runs from the
> **`firmware/`** directory unless the code block says otherwise —
> that's where `scripts/`, `patches/`, `src/` and `output/` live. Each
> block states its directory on the first line so you can never be in
> doubt. Commands that run *on the board itself* (over the serial
> console) are marked as such.

This clones the upstream source (a Zynq-7020 port of Analog Devices'
`plutosdr-fw`) into `src/` and applies this repo's `patches/` on top —
six real fixes plus the board's actual device tree (see the
[firmware README](firmware/README.md) for exactly
what each patch does and why). `src/` is gitignored and only exists on
your machine; re-run `setup.sh` any time you want a clean slate.

## 3. Open the block diagram

> Want to know what you're looking at before you open it? **[The stock block
> design](docs/block-design.md)** walks through every IP block, how they're wired,
> the clock domains, the address map, and which parts are safe to change.

```bash
# run from: firmware/
source ../tools/env-vivado.sh
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
  `tx_fir_interpolator` — 129-tap FIR filters that decimate by 8 on RX and
  interpolate by 8 on TX. They're built by the
  `ad_add_decimation_filter`/`ad_add_interpolation_filter` helpers called in
  `system_bd.tcl`, which instantiate **Xilinx's `fir_compiler` IP** directly
  and load taps from `library/util_fir_int/coefile_int.coe`.

  > **Don't be misled by `library/util_fir_int/` and `library/util_fir_dec/`.**
  > Neither has a `component.xml`, so neither is ever packaged or
  > instantiated — the `.v` files in them are dead code in this project. The
  > *only* thing used from those directories is `coefile_int.coe`. Note also
  > that the RX decimator and the TX interpolator are passed **the same**
  > coefficient file, so editing it in place changes both; point one of them
  > at a new file if you only mean to change that direction.

  Insert before these blocks (raw, full-rate samples) or after (post-filter,
  right before `cpack`/after `tx_upack`) — or just replace the `.coe`
  coefficient file to change the filter's response without touching any
  wiring.
- Both channel-0 signal groups run on `axi_ad9361/l_clk` (the AD9361
  interface clock) — match that clock domain for anything you insert there.
- You can edit the block design graphically (drag in your own IP, wire it
  up, right-click → **Create HDL Wrapper**), or edit `system_bd.tcl`
  directly.

> **Want to see all of this done for real?**
> **[Isolating one FM channel in the FPGA](docs/wbfm-channelizer.md)** is a
> complete worked example: a custom Verilog block inserted into the channel-0
> RX path, new FIR coefficients designed and verified from a script, the
> patch that makes it survive a clean `setup.sh`, and a GNU Radio flowgraph
> with no software channel filter left in it. It also explains why the
> obvious approach — "just lowpass the channel" — cannot work, which is worth
> reading before you design any filter for this board.

### Rebuilding after a GUI block-design edit

Your edit is picked up on the next build automatically — `build_hdl.tcl`
opens the existing `pluto.xpr` and does a full `reset_run synth_1`, so the
design is re-synthesised, re-implemented, and the new bitstream flows
through to `BOOT.bin`. Three things to do first:

1. **Save the block design** (`Ctrl-S`). An edit left unsaved on the canvas
   isn't in `pluto.xpr`, and the build will quietly produce firmware
   without it.
2. **Validate Design (F6)** — catches width/direction mistakes instantly
   instead of 15 minutes into synthesis.
3. **Close Vivado.** The GUI holds a lock on the project, and
   `build_all.sh` runs Vivado in batch mode against the same files.

```bash
# run from: firmware/
./scripts/build_all.sh
```

Note that GUI edits live in `src/`, which is gitignored and regenerated by
`setup.sh` — fine for iterating on your own machine, but to keep a change
long-term (or share it) port it into `system_bd.tcl` and add it to
`patches/`.

## 5. Build the firmware

```bash
# run from: firmware/
./scripts/build_all.sh
```

**Iterating on HDL?** Use `--hdl-only`. Stages 3–5 (U-Boot, kernel, root
filesystem) produce byte-identical output when only the FPGA design has
changed, and together they are most of the wall time. Skipping them cuts a
rebuild from roughly 70 minutes to about 20:

```bash
# run from: firmware/
./scripts/build_all.sh --hdl-only
```

It reuses the existing kernel/U-Boot/rootfs from `src/`, and refuses to run
if a previous full build hasn't produced them. Use a plain `build_all.sh`
after changing anything outside the HDL.

A full run executes every stage, in order, and leaves the final files in
`output/`:

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
# run from: firmware/
cp output/{BOOT.bin,devicetree.dtb,uEnv.txt,uImage,uramdisk.image.gz} /path/to/sd-card/
```

Eject it, insert it into the board, and power-cycle. This is the only
option that can update **everything**, including the FPGA bitstream, and
it's the one to use whenever you've changed HDL.

If the board comes up with the old firmware, or doesn't come up at all,
check the `BOOT` switch is in SD mode (`0 0`) — see
[Boot modes](#boot-modes-boot-dip-switch). A board in QSPI mode ignores the
card entirely, which looks exactly like a failed build.

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
   # run from: firmware/output/  (on your HOST, not the board)
   dfu-util -l   # confirms you can see uImage / devicetree.dtb / uramdisk.image.gz
   dfu-util -D uImage             -a uImage
   dfu-util -D devicetree.dtb     -a devicetree.dtb
   dfu-util -D uramdisk.image.gz  -a uramdisk.image.gz
   ```
4. Back on the serial console, press **Ctrl+C** to exit the DFU wait loop,
   then reboot into your new files:
   ```
   Zynq> reset
   ```

### Option C — JTAG (temporary, but the fastest HDL loop)

For iterating on PL changes you can push a bitstream straight into the FPGA
over JTAG — seconds, instead of a full `build_all.sh` plus reflash. Two
things to be clear about: it is **volatile** (gone on power-cycle) and it
does **not** update `BOOT.bin`, so it's for testing, not deployment.

**Use the debug port** — JTAG is interface 0 on that connector. Keep the
USB 2.0 port connected as well if that's what powers your board.

**One-time setup.** Vivado ships udev rules for Digilent cables but doesn't
install them. Without them the USB node stays `crw-rw-r-- root root`, so
libusb can't claim the device and Vivado reports
`ERROR: [Labtoolstcl 44-199] No matching targets found`.

Run this **in a real terminal on the machine the board is plugged into** —
`sudo` needs a TTY, so it won't work through an IDE/agent shell, and rules
installed inside a VM have no effect on the host:

```bash
# run on your HOST, from anywhere
sudo cp /tools/Xilinx/Vivado/2022.2/data/xicom/cable_drivers/lin64/install_script/install_drivers/*.rules \
        /etc/udev/rules.d/
sudo udevadm control --reload-rules
sudo udevadm trigger
```

Now **unplug and replug the debug cable**, and verify (neither needs sudo):

```bash
# run on your HOST, from anywhere
ls /etc/udev/rules.d/ | grep xilinx
ls -la /dev/bus/usb/003/$(lsusb | grep 0403:6010 | sed -E 's/.*Device ([0-9]+).*/\1/')
```

You want two `.rules` files listed, and permissions **`crw-rw-rw-`** (three
`rw` groups). Then confirm Vivado sees the cable:

```tcl
open_hw_manager
connect_hw_server
get_hw_targets
open_hw_target
get_hw_devices
```

Expected — the Digilent cable, then the Zynq's ARM debug port and the PL:

```
localhost:3121/xilinx_tcf/Digilent/000000000069A
arm_dap_0 xc7z020_1
```

**Keep BOTH cables connected the whole time.** The debug port powers the
board (verified: JTAG reaches the Zynq with only that cable attached), and
the USB 2.0 port carries the network/libiio data. Since the bitstream is
volatile, unplugging the debug port to "move to" the USB 2.0 port would cut
power and lose it. Connect both up front and unplug nothing.

**Never program while Linux is running.** Its drivers (`ad9361`, the DMAs)
are bound to the *old* PL; swapping the bitstream underneath them will break
or hang the system and needs a power-cycle to recover.

### C1. Quick method — Hardware Manager, halted at U-Boot

1. Open the debug UART console, power-cycle, press a key within 3 s to stop
   at the `Zynq>` prompt. (The FSBL has now configured the PS and enabled
   the PS↔PL level shifters, but Linux hasn't claimed anything.)
2. Program — GUI: **Open Hardware Manager → Auto Connect → right-click
   `xc7z020_1` → Program Device**. Or scripted:

   ```tcl
   open_hw_manager
   connect_hw_server
   open_hw_target
   current_hw_device [get_hw_devices xc7z020_1]
   set_property PROGRAM.FILE \
     {<repo>/firmware/src/hdl/projects/pluto/pluto.runs/impl_1/system_top.bit} \
     [current_hw_device]
   program_hw_devices [current_hw_device]
   ```

3. Back at `Zynq>`, type `boot`. Linux comes up against your new PL.

**Success indicator** — programming prints the FPGA's DONE pin going high:

```
INFO: [Labtools 27-3164] End of startup status: HIGH
```

`LOW` means the bitstream didn't take (wrong file, or the device was reset
mid-programming).

**Caveat.** This configures the PL fabric correctly, but on Zynq the PS↔PL
level shifters and PL resets are managed by *software* (`ps7_post_config`),
not by the act of programming. Re-loading the PL underneath a PS that was
set up for the previous bitstream can leave the AXI interfaces in an
undefined state. In practice this is usually fine for iterating on logic
that doesn't change the AXI topology — but if the design misbehaves in ways
the bitstream alone doesn't explain, use C2.

### C2. Robust method — full JTAG bootstrap (ADI's own flow)

Upstream ships `scripts/run-xsdb.tcl` and a `jtag-bootstrap` make target
for exactly this. It brings the whole board up from JTAG, so the PS is
initialised *for the bitstream you are loading*, in the correct order:
`ps7_init` → program PL → `ps7_post_config` → load U-Boot.

```tcl
# xsdb run-jtag.tcl     (run from: firmware/src/hdl/projects/pluto)
connect
target 2
rst
source ps7_init.tcl
ps7_init
fpga -f pluto.runs/impl_1/system_top.bit
ps7_post_config
dow ../../../u-boot-xlnx/u-boot
con
```

Ordering is the part that matters: `ps7_init` configures DDR/clocks/MIO,
the bitstream goes in next, and **`ps7_post_config` must come after it** —
that's the step that enables the PS↔PL level shifters and releases the PL
resets. ADI's shipped script has the `fpga` line commented out because
their use case was flashing U-Boot without a new bitstream; uncommenting it
in this position is the standard Zynq sequence.

Everything it needs is produced by the normal build: `ps7_init.tcl` (also
inside `system_top.xsa`), `system_top.bit`, and the `u-boot` ELF.

When the design is working, rebuild properly (`build_all.sh`) and flash via
Option A so it persists.

### If things go wrong: recovering the factory firmware

If a build misbehaves and you have no backup of your own, the distributor publishes the board's prebuilt factory firmware:
If you skipped the backup, or lost it, the distributor publishes the
board's prebuilt factory firmware here:

**[`OpenSourceSDRLab/PlutoSky_7020_AD936X_SDR`](https://github.com/OpenSourceSDRLab/PlutoSky_7020_AD936X_SDR)**

This is a genuine known-good fallback, not a guess: during this project the
binaries in that repo were compared byte-for-byte against a working unit's
SD card and confirmed as the real source of this board's factory firmware.
Copy its SD-card files onto a FAT32 card exactly as in
[Option A](#option-a--sd-card-always-works) and the board returns to its
shipped state.

Keep a copy locally *before* you start experimenting — a rescue that needs
a working internet connection and a third-party repo still being online is
a weaker safety net than a folder on your own disk.

## 7. Verify your build is actually running

**Before you flash**, check the build made sense. Flashing and rebooting costs
several minutes; this costs a second:

```bash
# run from: firmware/
./scripts/verify_output.sh
```

It asserts the five SD-card files are present and non-trivial, that the
bitstream is compressed (an uncompressed one overflows the FSBL's OCM and
BOOT.bin silently fails to boot), and that no setup endpoint fails timing —
then prints what is actually in the design, so you can see your change landed:

```
== FPGA design ==
  PASS  utilization report present
        DSP48s 96 / 220   Slice LUTs 12664 / 53200
        -> channelizer filter (321 taps)
        block design: rx_ddc (Fs/4 shifter) is wired in
  PASS  ad_fs4_ddc.v present alongside it
        FIR coefficients: coefile_wbfm_102100.coe
```

It exits non-zero if anything is wrong, so it works in a script too.


**Which USB port is which** — the board has two, and they do completely
different things:

| | **USB 2.0 (OTG) port** | **Debug port** |
|---|---|---|
| Enumerates as | `0456:b673` Analog Devices, typically `/dev/ttyACM*` (`-if03`) | `0403:6010` **Digilent Adept**, two `/dev/ttyUSB*` |
| Gives you | Network-over-USB (`192.168.2.1`), libiio / `iio_info`, mass storage, a console | **JTAG** (`-if00`) and the board's **real UART console** (`-if01`) |
| Available | Only **after Linux boots** — it's a USB gadget *created by* the board's own Linux | From **power-on** — real hardware, independent of software |

**For serial, use the debug port.** Its UART is the board's actual console
(`ttyPS0`), so you see the whole sequence: FSBL → U-Boot → kernel → login.
The OTG port's `ttyACM*` console only appears once Linux has booted far
enough to bring up the USB gadget — so you miss the entire boot, and see
nothing at all if the board fails to boot, which is precisely when you
need the console most.

With Digilent Adept, **`-if00` is JTAG and `-if01` is the UART**, so the
console is the `-if01` device (typically `/dev/ttyUSB1`).

**First, find the port.** Don't assume `/dev/ttyACM0` — the number depends
on what else is plugged into your machine. List the serial devices by their
stable, self-describing names:

```bash
# run on your HOST, from anywhere
ls -l /dev/serial/by-id/
```

On the **debug port** you'll see two entries — take the `-if01` one:

```
usb-Digilent_Digilent_Adept_USB_Device_<serial>-if00-port0 -> ../../ttyUSB0   <- JTAG
usb-Digilent_Digilent_Adept_USB_Device_<serial>-if01-port0 -> ../../ttyUSB1   <- console
```

On the **USB 2.0 port** (post-boot console only) it appears instead as:

```
usb-Analog_Devices_Inc._PlutoSDR__ADALM-PLUTO_-if03 -> ../../ttyACM0
```

**Then connect.** Use the `by-id` path directly — it's stable across
reboots and replugs, unlike the `ttyUSB*`/`ttyACM*` number:

```bash
# run on your HOST, from anywhere (substitute your own serial number)
screen /dev/serial/by-id/usb-Digilent_Digilent_Adept_USB_Device_<serial>-if01-port0 115200
```

(Tab-completion works on that path. If `/dev/serial/by-id/` doesn't exist
on your system, fall back to `ls /dev/ttyACM* /dev/ttyUSB*` and use the
device that appears when you plug the board in.)

Press Enter for a login prompt. The credentials are **`root` / `analog`**
(set by `BR2_TARGET_GENERIC_ROOT_PASSWD` in the Buildroot defconfig; change
it on the board with `device_passwd`). To exit `screen` cleanly: `Ctrl-A`
then `k`, then `y`.

**SSH works too**, which is often more convenient than a serial console —
the firmware runs dropbear, and the board is reachable over the USB network
(or Ethernet). Same credentials:

```bash
# run on your HOST, from anywhere
ssh root@192.168.2.1        # password: analog
```

Note SSH needs the **USB 2.0 port** (or Ethernet) for networking — the
debug port carries only JTAG and UART, no network.

If you're on the debug header instead, try `/dev/ttyUSB1` first, then
`/dev/ttyUSB0` if that one's silent or garbled — which channel carries
the console vs. JTAG depends on the header wiring.

Then confirm your build, not stock/vendor firmware, is running. **This one
runs on the board**, at the `#` prompt inside the serial console — not on
your host:

```
# on the BOARD (inside the screen session)
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

## Repository layout

```
fishball7020-fpga-devkit/
├── README.md                            ← you are here: the full build/flash workflow
├── LICENSE                              multiple licenses apply — see below
│
├── .claude/skills/                      ← Agent Skills: the rules above, loaded automatically
│   └── fishball7020-firmware/           by Claude Code when you work in this repo
│
├── tools/
│   ├── env-vivado.sh                    ← source this before any vivado/xsct/bootgen command
│   ├── selftest/                        ← is the board damaged? measures and says (see below)
│   │   ├── sdr_selftest.py              rails, BIST, receiver, and an RF loopback sweep
│   │   ├── iiod_min.py                  libiio's network protocol over a plain socket, stdlib only
│   │   └── test_dsp.py                  asserts the measurement maths, no board needed
│   └── legacy-libs/libs/                vendored libtinfo5/libncurses5/libssl1.1 (see below)
│
└── firmware/       the only firmware target — factory-default USB+Ethernet build
    ├── README.md                       deep technical reference: exact patch list, provenance,
    │                                   byte-for-byte comparison results against real hardware
    ├── patches/
    │   ├── 0001-fishball7020-fixes.patch        6 real fixes + a persistent hw_serial (see firmware README)
    │   ├── 0002-add-fishball-devicetree.patch   the board's actual device tree, as source
    │   ├── 0004-mute-tx-when-no-dma-stream.patch TX safeguard (see Transmitter safety)
    │   ├── 0005-dont-clobber-a-gain-set-before-streaming.patch  the unmute stops overwriting your gain
    │   └── optional/                             NOT applied by setup.sh — worked examples
    │       └── 0003-wbfm-channelizer.patch      the FM channelizer (docs/wbfm-channelizer.md)
    ├── scripts/
    │   ├── setup.sh                    (run once) clones upstream source into src/, applies patches/*.patch (not optional/)
    │   ├── build_all.sh                (run every time) full build → output/
    │   ├── build_hdl.tcl               Vivado batch script: synth → impl → export hardware platform
    │   ├── gen_fsbl_create.tcl         Vitis/xsct: scaffold the FSBL app from the hardware platform
    │   ├── gen_fsbl_build.tcl          Vitis/xsct: compile the FSBL app
    │   ├── fix_and_retry_buildroot.sh  auto-repairs a known Buildroot git-archive hash-drift issue
    │   ├── boot.bif                    bootgen recipe: FSBL + bitstream + U-Boot → BOOT.bin
    │   ├── gen_fir_coe.py              designs + verifies FIR coefficients (stdlib only, no MATLAB)
    │   ├── gen_fir_coe.m               the MATLAB equivalent (equiripple; needs the SP Toolbox)
    │   ├── verify_output.sh            checks output/ is complete and reports what's in the bitstream
    │   └── coefile_*.coe               generated coefficients; build_all.sh copies these into src/
    ├── sim/                            ← simulate the custom HDL in a second, no Vivado needed
    │   ├── run_sim.sh                  runs it; --mutate proves the testbench can fail
    │   └── tb_ad_fs4_ddc.v             self-checking testbench against a golden model
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

## Transmitter safety

**Stock firmware leaves the transmitter running.** Measured on a real board at
power-on: the AD9361 comes up in ENSM `fdd` with the TX synthesiser going and
only 10 dB of attenuation, so the TX port emits LO leakage continuously — with
nothing in the DAC DMA, no DDS tone, and nobody having asked to transmit. When
a transmission ends, ADI's driver reverts the baseband source to a silent DDS
but leaves the chain biased, so it goes straight back to idling hot.

Into an open or shorted port that is not a damage risk — the AD9361's outputs
tolerate both. But there is no reason to keep a transmitter energised that you
are not using, it warms a die that already sits above 50 °C, and on the **PA
variant of this board it is not a trivial amount of power**: see the loopback
warning below.

**This build fixes it in firmware.** `patches/0004-mute-tx-when-no-dma-stream.patch`
hooks the TX buffer lifecycle the DAC driver already has:

| Event | What happens |
|---|---|
| boot (`S21misc`) | TX attenuated to maximum, so the board is quiet before anything streams |
| a TX buffer starts streaming | TX unmuted — your gain if you set one, otherwise the last one you used |
| the buffer stops | TX muted again and the TX synthesiser powered down, automatically |

It works by calling `ad9361_tx_mute()`, ADI's own exported helper, which was
present in the kernel tree but called from nowhere. It caches both channels'
attenuation and restores exactly what was there, so a transmit gain you chose
survives a stream.

**Set the gain whenever you like.** `patches/0005` exists because restoring
that cache *unconditionally* was itself a trap: setting a gain and then
starting the stream is the obvious order, and the unmute would overwrite it a
moment later with the value cached at the end of the *previous* transmission.
Asking for −10 dB could put −60 dB on the wire, with nothing to say why. The
unmute now restores the cache only when nothing has been set since the mute, so
both orders work:

| What you do | What you get |
|---|---|
| set a gain, then start the stream | the gain you set |
| start the stream having set nothing | the last gain you used |

If you have a watchdog script polling `buffer/enable` to re-apply a gain — a
common workaround for exactly this — you no longer need it. Check
`/mnt/jffs2/autorun.sh`; that partition is persistent, so such a script
survives reflashing and will keep overriding your application's gain.
`tools/selftest/sdr_selftest.py --ssh` lists what is there.

The part that makes this a guarantee rather than best effort: the IIO core runs
the buffer's `postdisable` hook on teardown **even when the application crashed
or was killed**, because teardown happens on file close. A userspace watchdog
could never promise that.

No device tree change was needed — the driver finds the phy through the DDS
node's existing `clocks` phandle — so `devicetree.dtb` stays byte-identical to
the factory firmware.

Measured over a 50 dB attenuated TX→RX loopback: **the mute costs no output
power.** Commanded and applied attenuation matched to 0.01 dB at every point
including 0 dB, and received level tracked commanded gain across 40 dB within
1.9 dB. While a stream runs, the chip is in exactly the state it would be in
without the patch.

> ### A TX→RX loopback without an attenuator will destroy your receiver
>
> The receiver is the fragile end — the AD9361's RX input is rated to roughly
> **+2.5 dBm** — and **this board is sold in a variant with a power amplifier
> on transmit**, which most Pluto advice does not account for.
>
> The PA is a Mini-Circuits [**PGA-102+**](https://www.minicircuits.com/pdfs/PGA-102+.pdf),
> and its gain is strongly frequency dependent:
>
> | GHz | 0.05 | 0.8 | 2.0 | 3.0 | 4.0 | 6.0 |
> |---|---|---|---|---|---|---|
> | **Gain (dB)** | **17.7** | 15.9 | 14.0 | 12.5 | 11.5 | 10.4 |
>
> with P1dB around **+17.5 dBm**. Measured on a PA-equipped unit at 900 MHz
> through a 50 dB pad, flat out it delivers about **+18.5 dBm** — roughly
> **16 dB above what its own receive port survives**.
>
> So: **fit at least 20 dB of attenuation** in any loopback; 40–50 dB is
> comfortable and still leaves 60 dB of signal-to-noise. Start with TX
> attenuation at maximum and raise power in steps. `tools/selftest/` does all
> of this for you and never transmits with less than 35 dB of its own
> attenuation — see [Is the board healthy?](#is-the-board-healthy).
>
> The non-PA variant is 10–18 dB quieter, but check which one you have before
> relying on that.

## Simulating your HDL first

A Vivado build is about 20 minutes with `--hdl-only` and 70 from cold, and then
you still have to flash and reboot. Synthesis also cannot tell you the logic is
*wrong* — only that it fits and meets timing. So check the logic first:

```bash
# run from: firmware/
./sim/run_sim.sh
```

Needs only `iverilog` (`sudo apt install iverilog`), takes about a second, and
checks the repo's custom HDL against a golden model of what it is supposed to
compute. It works whether or not you have applied the channelizer patch — if
`ad_fs4_ddc.v` is not in `src/`, the runner lifts it straight out of the patch
file.

```
== ad_fs4_ddc ==
   [1] 200 random samples, valid every clock
   [2] 200 random samples with random 0-3 clock gaps
   [3] a tone at +Fs/4 becomes DC (with gaps, so it is a real test)
   [4] DC in comes out rotating through the four quadrants
   [5] outputs hold their value while valid_in is low
   [6] the endpoints of the documented input range
   PASS  473 checks, no mismatches against the golden model
```

Every check is exact integer arithmetic — an Fs/4 shift is a swap and a sign
flip, so there is no rounding and no tolerance to argue about.

**Test [2] is the one that earns its keep.** The phase counter must advance
once per *sample*, not once per *clock*, and on this board `valid` is genuinely
intermittent — in 2R2T mode the AD9361 asserts `adc_valid` every second clock.
Moving that counter outside its `if (valid_in)` guard looks correct in a
back-to-back simulation, synthesises cleanly, meets timing, and puts the
channel at the wrong frequency on hardware.

A green test suite means nothing until you have watched it go red, so the
runner can check itself:

```bash
./sim/run_sim.sh --mutate
```

It breaks the module four ways — the phase counter moved out of its guard, a
sign error in the −j quadrant, I and Q swapped in +j, `valid_out` unregistered
— and reports any mutant the testbench fails to catch. CI runs both.

If you add HDL of your own, add a testbench beside this one. It is the
cheapest verification available here by a factor of about a thousand.

## Measured performance

One board, six runs — both channels, looped back through a 20 dB, a 30 dB and a
50 dB attenuator. Measuring it three ways is what separates a property of the
*board* from a property of the *cable*.

| | |
|---|---|
| **Gain accuracy** | 12 slope measurements, every one within **1.4% of unity** |
| **Image rejection** | **55–63 dBc** after calibration (41–48 dBc as found) |
| **Harmonic distortion** | **−67 to −79 dBc** |
| **Transmit power** | **+19 dBm** flat out, agreeing to 0.7 dB across six runs |
| **Transmit mute depth** | **63–70 dB**, into the noise floor |
| **FPGA headroom** | 72 of 220 DSP48s used, timing met with **+0.214 ns** to spare |

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/img/loop-gain-dark.svg">
  <img alt="TX to RX loop gain against frequency for both channels, 100 MHz to 5 GHz. Both peak near +20 dB around 300-700 MHz and roll off to +5 to +9 dB at 5 GHz. A shaded band shows the spread across three attenuator values: about 1-2 dB below 2 GHz, widening to 6-8 dB at 5 GHz." src="docs/img/loop-gain-light.svg">
</picture>

The gain figure is the one that matters in practice: **a link budget you compute
is the one you get.** Ask for 6 dB less and you get 6.0, not 5.2.

The plot is 105 frequencies per channel from 70 MHz to 6 GHz, repeatable to
under 0.1 dB. The **step at 4 GHz is not the hardware** — the AD9361 swaps RX
gain table there and the two tables label their steps differently, so a gain
calibration made below 4 GHz is wrong above it: by about 5 dB on channel 0 and
7 dB on channel 1.

The spread is the second result. Repeated passes on the same cable agree to
0.06 dB; across three *different* attenuators the same frequencies scatter by
6–8 dB above 2 GHz. Same board, same instrument — the variable is the SMA
connectors. That is why the self-test compares against a baseline you record
with your own cable rather than against absolute thresholds.

Full tables, method and caveats — including what these numbers are *not* — in
**[docs/measured-performance.md](docs/measured-performance.md)**.

## Is the board healthy?

If you have overdriven an input, transmitted into an open port, or the board
has simply stopped behaving, `tools/selftest/` answers the question with
measurements rather than with "well, it still enumerates".

```bash
cd tools/selftest
./sdr_selftest.py --ssh                                   # no cable, never transmits
./sdr_selftest.py --ssh --loopback --pad 50               # + the RF tests
./sdr_selftest.py --ssh --loopback --pad 50 --channel both  # both TX/RX pairs
```

Python 3.8 and nothing else — `numpy` is used for the FFT if you have it and a
pure-Python transform if you don't.

**Without a cable**, it reads the six Zynq supply rails against their ±5%
limits and both die temperatures, runs the AD9361's own **digital-interface
eye scan** (all 16×16 clock/data delay combinations with a PRBS running, which
is how you catch an LVDS link that has gone marginal), pushes a tone through
the chip's **internal digital loopback** to prove both DMAs and the FPGA
datapath, then exercises the receiver: capture integrity, DC offset, gain-chain
response over 70 dB, both channels, and synthesiser lock from 70 MHz to 6 GHz.
The two BIST checks live in debugfs, which is why they need `--ssh`; everything
else runs over libiio alone.

**With a loopback** — `TX1 ─[20 or 30 dB pad]─ RX1` — it adds the analogue
path: TX attenuator linearity over 25 dB, RX gain linearity over 40 dB, image
rejection, 2nd and 3rd harmonic distortion, path loss at eight frequencies from
100 MHz to 5 GHz (which is what finds a blown balun — it shows up as a hole in
one band and nowhere else), and how far the transmitter actually falls when it
is stopped.

**It cannot overdrive your receiver, even if you forget the attenuator.** This
board's PA can put about +18.5 dBm on the transmit port against a receive port
rated to +2.5 dBm, so the script never transmits with less than **35 dB** of
its own attenuation — about −16 dBm at the drive level it uses, and −10 dBm
even at full-scale drive, with a *bare cable* and no pad at all. Sweeps start
at 50 dB and only ever work downward toward that floor. Nothing transmits at
all without `--loopback`, and every setting is restored on exit, including
after Ctrl-C.

It also **asks how much attenuation is in your cable** and then checks that
answer against what it measures, because a pad that is missing or not making
contact is the failure that kills receivers.

Path loss depends on your cable and your pad, so there is no universal number
for it. Record a baseline while the board is known good and compare later:

```bash
./sdr_selftest.py --ssh --loopback --save-baseline ~/board-healthy.json
./sdr_selftest.py --ssh --loopback --baseline     ~/board-healthy.json
```

That turns *"is 41.6 dB of loss at 2.4 GHz correct?"* into *"it was 41.5 dB in
March"*. Full details in [`tools/selftest/README.md`](tools/selftest/README.md).

## Troubleshooting

- **SDRangel lists the board as `PlutoSDR0 TBD` and won't open it** (`open
  serial TBD failed` in its log). SDRangel identifies Plutos by serial number,
  and firmware built before patch 0001 gained its serial fallback reported an
  empty one — this board's W25Q128 flash never emits the `SPI-NOR-UniqueID`
  line the boot script looks for. Rebuild with the current `patches/` and
  reflash; the board mints a persistent serial on first boot, and your network
  interface name and MAC do not change. If SDRangel is a snap, also
  `sudo snap connect sdrangel:raw-usb` — without it the Pluto scan fails
  before it ever reads a serial.

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
  # run from: firmware/
  rm -rf src/buildroot/output
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

### The end-to-end test

The claim this repo has to earn is narrow and testable: *a fresh clone, an
edit, and a rebuild produce firmware whose FPGA actually contains the edit.*
It is re-run before every release, from a genuinely clean clone, and flashed
via the SD partition to a real board.

**A clean clone reproduces stock.** `git clone`, `setup.sh`, then a cold full
build with nothing cached.

| | |
|---|---|
| Patches applied | 0001, 0002, 0004, 0005 — `optional/0003` skipped, with a message saying so |
| Buildroot auto-repair | `SUCCESS on iteration 1`, both passes — no repairs needed |
| Output | the 5 SD-card files, nothing else |
| `devicetree.dtb` | byte-for-byte identical to the factory board's |
| RX path | no `rx_ddc`, stock `coefile_int.coe`, **72 / 220 DSP48s**, 11 893 LUTs |
| BOOT.bin | 2 849 940 B, bitstream compressed to 2 329 140 B |
| Timing | WNS +0.214 ns, 0 failing endpoints of 48 248 |
| HDL simulation | 473 checks against the golden model, all 4 mutants caught |
| On the board | correct `hw_model`, persistent serial, TX muted at boot, 32 self-test checks passed |

**An HDL change reaches the fabric.** The same tree with
`optional/0003-wbfm-channelizer.patch` applied, the Vivado project deleted, and
`build_all.sh --hdl-only` re-run.

| | |
|---|---|
| RX path | `rx_ddc` wired, `coefile_wbfm_102100.coe`, **96 / 220 DSP48s** |
| BOOT.bin | a different bitstream, still compressed |
| Timing | WNS +0.292 ns, 0 failing endpoints of 55 269 |
| On the board | LO spur moved from **+0 kHz** to **−1000 kHz** |

That last row is the whole test in one number. The AD9361's LO leakage and DC
offset land at exactly 0 Hz and cannot be moved by anything in software — so a
spur that has moved to −1 MHz can only have been moved by logic running in the
FPGA. Engaging the ÷8 filter confirms the rest of the datapath: an out-of-band
signal 37.8 dB over the floor vanishes, and capture RMS drops from −49.2 to
−78.6 dBFS.

**This is not ceremony.** The v1.1 run found three real defects in the build's
own self-repair path, every one of which would have stopped the next person
building from a clean clone: it patched the alphabetically-first of the 1120
packages containing a `COPYING` rather than the one that failed; it recorded
the hash of a zero-byte download as if it were valid, disabling the check that
caught the corruption; and it cleared `dl/` without clearing the stamps that
stop buildroot re-fetching. None of them were visible by reading the code.

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
