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
# port. The AD9361's RX input is rated to about +2.5 dBm; its transmitter
# reaches about +7 dBm at 0 dB attenuation. Holding TX attenuation at 20 dB
# or more caps the output at roughly -13 dBm, which is 15 dB below the RX
# rating EVEN IF THE ATTENUATOR IS MISSING and the ports are joined by a bare
# cable. That margin is the reason for this floor, and the reason the sweeps
# below only ever work downward towards it from 40 dB.
#
# A 30 dB span is ample to prove the gain chain is linear, so there is nothing
# to gain from going louder. --min-tx-atten can lower it, and says why not to.
#
MIN_TX_ATTEN_DB = 20.0          # never transmit with less attenuation than this
START_TX_ATTEN_DB = 40.0        # where every loopback measurement begins
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
        if atten is not None:
            self.atten = min(60.0, max(self.min_atten, atten))
            self.b.set_tx_atten(-self.atten, self.pair)
        if rx_gain is not None:
            self.rx_gain = min(70.0, max(0.0, rx_gain))
            self.b.set_rx_gain(self.rx_gain, self.pair)
        time.sleep(0.05)

    # -- measurement ---------------------------------------------------------

    def measure(self, n=16384):
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


def test_loopback(b, rep, args):
    g = "RF loopback"
    fs = SWEEP_FS
    centre = args.centre
    b.set_rate(fs)
    b.tune(centre)
    b.tune(centre, tx=True)
    b.wr(PHY, TX_LO, "powerdown", 0, True)
    b.set_rx_gain(30)

    loop = Loop(b, fs, args.channel, args.min_tx_atten)
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
                  f"           Check the cable is between TX1 and RX1 (not RX2), "
                  f"that the pad is not more than about 50 dB, and that both "
                  f"connectors are tight.")
        return

    sysg = loop.system_gain(level)
    pad = _implied_pad_db(sysg)
    rep.check(g, "loopback detected", True,
              f"tone {snr:.1f} dB above the floor at {centre/1e6:.1f} MHz; "
              f"TX attenuation {loop.atten:.0f} dB, RX gain {loop.rx_gain:.0f} dB")
    rep.add(g, "external attenuation in the loop", INFO,
            f"about {pad:.0f} dB (+/-3 dB). System gain {sysg:.1f} dB.",
            value=round(sysg, 2), key="system_gain_db")
    rep.data["implied_pad_db"] = round(pad, 1)

    if pad < 0:
        rep.add(g, "attenuator check", WARN,
                f"the loop shows {-pad:.0f} dB MORE gain than a passive cable "
                f"can explain. Either something in the path is amplifying, or "
                f"this is not an RF loop at all - the AD9361's internal digital "
                f"loopback looks exactly like this. Check "
                f"/sys/kernel/debug/iio/iio:device0/loopback reads 0.")
    elif pad < 12:
        rep.add(g, "attenuator check", WARN,
                f"only about {pad:.0f} dB of external attenuation. This script "
                f"stays below -13 dBm so nothing is at risk, but fit at least "
                f"20 dB before driving this loop with anything else.")

    # -- image rejection ----------------------------------------------------
    image = spec.peak_near(-loop.f_off, fs)
    imr = _cap(level - image)
    rep.check(g, "image rejection", imr > 35,
              f"{imr:.1f} dBc (image at {-loop.f_off/1e3:.0f} kHz is "
              f"{image:.1f} dBFS). Below ~35 dBc points at the quadrature "
              f"calibration or an unbalanced mixer.",
              warn=imr > 25, value=round(imr, 1), key="image_rejection_dbc")

    # -- harmonic distortion ------------------------------------------------
    h2 = max(spec.peak_near(2 * loop.f_off, fs) - level, -DB_DISPLAY_CAP)
    h3 = max(spec.peak_near(3 * loop.f_off, fs) - level, -DB_DISPLAY_CAP)
    worst = max(h2, h3)
    rep.check(g, "harmonic distortion", worst < -40,
              f"2nd {h2:.1f} dBc, 3rd {h3:.1f} dBc at {level:.1f} dBFS",
              warn=worst < -30, value={"h2": round(h2, 1), "h3": round(h3, 1)},
              key="harmonics_dbc")

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
        # Every step clamped, so there is nothing to fit. Say that rather than
        # report a slope of zero as if the attenuator were broken.
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
                  key="tx_atten_linearity")

    # -- RX gain linearity ---------------------------------------------------
    pts = []
    for gain in [base_gain + d for d in (-20, -10, 0, 10, 20)]:
        if not 0 <= gain <= 70:
            continue
        loop.set_levels(rx_gain=gain)
        lv = loop.measure(8192)[1]
        if lv > MAX_RX_DBFS:                          # keep out of compression
            continue
        if pts and loop.rx_gain == pts[-1][0]:        # clamped against a limit
            continue
        pts.append((loop.rx_gain, lv))
    loop.set_levels(rx_gain=base_gain)
    span = pts[-1][0] - pts[0][0] if len(pts) > 1 else 0.0
    if len(pts) >= 3 and span >= 15:
        slope, dev = _fit_slope([gn for gn, _ in pts], [v for _, v in pts])
        rep.check(g, "RX gain is linear", 0.90 <= slope <= 1.10 and dev < 2.0,
                  f"{slope:.3f} dB per dB over {span:.0f} dB "
                  f"(worst deviation {dev:.2f} dB)",
                  value={"slope": round(slope, 4), "max_dev_db": round(dev, 2)},
                  key="rx_gain_linearity")
    else:
        rep.add(g, "RX gain is linear", WARN,
                f"only {span:.0f} dB of usable RX gain range at this signal "
                f"level, so linearity was not measured.")

    # -- how quiet is "off"? -------------------------------------------------
    # Stop the stream and mute, then look again at the same bin. This is the
    # transmit chain's residual leakage measured through the loop, and it is
    # the check that notices if something has quietly unmuted the radio.
    loop.stop()
    time.sleep(0.2)
    quiet_spec, quiet = loop.measure()
    drop = _cap(level - quiet)
    rep.check(g, "transmitter goes quiet when stopped", drop > 50,
              f"tone fell {drop:.1f} dB to {quiet:.1f} dBFS "
              f"(floor {quiet_spec.floor():.1f} dBFS) once the buffer closed "
              f"and the attenuators went to {TX_ATTEN_MUTE} dB",
              warn=drop > 35, value=round(drop, 1), key="tx_mute_depth_db")

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
            loop.set_levels(atten=base_atten, rx_gain=base_gain)
            _, lv = loop.autorange()
            curve[int(f)] = round(loop.system_gain(lv), 2)
        except Exception as exc:
            curve[int(f)] = None
            rep.add(g, f"path loss at {f/1e6:.0f} MHz", WARN, str(exc))
    loop.stop()
    rep.data["path_loss_curve"] = curve

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
    "system_gain_db": (3.0, "dB of loop gain"),
    "image_rejection_dbc": (8.0, "dBc of image rejection"),
    "tx_mute_depth_db": (10.0, "dB of mute depth"),
    "digital_loopback_error_db": (1.0, "dB of digital loopback level error"),
    "dig_eye_passes": (25.0, "passing eye positions"),
    "dc_offset_dbfs": (12.0, "dB of DC offset"),
}


def compare_baseline(rep, baseline):
    g = "Compared with the baseline"
    when = baseline.get("recorded_at", "an earlier run")
    rep.add(g, "baseline", INFO, f"recorded {when}")

    if baseline.get("hw_serial") and rep.data.get("hw_serial") \
            and baseline["hw_serial"] != rep.data["hw_serial"]:
        rep.add(g, "same board", WARN,
                f"baseline is from serial {baseline['hw_serial']}, this is "
                f"{rep.data['hw_serial']} - the comparison is meaningless")

    for key, (tol, what) in BASELINE_TOLERANCE.items():
        old, new = baseline.get(key), rep.data.get(key)
        if not isinstance(old, (int, float)) or not isinstance(new, (int, float)):
            continue
        delta = new - old
        rep.check(g, key.replace("_", " "), abs(delta) <= tol,
                  f"{old:+.1f} -> {new:+.1f} ({delta:+.1f}, tolerance "
                  f"{tol:.0f} {what})", warn=abs(delta) <= tol * 2)

    old_curve = baseline.get("path_loss_curve") or {}
    new_curve = rep.data.get("path_loss_curve") or {}
    shared = [k for k in new_curve
              if str(k) in {str(x) for x in old_curve} and new_curve[k] is not None]
    if shared:
        deltas = {}
        for k in shared:
            o = old_curve.get(str(k), old_curve.get(k))
            if isinstance(o, (int, float)):
                deltas[k] = new_curve[k] - o
        if deltas:
            worst_f = max(deltas, key=lambda k: abs(deltas[k]))
            worst = deltas[worst_f]
            rep.check(g, "path loss across the range", abs(worst) <= 4.0,
                      "  ".join(f"{int(k)/1e6:.0f}MHz {v:+.1f}"
                                for k, v in sorted(deltas.items()))
                      + f"\n           worst {worst:+.1f} dB at "
                        f"{int(worst_f)/1e6:.0f} MHz (tolerance 4 dB)",
                      warn=abs(worst) <= 8.0)

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
  TX1 ---[ 20 or 30 dB pad ]--- RX1     (both pads in series is fine too)

  This script never transmits with less than 20 dB of its own attenuation, so
  its output stays at or below about -13 dBm - roughly 15 dB under the AD9361
  receive port's +2.5 dBm rating even with no pad in the loop at all. Fit the
  pad anyway: it is the margin that protects you from everything else.

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
    p.add_argument("--channel", type=int, default=0, choices=(0, 1),
                   help="which TX/RX pair the loop is on (default: 0)")
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
              f"{MIN_TX_ATTEN_DB:g} dB default. Transmit power rises to about "
              f"{7 - args.min_tx_atten:.0f} dBm; the AD9361 receive port is "
              f"rated to +2.5 dBm, so the loop now NEEDS a real attenuator.",
              file=sys.stderr)

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
            test_loopback(board, rep, args)
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
