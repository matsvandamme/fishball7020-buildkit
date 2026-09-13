# Isolating one FM channel in the FPGA

A worked example of putting real DSP into the AD9361 datapath: the board is
made to deliver **one** 200 kHz WBFM broadcast channel and nothing else, with
every other station annihilated in the fabric before a single sample crosses
USB.

The default configuration targets **102.1 MHz** (Studio Brussel), but the
station is a runtime control — see [Retuning](#retuning-to-another-station).

- [Why the obvious approach doesn't work](#why-the-obvious-approach-doesnt-work)
- [The arithmetic](#the-arithmetic) · [What was built](#what-was-built)
- [Building it](#building-it) · [Running it](#running-it)
- [Retuning](#retuning-to-another-station) · [Designing your own filter](#designing-your-own-filter)

## Why the obvious approach doesn't work

The instinct is: tune the radio near the station, then lowpass the channel.
Two things break that.

**1. Tuning on-channel puts the radio's own defects in your audio.** A
zero-IF receiver like the AD9361 leaks its local oscillator into its own
mixer and carries a DC offset in the baseband ADCs. Both land at exactly
0 Hz — the centre of the channel, if you tuned on-channel. Standard practice
is to tune *off*-channel so the wanted signal sits at some baseband offset
and the junk at DC is somewhere harmless.

**2. But then a real filter cannot select it.** This is the part that
surprises people. A FIR with real coefficients, applied separately to I and
Q, has a magnitude response that is **symmetric about DC**. Ask it to pass
1.0–1.2 MHz and it passes −1.2 to −1.0 MHz just as faithfully — a different
station about 2 MHz below the one you want, mixed straight into your
demodulator, with no way to tell them apart afterwards.

Selecting a band that is *not* centred on DC needs complex coefficients, and
that costs four real filters instead of two.

**The cheap way out:** move the channel to DC first, then a real lowpass is
exactly the right tool. And if you choose the sample rate so the offset is
exactly **Fs/4**, that move is free.

Multiplying by `exp(-j*pi*n/2)` cycles through `1, -j, -1, +j`. Every
"multiply" is a swap of I and Q plus a sign flip:

```
n=0   x  1   ->  ( I,  Q)
n=1   x -j   ->  ( Q, -I)
n=2   x -1   ->  (-I, -Q)
n=3   x +j   ->  (-Q,  I)
```

No multipliers, no NCO, no coefficients, no block RAM. Two registers and a
2-bit counter.

## The arithmetic

Everything follows from two constraints: the offset must equal `Fs/4`, and
`Fs/8` (after the FPGA's ÷8 decimator) should be a whole multiple of 48 kHz
so the audio path needs no resampler.

```
Fs = 4 x offset                and       Fs / 8 = k x 48000
```

Solving both gives `Fs = 384000 x k`. For an offset near 1 MHz, `k = 11`:

| | |
|---|---|
| Station | 102.1 MHz |
| **AD9361 sample rate** | **4.224 MSPS** (= 384000 × 11) |
| Offset = Fs/4 | 1.056 MHz |
| **RX LO** | **101.044 MHz** (= 102.1 − 1.056) |
| FPGA decimation | ÷8 |
| **Delivered rate** | **528 kSPS**, channel centred at DC |
| Audio decimation | 528000 / 48000 = **11**, exact |

A detail worth knowing: **2.083 MSPS, the AD9361's floor, is too slow for
this.** Its Nyquist band is ±1.042 MHz and the channel's upper edge needs
1.2 MHz, so the channel would fold before any filter saw it. The old
flowgraph in this repo ran at 2.304 MSPS (±1.152 MHz) and had the same
problem. The rate has to go *up*, not down.

### The spur comes back, and the filter is what stops it

Offset tuning moves the LO-leakage spur off the channel — then decimation
brings it back. After the Fs/4 shift the spur sits at −1.056 MHz, and
1.056 MHz is exactly 2 × 528 kHz, so ÷8 decimation folds it **precisely onto
DC**: the centre of the wanted channel.

The only thing preventing that is the FIR's stopband depth at that one
frequency. It measures about −84 dB there, and the worst case anywhere in the
stopband is −78.5 dB, so the spur is comprehensively dead — but this is why
`gen_fir_coe.py` probes that exact frequency and prints it rather than
assuming. If you change the rate plan, re-check it.

## What was built

```
AD9361   LO 101.044 MHz, 4.224 MSPS
   |     wanted channel at +1.056 MHz;  LO-leak + DC offset at 0
   v
axi_ad9361   adc_data_i0 / adc_data_q0
   |
   v
rx_ddc  (ad_fs4_ddc.v)          <- NEW: x exp(-j*pi*n/2), 0 DSPs, 0 BRAM
   |     wanted channel now at DC;  spur pushed to -1.056 MHz
   v
rx_fir_decimator                <- EXISTING block, new coefficients
   |     321-tap lowpass, Fpass 100 kHz, Fstop 175 kHz, then /8
   v
cpack -> adc_dma -> USB         528 kSPS, the channel and nothing else
```

The whole TX path is unchanged, and channel 1 is untouched — but note that
channel 1 is *not* a usable second receiver while this is running. It has no
filter of its own and `cpack` clocks every channel on channel 0's write
strobe (`rx_fir_decimator/valid_out_0`), so with decimation engaged channel 1
gets a naive 1-in-8 downsample with no anti-aliasing. Its usable band shrinks
to ±264 kHz, and everything the analog filter let through — roughly ±2 MHz —
folds on top of that. Stock ADI behaviour, not something this change
introduced, but easy to trip over.

| File | What it does |
|---|---|
| `firmware/src/hdl/projects/pluto/ad_fs4_ddc.v` | the Fs/4 shifter |
| `firmware/src/hdl/projects/pluto/system_bd.tcl` | wires it in, repoints the coefficients |
| `firmware/patches/0003-wbfm-channelizer.patch` | both of the above, so a clean `setup.sh` reproduces them |
| `firmware/scripts/gen_fir_coe.py` | designs and verifies the coefficients (no MATLAB needed) |
| `firmware/scripts/coefile_wbfm_102100.coe` | its output, 321 taps |
| `docs/grc/fishball_wbfm_rx.grc` | the receiver, with no software channel filter left in it |

### Filter performance

321 taps, Kaiser window, quantised to 16-bit integers summing to 2^17 (the
stock DC-gain convention):

| | |
|---|---|
| Passband ripple, 0–100 kHz | 0.0019 dB |
| Stopband edge, 175 kHz | −113 dB |
| Adjacent channel carrier, 200 kHz | −78.7 dB |
| Folded LO spur, 1.056 MHz | far below the floor |
| **Worst case anywhere ≥ 175 kHz** | **−78.5 dB** |
| Cost | **96 of 220 DSP48s** measured (72 before the change, so +24) |

Do not raise the tap count hoping for more: past ~321 taps this filter is
limited by **16-bit coefficient quantization**, not by taps, and more taps
make it slightly worse. `gen_fir_coe.py` documents the measurements.

Taps are unusually cheap here because the filter decimates — the IP gets
eight input sample periods' worth of clock cycles to compute each output, so
it folds 321 taps onto a handful of multipliers.

These are measured, not predicted. A full synthesis and implementation run of
this design reports:

| | Before | After |
|---|---|---|
| DSP48s | 72 | **96** / 220 (43.6%) |
| Slice LUTs | 11 893 | 12 664 / 53 200 (23.8%) |
| Slice registers | 20 851 | 22 300 / 106 400 (21.0%) |
| Worst negative slack | — | **+0.292 ns**, 0 failing endpoints of 55 269 |

The Fs/4 shifter itself costs no DSPs, as intended; the whole +24 is the
channel filter going from 129 to 321 taps.

## Building it

The coefficients and the block-design change both need the FIR IP
regenerated, and `build_hdl.tcl` reuses an existing `pluto.xpr` rather than
re-running `system_bd.tcl`. So the project has to be deleted first, or the
build will quietly produce the old filter:

```bash
# run from: firmware/
./scripts/setup.sh          # if src/ does not exist yet; applies patches/
rm -rf src/hdl/projects/pluto/pluto.{xpr,cache,gen,hw,ip_user_files,runs,sim,srcs,sdk}
./scripts/build_all.sh --hdl-only
```

`--hdl-only` reuses the existing kernel, U-Boot and rootfs, which are
unaffected — about 20 minutes instead of 70. Then flash `output/` as usual.

Worth checking before you wait for the flash:

```bash
# run from: firmware/
grep -A6 "^4. DSP" src/hdl/projects/pluto/utilization.rpt   # measured: 96 / 220
grep -A3 "Design Timing Summary" src/hdl/projects/pluto/timing.rpt
```

## Running it

Two rates must be set, and **the second one is what actually engages the
filter** — it drives `GP_CONTROL` bit 0, which selects the filtered path
through the bypass mux in the bitstream. Without it the samples arrive
unfiltered:

```bash
# on the BOARD
iio_attr -o -c ad9361-phy altvoltage0 frequency 101044000     # RX LO
iio_attr -i -c ad9361-phy voltage0 sampling_frequency 4224000 # converter rate
iio_attr -i -c ad9361-phy voltage0 rf_bandwidth 4000000       # analog LPF
iio_attr -i -c cf-ad9361-lpc voltage0 sampling_frequency 528000   # <- engages the FIR
```

Read the converter rate back. If the AD9361 landed on something other than
4224000, the channel is no longer at exactly Fs/4 and will sit off-centre.

The flowgraph in `docs/grc/fishball_wbfm_rx.grc` does all of this for you
from a Python snippet that runs after initialisation, because gr-iio programs
the AD9361 on its own and does not know about the FPGA decimator.

**The test that actually proves it works** is an A/B. Set
`cf-ad9361-lpc voltage0 sampling_frequency` back to 4224000 to bypass the
filter, and a neighbouring station should reappear in the spectrum display.
Set it to 528000 again and it should vanish into the noise floor.

## Retuning to another station

The FPGA filter sits at DC, so it follows the LO — any FM station works at
runtime with no rebuild, as long as the sample rate stays 4.224 MSPS:

```
LO = station - 1056000
```

The `Station (Hz)` slider in the flowgraph does exactly this.

Changing the *sample rate* is the thing that needs a rebuild, because the
offset must stay at Fs/4 and the coefficients are designed for one Fs. If
you need a different rate, pick `Fs = 384000 x k` so the audio decimation
stays a whole number, set `FS_IN` in `gen_fir_coe.py`, regenerate, and
rebuild.

## Designing your own filter

```bash
# run from: firmware/scripts
python3 gen_fir_coe.py
```

Standard library only — no MATLAB, no numpy. It prints the response at the
four frequencies that decide whether the design works (passband edge,
stopband edge, adjacent-channel carrier, and the folded LO spur), checks
passband ripple and worst-case stopband, and **exits non-zero rather than
writing a file it cannot stand behind**. Edit the configuration block at the
top to retarget it.

`gen_fir_coe.m` is the older MATLAB equivalent. It designs an equiripple
filter, which reaches a given stopband with roughly 30% fewer taps, but needs
the Signal Processing Toolbox. Either is fine here; taps are cheap in a
decimating filter.

## A wrinkle worth remembering

`rx_ddc` sits *ahead* of the bypass mux, so it is always in the path. With
FPGA decimation switched off you get a 4.224 MSPS stream that is
frequency-shifted but unfiltered — not a raw one. That is harmless, and
useful for the A/B test above, but it will confuse you in six months if you
have forgotten.
