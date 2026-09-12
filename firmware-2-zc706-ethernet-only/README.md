# Firmware 2 — ZC706/FMCOMMS2-3 port (Ethernet only)

This is the board's **alternate firmware** — an Ethernet-only build based on
Analog Devices' stock `fmcomms2`/`zc706` reference design, retargeted from
the real ZC706's Zynq-7045/FMC connector to this board's actual
XC7Z020-CLG400 and on-board (non-FMC) AD9361 wiring.

Unlike Firmware 1, this one shipped from the board's own accessory files
with a **full, editable Vivado project already included** (`hdl/`) — a
complete Analog Devices HDL library checkout plus this board's customized
`projects/fmcomms2/zc706` project. It is a full PS+PL Linux-capable design
(not a bare-metal demo), and is the recommended starting point if you want
to **write and iterate on your own custom PL HDL** for this board, since:

- Its pin constraints were verified pin-by-pin against the board's real
  schematic (`enable`=T15, `txnrx`=P18, `spi_mosi`=P16, `spi_clk`=V18,
  `spi_miso`=V17, `spi_csn`=R17, RX/TX clocks on U18/U19/U14/U15 — all
  matching).
- It's been built and flashed to real hardware successfully (confirmed via
  the build-earmark mechanism described below).
- It targets the correct chip on every synthesis/implementation run
  (`xc7z020clg400-2`).

## Directory layout

```
firmware-2-zc706-ethernet-only/
├── hdl/                    Vivado project + full Analog Devices HDL library
│   └── projects/fmcomms2/zc706/     <-- YOUR CUSTOM HDL GOES HERE
│       ├── fmcomms2_zc706.xpr       Vivado project file — open this
│       ├── system_top.v             top-level Verilog wrapper
│       ├── system_constr.xdc        board pin constraints (verified vs. schematic)
│       └── fmcomms2_zc706.runs/impl_1/system_top.bit   last built bitstream
│   └── fsbl/                        FSBL (First Stage Bootloader) project + build script
├── firmware/                Everything else needed for the SD card, unchanged from the
│                             known-working firmware, plus the u-boot re-wrap recipe
│   ├── boot.scr, image.ub, rootfs.tar.gz   unchanged Linux firmware components
│   ├── uboot-raw.bin, system.dtb           extracted from the working BOOT.BIN
│   ├── uboot_wrap.ld, boot.bif             recipes to re-package them
├── scripts/
│   ├── build_all.sh          one command: HDL change -> BOOT.BIN -> SD-card file set
│   └── patch_fsbl_earmark.py injects a unique build tag into the FSBL (see below)
└── output/                   build_all.sh writes the final SD-card files here
```

The shared Vivado environment fix lives at `../tools/env-vivado.sh` (one
level up, shared with Firmware 1).

---

## Quick start: build the firmware

```bash
cd firmware-2-zc706-ethernet-only
./scripts/build_all.sh
```

This runs the full pipeline: rebuild the bitstream from
`hdl/projects/fmcomms2/zc706/`, regenerate the FSBL with a fresh unique
build-earmark string baked in, re-wrap u-boot, and package a fresh
`BOOT.BIN`. When it finishes, `output/` contains the four files you copy to
the SD card:

| File | Goes on | Notes |
|---|---|---|
| `BOOT.BIN` | FAT32 boot partition | rebuilt fresh every run (FSBL + your bitstream + u-boot + device tree) |
| `boot.scr` | FAT32 boot partition | unchanged u-boot script |
| `image.ub` | FAT32 boot partition | unchanged Linux kernel+initrd+dtb FIT image |
| `rootfs.tar.gz` | **extract onto the EXT4 partition** | unchanged root filesystem |

The SD card uses a two-partition layout: a small FAT32 boot partition
holding the first three files, and an EXT4 partition holding the extracted
contents of `rootfs.tar.gz` (`tar xzf rootfs.tar.gz -C /path/to/ext4/mount`).

### Confirming a build actually landed on the device

Every `build_all.sh` run generates a fresh, unique build tag
(`FISHBALL7020-<UTC timestamp>`) and patches it into the FSBL's
`FsblHookAfterBitstreamDload()` hook via
`scripts/patch_fsbl_earmark.py` — this hook runs unconditionally, on every
single boot, right after your PL bitstream loads and before handoff to
u-boot, printed straight to the serial UART. It's independent of
Linux/rootfs, so it works regardless of what's running.

To check: connect a serial console at 115200 baud (via the board's
USB-JTAG port), power-cycle, and look for a line like

```
*** BUILD EARMARK: FISHBALL7020-20260912-042710 ***
```

right after the standard Xilinx FSBL banner and before u-boot starts. The
tag is also saved to `output/BUILD_TAG.txt` for later reference.
`build_all.sh` refuses to package `BOOT.BIN` if the compiled FSBL doesn't
actually contain the expected tag — see the pitfalls below for why that
check exists.

### What each build step does, if you want to run it by hand

```bash
source ../tools/env-vivado.sh                              # Vivado 2022.2 + compat libs

# 1. HDL -> bitstream + hardware platform export
cd hdl/projects/fmcomms2/zc706
vivado -mode batch -source build.tcl
#    -> fmcomms2_zc706.runs/impl_1/system_top.bit
#    -> system_top.xsa   (hardware platform, needed by the FSBL)

# 2. Hardware platform -> FSBL, scaffold + patch + build as separate steps
cd ../../../fsbl
rm -rf system_top fsbl fsbl_system .metadata   # .metadata is the Vitis workspace
                                                # registry - it must be wiped too, or
                                                # "app create" fails claiming the
                                                # project name already exists
xsct gen_fsbl_create.tcl
python3 ../../scripts/patch_fsbl_earmark.py fsbl/src/fsbl_hooks.c "FISHBALL7020-$(date -u +%Y%m%d-%H%M%S)"
xsct gen_fsbl_build.tcl
make -C fsbl/Debug   # see "xsct's build isn't reliably synchronous" below

# 3. Re-wrap the existing u-boot binary as a loadable ELF (only needed once,
#    already done for you in firmware/ - repeat only if you ever need a new
#    u-boot binary from elsewhere)
arm-none-eabi-objcopy -I binary -O elf32-littlearm -B arm \
    firmware/uboot-raw.bin uboot_blob.o
arm-none-eabi-ld -T firmware/uboot_wrap.ld uboot_blob.o -o u-boot.elf

# 4. Package BOOT.BIN (fsbl.elf FROM fsbl/Debug/, system_top.bit, u-boot.elf,
#    system.dtb - all need to be in the current directory)
bootgen -image firmware/boot.bif -arch zynq -o BOOT.BIN -w
```

`build_all.sh` does all of this for you, in order, into `output/`.

---

## Where to inject your custom HDL

Open `hdl/projects/fmcomms2/zc706/fmcomms2_zc706.xpr` in Vivado, then in the
**Sources** panel: `Design Sources → system_top → system_i` and click
**Open Block Design**. This is the standard Analog Devices AD9361 reference
architecture. The relevant part of the signal chain (from the project's own
build script, `hdl/projects/fmcomms2/common/fmcomms2_bd.tcl`):

```
                     AD9361 (physical LVDS pins)
                            │
                     ┌──────▼───────┐
                     │  axi_ad9361   │   <- deserializes LVDS, exposes clean
                     │               │      per-channel 16-bit I/Q ports
                     └──┬────────▲───┘
              RX:  adc_data_i0/q0/i1/q1   TX:  dac_data_i0/q0/i1/q1
                        │                        ▲
                 ┌──────▼──────┐          ┌───────┴───────┐
                 │ adc_fifo     │          │  dac_fifo      │  clock-domain
                 │ (util_wfifo) │          │ (util_rfifo)   │  crossing
                 └──────┬──────┘          └───────▲───────┘
                        │                          │
                 ┌──────▼──────┐          ┌───────┴───────┐
                 │ adc_pack     │          │  dac_upack     │  interleave/
                 │(util_cpack2) │          │ (util_upack2)  │  deinterleave
                 └──────┬──────┘          └───────▲───────┘
                        │                          │
                 ┌──────▼──────┐          ┌───────┴───────┐
                 │ adc_dma      │          │  dac_dma       │  AXI-DMA
                 │ (axi_dmac)   │          │ (axi_dmac)     │  to/from DDR
                 └──────────────┘          └────────────────┘
```

**Insertion point:** the raw per-channel I/Q buses right at the
`axi_ad9361` boundary, *before* the pack/unpack stage — this is where
Analog Devices' own application notes for adding custom IP to AD9361
designs recommend working, since you get clean, full-resolution,
per-channel samples rather than the interleaved multi-channel word that
exists after `util_cpack2`/`util_upack2`.

- **RX path**: break the existing connections
  `axi_ad9361/adc_data_i0` → `util_ad9361_adc_fifo/din_data_0` (and the
  `_q0`, `_i1`, `_q1` siblings, each paired with
  `adc_valid_*`/`adc_enable_*`), and insert your custom block (filter,
  decimator, DDC, …) in between, feeding the FIFO exactly as before.
- **TX path**, mirror image: break
  `axi_ad9361_dac_fifo/dout_data_0..3` → `axi_ad9361/dac_data_i0/q0/i1/q1`
  and insert your custom TX processing there.

Both signal groups run on `axi_ad9361/l_clk` (the AD9361 interface clock),
before the divided sample-rate clock domain used downstream — keep that in
mind if your block needs to be clocked differently.

---

## Working with Vivado on this project — tips, gotchas, useful IP

- **Version**: this repo's Vivado toolchain target is **2022.2**; the
  project's native format is 2021.1. Opening it triggers an automatic
  IP/project upgrade — routine (revision bumps like Block Memory Generator
  8.4 rev4→rev5). If you ever see an IP marked "locked" in the IP catalog,
  re-run `upgrade_ip [get_ips]`.
- **Known timing violation, not yet fixed**: the last full build had **one
  failing timing path** (WNS ‑0.877 ns) on
  `axi_ad9361/inst/i_tdd/tdd_sync_cntr_reg` (`rx_clk` domain) →
  `util_ad9361_tdd_sync/inst/sync_out_reg` (`clk_fpga_0` domain). This is a
  clock-domain crossing for AD9361 **TDD sync**, a feature not used in this
  design's normal FDD/continuous-duplex mode — almost certainly a missing
  `set_false_path` in ADI's stock constraints, not a functional bug. It has
  not caused observed problems, but it's a real unmet constraint; check
  `timing.rpt` after every build and don't assume it's gone.
- **`xsct`'s FSBL build completion signal isn't reliable** — observed
  during development: the compiled FSBL was missing the just-injected
  earmark string immediately after `xsct gen_fsbl_build.tcl` returned (even
  after a 10-second poll), yet was present moments later with no further
  action taken, with the file's own md5 stable throughout. Root cause not
  fully isolated (suspected async Eclipse/CDT job scheduling inside
  `xsct`), so `build_all.sh` routes around it entirely by invoking the real
  underlying `make -C fsbl/Debug` directly afterward — deterministic, fast
  no-op if `xsct`'s build was already current.
- **`system_top/zynq_fsbl/fsbl.elf` is a stale, stripped copy** —
  `app build` doesn't refresh it after the initial scaffold (confirmed by
  checksum/`strings` diff). Always take the ELF from `fsbl/Debug/fsbl.elf`.
- **A `pipefail` + `grep -q` gotcha, if you write your own verification
  checks**: `if strings file | grep -qF "$TAG"; then` is broken under
  `set -o pipefail` (which `build_all.sh` uses) even when grep genuinely
  finds the match — `grep -q` exits the instant it finds a match, which can
  `SIGPIPE` the still-writing `strings` process, and `pipefail` then
  reports that `SIGPIPE` as the pipeline's exit status instead of grep's
  own success. Fix: capture `strings`' output into a variable first (no
  concurrent reader/writer, so no `SIGPIPE` is possible), then `grep` that.
- **Where the reports land**: `build_all.sh`/`build.tcl` write
  `utilization.rpt` and `timing.rpt` into `hdl/projects/fmcomms2/zc706/`
  after every build — always skim `timing.rpt`'s "Design Timing Summary"
  for new failing paths before flashing.
- **JTAG bring-up without touching the SD card**: for quick iteration you
  don't need to repackage `BOOT.BIN` at all — open **Hardware Manager** in
  Vivado, connect to the board over its USB-JTAG port, and "Program Device"
  directly with the freshly built `system_top.bit`. Much faster than a full
  SD-card cycle when iterating on PL logic, but it's volatile (lost on
  power-cycle) and doesn't touch u-boot/Linux — use it to validate your
  custom HDL block before doing a full rebuild.
- **Interesting IP already in this design, worth knowing about for your own
  additions**:
  - `axi_dmac` — the AXI-DMA controller ADI uses everywhere for streaming
    ADC/DAC data to/from DDR; if your custom block needs its own separate
    DMA channel (e.g., a side-channel capture buffer), this is the core to
    instantiate again.
  - `util_wfifo` / `util_rfifo` — simple clock-domain-crossing FIFOs; reuse
    these rather than writing your own CDC logic when your custom block
    runs at a different clock than `axi_ad9361/l_clk`.
  - `util_cpack2` / `util_upack2` — channel interleavers; only needed if
    you're feeding the DMA path directly.
  - `util_tdd_sync` — the TDD synchronization core mentioned in the timing
    caveat above; if you're not using TDD mode, its
    `tdd_sync_cntr_reg`→`sync_out_reg` path is likely safe to add a
    `set_false_path` constraint on rather than trying to actually close
    timing on it.
  - `axi_sysid` / `sysid_rom` — a read-only system-ID register block ADI
    uses to stamp the bitstream with a build identifier string; a second,
    lower-level alternative to the FSBL earmark above if you'd rather the
    identifier live in the PL fabric itself, readable via
    `devmem 0x45000800` (mapped at `0x45000000` + register offset `0x200`,
    per `docs/regmap/adi_regmap_system_id.txt`) without needing a serial
    console.

---

## Notes on how this baseline was assembled

- `hdl/` is the developer-provided `fmcomms2_zc706` project (full Analog
  Devices HDL library + this board's project), copied from the board's
  accessory files, minus stray Vivado journal/log noise from prior
  interactive sessions.
- `firmware/uboot-raw.bin` and `firmware/system.dtb` were extracted from
  the existing, known-working `BOOT.BIN` (from the board's Firmware 2
  package) using `bootgen -arch zynq -read BOOT.BIN` to find partition
  offsets, then a raw byte-range `dd`. Neither u-boot's logic nor the
  device tree needed to change — only the bitstream/FSBL needed
  regenerating to match a fresh HDL build — so these are reused as-is.
- **Important quirk**: `bootgen` strips the ELF envelope when it stores a
  partition inside `BOOT.BIN`, so `uboot-raw.bin` is bare ARM machine code
  (starts with a branch instruction), not a valid ELF file.
  `firmware/uboot_wrap.ld` re-wraps it into a minimal valid ELF at its
  original load/exec address (`0x04000000`, confirmed from the original
  partition header) so `bootgen` will accept it again as an input
  partition. Already handled by `build_all.sh`.
- The resulting `BOOT.BIN` was verified byte-identical in size (5,142,172
  bytes) and partition structure/order to the original working firmware
  image, and has been field-tested: flashed to the board and confirmed
  working via the build-earmark string over serial console.
