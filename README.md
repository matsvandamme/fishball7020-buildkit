# Fishball7020 SDR — Firmware & HDL Build Kit

Reproducible build systems for the **"7020-SDR"** board — a Zynq
XC7Z020-CLG400 + AD9361 board derived from ADALM-PLUTO with a dual TX/RX
FMCOMMS2-style front end, sold via the Xianyu (闲鱼) shop
**"唐朝击剑鱼丸"** ("Tang Dynasty Fencing Fishball" — hence "Fishball7020"),
also distributed as **"PlutoSky"** by OpenSourceSDRLab
([`OpenSourceSDRLab/PlutoSky_7020_AD936X_SDR`](https://github.com/OpenSourceSDRLab/PlutoSky_7020_AD936X_SDR)
on GitHub — confirmed the genuine upstream for this board's factory
firmware by byte-for-byte checksum match, see below).

The board ships with **two independent firmware options**, and this repo
has a fully verified, working build system for both:

| | [`firmware-1-pluto-usb-ethernet/`](firmware-1-pluto-usb-ethernet/) | [`firmware-2-zc706-ethernet-only/`](firmware-2-zc706-ethernet-only/) |
|---|---|---|
| **What it is** | The board's actual **factory-default** firmware | An **alternate** firmware, Ethernet-only |
| **Connectivity** | USB + Ethernet | Ethernet only |
| **Based on** | ADI's `plutosdr-fw` (Pluto lineage), retargeted | ADI's `fmcomms2`/`zc706` reference design, retargeted |
| **HDL project source** | Not shipped by the vendor as editable source — this repo's `patches/` reconstruct it against the public upstream fork | **Shipped as a full editable Vivado project** by the board's developer |
| **Boot layout** | Single FAT32 partition, classic Pluto ramdisk-root | Two partitions: FAT32 boot + EXT4 rootfs |
| **Best used for** | Reproducing/understanding the board's real day-to-day firmware | **Writing your own custom PL HDL** — verified pin-correct, buildable, flashable, field-tested |

Read each directory's own README for full detail. The short version: if you
want to modify the FPGA fabric and get new firmware onto the board, start
with `firmware-2-zc706-ethernet-only/` — it's the one with real, editable,
verified-correct HDL source. `firmware-1-pluto-usb-ethernet/` exists to
reproduce and understand the firmware you're actually running day to day
(and reproduces it almost perfectly — see its README for exactly how close,
and why the last few bytes are unrecoverable from public sources).

## Quick start

```bash
# Firmware 2 (recommended starting point for custom HDL work)
cd firmware-2-zc706-ethernet-only
./scripts/build_all.sh

# Firmware 1 (reproduces the actual factory firmware)
cd firmware-1-pluto-usb-ethernet
./scripts/setup.sh      # clone upstream source + apply patches (once)
./scripts/build_all.sh
```

Both need the shared Vivado 2022.2 environment at [`tools/env-vivado.sh`](tools/env-vivado.sh)
sourced first (each `build_all.sh` does this for you automatically).

## Repository layout

```
fishball7020-sdr-firmware/
├── README.md                          this file
├── LICENSE                            multiple licenses apply - see below
├── tools/
│   └── env-vivado.sh                  Vivado 2022.2 environment fix (see below)
├── firmware-1-pluto-usb-ethernet/     factory-default firmware (USB + Ethernet)
│   ├── README.md
│   ├── patches/                       fixes against the public upstream fork
│   └── scripts/                       setup.sh + build_all.sh
└── firmware-2-zc706-ethernet-only/    alternate firmware (Ethernet only)
    ├── README.md
    ├── hdl/                           full editable Vivado project + ADI HDL library
    ├── firmware/                      u-boot/devicetree components + repackaging recipe
    └── scripts/                       build_all.sh + FSBL build-earmark tooling
```

## Why a shared Vivado environment fix is needed

Vivado 2022.2's bundled binaries are linked against `libtinfo.so.5`,
`libncurses.so.5`, and `libssl.so.1.1`/`libcrypto.so.1.1` — libraries recent
Ubuntu releases (26.04+) no longer ship. `tools/env-vivado.sh` prepends
locally-vendored copies of exactly these libraries to `LD_LIBRARY_PATH`
before sourcing Vivado's own `settings64.sh`, without touching anything
system-wide. Source it (`source tools/env-vivado.sh`) instead of Vivado's
`settings64.sh` directly, in any shell where you'll run `vivado`, `xsct`,
or `bootgen`.

You'll also need, alongside your system's default GCC:
`gcc-13`/`g++-13` (one legacy Buildroot host tool, `host-m4`, doesn't build
under GCC ≥14's stricter default C standard — see Firmware 1's README for
where this is used), `bison`, `flex`, `dtc`, `mkimage` (u-boot-tools),
`bootgen` (ships with Vivado/Vitis), and Vitis 2022.2 for `xsct`.

## How this repo came to exist

Both firmware options were reverse-engineered and rebuilt from scratch this
session, starting from a folder of accessory files (schematics, PCB layout,
prebuilt SD-card images, and — for Firmware 2 only — a full Vivado project)
that shipped with a physical unit, cross-referenced against:

- The board's real schematic, to verify every pin constraint by hand before
  trusting any HDL project as "correct for this board" (several candidates
  turned out to target *different*, similarly-named boards despite
  compiling successfully — a real trap, documented in each firmware's
  README).
- A byte-for-byte checksum comparison against
  [`OpenSourceSDRLab/PlutoSky_7020_AD936X_SDR`](https://github.com/OpenSourceSDRLab/PlutoSky_7020_AD936X_SDR),
  which confirmed that repo as the genuine vendor source for Firmware 1's
  prebuilt binaries (though not its editable HDL/kernel source, which was
  never published there — only prebuilt artifacts).
- An extracted `IKCONFIG`/embedded kernel `.config` and kernel version
  banner pulled directly out of the real working firmware's compiled kernel
  image, used to prove the Firmware 1 rebuild's kernel configuration is
  provably identical to the original, not just "close."

Both firmwares have been built end-to-end and, for Firmware 2, flashed to
real hardware and confirmed booting via a build-identifying string printed
to the serial console at boot.
