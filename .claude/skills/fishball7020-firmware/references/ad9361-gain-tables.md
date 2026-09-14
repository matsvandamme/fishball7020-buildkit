# The AD9361 RX gain table

The single largest source of confusing measurements on this board. Almost every
"the gain is wrong" result traces back to here.

## What it is

Receive gain is not one variable amplifier. It is a chain of discrete stages —
LNA, mixer, TIA, baseband LPF, digital — each with a handful of legal settings.
The gain table maps **one index** onto a combination of all of them:

| index | nominal dB | LNA/mixer | TIA/LPF | digital |
|---|---|---|---|---|
| 50 | 47 | `0x44` | `0x2E` | `0x00` |
| 54 | 51 | `0x44` | `0x32` | `0x00` |
| **55** | **52** | **`0x64`** | `0x2E` | `0x20` |
| 56 | 53 | `0x64` | `0x2F` | `0x00` |

Setting one index sets the whole chain, which is what makes AGC tractable: it
walks an index rather than juggling five interacting controls.

## Three tables, one per band

| Table | Band | Nominal range |
|---|---|---|
| 1 | 200 MHz – 1.3 GHz | −1 … 73 dB |
| 2 | 1.3 – 4 GHz | −3 … 71 dB |
| 3 | 4 – 6 GHz | −10 … 62 dB |

The driver loads whichever matches the LO. Crossing a boundary changes the whole
mapping, so `hardwaregain_available` changes with frequency — writing a value
legal in one band returns `-22 EINVAL` in another. Read it after every retune if
you sweep.

## The dB label is a nominal, not the truth

`full_gain_table_abs_gain[]` gives one nominal dB per index, incrementing by
exactly 1. That is what `hardwaregain` reports and accepts. **The hardware does
not follow it.** At index 54→55 the LNA/mixer word changes from `0x44` to `0x64`
— a different amplifier state, with digital gain shifted to partly compensate.
The label says +1 dB; measured, it is about 10.

Transitions sit at index 8, 20, 30, ~34–40, 55, and every step above 66, in all
three tables at slightly different indices. Subtract 3 for commanded dB:
**5, 17, 27, ~31–37, 52, and 63 upward.**

**38–51 dB is the widest transition-free window in every band.** It is the only
span where "is the gain control linear?" has a meaningful answer.

## Measured consequences on this board

- Fitting a line across the whole range gives **0.65 dB/dB** for a perfectly
  healthy front end. Restricted to 38–51 dB it gives **1.000**, with 0.13 dB of
  worst-case residual.
- The 4 GHz band-table swap produces a step in loop gain: **4.6 dB on channel 0,
  7.4 dB on channel 1**. Confirmed to be receive-side by a crossed measurement —
  the receive difference steps 2.40 dB across the boundary while the transmit
  difference moves 0.27 dB.
- Because the two channels disagree on the step size, **it is not one constant
  you can correct out globally**. Calibrate per band *and* per channel.

## Practical rules

- Treat `hardwaregain` as an index with a dB-shaped name: monotonic and
  repeatable to 0.07 dB, but not proportional.
- Any gain sweep that needs to be linear must stay inside 38–51 dB.
- Re-read `hardwaregain_available` after retuning; clamp to it.
- A gain calibration made below 4 GHz is wrong above it.
- Reading the tables: `drivers/iio/adc/ad9361.c`, `full_gain_table[]` and
  `full_gain_table_abs_gain[]`.
