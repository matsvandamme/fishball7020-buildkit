# The stock block design: what's in it, how it connects, what you can change

This is a guided tour of the Vivado design the devkit builds — the one
`firmware/src/hdl/projects/pluto/system_bd.tcl` generates when
`system_project.tcl` runs. Read it before changing HDL: most of the traps in
this design are about *how the pieces are wired*, not about any one piece.

Everything below is read straight from `system_bd.tcl`, `system_top.v` and
`system_constr.xdc`. Line numbers refer to the stock (channelizer-free) tree.

- [The picture](#the-picture)
- [The IP blocks, one by one](#the-ip-blocks-one-by-one)
- [Clocks and resets](#clocks-and-resets)
- [Address map and interrupts](#address-map-and-interrupts)
- [The receive path, sample by sample](#the-receive-path-sample-by-sample)
- [The transmit path](#the-transmit-path)
- [What you can change](#what-you-can-change) · [What not to touch](#what-not-to-touch)
- [How a change becomes firmware](#how-a-change-becomes-firmware)

## The picture

```
                       AD9361 (LVDS, 6 data lanes each way)
                     rx_clk/frame/data   tx_clk/frame/data   enable  txnrx
                            │                    ▲              ▲       ▲
                            ▼                    │              │       │
                  ┌─────────────────────────────────────────────────────┐
                  │              axi_ad9361  (ID 0, LVDS, 2R2T)          │  0x7902_0000
                  │  adc_data_i0/q0  adc_data_i1/q1   dac_*   l_clk      │
                  └───┬────┬─────────┬────┬────────────▲──▲──────┬──────┘
   channel 0 ─────────┤    │         │    │            │  │      │ l_clk drives
   channel 1 ────────────────────────┤    │            │  │      │ everything below
                      ▼    ▼         │    │            │  │      ▼
              ┌──────────────┐       │    │     ┌──────────────────┐
              │rx_fir_       │       │    │     │tx_fir_           │
              │decimator ÷8  │       │    │     │interpolator ×8   │
              │(2× fir_compiler│     │    │     │(2× fir_compiler  │
              │ + bypass mux) │      │    │     │ + bypass mux)    │
              └──────┬───────┘       │    │     └───────▲──────────┘
                     │  ch0 filtered │    │ ch1 raw     │ ch0          ch1
                     ▼               ▼    ▼             │               │
              ┌────────────────────────────┐   ┌────────────────────────────┐
              │ cpack (util_cpack2)         │   │ tx_upack (util_upack2)     │
              │ 4 ch in → one 64-bit stream │   │ one 64-bit stream → 4 ch   │
              └─────────────┬──────────────┘   └──────────────▲─────────────┘
                            │ fifo_wr                          │ s_axis
                            ▼                                  │
              ┌────────────────────────┐        ┌──────────────────────────┐
              │ axi_ad9361_adc_dma      │        │ axi_ad9361_dac_dma        │
              │ (axi_dmac) 0x7C40_0000  │        │ (axi_dmac, CYCLIC)        │
              └───────────┬────────────┘        │ 0x7C42_0000               │
                          │ m_dest_axi           └──────────────▲───────────┘
                          ▼                                     │ m_src_axi
                      S_AXI_HP1  ┌─────────────────────┐  S_AXI_HP2
                                 │  sys_ps7 (Zynq PS)   │
                                 │  DDR, USB, ETH, SD,  │
                                 │  UART, QSPI, SPI0,   │
                                 │  EMIO GPIO ×18       │
                                 └─────────────────────┘
                                   │            │
                              axi_iic_main    axi_spi
                              0x4160_0000     0x7C43_0000
```

Two things to take from the picture before anything else:

**Everything in the datapath runs on `l_clk`**, the AD9361's own data clock,
not on a fabric clock. It scales with the sample rate. Any block you insert
between `axi_ad9361` and the packers has to live in that domain.

**Channel 0 and channel 1 are not symmetric.** Channel 0 passes through a
filter hierarchy with a runtime bypass; channel 1 goes straight to the packer.
The consequences of that show up in [the receive path](#the-receive-path-sample-by-sample).

## The IP blocks, one by one

### `sys_ps7` — the Zynq processing system (`system_bd.tcl:35`)

Xilinx `processing_system7`. Holds the two ARM cores that run Linux and every
hard peripheral: DDR controller (MT41K256M16, 32-bit, 1 GB), USB0 (the gadget
you talk to), ENET0 (the RJ45), SD0 (boot), UART1 (console), QSPI (the 16 MiB
flash), SPI0 via EMIO (the AD9361's control bus), and 18 EMIO GPIOs.

It exports two clocks to the fabric: `FCLK_CLK0` at 100 MHz (`sys_cpu_clk`,
the AXI-Lite control fabric) and `FCLK_CLK1` at 200 MHz (`sys_200m_clk`, used
only as the IODELAY reference for the LVDS interface). Two high-performance
slave ports, `S_AXI_HP1` and `S_AXI_HP2`, give the two DMAs direct paths to
DDR.

The DDR timing parameters (`PCW_UIPARAM_DDR_*`) are board-specific and were
tuned for this PCB. See [What not to touch](#what-not-to-touch).

### `axi_ad9361` — the transceiver interface (`system_bd.tcl:204`)

ADI's core. It owns the LVDS link to the AD9361 — clock/frame recovery on
receive, framing on transmit, and the IODELAY calibration that makes a
61.44 MHz DDR link work — and presents the result as four 16-bit sample
streams each way (`adc_data_i0/q0/i1/q1`, `dac_data_*`) with per-channel
`valid` and `enable` pins.

Parameters that matter:

| Parameter | Value | Meaning |
|---|---|---|
| `ID` | 0 | Register-visible core ID. **The Linux driver checks this** (see below) |
| `CMOS_OR_LVDS_N` | 0 | LVDS. The board is wired LVDS; this is not a choice |
| `MODE_1R1T` | 0 | Two receive + two transmit channels present |
| `ADC_INIT_DELAY` | 30 | Initial IODELAY tap for the receive lanes |

It also carries the DC-offset and I/Q-correction blocks, a PN-sequence
checker for link test, and two GPIO registers — `up_adc_gpio_out` and
`up_dac_gpio_out` — which the driver writes and which this design uses to
control the filter bypass muxes (below).

Samples are 12 bits from the converter, **sign-extended into 16** on receive
(`ad_datafmt.v`). On transmit the DAC takes the full 16-bit word. That
asymmetry is easy to get wrong when you scale things.

### `rx_fir_decimator` and `tx_fir_interpolator` — the channel-0 filters (`system_bd.tcl:235`, `:219`)

Not IP blocks as such — hierarchies built by `ad_add_decimation_filter` and
`ad_add_interpolation_filter` in `projects/common/xilinx/adi_fir_filter_bd.tcl`.
Each contains **two Xilinx `fir_compiler` instances** (one for I, one for Q),
a `sync_bits` clock-domain crossing for the enable, and per-channel
`ad_bus_mux` bypass muxes.

```tcl
ad_add_decimation_filter   "rx_fir_decimator"   8 2 1 {61.44} {61.44} <coe>
#                           name              rate n_ch paths  clk   sample
ad_add_interpolation_filter "tx_fir_interpolator" 8 2 1 {61.44} {7.68}  <coe>
```

The stock coefficients are 129 taps from `library/util_fir_int/coefile_int.coe`
— **the same file for both directions**. Editing it changes RX and TX
together, which is rarely what you want; point one side at a different file.

Don't be misled by `library/util_fir_int/` and `library/util_fir_dec/`: the
`.v` files there are dead code in this project (no `component.xml`, never
packaged). Only the `.coe` is used.

**The bypass mux is the important part.** Each hierarchy's `active` pin
selects raw pass-through (0) or filtered (1). It's driven from bit 0 of
`up_adc_gpio_out` via `decim_slice`, which Linux sets when you write the
decimated rate to `cf-ad9361-lpc`'s `sampling_frequency`. So the filter is
**bypassed by default** and only engages on request. If you retune the
coefficients and see no effect, this is why.

### `cpack` and `tx_upack` — the channel packers (`system_bd.tcl:238`, `:222`)

`util_cpack2` takes the four receive channels (two after the filter, two raw)
and packs whichever are `enable`d into one 64-bit-wide stream for the DMA.
`util_upack2` is the mirror image on transmit.

`cpack` has **one write strobe**, `fifo_wr_en`, and it's driven by
`rx_fir_decimator/valid_out_0` — channel 0's valid. Every channel is sampled
on channel 0's timing. Keep that in mind; it's the source of the channel-1
behaviour described below.

### `axi_ad9361_adc_dma` and `axi_ad9361_dac_dma` — the DMAs (`system_bd.tcl:224`, `:210`)

ADI's `axi_dmac`, one per direction, 64 bits wide on the fabric side.

- **RX:** `DMA_TYPE_SRC 2` (FIFO input from `cpack`), destination AXI-MM to
  DDR over `S_AXI_HP1`. `SYNC_TRANSFER_START` true, so a capture starts on a
  clean packer boundary.
- **TX:** source AXI-MM from DDR over `S_AXI_HP2`, `DMA_TYPE_DEST 1`
  (AXI-Stream into `tx_upack`). **`CYCLIC 1`** — the hardware can repeat a
  buffer forever without software involvement. That's what makes a cyclic
  transmit keep running after the application returns.

The DMAs are what Linux's `cf-ad9361-lpc` (RX) and `cf-ad9361-dds-core-lpc`
(TX) devices stream through.

### `axi_iic_main`, `axi_spi` — control-bus peripherals (`system_bd.tcl:175`, `:108`)

An AXI I²C master on `iic_scl/iic_sda`, and an AXI Quad SPI master on the
`pl_spi_*` pins. Neither is on the AD9361's control path — **the AD9361 is
controlled over the PS's own SPI0, routed through EMIO** (`spi0_*` ports in
`system_top.v:155-161`), not through `axi_spi`. These two exist for the
board's other devices and are available to you if you need a bus to the
expansion header.

### `sys_rstgen`, `sys_concat_intc`, `decim_slice`, `interp_slice`, `logic_or` — glue

`proc_sys_reset` derives the fabric reset from `FCLK_RESET0_N`. `xlconcat`
gathers 16 interrupt lines into `IRQ_F2P` (only 4 are used; the rest are
grounded). The two `xlslice`s pull bit 0 out of the GPIO registers for the
filter muxes. `logic_or` combines the interpolator's valid with channel 1's
DAC valid to drive `tx_upack/fifo_rd_en` — the transmit side *does* let
channel 1 contribute to timing, unlike receive.

## Clocks and resets

| Clock | Source | Rate | Drives |
|---|---|---|---|
| `sys_cpu_clk` | `FCLK_CLK0` | 100 MHz | AXI-Lite control, both DMAs' memory-side ports, `axi_spi` |
| `sys_200m_clk` | `FCLK_CLK1` | 200 MHz | `axi_ad9361/delay_clk` only (IODELAY reference) |
| `l_clk` | AD9361 `rx_clk_in` via `axi_ad9361` | = data clock, scales with sample rate | **the entire datapath**: filters, packers, DMA fabric-side ports |

`l_clk` is the one that matters for your HDL. In 2R2T LVDS mode it runs at
twice the sample rate and `adc_valid` asserts every other cycle. At the
AD9361's floor of 2.083 MSPS that's a ~4 MHz clock; at 30.72 MSPS it's
61.44 MHz. Design for the top end.

Reset: `axi_ad9361/rst` feeds `cpack` and `tx_upack`. The filter hierarchies
have no reset input at all — they free-run from power-on.

## Address map and interrupts

| Base | Block | Linux device | IRQ |
|---|---|---|---|
| `0x4160_0000` | `axi_iic_main` | `axi_iic` | ps-15 |
| `0x7902_0000` | `axi_ad9361` | `cf-ad9361-lpc` (RX regs) and `cf-ad9361-dds-core-lpc` (TX regs, at +0x4000) | — |
| `0x7C40_0000` | `axi_ad9361_adc_dma` | `dma@7c400000` | ps-13 |
| `0x7C42_0000` | `axi_ad9361_dac_dma` | `dma@7c420000` | ps-12 |
| `0x7C43_0000` | `axi_spi` | `axi_quad_spi` | ps-11 |

These addresses are **mirrored in the device tree**
(`zynq-pluto-sdr-fishball.dts`). Move a block and the driver will probe the
wrong place. Add a block and it needs a DT node before Linux can see it.

## The receive path, sample by sample

1. LVDS lanes arrive at `axi_ad9361`. It recovers clock and frame, deserialises
   12-bit I/Q for both channels, sign-extends to 16 bits, applies DC-offset
   correction.
2. **Channel 0** (`adc_data_i0/q0`) enters `rx_fir_decimator`. With `active`
   low — the default — the mux passes the raw samples straight through and
   `valid_out_0` is just `adc_valid_i0`. With `active` high, the two
   `fir_compiler`s decimate by 8 and `valid_out_0` pulses once per 8 inputs.
3. **Channel 1** (`adc_data_i1/q1`) goes **directly** to `cpack` inputs 2
   and 3. `adc_valid_i1` is connected to nothing.
4. `cpack` captures all enabled channels on `fifo_wr_en` — channel 0's valid.

Step 4 is the trap. With decimation engaged, channel 1 is sampled at 1/8 rate
**with no anti-alias filter**: everything outside ±Fs/16 folds onto it, and
it's offset from channel 0 by the FIR's group delay. Channel 1 is only a
usable receiver with the filter bypassed. This is stock ADI behaviour.

## The transmit path

The mirror image, with one difference: `tx_upack/fifo_rd_en` is the OR of the
interpolator's valid and channel 1's DAC valid, so channel 1 does participate
in transmit timing. `axi_ad9361`'s `dac_data_*` inputs are 16-bit and the DAC
uses all 16 — measured: digital amplitude 32767 produces 24 dB more output
than 2047, cleanly. Scale to ±32767, not ±2047.

Baseband source per channel is selectable at runtime by the DDS core's driver:
DMA buffer, internal DDS tone generators (`altvoltage0..7` in IIO), or zero.
When a TX buffer stops, the driver reverts to DDS; with the DDS scales at 0
that's silence in baseband — but see the [transmitter safety](../README.md#transmitter-safety)
notes for what the RF chain does.

## What you can change

Roughly in order of ambition.

**Retune the channel-0 filters.** Pure `.coe` change in `system_bd.tcl:235`
or `:219`. Point RX and TX at *different* files. `firmware/scripts/gen_fir_coe.py`
designs coefficients in the exact format the IP expects (16-bit integers,
DC gain 2¹⁷ to match stock output level). Remember the filter must be engaged
at runtime to see any effect. The project must be regenerated (below) —
coefficients are baked into the IP at generation.

**Insert a block on channel 1.** The cleanest insertion point: no filter, no
mux, just `adc_data_i1 → cpack/fifo_wr_data_2` (and `_q1 → _3`). Break those
two `ad_connect` lines, put your module in between. Runs on `l_clk`, must
carry `enable` through unchanged, and — because of `cpack`'s single strobe —
must not change the sample timing relative to channel 0 unless you also take
over `fifo_wr_en`.

**Insert a block on channel 0.** Before the filter (raw, full rate, in
`l_clk`, valid every other cycle in 2R2T) or after it (decimated when engaged).
The optional channelizer patch is a worked example of inserting *before*:
`patches/optional/0003-wbfm-channelizer.patch` adds a 25-line Verilog module
and six `ad_connect` edits. Read it as a template.

**Add a Verilog module to the block design.** Two steps people miss:
`add_files -norecurse` the `.v` *inside* `system_bd.tcl` (it runs before
`adi_project_files` in `system_project.tcl`, so the module isn't in the
fileset yet — `adi_fir_filter_bd.tcl` does the same), then
`create_bd_cell -type module -reference <module> <instance>`.

**Add an AXI peripheral of your own.** `ad_ip_instance` it, `ad_cpu_interconnect
<base> <name>` to put it on the control fabric at a free address (e.g.
`0x7C44_0000`), `ad_cpu_interrupt ps-N mb-N <name>/irq` if it needs one
(ps-14, and ps-10 down to ps-0, are free — 11, 12, 13 and 15 are taken), and **add a device-tree node** with matching
`reg` and `compatible`, or Linux won't know it exists. Your userspace then
talks to it via UIO or a small driver.

**Add pins.** Every currently-constrained PL pin is spoken for (LVDS, AD9361
GPIOs, I²C, SPI). New I/O means the expansion header, which means the board
schematic — a pin that turns out to be an input or tied elsewhere can damage
the board. Pattern in `system_constr.xdc`:

```tcl
set_property -dict {PACKAGE_PIN <pin> IOSTANDARD LVCMOS25} [get_ports my_sig]
```

plus a matching port in `system_top.v` and a `create_bd_port` in
`system_bd.tcl` if the block design needs to see it.

**Change DMA width or depth.** `DMA_DATA_WIDTH_SRC/DEST` in the `axi_dmac`
parameters. Must match what `cpack`/`tx_upack` produce and what the driver
expects — the ADI driver reads the width from the core's registers, so
mismatches show up as garbage rather than errors.

## What not to touch

- **`sys_ps7` DDR parameters.** Board-specific timing, tuned for this PCB's
  MT41K256M16. Change them and it won't boot.
- **`axi_ad9361/ID`.** The Linux AXI-ADC driver checks it (`cf_axi_adc_core.c`
  gates ring-stream setup on `ID == 0`). Nonzero and capture silently stops
  working.
- **`CMOS_OR_LVDS_N`.** The board is wired LVDS. Not a preference.
- **AXI base addresses** without the matching device-tree change. Every one is
  mirrored in `zynq-pluto-sdr-fishball.dts`.
- **`cpack/fifo_wr_en` wiring** unless you understand the channel-1 consequence
  above and mean to change it.
- **The two decimation/interpolation filter *rates* (÷8/×8).** The Linux driver
  offers exactly `{1, 8}` (`decimation_factors_available`). A ÷4 filter would
  build fine and be unreachable from software.

## How a change becomes firmware

`system_bd.tcl` is the source of truth. The Vivado project is a *build
product* of it — but `build_hdl.tcl` **reuses an existing `pluto.xpr`** rather
than regenerating, so:

- A GUI edit saved into `pluto.xpr` is picked up automatically on the next
  build (it re-runs `reset_run synth_1`).
- A `system_bd.tcl` or `.coe` edit is **not** — you have to delete the project
  first so it gets regenerated:

```bash
# run from: firmware/
rm -rf src/hdl/projects/pluto/pluto.{xpr,cache,gen,hw,ip_user_files,runs,sim,srcs,sdk}
./scripts/build_all.sh --hdl-only     # ~20 min; skips kernel/u-boot/rootfs
```

Forget that step and the build quietly produces the old design. This has cost
more than one afternoon.

To keep the change: `firmware/src/` is regenerated by `setup.sh`, so a change
only survives as a patch in `firmware/patches/` (see CONTRIBUTING.md). CI
checks that every patch — including the optional ones — still applies against
upstream.

For the runtime knobs that select behaviour *within* this design (filter
engage, DDS vs DMA source, TX mute), see the [MCP server](https://github.com/matsvandamme/Fishball7020-mcp),
whose tools drive exactly those.
