# Transmitting with this board without destroying it

**The receiver is the fragile end.** The AD9361's RX input is rated to about
**+2.5 dBm**. That is the number every decision here is measured against.

## This board may have a power amplifier

It is sold in two variants, "with PA" and "without PA", and the vendor does not
publish the difference. The PA is a Mini-Circuits **PGA-102+**, whose gain is
strongly frequency dependent:

| GHz | 0.05 | 0.8 | 2.0 | 3.0 | 4.0 | 6.0 |
|---|---|---|---|---|---|---|
| **Gain (dB)** | **17.7** | 15.9 | 14.0 | 12.5 | 11.5 | 10.4 |

P1dB is about +17.5 dBm. Measured on a PA-equipped unit at 900 MHz through a
50 dB pad: **+18.5 dBm flat out**, roughly **16 dB above what its own receive
port survives**.

Sizing a loopback for a bare AD9361 (+7 dBm) is therefore wrong by 10-18 dB,
and most Pluto advice on the internet does exactly that.

`tools/selftest/sdr_selftest.py --loopback` reports which variant a board is,
by comparing measured loop gain against both models.

## Rules

- **Never loop TX to RX without an attenuator.** Fit at least 20 dB; 40-50 dB
  is comfortable and still leaves ~60 dB of signal-to-noise.
- **Never transmit into an antenna** unless you hold a licence for the
  frequency. This board covers the FM broadcast band, and with the PA it is
  not a trivial transmitter.
- Start at maximum attenuation and work down, measuring as you go. Never start
  loud and back off.
- The self-test never transmits with less than **35 dB** of its own
  attenuation: worst case (full-scale drive, 18 dB of PA gain, no external
  pad) that is -10 dBm, 12.5 dB under the RX rating.

## TX muting in this firmware

`patches/0004` mutes the transmitter whenever no DMA buffer is streaming, and
unmutes when one starts. `patches/0005` makes that unmute non-destructive:

| What you do | What you get |
|---|---|
| set a gain, then start the stream | the gain you set |
| start the stream having set nothing | the last gain you used |
| stop the stream | maximum attenuation, TX synthesiser down |

The `postdisable` hook runs even if the application crashed, because teardown
happens on file close — which is why this is a guarantee and a userspace
watchdog is not.

If you find a script polling `buffer/enable` to re-apply a gain, it is a
workaround for the pre-0005 behaviour and should be deleted; it overrides the
application silently. Look in `/mnt/jffs2/autorun.sh`.

## Measuring, not guessing

Received levels are dBFS against a **12-bit** converter (full scale ±2047).
Transmit is **16-bit** — scaling transmit samples to ±2047 emits 24 dB low.

RX gain is not linear in the way its label suggests: the AD9361's gain table
changes the LNA/mixer word at commanded 5, 17, 27, roughly 31-37, 52, and
every step above 63, and the real gain steps by up to 10 dB there while the
label claims 1 dB. **38-51 dB is the widest window with no transition in it**
in any band, and it is the only place a gain sweep means anything. Fitting a
line across the whole range reports ~0.67 dB/dB for a perfectly healthy front
end.

The legal gain range also moves with frequency: `[-1, 73]` below 1.3 GHz,
`[-3, 71]` to 4 GHz, `[-10, 62]` above. Writing outside it returns `-22 EINVAL`.
