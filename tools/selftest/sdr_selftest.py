#!/usr/bin/env python3
"""Health check for a Fishball7020 / PlutoSky board: is it still undamaged?

    ./sdr_selftest.py                        # no cable needed, never transmits
    ./sdr_selftest.py --loopback             # + RF tests, TX->RX through a pad
    ./sdr_selftest.py --loopback --save-baseline health.json
    ./sdr_selftest.py --loopback --baseline health.json    # compare to then

Two halves.

The FIRST half needs no cable and never keys the transmitter. It reads the
Zynq's own supply rails and die temperature, runs the AD9361's built-in
digital-interface eye scan, and exercises the receiver on its own. That is
already enough to catch a failed regulator, an overheating part, a marginal
LVDS interface, a dead ADC, or a receiver whose gain chain no longer responds.

The SECOND half needs a cable from TX1 to RX1 with an attenuator in it, and
is the only part that transmits. It measures the loop end to end: level
linearity over the TX attenuator and the RX gain range, path loss across the
tuning range, image rejection, harmonic distortion, and how quiet the
transmitter really is when it is supposed to be off.

    TX1 ---[ 20 or 30 dB pad ]--- RX1

Use at least 20 dB. The AD9361's receive input is rated to about +2.5 dBm and
its transmitter reaches about +7 dBm at full output, so a bare cable can
overdrive it. This script never starts loud: it begins at 40 dB of TX
attenuation, measures the loop, and only then works out how hard it may
drive. It refuses to go anywhere near full scale on the receiver.

WHAT "PASS" MEANS. Some checks are absolute - a supply rail is in spec or it
is not, and a slope that should be 1.00 dB/dB either is or is not. Others,
above all the path loss at each frequency, depend on your cable and your
attenuator, so there is no universal number to compare against. For those,
record a baseline while the board is known good and compare against it later:
that turns "is 41.6 dB of loss at 2.4 GHz correct?" into "it was 41.5 dB in
March", which is the question you actually want answered.

Nothing here is destructive and every setting is restored on exit, including
after Ctrl-C.
"""

from __future__ import annotations

import argparse
import cmath
import json
import math
import os
import re
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from iiod_min import Iiod, mask_for                                    # noqa: E402

try:
    import numpy as np
except ImportError:                                     # optional, only faster
    np = None

PHY, RX, TX, XADC = "ad9361-phy", "cf-ad9361-lpc", "cf-ad9361-dds-core-lpc", "xadc"
RX_LO, TX_LO = "altvoltage0", "altvoltage1"

RX_FULL_SCALE = 2048.0          # 12-bit converter, sign-extended into int16
TX_FULL_SCALE = 32768.0         # the DAC takes the full 16-bit range
TX_ATTEN_MUTE = -89.75          # most attenuation the AD9361 offers

PASS, WARN, FAIL, INFO = "PASS", "WARN", "FAIL", "info"


# --------------------------------------------------------------------------
# signal processing - small enough to not need numpy, faster with it
# --------------------------------------------------------------------------

def _hann(n):
    return [0.5 - 0.5 * math.cos(2 * math.pi * i / n) for i in range(n)]


def _fft(x):
    """Iterative radix-2 Cooley-Tukey. len(x) must be a power of two."""
    n = len(x)
    j = 0
    x = list(x)
    for i in range(1, n):                               # bit-reversal permute
        bit = n >> 1
        while j & bit:
            j ^= bit
            bit >>= 1
        j |= bit
        if i < j:
            x[i], x[j] = x[j], x[i]
    length = 2
    while length <= n:
        ang = -2 * math.pi / length
        wl = cmath.exp(1j * ang)
        for i in range(0, n, length):
            w = 1 + 0j
            for k in range(i, i + length // 2):
                u, v = x[k], x[k + length // 2] * w
                x[k], x[k + length // 2] = u + v, u - v
                w *= wl
        length <<= 1
    return x


class Spectrum:
    """One capture, turned into dBFS bins centred on DC.

    Amplitude is normalised so that a full-scale complex tone reads 0 dBFS,
    independent of transform length or window.
    """

    def __init__(self, iq, n=None):
        n = n or 1 << int(math.log2(len(iq)))
        iq = iq[:n]
        if np is not None:
            w = np.hanning(n + 1)[:n]
            spec = np.fft.fftshift(np.fft.fft(np.asarray(iq) * w)) / w.sum()
            self.mag = np.abs(spec)
            self.db = 20 * np.log10(np.maximum(self.mag, 1e-12) / RX_FULL_SCALE)
            self.db = self.db.tolist()
        else:
            w = _hann(n)
            spec = _fft([v * wi for v, wi in zip(iq, w)])
            spec = spec[n // 2:] + spec[:n // 2]
            s = sum(w)
            self.db = [20 * math.log10(max(abs(v) / s, 1e-12) / RX_FULL_SCALE)
                       for v in spec]
        self.n = n

    def bin_of(self, freq_hz, fs):
        return int(round(self.n / 2 + freq_hz * self.n / fs))

    def peak_near(self, freq_hz, fs, span_bins=4):
        """Strongest bin within +/- span_bins of a frequency, in dBFS."""
        c = self.bin_of(freq_hz, fs)
        lo, hi = max(0, c - span_bins), min(self.n, c + span_bins + 1)
        return max(self.db[lo:hi]) if hi > lo else -200.0

    def floor(self):
        """Median bin: a robust noise-floor estimate that ignores the tones."""
        return statistics.median(self.db)


def to_complex(interleaved):
    it = iter(interleaved)
    return [complex(i, q) for i, q in zip(it, it)]


# --------------------------------------------------------------------------
# board access
# --------------------------------------------------------------------------

class Board:
    def __init__(self, uri="ip:192.168.2.1", timeout=15.0):
        host, port = self._split(uri)
        self.c = Iiod(host, port, timeout).connect()
        self.dev = self.c.devices()
        self.saved = {}

    @staticmethod
    def _split(uri):
        if uri.startswith("ip:"):
            uri = uri[3:]
        if ":" in uri and not uri.startswith("["):
            host, _, port = uri.rpartition(":")
            return host, int(port)
        return uri, 30431

    # attribute helpers -----------------------------------------------------

    def rd(self, dev, ch, attr, output=False):
        return self.c.read(dev, ch, attr, output)

    def rdf(self, dev, ch, attr, output=False):
        return float(self.rd(dev, ch, attr, output).split()[0])

    def wr(self, dev, ch, attr, value, output=False):
        self.c.write(dev, ch, attr, value, output)

    def rd_dev(self, dev, attr):
        return self.c.read_device(dev, attr)

    def wr_dev(self, dev, attr, value):
        self.c.write_device(dev, attr, value)

    # state ------------------------------------------------------------------

    def save_state(self):
        g = self.saved
        g["rx_lo"] = self.rd(PHY, RX_LO, "frequency", True)
        g["tx_lo"] = self.rd(PHY, TX_LO, "frequency", True)
        g["tx_lo_pd"] = self.rd(PHY, TX_LO, "powerdown", True)
        g["fs"] = self.rd(PHY, "voltage0", "sampling_frequency")
        g["bw"] = self.rd(PHY, "voltage0", "rf_bandwidth")
        g["gain_mode"] = self.rd(PHY, "voltage0", "gain_control_mode")
        g["gain"] = self.rd(PHY, "voltage0", "hardwaregain").split()[0]
        g["tx_atten0"] = self.rd(PHY, "voltage0", "hardwaregain", True).split()[0]
        g["tx_atten1"] = self.rd(PHY, "voltage1", "hardwaregain", True).split()[0]
        g["rx_delivered"] = self.rd(RX, "voltage0", "sampling_frequency")

    def restore_state(self):
        """Put everything back, quietening the transmitter first."""
        g = self.saved
        if not g:
            return
        order = [
            (PHY, "voltage0", "hardwaregain", TX_ATTEN_MUTE, True),
            (PHY, "voltage1", "hardwaregain", TX_ATTEN_MUTE, True),
            (PHY, "voltage0", "sampling_frequency", g.get("fs"), False),
            (PHY, "voltage0", "rf_bandwidth", g.get("bw"), False),
            (PHY, RX_LO, "frequency", g.get("rx_lo"), True),
            (PHY, TX_LO, "frequency", g.get("tx_lo"), True),
            (PHY, "voltage0", "gain_control_mode", g.get("gain_mode"), False),
            (RX, "voltage0", "sampling_frequency", g.get("rx_delivered"), False),
            (PHY, "voltage0", "hardwaregain", g.get("tx_atten0"), True),
            (PHY, "voltage1", "hardwaregain", g.get("tx_atten1"), True),
            (PHY, TX_LO, "powerdown", g.get("tx_lo_pd"), True),
        ]
        for dev, ch, attr, val, out in order:
            if val is None:
                continue
            try:
                self.wr(dev, ch, attr, val, out)
            except Exception:
                pass

    # radio ------------------------------------------------------------------

    def tune(self, hz, tx=False):
        self.wr(PHY, TX_LO if tx else RX_LO, "frequency", int(hz), True)

    def set_rate(self, fs, bw=None):
        self.wr(PHY, "voltage0", "sampling_frequency", int(fs))
        self.wr(PHY, "voltage0", "rf_bandwidth", int(bw or fs * 0.8))
        # Keep the FPGA decimator bypassed: it is a channel filter, and it
        # would remove the very tones this test relies on.
        self.wr(RX, "voltage0", "sampling_frequency", int(self.rate()))

    def rate(self):
        return self.rdf(PHY, "voltage0", "sampling_frequency")

    def rx_gain_limits(self, pair=0):
        """(min, max) manual gain for the CURRENT band, in dB.

        Not a constant: the AD9361 swaps gain tables with frequency, and the
        range moves with them - [-1, 73] below 1.3 GHz, [-3, 71] to 4 GHz,
        [-10, 62] above. Writing outside it is rejected with EINVAL, which is
        how a frequency sweep falls over if it assumes one range everywhere.
        """
        try:
            raw = self.rd(PHY, f"voltage{pair}", "hardwaregain_available")
            lo, _step, hi = raw.strip("[] ").split()
            return float(lo), float(hi)
        except Exception:
            return -1.0, 62.0                     # the narrowest of the three

    def set_rx_gain(self, db, pair=0):
        self.wr(PHY, f"voltage{pair}", "gain_control_mode", "manual")
        self.wr(PHY, f"voltage{pair}", "hardwaregain", db)

    def set_tx_atten(self, db, pair=0):
        """db is negative: -10 means 10 dB of attenuation, 0 is full output."""
        self.wr(PHY, f"voltage{pair}", "hardwaregain", round(db, 2), True)

    def mute_tx(self):
        for ch in ("voltage0", "voltage1"):
            try:
                self.wr(PHY, ch, "hardwaregain", TX_ATTEN_MUTE, True)
            except Exception:
                pass

    def capture(self, nsamples, pair=0):
        did, total = self.dev[RX]
        first = pair * 2
        raw = self.c.read_samples(did, nsamples, mask_for([first, first + 1], total))
        return to_complex(raw)

    def tx_tone(self, offset_hz, fs, amplitude, pair=0, nsamples=4096):
        """Start a cyclic complex tone. Returns after the DAC is running.

        The tone is placed on an exact bin of the cyclic buffer so it wraps
        without a discontinuity - otherwise the seam sprays spurs across the
        whole span and the harmonic test measures the seam, not the radio.
        """
        k = round(offset_hz * nsamples / fs)
        values = []
        for n in range(nsamples):
            ph = 2 * math.pi * k * n / nsamples
            values += [int(round(amplitude * math.cos(ph))),
                       int(round(amplitude * math.sin(ph)))]
        did, total = self.dev[TX]
        self.c.close_buffer(did)
        first = pair * 2
        self.c.write_samples(did, values, mask_for([first, first + 1], total),
                             nchannels=2, cyclic=True)
        return k * fs / nsamples                       # the frequency actually sent

    def tx_stop(self):
        self.c.close_buffer(self.dev[TX][0])
        self.mute_tx()

    # sensors -----------------------------------------------------------------

    def xadc_rails(self):
        out = {}
        for i in range(6):
            for name in ("vccint", "vccaux", "vccbram", "vccpint", "vccpaux",
                         "vccoddr"):
                try:
                    label = self.rd(XADC, f"voltage{i}", f"{name}_label")
                except Exception:
                    continue
                raw = self.rdf(XADC, f"voltage{i}", f"{name}_raw")
                scale = self.rdf(XADC, f"voltage{i}", f"{name}_scale")
                out[label] = raw * scale / 1000.0
                break
        return out

    def zynq_temp(self):
        raw = self.rdf(XADC, "temp0", "raw")
        off = self.rdf(XADC, "temp0", "offset")
        sc = self.rdf(XADC, "temp0", "scale")
        return (raw + off) * sc / 1000.0

    def ad9361_temp(self):
        return self.rdf(PHY, "temp0", "input") / 1000.0


# --------------------------------------------------------------------------
# how loud this script is ever allowed to be
# --------------------------------------------------------------------------
#
# The only way a loopback can damage this board is RF power into the receive
# port, and the receive port is the fragile end: the AD9361's RX input is
# rated to about +2.5 dBm.
#
# THIS BOARD HAS A POWER AMPLIFIER, and sizing the limit for a bare AD9361
# gets it dangerously wrong. The PA is a Mini-Circuits PGA-102+, whose gain is
# strongly frequency dependent - 17.7 dB at 50 MHz, 15.9 at 800 MHz, 14.0 at
# 2 GHz, 10.4 at 6 GHz - with P1dB around +17.5 dBm. So at the bottom of the
# range the transmit port delivers roughly
#
#     +7 dBm (AD9361 at 0 dB attenuation) + 17.7 dB  ->  PA saturation, ~+17 dBm
#
# which is about 15 dB ABOVE what the receiver can survive. A loopback with no
# attenuator in it will damage this board. That is not true of a stock
# PlutoSDR, and it is why the floor here is higher than you might expect.
#
# Sizing the floor for the worst case - full-scale digital drive, 18 dB of PA
# gain, no external pad at all - and leaving 12 dB of margin under the +2.5 dBm
# rating:
#
#     7 + 18 - A <= -10 dBm   ->   A >= 35 dB
#
# So 35 dB is the floor, and sweeps start at 50 dB and only work downward
# towards it. At the -6 dBFS this script actually drives, that is about
# -16 dBm into a bare cable: 18 dB of margin. A 25 dB span is ample to prove
# the gain chain is linear, so there is nothing to gain from going louder.
# --min-tx-atten can lower it, and says why not to.
#
# The "without PA" variant of this board is 15-18 dB quieter, so this floor is
# conservative there. Being conservative on the quieter variant is the right
# way round.
#
MIN_TX_ATTEN_DB = 35.0          # never transmit with less attenuation than this
START_TX_ATTEN_DB = 50.0        # where every loopback measurement begins
PA_GAIN_DB = 18.0               # PGA-102+ worst case, used only for the budget
AD9361_TX_MAX_DBM = 7.0         # at 0 dB attenuation, full-scale digital
RX_MAX_INPUT_DBM = 2.5          # what the receive port survives
TARGET_RX_DBFS = -22.0          # aim the received tone here: loud, not clipping
MAX_RX_DBFS = -6.0              # back off if anything gets nearer full scale


class Report:
    def __init__(self):
        self.rows = []
        self.data = {}

    def add(self, group, name, verdict, detail="", value=None, key=None):
        self.rows.append((group, name, verdict, detail))
        if key:
            self.data[key] = value
        return verdict

    def check(self, group, name, ok, detail="", warn=False, value=None, key=None):
        verdict = PASS if ok else (WARN if warn else FAIL)
        return self.add(group, name, verdict, detail, value, key)

    def counts(self):
        c = {PASS: 0, WARN: 0, FAIL: 0, INFO: 0}
        for _, _, v, _ in self.rows:
            c[v] = c.get(v, 0) + 1
        return c

    def render(self, colour=True):
        def paint(v):
            if not colour:
                return f"{v:5}"
            code = {PASS: "32", WARN: "33", FAIL: "31", INFO: "36"}.get(v, "0")
            return f"\033[{code}m{v:5}\033[0m"
        out, last = [], None
        for group, name, verdict, detail in self.rows:
            if group != last:
                out.append(f"\n== {group} ==")
                last = group
            line = f"  {paint(verdict)}  {name}"
            if detail:
                line += f"\n           {detail}"
            out.append(line)
        return "\n".join(out)


# --------------------------------------------------------------------------
# tests that need no cable and never transmit
# --------------------------------------------------------------------------

def test_identity(b, rep):
    g = "Identity"
    attrs = b.c.context_attrs()
    model = attrs.get("hw_model", "")
    rep.check(g, "board answers over libiio", bool(model),
              f"IIOD {b.c.version()}", value=attrs.get("fw_version"), key="fw_version")
    rep.check(g, "hardware model reported", "AD9361" in model or "Pluto" in model,
              model, value=model, key="hw_model")
    serial = attrs.get("hw_serial", "")
    rep.check(g, "serial number present", bool(serial) and serial != "TBD",
              serial or "<empty> - tools that identify boards by serial will "
                        "refuse this one; reflash from the current devkit",
              value=serial, key="hw_serial")
    for name in (PHY, RX, TX, XADC):
        rep.check(g, f"device '{name}' present", name in b.dev,
                  b.dev.get(name, ("missing", 0))[0])


def test_power(b, rep):
    """Supply rails and die temperature. Absolute limits, no baseline needed."""
    g = "Power and thermal"
    # Zynq-7000 DC characteristics: each rail +/-5%. VCCODDR follows the memory,
    # 1.35 V for the DDR3L fitted here.
    nominal = {"vccint": 1.00, "vccaux": 1.80, "vccbram": 1.00,
               "vccpint": 1.00, "vccpaux": 1.80, "vccoddr": 1.35}
    rails = b.xadc_rails()
    for name, volts in sorted(rails.items()):
        nom = nominal.get(name)
        if nom is None:
            rep.add(g, f"rail {name}", INFO, f"{volts:.3f} V")
            continue
        err = (volts - nom) / nom * 100
        rep.check(g, f"rail {name}", abs(err) <= 5.0,
                  f"{volts:.3f} V ({err:+.1f}% of {nom:.2f} V nominal)")
    rep.data["rails"] = {k: round(v, 4) for k, v in rails.items()}

    zt, at = b.zynq_temp(), b.ad9361_temp()
    rep.data["zynq_temp_c"], rep.data["ad9361_temp_c"] = round(zt, 1), round(at, 1)
    # Commercial-grade XC7Z020 is specified to 85 C junction.
    rep.check(g, "Zynq die temperature", zt < 85, f"{zt:.1f} C (limit 85 C)",
              warn=zt < 95)
    rep.check(g, "AD9361 die temperature", -10 < at < 90, f"{at:.1f} C")


def test_receiver(b, rep, quick=False):
    """The receiver on its own: does it capture, and does its gain respond?"""
    g = "Receiver (no cable)"
    fs = 4_000_000
    b.set_rate(fs)
    b.tune(435_000_000)
    b.set_rx_gain(30)
    time.sleep(0.1)

    n = 16384
    iq = b.capture(n)
    rep.check(g, "capture returns the requested length", len(iq) == n,
              f"{len(iq)} of {n} samples")
    if not iq:
        return

    reals = [z.real for z in iq]
    imags = [z.imag for z in iq]
    rep.check(g, "I and Q are both live", len(set(reals)) > 8 and len(set(imags)) > 8,
              f"{len(set(reals))} distinct I values, {len(set(imags))} distinct Q "
              f"- a stuck converter shows one or two")

    dc_i, dc_q = statistics.fmean(reals), statistics.fmean(imags)
    dc = math.hypot(dc_i, dc_q) / RX_FULL_SCALE
    dc_db = 20 * math.log10(max(dc, 1e-9))
    rep.check(g, "DC offset within range", dc_db < -30,
              f"{dc_db:.1f} dBFS (I {dc_i:+.1f}, Q {dc_q:+.1f} LSB)",
              warn=dc_db < -20, value=round(dc_db, 1), key="dc_offset_dbfs")

    # Sweeping gain against the receiver's own noise, with nothing connected.
    # Read this the right way round: at LOW gain the output floor is set by the
    # converter's quantisation noise, not by the front end, so it barely moves
    # however much gain is applied. Only once the amplified front-end noise
    # rises above the converter floor - the top of the range here - does the
    # floor track gain, and only that part of the curve says anything about the
    # front end. It has to be monotonic throughout, and it has to respond at
    # the top; a front end that has lost its LNA goes flat there.
    # The real linearity measurement is the RX gain sweep against a known tone
    # in the loopback half, which needs no such caveat.
    floors = []
    for gain in (0, 20, 40, 60, 70):
        b.set_rx_gain(gain)
        time.sleep(0.08)
        floors.append(Spectrum(b.capture(8192)).floor())
    steps = [round(y - x, 1) for x, y in zip(floors, floors[1:])]
    monotonic = all(s > -1.5 for s in steps)
    responds_at_top = floors[-1] - floors[-3] > 5.0
    rep.check(g, "RX gain chain responds at the top of its range",
              monotonic and responds_at_top,
              f"noise floor {' -> '.join(f'{f:.1f}' for f in floors)} dBFS "
              f"at 0/20/40/60/70 dB gain; converter-limited below ~40 dB, "
              f"{floors[-1] - floors[-3]:+.1f} dB over the top 30 dB",
              value=[round(f, 1) for f in floors], key="rx_gain_floors_dbfs")

    b.set_rx_gain(30)
    if b.dev[RX][1] >= 4:
        iq2 = b.capture(8192, pair=1)
        alive = len(set(z.real for z in iq2)) > 8
        rep.check(g, "second receive channel alive", alive,
                  f"RX2 floor {Spectrum(iq2).floor():.1f} dBFS "
                  f"(its input is whatever is on the RX2 port)")

    # Tuning: every band the synthesiser has to cover.
    points = [70e6, 900e6, 2400e6] if quick else \
             [70e6, 300e6, 900e6, 1800e6, 2400e6, 3500e6, 5000e6, 6000e6]
    bad = []
    for f in points:
        try:
            b.tune(f)
            time.sleep(0.05)
            got = b.rdf(PHY, RX_LO, "frequency", True)
            if abs(got - f) > 1000:
                bad.append(f"{f/1e6:.0f} MHz -> {got/1e6:.3f}")
                continue
            if len(set(z.real for z in b.capture(4096))) < 8:
                bad.append(f"{f/1e6:.0f} MHz (no samples)")
        except Exception as exc:
            bad.append(f"{f/1e6:.0f} MHz ({exc})")
    rep.check(g, "RX synthesiser tunes across the range", not bad,
              f"{len(points) - len(bad)} of {len(points)} points from "
              f"{points[0]/1e6:.0f} to {points[-1]/1e6:.0f} MHz"
              + (f"; failed: {', '.join(bad)}" if bad else ""))


# --------------------------------------------------------------------------
# tests that transmit, into a cable with a pad in it
# --------------------------------------------------------------------------

TX_AMPLITUDE = 16384            # half of DAC full scale: -6 dBFS, headroom left
TX_DIGITAL_DBFS = 20 * math.log10(TX_AMPLITUDE / TX_FULL_SCALE)
SWEEP_FS = 4_000_000


# No real measurement through an RF loop has more than ~120 dB of range: the
# receiver's own noise sets the floor long before that. A larger figure means
# the "noise" bins are empty - a synthetic or digital-loopback path - so cap it
# rather than print a number like 300 dBc that no radio could produce.
DB_DISPLAY_CAP = 120.0


def _cap(value):
    return min(value, DB_DISPLAY_CAP)


def _system_gain(rx_dbfs, atten_db, rx_gain_db):
    """Loop transfer normalised for both programmable gains, in dB.

    Divides out the two settings this script keeps changing, so the number
    describes the CABLE and the RADIO's analogue path and nothing else. That
    makes it comparable between frequencies and between runs months apart.
    """
    return rx_dbfs - TX_DIGITAL_DBFS + atten_db - rx_gain_db


def _implied_pad_db(system_gain):
    """Roughly how much external attenuation is in the loop.

    Uses nominal figures for this board (about +7 dBm at full output, receive
    full scale about +2.5 dBm at 0 dB gain), so treat it as +/-3 dB - enough
    to tell a 20 dB pad from a 50 dB one, or from a bare cable, which is all
    it is for.
    """
    return 4.5 - system_gain


class Loop:
    """A TX->RX measurement at one frequency, kept at a safe level."""

    def __init__(self, board, fs=SWEEP_FS, pair=0, min_atten=MIN_TX_ATTEN_DB):
        self.b = board
        self.fs = fs
        self.pair = pair
        self.min_atten = min_atten
        self.atten = START_TX_ATTEN_DB
        self.rx_gain = 30.0
        self.f_off = fs / 16                          # exact bin of the cyclic buffer
        self.running = False
        self.gain_lo, self.gain_hi = board.rx_gain_limits(pair)
        self.drifted = []                             # settings that moved on their own

    def refresh_gain_limits(self):
        """Call after retuning: the legal gain range moves with the band."""
        self.gain_lo, self.gain_hi = self.b.rx_gain_limits(self.pair)
        if not self.gain_lo <= self.rx_gain <= self.gain_hi:
            self.set_levels(rx_gain=self.rx_gain)     # re-clamp into the new band

    # -- transmitter ---------------------------------------------------------

    def start(self):
        self.b.mute_tx()
        self.f_off = self.b.tx_tone(self.f_off, self.fs, TX_AMPLITUDE, self.pair)
        # Order matters. Opening the DAC buffer fires the kernel's preenable
        # hook, which on TX-mute firmware unmutes by restoring a CACHED
        # attenuation - so anything written before this point is overwritten.
        self.b.set_tx_atten(-self.atten, self.pair)
        self.running = True

    def stop(self):
        if self.running:
            self.b.tx_stop()
            self.running = False

    def set_levels(self, atten=None, rx_gain=None):
        """Command a level, snapped to the grid the hardware actually uses.

        The AD9361 quantises: attenuation to 0.25 dB, manual gain to whole dB.
        Snapping here keeps the commanded value equal to the value the chip
        will report, so the drift check below compares like with like instead
        of firing on its own rounding.

        The commanded value stays authoritative. An earlier version adopted
        whatever read back, which is wrong in exactly the case that matters:
        read the radio while something else has transiently moved it and the
        bad value becomes the new target, and the test then chases it.
        """
        if atten is not None:
            self.atten = round(min(60.0, max(self.min_atten, atten)) * 4) / 4
            self.b.set_tx_atten(-self.atten, self.pair)
        if rx_gain is not None:
            self.rx_gain = float(round(min(self.gain_hi,
                                           max(self.gain_lo, rx_gain))))
            self.b.set_rx_gain(self.rx_gain, self.pair)
        time.sleep(0.05)

    def reassert(self):
        """Put the commanded levels back, whatever the radio currently thinks."""
        self.b.set_tx_atten(-self.atten, self.pair)
        self.b.set_rx_gain(self.rx_gain, self.pair)
        time.sleep(0.05)

    # -- measurement ---------------------------------------------------------

    def measure(self, n=16384, verify=True):
        """Capture and find the tone, checking the radio is still where we put it.

        The read-back is not paranoia. During development the received level
        jumped by ~48 dB twice, mid-sweep, with no command issued to cause it,
        and the cause was never pinned down - gain mode, gain, attenuation and
        the driver's mute paths all checked out afterwards. Rather than trust
        that it cannot happen, every measurement confirms the settings still
        read back as commanded, re-asserts them if not, and counts it. If the
        count is non-zero the report says so, which turns an invisible source
        of wrong numbers into a visible one.
        """
        if verify:
            try:
                atten = -self.b.rdf(PHY, f"voltage{self.pair}", "hardwaregain", True)
                gain = self.b.rdf(PHY, f"voltage{self.pair}", "hardwaregain")
                if abs(atten - self.atten) > 0.3 or abs(gain - self.rx_gain) > 0.3:
                    self.drifted.append(
                        f"TX attenuation {atten:.2f} dB (set {self.atten:.2f}), "
                        f"RX gain {gain:.2f} dB (set {self.rx_gain:.2f})")
                    self.reassert()
            except Exception:
                pass
        spec = Spectrum(self.b.capture(n, self.pair))
        return spec, spec.peak_near(self.f_off, self.fs)

    def autorange(self, target=TARGET_RX_DBFS):
        """Bring the received tone to a useful level without ever going loud.

        Only ever reduces attenuation towards the floor set in
        MIN_TX_ATTEN_DB, and backs straight off if the receiver gets anywhere
        near full scale.
        """
        for _ in range(5):
            _, level = self.measure(8192)
            if level > MAX_RX_DBFS:                   # too hot: retreat first
                self.set_levels(atten=self.atten + 10, rx_gain=self.rx_gain - 10)
                continue
            err = target - level                      # >0 means "need more"
            if abs(err) < 2.0:
                break
            want = self.atten - err
            new_atten = min(60.0, max(self.min_atten, want))
            gained = self.atten - new_atten
            self.set_levels(atten=new_atten)
            err -= gained
            if abs(err) > 1.0:
                self.set_levels(rx_gain=self.rx_gain + err)
        spec, level = self.measure()
        return spec, level

    def system_gain(self, level):
        return _system_gain(level, self.atten, self.rx_gain)

    def set_operating_point(self, sysg, rx_gain=46.0, target=TARGET_RX_DBFS):
        """Move to a DEFINED gain/attenuation pair, not wherever we landed.

        Autoranging ends somewhere different every run, and measurements like
        image rejection depend on where it ended: the AD9361's IQ balance is
        not the same above and below the gain table's LNA transition at 52 dB.
        Measured at 66 dB one run and 36 dB the next, image rejection appeared
        to move 15 dB when nothing had changed. Pinning the operating point
        inside the transition-free window makes the number mean something,
        and makes it comparable with a baseline taken months earlier.

        Returns False if the level cannot be reached from here.
        """
        rx_gain = min(self.gain_hi, max(self.gain_lo, rx_gain))
        want = sysg + TX_DIGITAL_DBFS + rx_gain - target
        atten = min(60.0, max(self.min_atten, want))
        self.set_levels(atten=atten, rx_gain=rx_gain)
        return abs(atten - want) < 6.0


def ask_pad_db(args):
    """How much attenuation is in the loop? Get it from the user, not a guess.

    This is not bureaucracy. Knowing the pad is what lets the script turn a
    received level into an absolute transmit power in dBm, and what lets it
    tell you that the loop does not contain the attenuation you think it does
    - which is the failure that destroys receivers.
    """
    if args.pad is not None:
        return args.pad
    if not sys.stdin.isatty():
        raise SystemExit(
            "--loopback needs to know how much attenuation is in the cable.\n"
            "Pass --pad DB (for example --pad 50 for a 20 dB and a 30 dB pad in\n"
            "series, or --pad 0 for a bare cable - which this board's PA can\n"
            "damage the receiver with, so fit one).")
    print("This board has a PGA-102+ power amplifier on transmit: up to about")
    print("+17 dBm, against a receive port rated to +2.5 dBm. A loopback with")
    print("no attenuator in it can damage the receiver.\n")
    while True:
        raw = input("How much attenuation is in the loop, in dB? "
                    "(e.g. 50 for 20+30 in series) ").strip()
        try:
            value = float(raw)
        except ValueError:
            print("  a number, please")
            continue
        if value < 0:
            print("  attenuation is not negative")
            continue
        if value < 15:
            print(f"  {value:g} dB is thin for this board. The script stays "
                  f"below -16 dBm so it will not hurt anything itself, but "
                  f"fit at least 20 dB.")
        return value


def test_loopback(b, rep, args, pair=0):
    g = f"RF loopback, channel {pair}"
    fs = SWEEP_FS
    centre = args.centre

    if b.dev[RX][1] < (pair + 1) * 2:
        rep.add(g, "channel available", WARN,
                f"the capture device has only {b.dev[RX][1]} scan channels, so "
                f"pair {pair} does not exist in this bitstream")
        return

    b.set_rate(fs)
    b.tune(centre)
    b.tune(centre, tx=True)
    b.wr(PHY, TX_LO, "powerdown", 0, True)
    b.set_rx_gain(30, pair)

    loop = Loop(b, fs, pair, args.min_tx_atten)
    loop.start()
    spec, level = loop.autorange()
    floor = spec.floor()
    snr = _cap(level - floor)

    if snr < 12:
        loop.stop()
        rep.check(g, "loopback detected", False,
                  f"transmitted a tone at {centre/1e6:.1f} MHz + "
                  f"{loop.f_off/1e3:.0f} kHz with {loop.atten:.0f} dB of TX "
                  f"attenuation and {loop.rx_gain:.0f} dB of RX gain, and saw "
                  f"only {snr:.1f} dB above the noise floor.\n"
                  f"           Check the cable runs from TX{pair+1} to "
                  f"RX{pair+1}, that the pad is not more than about 60 dB, and "
                  f"that both connectors are tight.")
        return

    sysg = loop.system_gain(level)
    pad_measured = _implied_pad_db(sysg)
    rep.check(g, "loopback detected", True,
              f"tone {snr:.1f} dB above the floor at {centre/1e6:.1f} MHz; "
              f"TX attenuation {loop.atten:.0f} dB, RX gain {loop.rx_gain:.0f} dB")
    rep.add(g, "loop attenuation", INFO,
            f"measures about {pad_measured:.0f} dB (+/-3 dB); you said "
            f"{args.pad:.0f} dB. System gain {sysg:.1f} dB.",
            value=round(sysg, 2), key=f"ch{pair}_system_gain_db")
    rep.data[f"ch{pair}_implied_pad_db"] = round(pad_measured, 1)

    # Does the loop contain what the user believes it contains? Getting this
    # wrong is the mistake that kills receivers, so it is worth saying out loud
    # rather than leaving in a number nobody reads.
    disagreement = pad_measured - args.pad
    rep.check(g, "the loop contains the attenuation you declared",
              abs(disagreement) <= 8,
              f"declared {args.pad:.0f} dB, measured {pad_measured:.0f} dB "
              f"({disagreement:+.0f} dB). More than about 8 dB apart usually "
              f"means a pad is missing, is a different value, or a connector "
              f"is not making.", warn=abs(disagreement) <= 15)

    # -- image rejection ----------------------------------------------------
    # From here on, measure at a fixed operating point rather than wherever
    # autoranging stopped, so these numbers are repeatable and comparable.
    if loop.set_operating_point(sysg):
        spec, level = loop.measure()
    else:
        rep.add(g, "operating point", INFO,
                f"could not reach {TARGET_RX_DBFS:.0f} dBFS at 46 dB RX gain "
                f"within the {loop.min_atten:.0f}-60 dB attenuation range; "
                f"measuring at {loop.atten:.0f} dB / {loop.rx_gain:.0f} dB "
                f"instead, so these figures are less comparable than usual.")
        spec, level = loop.measure()
    rep.add(g, "measured at", INFO,
            f"{level:.1f} dBFS with {loop.atten:.0f} dB TX attenuation and "
            f"{loop.rx_gain:.0f} dB RX gain")

    # A long transform for the spur measurements. The image and the harmonics
    # can sit close to the noise, and the floor per BIN drops 3 dB every time
    # the transform doubles - 131072 points buys about 9 dB over the 16384 used
    # elsewhere. Without it, a spur below the floor reads as "the floor" and
    # the result is a measurement of the noise, quietly reported as distortion.
    spec, level = loop.measure(131072)
    floor = spec.floor()
    usable = level - (floor + 6.0)          # best ratio this capture can resolve

    # Measure as found, then again after forcing a TX quadrature calibration.
    # The AD9361 calibrates quadrature when the LO moves, but the result goes
    # stale, and on this board the as-found figure sits around 35 dBc while the
    # same hardware manages about 54 dBc immediately after a fresh calibration.
    # Failing on the stale number would condemn a perfectly good transmitter,
    # so the check is against what the hardware CAN do; the as-found value is
    # reported next to it, because a large gap is itself worth knowing.
    imr_found = level - spec.peak_near(-loop.f_off, fs)
    try:
        b.wr_dev(PHY, "calib_mode", "tx_quad")
        time.sleep(0.8)
        b.wr_dev(PHY, "calib_mode", "auto")
        loop.reassert()
        spec, level = loop.measure(131072)
        floor = spec.floor()
        usable = level - (floor + 6.0)
    except Exception as exc:
        rep.add(g, "TX quadrature calibration", INFO, f"could not run: {exc}")

    image = spec.peak_near(-loop.f_off, fs)
    imr = level - image
    rep.add(g, "image rejection before recalibrating", INFO,
            f"{imr_found:.1f} dBc as found, {imr:.1f} dBc after a fresh "
            f"TX quadrature calibration",
            value=round(imr_found, 1), key=f"ch{pair}_image_rejection_asfound_dbc")
    if image < floor + 6.0:
        rep.check(g, "image rejection", usable > 35,
                  f"better than {usable:.1f} dBc - the image is below the "
                  f"noise floor of this capture ({floor:.1f} dBFS), so this is "
                  f"a bound, not a reading.",
                  value=round(usable, 1), key=f"ch{pair}_image_rejection_dbc")
    else:
        rep.check(g, "image rejection", imr > 35,
                  f"{imr:.1f} dBc (image at {-loop.f_off/1e3:.0f} kHz is "
                  f"{image:.1f} dBFS, floor {floor:.1f}). Below ~35 dBc points "
                  f"at the quadrature calibration or an unbalanced mixer.",
                  warn=imr > 25, value=round(imr, 1),
                  key=f"ch{pair}_image_rejection_dbc")

    # -- harmonic distortion ------------------------------------------------
    h2 = spec.peak_near(2 * loop.f_off, fs) - level
    h3 = spec.peak_near(3 * loop.f_off, fs) - level
    worst = max(h2, h3)
    buried = spec.peak_near(2 * loop.f_off, fs) < floor + 6.0 and \
             spec.peak_near(3 * loop.f_off, fs) < floor + 6.0
    rep.check(g, "harmonic distortion", worst < -40 or buried,
              (f"both below the noise floor of this capture, so better than "
               f"{-usable:.1f} dBc" if buried else
               f"2nd {h2:.1f} dBc, 3rd {h3:.1f} dBc at {level:.1f} dBFS"),
              warn=worst < -30, value={"h2": round(h2, 1), "h3": round(h3, 1)},
              key=f"ch{pair}_harmonics_dbc")

    # -- TX attenuator linearity --------------------------------------------
    # Sweep quieter only - never towards full output - and check the received
    # level follows one for one. Compression or a damaged output stage bends it.
    base_atten, base_gain = loop.atten, loop.rx_gain
    pts = []
    for step in (0, 5, 10, 15, 20, 25):
        loop.set_levels(atten=base_atten + step)
        lv = loop.measure(8192)[1]
        if not pts or loop.atten != pts[-1][0]:      # clamped: stop, do not repeat
            pts.append((loop.atten, lv))
    loop.set_levels(atten=base_atten)
    span = pts[-1][0] - pts[0][0] if len(pts) > 1 else 0.0
    if span < 10:
        rep.add(g, "TX attenuator is linear", WARN,
                f"could not vary TX attenuation over more than {span:.0f} dB "
                f"(sitting at {base_atten:.0f} dB against the "
                f"{loop.min_atten:.0f}-60 dB limits), so linearity was not "
                f"measured. More attenuation in the cable would give room.")
    else:
        slope, dev = _fit_slope([-a for a, _ in pts], [v for _, v in pts])
        rep.check(g, "TX attenuator is linear",
                  0.94 <= slope <= 1.06 and dev < 1.5,
                  f"{slope:.3f} dB per dB over {span:.0f} dB "
                  f"(worst deviation {dev:.2f} dB); ideal is 1.000",
                  value={"slope": round(slope, 4), "max_dev_db": round(dev, 2)},
                  key=f"ch{pair}_tx_atten_linearity")

    # -- RX gain: does it respond, and is it linear where it can be? --------
    #
    # Two separate questions, because the obvious single test gives the wrong
    # answer. The AD9361's gain "table" is a ladder the driver labels one dB
    # per index, but the labels are nominal: at several indices the LNA/mixer
    # word changes and the real gain takes a step the label does not admit to.
    # Reading the driver's own tables, those transitions sit at commanded 5,
    # 17, 27, roughly 31-37, 52, and every step above 63 - in all three bands.
    # Measured here, the one at 52 is worth about 10 dB. Fit a straight line
    # across them and a perfectly healthy front end reports 0.67 dB per dB.
    #
    # So: check the RANGE across the whole sweep, which is what a dead gain
    # chain loses, and check the SLOPE only inside 38-51 dB, the widest window
    # with no transition in it in any band.
    lo, hi = loop.gain_lo, loop.gain_hi
    want_atten = sysg + TX_DIGITAL_DBFS + min(hi, 60.0) - TARGET_RX_DBFS
    loop.set_levels(atten=min(60.0, max(loop.min_atten, want_atten)))

    coarse = []
    for gain in (10.0, 20.0, 30.0, 40.0, 50.0, 60.0):
        if not lo <= gain <= hi:
            continue
        loop.set_levels(rx_gain=gain)
        lv = loop.measure(8192)[1]
        if lv > MAX_RX_DBFS:
            continue
        coarse.append((loop.rx_gain, lv))
    if len(coarse) >= 3:
        delivered = coarse[-1][1] - coarse[0][1]
        commanded = coarse[-1][0] - coarse[0][0]
        worst_back = min((y[1] - x[1] for x, y in zip(coarse, coarse[1:])),
                         default=0.0)
        rep.check(g, "RX gain responds across its range",
                  delivered > 0.55 * commanded and worst_back > -12.0,
                  f"{delivered:.1f} dB delivered for {commanded:.0f} dB "
                  f"commanded ({delivered/commanded:.2f} dB per dB overall); "
                  f"largest backward step {worst_back:+.1f} dB at a gain-table "
                  f"transition. Both are normal for this chip.",
                  value=round(delivered, 1), key=f"ch{pair}_rx_gain_range_db")

    fine = []
    for gain in (38.0, 42.0, 46.0, 50.0):
        if not lo <= gain <= hi:
            continue
        loop.set_levels(rx_gain=gain)
        lv = loop.measure(8192)[1]
        if lv > MAX_RX_DBFS:
            continue
        fine.append((loop.rx_gain, lv))
    tx_power_point = fine[len(fine) // 2] if fine else None
    if len(fine) >= 3:
        slope, dev = _fit_slope([gn for gn, _ in fine], [v for _, v in fine])
        rep.check(g, "RX gain is linear where the table is honest",
                  0.90 <= slope <= 1.10 and dev < 1.5,
                  f"{slope:.3f} dB per dB over "
                  f"{fine[-1][0]-fine[0][0]:.0f} dB in the 38-51 dB window "
                  f"(worst deviation {dev:.2f} dB)",
                  value={"slope": round(slope, 4), "max_dev_db": round(dev, 2)},
                  key=f"ch{pair}_rx_gain_linearity")
    else:
        rep.add(g, "RX gain is linear where the table is honest", WARN,
                f"only {len(fine)} usable points in the 38-51 dB window at "
                f"this signal level, so linearity was not measured.")

    # Absolute transmit power, now that the pad is known. Receive full scale is
    # about +2.5 dBm at 0 dB gain and moves one for one with gain - but ONLY
    # where the gain table is honest, which is why this uses a point from the
    # window above rather than wherever autoranging happened to land. Treat it
    # as +/-3 dB: it inherits the accuracy of that calibration point.
    if tx_power_point:
        gain_pt, level_pt = tx_power_point
        p_rx_dbm = level_pt + RX_MAX_INPUT_DBM - gain_pt
        p_tx_dbm = p_rx_dbm + args.pad
        p_tx_full = p_tx_dbm + loop.atten - TX_DIGITAL_DBFS
        headroom = p_tx_full - RX_MAX_INPUT_DBM
        rep.add(g, "transmit power", INFO,
                f"{p_tx_dbm:+.1f} dBm at {loop.atten:.0f} dB attenuation and "
                f"{TX_DIGITAL_DBFS:.0f} dBFS drive, so roughly "
                f"{p_tx_full:+.1f} dBm flat out. Looping that back with no "
                f"attenuator would put {headroom:+.0f} dB relative to the "
                f"{RX_MAX_INPUT_DBM:+.1f} dBm the receive port survives.",
                value=round(p_tx_full, 1), key=f"ch{pair}_tx_power_max_dbm")

    loop.set_levels(atten=base_atten, rx_gain=base_gain)

    # -- how quiet is "off"? -------------------------------------------------
    loop.stop()
    time.sleep(0.2)
    quiet_spec, quiet = loop.measure()
    drop = _cap(level - quiet)
    rep.check(g, "transmitter goes quiet when stopped", drop > 50,
              f"tone fell {drop:.1f} dB to {quiet:.1f} dBFS "
              f"(floor {quiet_spec.floor():.1f} dBFS) once the buffer closed "
              f"and the attenuators went to {TX_ATTEN_MUTE} dB",
              warn=drop > 35, value=round(drop, 1), key=f"ch{pair}_tx_mute_depth_db")

    # -- path loss across the tuning range -----------------------------------
    points = [100e6, 900e6, 2400e6] if args.quick else \
             [100e6, 300e6, 700e6, 1200e6, 1800e6, 2400e6, 3500e6, 5000e6]
    curve = {}
    loop.start()
    for f in points:
        try:
            b.tune(f)
            b.tune(f, tx=True)
            time.sleep(0.12)
            loop.refresh_gain_limits()
            loop.set_levels(atten=base_atten, rx_gain=base_gain)
            lv = loop.autorange()[1]
            curve[int(f)] = round(loop.system_gain(lv), 2)
        except Exception as exc:
            curve[int(f)] = None
            rep.add(g, f"path loss at {f/1e6:.0f} MHz", WARN, str(exc))
    loop.stop()
    rep.data[f"ch{pair}_path_loss_curve"] = curve

    if loop.drifted:
        rep.add(g, "settings changed on their own", WARN,
                f"{len(loop.drifted)} time(s) the radio was not where it had "
                f"been set; each was corrected before measuring. First: "
                f"{loop.drifted[0]}")
    else:
        rep.add(g, "settings held throughout", INFO,
                "every measurement confirmed the commanded gain and "
                "attenuation were still in force")

    good = {k: v for k, v in curve.items() if v is not None}
    if good:
        med = statistics.median(good.values())
        outliers = {k: v for k, v in good.items() if abs(v - med) > 15}
        rep.check(g, "path loss is smooth across the range", not outliers,
                  "  ".join(f"{k/1e6:.0f}MHz {v:+.1f}" for k, v in sorted(good.items()))
                  + f"\n           median {med:+.1f} dB"
                  + (f"; more than 15 dB from it: "
                     f"{', '.join(f'{k/1e6:.0f} MHz' for k in outliers)}"
                     if outliers else "; no band-specific hole"),
                  warn=bool(outliers))


def _fit_slope(xs, ys):
    """Least-squares slope, and the worst residual from that fit."""
    n = len(xs)
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    slope = sxy / sxx if sxx else 0.0
    intercept = my - slope * mx
    dev = max(abs(y - (slope * x + intercept)) for x, y in zip(xs, ys))
    return slope, dev


# --------------------------------------------------------------------------
# tests that reach the AD9361's own BIST hardware, over ssh
# --------------------------------------------------------------------------
#
# These read the chip's built-in self test through debugfs, which libiio does
# not expose - hence ssh. They are the deepest checks here and they need no
# cable at all, so they are worth the extra access. Skipped silently if ssh
# cannot log in.

DEBUGFS = "/sys/kernel/debug/iio/iio:device0"


class Shell:
    """Run commands on the board. Uses sshpass if it is installed."""

    def __init__(self, host, password="analog", user="root"):
        import shutil
        import subprocess
        self.sp = subprocess
        self.base = []
        if password and shutil.which("sshpass"):
            self.base = ["sshpass", "-p", password]
        self.base += ["ssh", "-o", "StrictHostKeyChecking=no",
                      "-o", "UserKnownHostsFile=/dev/null",
                      "-o", "LogLevel=ERROR", "-o", "ConnectTimeout=8",
                      f"{user}@{host}"]

    def run(self, command, timeout=90):
        r = self.sp.run(self.base + [command], capture_output=True,
                        text=True, timeout=timeout)
        if r.returncode != 0:
            raise RuntimeError((r.stderr or r.stdout).strip() or
                               f"exit {r.returncode}")
        return r.stdout

    def works(self):
        try:
            return self.run("echo ok", timeout=15).strip() == "ok"
        except Exception:
            return False


def test_digital_interface(b, rep, sh):
    """The LVDS link between the FPGA and the AD9361, and the DMA path."""
    g = "Digital interface (BIST)"

    # 1. The interface eye. The chip walks all 16x16 clock/data delay
    #    combinations with a PRBS running and reports which ones receive it
    #    cleanly. A healthy link has a large contiguous region of passes; a
    #    marginal one - a cracked joint, a degraded driver - shrinks it.
    try:
        sh.run(f"echo 1 > {DEBUGFS}/bist_timing_analysis")
        eye = sh.run(f"cat {DEBUGFS}/bist_timing_analysis")
    except Exception as exc:
        rep.add(g, "digital interface eye scan", WARN, f"unavailable: {exc}")
        eye = ""
    if "PASS" in eye:
        # Each row is one clock delay, each column one data delay. The useful
        # figure is the widest run of passes WITHIN a row - that is the margin
        # the interface actually has at its chosen clock delay. Measuring runs
        # across the whole map end to end would join unrelated rows together.
        # Data rows look like "3:o o o . . ." - one hex label, then 16 cells.
        # The "CLK: ... 'o' = PASS" header also contains a colon and a space,
        # so match the shape rather than just looking for a separator.
        rows = [m.group(2).replace(" ", "") for m in
                (re.match(r"^([0-9a-f]):((?:[o.] ?)+)$", ln.strip())
                 for ln in eye.splitlines()) if m]
        passes = sum(r.count("o") for r in rows)
        widest = max((max((len(run) for run in r.split(".")), default=0)
                      for r in rows), default=0)
        rep.check(g, "digital interface eye", passes >= 40 and widest >= 4,
                  f"{passes} of 256 delay combinations pass, widest window "
                  f"{widest} steps in one row. A healthy link is comfortably "
                  f"above 40 passes.",
                  warn=passes >= 20, value=passes, key="dig_eye_passes")
        rep.data["dig_eye_map"] = eye.strip()

    # 2. The AD9361's internal digital loopback: DAC data is looped straight
    #    back into the ADC path inside the chip, bypassing all RF. A tone that
    #    survives this proves both DMAs, the FPGA datapath and both directions
    #    of the LVDS link, with nothing radiated and no cable fitted.
    try:
        b.mute_tx()
        sh.run(f"echo 1 > {DEBUGFS}/loopback")
        fs = b.rate()
        f_off = fs / 8
        sent = b.tx_tone(f_off, fs, TX_AMPLITUDE)
        time.sleep(0.15)
        spec = Spectrum(b.capture(16384))
        level = spec.peak_near(sent, fs)
        # This path is bit-exact - no converter, no mixer, no noise - so the
        # empty bins are literally zero and a plain SNR would read a few
        # hundred dB. Cap it, and check the LEVEL instead: the tone has to come
        # back at the amplitude it was sent at, which is the part that would
        # break if the datapath were dropping or mangling bits.
        snr = min(level - spec.floor(), 120.0)
        expected = TX_DIGITAL_DBFS
        rep.check(g, "internal digital loopback carries a tone",
                  abs(level - expected) < 3.0 and snr > 60,
                  f"tone at {sent/1e3:.0f} kHz came back at {level:.1f} dBFS "
                  f"(sent at {expected:.1f}), spurious-free by >{snr:.0f} dB - "
                  f"both DMAs and the LVDS link in both directions, no RF "
                  f"involved",
                  value=round(level - expected, 2), key="digital_loopback_error_db")
    except Exception as exc:
        rep.add(g, "internal digital loopback", WARN, f"unavailable: {exc}")
    finally:
        try:
            b.tx_stop()
        except Exception:
            pass
        try:
            sh.run(f"echo 0 > {DEBUGFS}/loopback")
        except Exception:
            rep.add(g, "internal digital loopback disabled again", FAIL,
                    "could not clear the loopback - reboot the board before "
                    "using it for anything else")


# --------------------------------------------------------------------------
# baselines
# --------------------------------------------------------------------------

# What to compare, and how far it may drift before it is worth mentioning.
BASELINE_TOLERANCE = {
    "digital_loopback_error_db": (1.0, "dB of digital loopback level error"),
    "dig_eye_passes": (25.0, "passing eye positions"),
    "dc_offset_dbfs": (12.0, "dB of DC offset"),
}
# ... plus these for every channel that was measured.
BASELINE_TOLERANCE_PER_CHANNEL = {
    "system_gain_db": (3.0, "dB of loop gain"),
    "image_rejection_dbc": (8.0, "dBc of image rejection"),
    "tx_mute_depth_db": (10.0, "dB of mute depth"),
    "tx_power_max_dbm": (3.0, "dB of transmit power"),
}


def _compare_curve(rep, g, baseline, pair):
    """Path loss then versus now, for one channel."""
    key = f"ch{pair}_path_loss_curve"
    old_curve = baseline.get(key) or {}
    new_curve = rep.data.get(key) or {}
    deltas = {}
    for freq, now in new_curve.items():
        then = old_curve.get(str(freq), old_curve.get(freq))
        if isinstance(then, (int, float)) and isinstance(now, (int, float)):
            deltas[int(freq)] = now - then
    if not deltas:
        return
    worst_f = max(deltas, key=lambda k: abs(deltas[k]))
    worst = deltas[worst_f]
    rep.check(g, f"channel {pair} path loss across the range", abs(worst) <= 4.0,
              "  ".join(f"{k/1e6:.0f}MHz {v:+.1f}" for k, v in sorted(deltas.items()))
              + f"\n           worst {worst:+.1f} dB at {worst_f/1e6:.0f} MHz "
                f"(tolerance 4 dB)",
              warn=abs(worst) <= 8.0)


def compare_baseline(rep, baseline):
    g = "Compared with the baseline"
    when = baseline.get("recorded_at", "an earlier run")
    rep.add(g, "baseline", INFO, f"recorded {when}")

    if baseline.get("hw_serial") and rep.data.get("hw_serial") \
            and baseline["hw_serial"] != rep.data["hw_serial"]:
        rep.add(g, "same board", WARN,
                f"baseline is from serial {baseline['hw_serial']}, this is "
                f"{rep.data['hw_serial']} - the comparison is meaningless")

    tolerances = dict(BASELINE_TOLERANCE)
    for pair in (0, 1):
        for key, tv in BASELINE_TOLERANCE_PER_CHANNEL.items():
            tolerances[f"ch{pair}_{key}"] = tv

    for key, (tol, what) in tolerances.items():
        old, new = baseline.get(key), rep.data.get(key)
        if not isinstance(old, (int, float)) or not isinstance(new, (int, float)):
            continue
        delta = new - old
        rep.check(g, key.replace("_", " "), abs(delta) <= tol,
                  f"{old:+.1f} -> {new:+.1f} ({delta:+.1f}, tolerance "
                  f"{tol:.0f} {what})", warn=abs(delta) <= tol * 2)

    for pair in (0, 1):
        _compare_curve(rep, g, baseline, pair)

    old_rails = baseline.get("rails") or {}
    new_rails = rep.data.get("rails") or {}
    drift = {k: new_rails[k] - old_rails[k] for k in new_rails if k in old_rails}
    if drift:
        worst_rail = max(drift, key=lambda k: abs(drift[k]))
        rep.check(g, "supply rails", abs(drift[worst_rail]) < 0.03,
                  f"worst drift {drift[worst_rail]*1000:+.0f} mV on "
                  f"{worst_rail}")


# --------------------------------------------------------------------------
# command line
# --------------------------------------------------------------------------

EPILOG = """
examples:
  %(prog)s                                  everything that needs no cable
  %(prog)s --loopback                       + the RF tests (transmits)
  %(prog)s --loopback --save-baseline hb.json
  %(prog)s --loopback --baseline hb.json    compare against that recording

the loopback:
  TX1 ---[ 20 dB + 30 dB pad ]--- RX1        and TX2 ---[ pad ]--- RX2

  THIS BOARD HAS A POWER AMPLIFIER (Mini-Circuits PGA-102+, 17.7 dB of gain at
  50 MHz falling to 10.4 dB at 6 GHz, P1dB +17.5 dBm). Flat out it delivers
  about +17 dBm into a receive port rated to +2.5 dBm, so a loopback with no
  attenuator in it WILL damage the receiver. Fit at least 20 dB; 40-50 dB is
  comfortable.

  This script itself never transmits with less than 35 dB of its own
  attenuation, which holds it under -16 dBm even into a bare cable. But it can
  only protect you from itself.

  Never run --loopback with an antenna on the TX port.
"""


def build_parser():
    p = argparse.ArgumentParser(
        description="Check a Fishball7020 / PlutoSky board for damage.",
        epilog=EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--uri", default=os.environ.get("SDR_URI", "ip:192.168.2.1"),
                   help="board address (default: %(default)s)")
    p.add_argument("--loopback", action="store_true",
                   help="run the RF tests. THIS TRANSMITS. Needs TX1 cabled to "
                        "RX1 through an attenuator")
    p.add_argument("--channel", default="0", choices=("0", "1", "both"),
                   help="which TX/RX pair the loop is on. 'both' runs channel "
                        "0, then asks you to move the cable to TX2/RX2 "
                        "(default: %(default)s)")
    p.add_argument("--pad", type=float, metavar="DB",
                   help="how much attenuation is in the loopback cable, dB. "
                        "Asked for interactively if not given. Needed to "
                        "report absolute transmit power, and to notice that "
                        "the loop is not what you think it is")
    p.add_argument("--centre", type=float, default=900e6,
                   help="frequency for the detailed loopback tests, Hz "
                        "(default: 900e6)")
    p.add_argument("--min-tx-atten", type=float, default=MIN_TX_ATTEN_DB,
                   metavar="DB",
                   help="lowest TX attenuation the script may use, dB "
                        "(default: %(default)s). Lowering this raises transmit "
                        "power; only do it if you know what is on the cable")
    p.add_argument("--ssh", nargs="?", const="analog", metavar="PASSWORD",
                   help="also run the AD9361 BIST checks, which need shell "
                        "access to the board (default password: analog)")
    p.add_argument("--quick", action="store_true",
                   help="fewer frequency points")
    p.add_argument("--baseline", metavar="FILE",
                   help="compare the results against this recording")
    p.add_argument("--save-baseline", metavar="FILE",
                   help="write this run out as a baseline for later")
    p.add_argument("--json", metavar="FILE", help="write the full results as JSON")
    p.add_argument("--no-colour", action="store_true")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    colour = not args.no_colour and sys.stdout.isatty()

    if args.min_tx_atten < MIN_TX_ATTEN_DB:
        print(f"note: --min-tx-atten {args.min_tx_atten:g} dB is below the "
              f"{MIN_TX_ATTEN_DB:g} dB default. With the PA that puts up to "
              f"{AD9361_TX_MAX_DBM + PA_GAIN_DB - args.min_tx_atten:+.0f} dBm "
              f"on the transmit port, against a receive port rated to "
              f"{RX_MAX_INPUT_DBM:+.1f} dBm. The loop now NEEDS at least "
              f"{max(0, AD9361_TX_MAX_DBM + PA_GAIN_DB - args.min_tx_atten - RX_MAX_INPUT_DBM):.0f} dB "
              f"of attenuation in it.", file=sys.stderr)

    if args.loopback:
        args.pad = ask_pad_db(args)

    rep = Report()
    try:
        board = Board(args.uri)
    except Exception as exc:
        print(f"cannot reach the board at {args.uri}: {exc}\n"
              f"Check the USB Ethernet interface is up and 'ping "
              f"{args.uri.replace('ip:', '')}' answers.", file=sys.stderr)
        return 2

    started = time.time()
    board.save_state()
    try:
        test_identity(board, rep)
        test_power(board, rep)

        if args.ssh:
            host = Board._split(args.uri)[0]
            sh = Shell(host, args.ssh)
            if sh.works():
                test_digital_interface(board, rep, sh)
            else:
                rep.add("Digital interface (BIST)", "shell access", WARN,
                        f"cannot log in to root@{host}. Install sshpass, or add "
                        f"an ssh key, to run the BIST checks.")

        test_receiver(board, rep, args.quick)

        if args.loopback:
            pairs = [0, 1] if args.channel == "both" else [int(args.channel)]
            for i, pair in enumerate(pairs):
                if i:
                    if sys.stdin.isatty():
                        input(f"\nMove the loopback to TX{pair+1} -> RX{pair+1} "
                              f"(same attenuators), then press Enter. ")
                    else:
                        rep.add(f"RF loopback, channel {pair}", "skipped", INFO,
                                "cannot prompt for the cable to be moved when "
                                "not attached to a terminal; rerun with "
                                f"--channel {pair}")
                        break
                test_loopback(board, rep, args, pair)
        else:
            rep.add("RF loopback", "skipped", INFO,
                    "not requested. Cable TX1 to RX1 through a 20-30 dB pad "
                    "and add --loopback to measure the analogue path.")
    except KeyboardInterrupt:
        print("\ninterrupted - restoring the radio", file=sys.stderr)
    finally:
        # Always, on every path: stop transmitting, then put back what we found.
        try:
            board.tx_stop()
        except Exception:
            pass
        board.restore_state()

    rep.data["recorded_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    rep.data["duration_s"] = round(time.time() - started, 1)

    if args.baseline:
        try:
            with open(args.baseline) as fh:
                compare_baseline(rep, json.load(fh))
        except FileNotFoundError:
            rep.add("Compared with the baseline", "baseline file", WARN,
                    f"{args.baseline} does not exist yet - run with "
                    f"--save-baseline {args.baseline} while the board is known good")

    print(rep.render(colour))
    counts = rep.counts()
    print(f"\n{counts[PASS]} passed, {counts[WARN]} warnings, {counts[FAIL]} failed "
          f"in {rep.data['duration_s']:.0f} s")

    if counts[FAIL]:
        verdict, code = "FAULT - see the failures above", 1
    elif counts[WARN]:
        verdict, code = "WORKING, with warnings worth reading", 0
    else:
        verdict, code = "HEALTHY", 0
    print(verdict)
    if not args.loopback:
        print("(analogue front end untested - rerun with --loopback)")

    for path, payload in ((args.save_baseline, rep.data), (args.json, rep.data)):
        if path:
            with open(path, "w") as fh:
                json.dump(payload, fh, indent=2, sort_keys=True)
            print(f"wrote {path}")
    return code


if __name__ == "__main__":
    sys.exit(main())
