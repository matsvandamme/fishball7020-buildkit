# Measuring this board, and trusting the result

`tools/selftest/sdr_selftest.py` is the instrument. Standard library only, no
`pylibiio`, and it never transmits without `--loopback`.

```bash
./sdr_selftest.py --ssh                                   # no cable, never transmits
./sdr_selftest.py --ssh --loopback --pad 30 --channel 0    # + the RF tests
./sdr_selftest.py --ssh --loopback --pad 20 --channel both # asks you to recable
```

`--pad` is not optional bookkeeping: it is how the tool turns a received level
into absolute power, and how it notices that the loop does not contain what you
think it does — the failure that destroys receivers.

## What is a property of the board, and what is a property of your cable

This distinction decides which numbers mean anything.

**Board properties — ratios, cable-independent.** Gain slopes, image rejection,
harmonic distortion, mute depth, supply rails, die temperature, the digital
interface eye. Quote these freely.

**Setup properties — absolute path loss.** Measured here, three different
attenuators gave results agreeing to 1–2 dB below 2 GHz and scattering by
**6–8 dB at 5 GHz**. Repeated passes on the *same* cable agree to **0.06 dB
median, 0.11 dB above 2 GHz**. Same board, same instrument — the variable is SMA
connector repeatability.

So above 2 GHz absolute path loss is dominated by your cabling, and that is why
the tool compares against a **baseline you record with your own cable** rather
than absolute thresholds:

```bash
./sdr_selftest.py --ssh --loopback --pad 30 --save-baseline ~/board-healthy.json
./sdr_selftest.py --ssh --loopback --pad 30 --baseline     ~/board-healthy.json
```

`--save-baseline` merges, so a two-channel baseline can be built from two runs.
A baseline is only meaningful against the same cable and pad — do not mix.

## Separating the transmit chain from the receive chain

A straight loopback measures a **product**, `T + R` for one channel, and cannot
say which chain an asymmetry belongs to. Crossing the loop makes the differences
solvable:

```
L00 = T0 + R0     L11 = T1 + R1     L01 = T0 + R1     L10 = T1 + R0
  R0 − R1 = L00 − L01 = L10 − L11        T0 − T1 = L01 − L11 = L00 − L10
```

```bash
./sdr_selftest.py --loopback --pad 20 --tx-channel 0 --rx-channel 1
```

Absolutes stay unreachable — three equations, four unknowns — but the
differences are fully determined. Measuring both crosses over-determines the
system and gives a closure check needing no external reference:
`L00 + L11 = L01 + L10`. On this board that closed to **−0.05 dB mean** over 105
frequencies, with the two routes to each difference agreeing to 0.04 dB.

That method answered a question a straight loopback could not: channel 1's loop
runs 1.8 dB hotter because its **receiver** is more sensitive, not its
transmitter (transmitters match to 0.25 dB).

## Sweeps

```bash
--sweep-points 60 --sweep-start 70e6 --sweep-stop 6e9
```

Log-spaced, clamped to the AD9361's range. Default 8 keeps a routine check fast;
60 costs about 20 seconds. 70 MHz is the one point not to trust — it spread
4.7 dB across passes where everything else was inside 1.6 dB.

## Reading the output

- **"settings changed on their own"** — something moved the gain or attenuation
  underneath the measurement. It was corrected before measuring, but look at
  `/mnt/jffs2`.
- **Image rejection "as found" versus "recalibrated"** — the AD9361's quadrature
  calibration goes stale. As found 41–48 dBc; after a forced calibration 55–63.
  The check is against the recalibrated figure, because failing on a stale one
  would condemn a good transmitter.
- **"could not vary TX attenuation"** — the sweep had no room. It is honest
  about not measuring rather than reporting a slope it could not fit.

## Verifying the instrument

`tools/selftest/test_dsp.py` asserts the measurement maths against signals whose
answers are known exactly — amplitude calibration lands within 0.004 dB, and the
pure-Python FFT fallback matches numpy to four decimals. CI runs it on 3.8 and
3.12, with and without numpy. If a measurement looks wrong, run this first: it
distinguishes a broken board from a broken instrument.
