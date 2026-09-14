# Is the board still healthy?

A self-test for the Fishball7020 / PlutoSky. It answers one question — *has
this radio been damaged?* — and it answers it with measurements rather than
with "well, it still enumerates".

```bash
./sdr_selftest.py                 # no cable, never transmits
./sdr_selftest.py --loopback      # + the RF tests
```

Python 3.8+ and nothing else. `numpy` is used for the FFT if you happen to
have it and a pure-Python transform if you don't; the results are identical to
four decimal places, which `test_dsp.py` asserts.

---

## Two halves

### Without a cable — and without transmitting

| What it checks | What a failure means |
|---|---|
| Six Zynq supply rails against ±5% | a regulator has drifted or failed |
| Zynq and AD9361 die temperature | thermal path or a part drawing too much |
| **AD9361 digital-interface eye** — all 16×16 clock/data delays with a PRBS running | the LVDS link to the FPGA is marginal: a cracked joint, a degraded driver |
| **AD9361 internal digital loopback** — a tone through both DMAs and back | the FPGA datapath or either DMA is dropping data |
| Capture length, I/Q both live, DC offset | a stuck or dead converter |
| RX gain chain response over 70 dB | a front end that no longer amplifies |
| Both receive channels | one channel lost |
| Synthesiser tuning, 70 MHz – 6 GHz | a VCO band that no longer locks |

The two BIST checks need shell access to the board (`--ssh`), because they
live in debugfs and libiio does not expose them. They are the deepest tests
here and they need no cable, so they are worth the extra access. Without
`--ssh` they are skipped and everything else still runs.

### With a loopback — this transmits

```
TX1 ---[ 20 or 30 dB pad ]--- RX1        (both in series is fine)
```

| What it checks | What a failure means |
|---|---|
| Loop detected, and the path loss in it | an open connector, or a port that has gone deaf |
| **TX attenuator linearity** over 25 dB | compression, or a damaged output stage |
| **RX gain linearity** over 40 dB | a damaged gain stage |
| **Image rejection** | quadrature calibration, or an unbalanced mixer |
| **Harmonic distortion**, 2nd and 3rd | a non-linear output stage |
| **Path loss at 8 frequencies, 100 MHz – 5 GHz** | a band-specific hole: a blown balun or matching network |
| **How far the transmitter actually falls when stopped** | something is leaving the radio keyed |

## Safety

**This cannot overdrive your receiver, even if you forget the attenuator.**

The receiver is the fragile end: the AD9361's RX input is rated to about
**+2.5 dBm**. And this board is sold in a variant with a **power amplifier** on
transmit — a Mini-Circuits [PGA-102+](https://www.minicircuits.com/pdfs/PGA-102+.pdf)
whose gain is strongly frequency dependent:

| GHz | 0.05 | 0.8 | 2.0 | 3.0 | 4.0 | 6.0 |
|---|---|---|---|---|---|---|
| **Gain (dB)** | **17.7** | 15.9 | 14.0 | 12.5 | 11.5 | 10.4 |

P1dB is about +17.5 dBm. Measured here at 900 MHz through a 50 dB pad, the
board delivers roughly **+18.5 dBm** flat out — about **16 dB above what its
own receive port survives**. Sizing a loopback for a bare AD9361, as most Pluto
advice does, gets this dangerously wrong.

So the script never transmits with less than **35 dB** of its own attenuation:

| | TX output | At RX with **no** pad | With a 50 dB pad |
|---|---|---|---|
| Script's floor, at its −6 dBFS drive | −16 dBm | **−16 dBm** | −66 dBm |
| Same floor, at full-scale drive | −10 dBm | −10 dBm | −60 dBm |
| Damage threshold | | **+2.5 dBm** | |

That is 12.5 dB of margin in the worst case that can be constructed — full
scale, highest PA gain, two ports joined by a barrel. Sweeps *start* at 50 dB,
measure the loop, and only then work downward toward the floor. A 25 dB span is
ample to prove the gain chain is linear, so there is nothing to gain from going
louder. `--min-tx-atten` can lower it and prints the resulting power budget.

**It also asks how much attenuation is in your cable**, then checks your answer
against what it measures and says so if the two disagree by more than 8 dB. A
pad that is missing, is the wrong value, or is not making contact is the
failure that destroys receivers, so it is worth one question.

That comparison has a useful side effect: the PA and non-PA variants of this
board differ by exactly the PA's gain, so a pad you are confident about tells
the script which one you have. It reports that too — measured here, a declared
50 dB pad reads back as 51 dB against the PA model and 35 dB against the
bare-AD9361 one, which settles it.

Other guarantees:

- **Nothing transmits without `--loopback`.** The default run never keys the
  radio at all.
- Every setting is saved at the start and restored at the end — including
  after Ctrl-C or an exception. The transmitter is muted *before* anything
  else is put back.
- The receiver is held around −22 dBFS and backed off if it approaches full
  scale, so measurements are never taken in compression.
- Every measurement reads back the gain and attenuation actually in force and
  re-asserts them if they have moved, so a setting that changes underneath the
  test is corrected and reported rather than silently corrupting a number.

**Do not run `--loopback` with an antenna on the TX port.** Most of this
board's range is licensed spectrum, and with the PA it is not a trivial
transmitter.

## Both channels

The board has two transmit and two receive ports. `--channel both` measures
pair 0, then asks you to move the loopback to TX2/RX2 and press Enter:

```bash
./sdr_selftest.py --ssh --loopback --pad 50 --channel both
```

With one set of attenuators you can only test one pair at a time, which is why
it prompts rather than assuming. `--channel 1` runs just the second pair.

## Baselines

Some things have absolute answers: a supply rail is in spec or it is not, and
a gain slope that should be 1.00 dB/dB either is or is not. Those pass or fail
on their own.

Path loss does not. It depends on your cable, your attenuator and your
connectors, so there is no universal number to compare against. Record a
baseline while the board is known good:

```bash
./sdr_selftest.py --loopback --ssh --save-baseline ~/board-healthy.json
```

and compare against it whenever you suspect something:

```bash
./sdr_selftest.py --loopback --ssh --baseline ~/board-healthy.json
```

That turns *"is 41.6 dB of loss at 2.4 GHz correct?"* — unanswerable — into
*"it was 41.5 dB in March"*, which is the question you actually wanted. Use
the same cable and pad both times, or the comparison means nothing.

## Reading the output

```
== RF loopback ==
  PASS   loopback detected
         tone 68.3 dB above the floor at 900.0 MHz; TX attenuation 20 dB, RX gain 34 dB
  info   external attenuation in the loop
         about 30 dB (+/-3 dB). System gain -25.4 dB.
  PASS   image rejection
         48.2 dBc (image at -250 kHz is -70.4 dBFS)
```

"System gain" is the loop's transfer function with both programmable gains
divided out, so it describes the cable and the radio's analogue path and
nothing else. That is what makes it comparable between frequencies and between
runs months apart. The implied pad is derived from it using nominal figures
for this board, so treat it as ±3 dB — enough to tell a 20 dB pad from a 50 dB
one, or from a bare cable, which is all it is for.

Exit status is 0 if nothing failed, 1 if anything did.

## Files

| | |
|---|---|
| `sdr_selftest.py` | the test itself |
| `iiod_min.py` | libiio's network protocol over a plain socket, stdlib only |
| `test_dsp.py` | asserts the measurement maths against known signals, no board needed |

`iiod_min.py` is vendored deliberately. The moment you suspect your board is
damaged is the worst possible time to discover that your libiio no longer
matches the firmware's, or that a C extension will not build.
