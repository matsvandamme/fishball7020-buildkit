# Fishball7020 SDR — Firmware Build Kit

A reproducible build system for the **"7020-SDR"** board's factory-default
firmware — a Zynq XC7Z020-CLG400 + AD9361 board derived from ADALM-PLUTO
with a dual TX/RX FMCOMMS2-style front end, sold via the Xianyu (闲鱼) shop
**"唐朝击剑鱼丸"** ("Tang Dynasty Fencing Fishball" — hence "Fishball7020"),
also distributed as **"PlutoSky"** by OpenSourceSDRLab
([`OpenSourceSDRLab/PlutoSky_7020_AD936X_SDR`](https://github.com/OpenSourceSDRLab/PlutoSky_7020_AD936X_SDR)
on GitHub — confirmed the genuine upstream for this board's factory
firmware by byte-for-byte checksum match).

See [`firmware-1-pluto-usb-ethernet/README.md`](firmware-1-pluto-usb-ethernet/README.md)
for the full story: this build reproduces the board's real, day-to-day
firmware (USB + Ethernet), verified file-by-file against a working unit —
`devicetree.dtb` builds byte-for-byte identical, `uEnv.txt` and the root
filesystem are content-identical, and the kernel/bootloader are within a
few hundred bytes of identical (the small remaining gap is explained in
detail there, and isn't recoverable from public sources).

## Quick start

```bash
cd firmware-1-pluto-usb-ethernet
./scripts/setup.sh      # clone upstream source + apply patches (once)
./scripts/build_all.sh
```

This needs the shared Vivado 2022.2 environment at
[`tools/env-vivado.sh`](tools/env-vivado.sh) (sourced automatically by
`build_all.sh`).

## Repository layout

```
fishball7020-sdr-firmware/
├── README.md                          this file
├── LICENSE                            multiple licenses apply - see below
├── tools/
│   └── env-vivado.sh                  Vivado 2022.2 environment fix (see below)
└── firmware-1-pluto-usb-ethernet/     factory-default firmware (USB + Ethernet)
    ├── README.md
    ├── patches/                       fixes against the public upstream fork
    └── scripts/                       setup.sh + build_all.sh
```

## Why a Vivado environment fix is needed

Vivado 2022.2's bundled binaries are linked against `libtinfo.so.5`,
`libncurses.so.5`, and `libssl.so.1.1`/`libcrypto.so.1.1` — libraries recent
Ubuntu releases (26.04+) no longer ship. `tools/env-vivado.sh` prepends
locally-vendored copies of exactly these libraries to `LD_LIBRARY_PATH`
before sourcing Vivado's own `settings64.sh`, without touching anything
system-wide. Source it (`source tools/env-vivado.sh`) instead of Vivado's
`settings64.sh` directly, in any shell where you'll run `vivado`, `xsct`,
or `bootgen`.

You'll also need, alongside your system's default GCC: `gcc-13`/`g++-13`
(one legacy Buildroot host tool, `host-m4`, doesn't build under GCC ≥14's
stricter default C standard — see the firmware README for where this is
used), `bison`, `flex`, `dtc`, `mkimage` (u-boot-tools), `bootgen` (ships
with Vivado/Vitis), and Vitis 2022.2 for `xsct`.

## How this repo came to exist

This firmware was reverse-engineered and rebuilt from scratch, starting
from the public upstream fork
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

See the firmware README for the complete list of fixes found and applied,
including two genuine upstream bugs (hardcoded debug leftovers) discovered
and corrected along the way.
