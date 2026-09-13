# How it works: from power-on to a running radio

This explains what the five files on the SD card actually *are*, and what
happens in the seconds between plugging the board in and getting a login
prompt. No prior FPGA or embedded-Linux knowledge assumed.

If you only want to build and flash, you don't need any of this — see the
[README](../README.md). This is for when you want to understand *why* the
build has seven stages, or why changing one line of HDL means rebuilding a
file called `BOOT.bin`.

---

## Why this is more complicated than a PC

On a laptop, starting up is somebody else's problem. Firmware you never
wrote (BIOS/UEFI) initialises the hardware, finds your disk, and starts
your operating system. The hardware itself is fixed — soldered down at the
factory.

On this board, **you build every layer, including the hardware**. That's
the whole point of it, and it's why there's a chain of stages instead of
one image.

## The chip has two halves

The Zynq-7020 at the centre of the board is two different things sharing
one package:

- **The PS — "Processing System".** An ordinary little ARM computer: two
  CPU cores, a memory controller, USB, Ethernet, SD card, serial ports.
  This is what runs Linux.
- **The PL — "Programmable Logic".** An **FPGA**: a sea of blank, generic
  logic elements with wires between them that can be reconfigured.

An FPGA is worth pausing on if it's new to you. A CPU is fixed silicon that
*reads instructions* and does what they say. An FPGA has no instructions —
instead you describe a **circuit**, and the fabric physically rearranges
itself to *become* that circuit. That's why FPGA work is called "hardware
design" even though you never pick up a soldering iron.

The description you write (in a language like Verilog, or by wiring blocks
together in Vivado) gets compiled into a **bitstream**: a big blob of
configuration data that says which logic elements do what and which wires
connect where. Loading a bitstream is how a blank FPGA becomes *your*
design.

On this board the PL is where the radio lives: the interface to the AD9361
chip, the DMA engines that shovel samples into memory, the digital filters.
When you add your own HDL, this is what you're changing.

## The chain

```
 BootROM  ──►  FSBL  ──►  bitstream  ──►  U-Boot  ──►  kernel + DTB  ──►  root filesystem
(in silicon)  (on-chip     (into the      (in DDR)      (in DDR)           (in RAM)
               RAM)         FPGA)
```

Each link exists because the previous one **physically cannot** do the next
job. That's the key to understanding the whole thing.

### 1. BootROM — burned into the chip

A tiny program etched into the silicon at the factory. You can't build,
change, or read it. At power-on it checks some pins to see where it should
boot from (here: the SD card), finds `BOOT.bin`, and copies the first piece
of it into **OCM** — a small block of memory inside the chip itself, only
256 KB.

*Why doesn't it just load Linux?* Two reasons. 256 KB isn't remotely enough
— the kernel alone is 4.5 MB. And the board's main memory (the **DDR** RAM
chips, 1 GB here) **does not work yet**. DDR is not like a USB stick; the
memory controller needs a long list of precise timing parameters configured
before a single byte can be stored. Nothing has done that yet.

So BootROM does the only thing it can: load something small into the little
memory that *does* work.

### 2. FSBL — "First Stage Bootloader"

A small bare-metal ARM program (no operating system under it) that fits in
those 256 KB. It does three things, in this order:

1. **`ps7_init`** — configures the DDR controller, the clocks, and the pin
   multiplexing. *After this step, main memory exists.* Almost everything
   else depends on it.
2. **Loads the bitstream into the PL** — the FPGA stops being blank and
   becomes your radio design.
3. **`ps7_post_config`** — switches on the "level shifters" between the PS
   and the PL. The two halves run at different voltages, so the electrical
   bridges between them are held off until the FPGA is configured. Until
   this runs, the ARM side can't talk to your logic.

Then it loads the next stage into the now-working DDR and jumps to it.

> That ordering — configure PS, load bitstream, *then* post-config — is
> exactly why the JTAG procedure in the README looks the way it does.

### 3. U-Boot — the bootloader you can actually talk to

Now running in DDR with room to breathe, U-Boot is a proper bootloader with
device drivers (SD, Ethernet, USB), a command prompt, and a scripting
language. This is the `Zynq>` prompt you reach by pressing a key during
boot.

Its job is to find and load the operating system. It reads `uEnv.txt` for
settings, pulls three files off the SD card into memory, patches the
hardware description on the fly (for things like MAC addresses), and jumps
into the kernel.

### 4. The kernel — Linux itself

`uImage` is the Linux kernel with a small U-Boot header glued on the front
recording where to load it and a checksum. Linux 5.15, in this case.

### 5. The device tree — how Linux knows what hardware exists

On a PC, the operating system can *ask*: PCI and USB devices announce
themselves. On an embedded chip like this, **nothing announces anything**.
There is no way for Linux to discover that an AD9361 radio chip is wired to
SPI port 0, or that DMA engines live at memory address `0x7c400000`.

So it's told, by a file: the **device tree** (`devicetree.dtb`, "dtb" =
device tree blob). It contains no code — it's a structured description of
every piece of hardware and where to find it.

This has an important consequence: **the device tree must match the
bitstream**. The bitstream decides what really exists in the FPGA; the
device tree tells Linux what to expect. Change the hardware and the
description may need to change too, or Linux will look for something that
isn't there.

### 6. The root filesystem — userspace

`uramdisk.image.gz` holds everything above the kernel: `/bin`, `/etc`, the
startup scripts, and the radio software (`libiio`, `iiod`) that lets your
PC stream samples. It's built by **Buildroot**, a tool that compiles a
complete miniature Linux distribution from source.

It's a **ramdisk**: the whole filesystem is decompressed into RAM at boot
and lives there. Fast and robust — but it means **changes you make on the
board are lost when you reboot**, unless written to the small separate
flash partition mounted at `/mnt/jffs2`.

---

## Your five SD-card files

| File | What it is |
|---|---|
| `BOOT.bin` | **FSBL + bitstream + U-Boot**, packaged into one file |
| `uImage` | The Linux kernel |
| `devicetree.dtb` | The description of what hardware exists |
| `uramdisk.image.gz` | The root filesystem (userspace) |
| `uEnv.txt` | U-Boot settings, read at boot |

The one that catches people out is **`BOOT.bin` containing three separate
things**. A tool called `bootgen` staples them together, because BootROM
expects to find exactly one file in a specific format.

That single fact explains a limitation elsewhere in the README: updating
over USB (DFU) can replace the kernel, device tree and filesystem, but
**not** `BOOT.bin`. So any change to your FPGA design needs an SD card —
DFU cannot help you.

## What to rebuild when you change something

| You changed… | Which file changes | Can you use DFU? |
|---|---|---|
| HDL / block design | `BOOT.bin` (contains the bitstream) | ❌ SD card only |
| Kernel config or a driver | `uImage` | ✅ |
| Userspace, packages, init scripts | `uramdisk.image.gz` | ✅ |
| Hardware description | `devicetree.dtb` | ✅ |
| Boot settings | `uEnv.txt` | ❌ (edit on the card) |

`build_all.sh`'s seven stages are simply this chain in dependency order:
HDL → bitstream → FSBL (which needs the bitstream) → U-Boot → kernel →
root filesystem → package it all into `BOOT.bin`.

## Watching it happen

Connect the serial console (README [step 7](../README.md#7-verify-your-build-is-actually-running))
and you can watch every stage announce itself:

```
U-Boot PlutoSDR (Sep 12 2026 - 15:31:07 +0200)   ← stage 3: FSBL has run,
DRAM:  ECC disabled 1 GiB                           DDR works, U-Boot is alive

reading uImage                                    ← stage 3 loading stage 4
4541632 bytes read in 417 ms
reading devicetree.dtb
22516 bytes read in 18 ms
reading uramdisk.image.gz
6757376 bytes read in 611 ms

## Booting kernel from Legacy Image at 02080000 ...
   Image Name:   Linux-5.15.0
Starting kernel ...                               ← handover to Linux

Linux version 5.15.0 ...                          ← stage 4 running
OF: fdt: Machine model: FISH Ball PlutoSDR Rev.A  ← read from the device tree
ad9361 spi0.0: ad9361_probe : AD936x Rev 0        ← Linux finds the radio,
                successfully initialized             because the DTB told it to look

Welcome to Pluto                                  ← stage 6: userspace is up
fishball7020 login:
```

Everything before `Starting kernel ...` happened in the bootloaders; the
`ad9361` line is Linux talking to hardware that only exists because the
bitstream configured the FPGA a couple of seconds earlier.
