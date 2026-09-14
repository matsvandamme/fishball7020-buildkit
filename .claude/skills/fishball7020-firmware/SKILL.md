---
name: fishball7020-firmware
description: Build, flash, measure and safely transmit with the Fishball7020 / PlutoSky SDR (Zynq XC7Z020 + AD9361, sold also as PlutoSky R1 and 7020-SDR). Use for FPGA and HDL changes, Vivado block-design work, kernel and device-tree patches, BOOT.bin and bitstreams, flashing, libiio/iiod and sysfs access, IQ capture, transmitting, RF loopback measurement, AD9361 gain tables and ENSM, TX muting, and diagnosing a board that misbehaves. Encodes rules that are expensive to rediscover - flash only via the SD partition and never DFU, delete the Vivado project before an HDL change or the build silently reuses the old one, simulate before synthesising, and check /mnt/jffs2 before believing anything about the firmware.
license: MIT
compatibility: Board reached over its USB Ethernet gadget (default ip:192.168.2.1). HDL builds need Vivado/Vitis 2022.2; HDL simulation needs only iverilog; the host tools need Python 3.8 and nothing else.
metadata:
  repository: fishball7020-fpga-devkit
  board: Fishball7020 / PlutoSky R1 (XC7Z020 + AD9361)
---

# Working on the Fishball7020 / PlutoSky

A reverse-engineered, buildable firmware for a board sold under several names.
Zynq XC7Z020 + AD9361, two transmit and two receive chains, and — on the common
variant — **a power amplifier**, which changes the safety arithmetic that most
Pluto advice assumes.

Depth lives in `references/`; load only what the task needs.

| | |
|---|---|
| [`rf-safety.md`](references/rf-safety.md) | **Read before anything transmits.** The PA, the power budget, TX muting |
| [`build-and-flash.md`](references/build-and-flash.md) | Exact command sequences for building, flashing, recovering |
| [`ad9361-gain-tables.md`](references/ad9361-gain-tables.md) | Why gain in dB is not gain in dB, and where the discontinuities are |
| [`measuring.md`](references/measuring.md) | The self-test, baselines, and what is a property of the board versus the cable |
| [`talking-to-the-board.md`](references/talking-to-the-board.md) | libiio/IIOD, sysfs, debugfs, and what busybox does not have |
| [`debugging.md`](references/debugging.md) | The traps that have actually cost hours here |

## The rules

**Flash via the SD partition. Never DFU.** DFU has bricked units. Mount
`/dev/mmcblk0p1` on the board over ssh, copy, sync, reboot. Copy only what
changed — `uImage` alone for a kernel change, `BOOT.bin` alone for HDL.

**Delete the Vivado project before any HDL or coefficient change.**
`build_hdl.tcl` reuses an existing `pluto.xpr` rather than re-running
`system_bd.tcl`, so a changed block design or `.coe` is *silently ignored* and
you flash the old bitstream:

```bash
rm -rf src/hdl/projects/pluto/pluto.{xpr,cache,gen,hw,ip_user_files,runs,sim,srcs,sdk}
```

**Simulate before you synthesise.** `./sim/run_sim.sh` checks the custom HDL
against a golden model in about a second; a Vivado build is 20 minutes with
`--hdl-only` and 70 from cold, and synthesis cannot tell you the logic computes
the wrong thing. `--mutate` proves the testbench can still fail.

**Verify before you flash.** `./scripts/verify_output.sh` checks the five files,
that the bitstream is compressed (an uncompressed one overflows the FSBL's OCM
and BOOT.bin fails to boot with no message), and that timing is met. It prints
the DSP count and which coefficients are in use, so you can see your change
landed.

**When the radio misbehaves, check `/mnt/jffs2` first.** It is the one writable,
persistent partition, and `/mnt/jffs2/autorun.sh` runs at every boot. Scripts
there survive reflashing the kernel, device tree and bitstream, appear nowhere
in the firmware source, and can rewrite IIO attributes underneath an
application. Three kernel rebuilds were once spent chasing a "firmware bug" that
was a script on this partition. `sdr_selftest.py --ssh` lists what is there.

**Do not change the device tree without a strong reason.** `devicetree.dtb`
builds byte-for-byte identical to the factory board's, which is a load-bearing
provenance claim. Most things people reach for it for belong in `S21misc` or in
the driver.

**A loopback without an attenuator destroys the receiver.** The RX input is
rated to about +2.5 dBm; this board measures **+19 dBm** flat out. Fit at least
20 dB; 40–50 dB is comfortable. Details in `rf-safety.md`.

## Where things are

| | |
|---|---|
| `firmware/patches/` | what makes this board's firmware; `setup.sh` applies these |
| `firmware/patches/optional/` | worked examples, **not** applied by default |
| `firmware/src/` | upstream source, created by `setup.sh`, not committed |
| `firmware/output/` | the five SD-card files |
| `firmware/sim/` | Icarus Verilog testbenches for the custom HDL |
| `firmware/scripts/verify_output.sh` | pre-flash sanity check |
| `tools/selftest/` | is the board damaged? measures and says |
| `docs/block-design.md` | the stock Vivado project, IP by IP |
| `docs/measured-performance.md` | what one board actually does |

The patches, in order: `0001` fixes and a persistent serial; `0002` the device
tree; `0004` mute TX when no DMA stream; `0005` stop the unmute overwriting a
gain set before the stream. `optional/0003` is the FM channelizer.

## Typical work

**An HDL change** — edit, `./sim/run_sim.sh`, delete the Vivado project,
`./scripts/build_all.sh --hdl-only`, `./scripts/verify_output.sh`, flash
`BOOT.bin`, then `sdr_selftest.py --ssh`.

**A kernel change** — edit `src/linux/`, rebuild `uImage` alone (a few minutes;
the full `build_all.sh` is not needed), flash `uImage`, reboot. Then fold the
change into a numbered patch in `firmware/patches/` so a fresh clone gets it,
and add an assertion to `.github/workflows/verify-patches.yml`.

**Diagnosing the radio** — `sdr_selftest.py --ssh` first: read-only, never
transmits, and it reports supply rails, die temperatures, the AD9361 interface
eye, the internal digital loopback and the receiver. Add `--loopback --pad <dB>`
only with a cable and attenuator fitted.

**Before a release** — a clean-clone end-to-end build. This is not ceremony: the
v1.1 run found three defects in the build's own self-repair path, every one of
which would have stopped the next person building from a fresh clone.

## What a healthy board looks like

Measured on one unit, so treat as indicative rather than specification. Useful
for judging whether something is actually wrong.

| | |
|---|---|
| Gain slopes (TX attenuator, RX gain) | within **1.4% of 1.000 dB/dB** |
| Image rejection, after a fresh TX quad calibration | **55–63 dBc** (41–48 as found) |
| Harmonics | **−67 to −79 dBc** |
| Transmit power flat out | **+19 dBm** |
| TX mute depth | **63–70 dB** |
| Loop gain, 200 MHz – 1 GHz | ~**+20 dB** through a 20 dB pad |
| Supply rails | all six within **1.1%** of nominal |
| Digital interface eye | **157–181** of 256 delay positions pass |
| FPGA, stock build | 72/220 DSP48s, WNS **+0.214 ns** |

Two channels on one board differed by 1.5 dB in receive and 0.25 dB in
transmit, so some asymmetry is normal.
