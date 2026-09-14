---
name: fishball7020-firmware
description: Build, flash, verify and safely transmit with the Fishball7020 / PlutoSky SDR (Zynq XC7Z020 + AD9361). Use when working in this repository on FPGA/HDL changes, kernel or device tree patches, BOOT.bin, flashing the board, or anything involving its transmitter, RF loopback, or libiio/iiod access. Encodes rules that are expensive to rediscover - flash only via the SD partition, delete the Vivado project before an HDL change, simulate before synthesising, and check /mnt/jffs2 before believing anything about the firmware.
license: MIT
compatibility: Requires the board reachable over its USB Ethernet gadget (default ip:192.168.2.1). HDL builds need Vivado/Vitis 2022.2; simulation needs only iverilog.
metadata:
  repository: fishball7020-fpga-devkit
---

# Working on the Fishball7020 / PlutoSky

A reverse-engineered, buildable firmware for a board sold as Fishball7020,
PlutoSky R1 and several other names. Zynq XC7Z020 + AD9361, and **some units
carry a power amplifier** — which changes the safety arithmetic completely.

Read [references/rf-safety.md](references/rf-safety.md) before anything that
transmits, and [references/build-and-flash.md](references/build-and-flash.md)
for the exact command sequences.

## The rules that cost the most to rediscover

**Flash via the SD partition. Never via DFU.** DFU on this board has bricked
units. Mount `/dev/mmcblk0p1` on the board over ssh, copy the five files, sync,
reboot. The exact sequence is in the reference.

**Delete the Vivado project before any HDL or coefficient change.**
`build_hdl.tcl` reuses an existing `pluto.xpr` rather than re-running
`system_bd.tcl`, so a changed block design or `.coe` is *silently ignored* and
you flash the old bitstream:

```bash
rm -rf src/hdl/projects/pluto/pluto.{xpr,cache,gen,hw,ip_user_files,runs,sim,srcs,sdk}
```

**Simulate before you synthesise.** `./sim/run_sim.sh` checks the custom HDL
against a golden model in about a second. A Vivado build is ~20 minutes with
`--hdl-only` and ~70 from cold, and synthesis cannot tell you the logic
computes the wrong thing. `./sim/run_sim.sh --mutate` proves the testbench can
still fail.

**Verify the build before you flash it.** `./scripts/verify_output.sh` checks
the five SD-card files, that the bitstream is compressed (an uncompressed one
overflows the FSBL's OCM and BOOT.bin fails to boot with no message), and that
no endpoint fails timing. It prints the DSP count and whether `rx_ddc` is
wired, so you can see your change actually landed.

**When the radio misbehaves, check `/mnt/jffs2` first.** It is the one
writable, persistent partition, and `/mnt/jffs2/autorun.sh` runs at every boot.
Scripts there survive reflashing the kernel, device tree and bitstream, appear
nowhere in the firmware source, and can rewrite IIO attributes underneath your
application. `tools/selftest/sdr_selftest.py --ssh` lists what is there.
Three kernel rebuilds were once spent chasing a "firmware bug" that was a
script on this partition.

**Do not change the device tree without a strong reason.** `devicetree.dtb`
currently builds byte-for-byte identical to the factory unit's, which is a
load-bearing provenance claim in the README. Most things people reach for the
device tree for can be done in `S21misc` or in the driver instead.

## Where things are

| | |
|---|---|
| `firmware/patches/` | what makes this board's firmware; applied by `setup.sh` |
| `firmware/patches/optional/` | worked examples, **not** applied by default |
| `firmware/src/` | upstream source, created by `setup.sh`, not committed |
| `firmware/output/` | the five SD-card files |
| `firmware/sim/` | Icarus Verilog testbenches for the custom HDL |
| `firmware/scripts/verify_output.sh` | pre-flash sanity check |
| `tools/selftest/` | is the board damaged? measures and says |

## Typical loops

**Changing HDL** — edit, `./sim/run_sim.sh`, delete the project, `./scripts/build_all.sh --hdl-only`,
`./scripts/verify_output.sh`, flash, `tools/selftest/sdr_selftest.py --ssh`.

**Changing the kernel** — edit `src/linux/`, rebuild `uImage` only (a few
minutes; the full `build_all.sh` is not needed), flash just `uImage`, reboot.
Then fold the change into a numbered patch in `firmware/patches/` so a fresh
clone gets it, and add an assertion to `.github/workflows/verify-patches.yml`.

**Diagnosing the radio** — `tools/selftest/sdr_selftest.py --ssh` first; it is
read-only and never transmits. Add `--loopback --pad <dB>` only with a cable
and an attenuator fitted.

## Talking to the board

- ssh: `root@192.168.2.1`, password `analog`. Busybox: **no `pkill`**, no
  `ftrace`. Use `ps` and `kill`.
- libiio network protocol on port 30431. `tools/selftest/iiod_min.py` speaks it
  with the standard library alone — no `pylibiio` needed.
- Opening the board in SDRangel or similar takes over the USB gadget, and
  `192.168.2.1` stops answering until that application closes.
- `dmesg` is the first place to look, and it is usually empty — which is itself
  information: it means the kernel is not doing what you suspect.
