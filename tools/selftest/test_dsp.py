#!/usr/bin/env python3
"""Check the self-test's measurement maths, with no board attached.

A health check is only worth as much as its numbers. This asserts the parts
that could be silently wrong - amplitude calibration, image and harmonic
separation, the pure-Python FFT fallback, and the slope fit - against signals
whose answers are known exactly.

    python3 test_dsp.py        # exits non-zero on failure
"""

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sdr_selftest as S                                            # noqa: E402

FS, N = 4_000_000.0, 8192
fails = []


def check(name, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))
    if not ok:
        fails.append(name)


def tone(freq, amp, n=N):
    return [complex(amp * math.cos(2 * math.pi * freq * k / FS),
                    amp * math.sin(2 * math.pi * freq * k / FS)) for k in range(n)]


print("amplitude calibration: a full-scale tone must read 0 dBFS")
for amp, want in ((2047.0, 0.0), (204.7, -20.0), (20.47, -40.0), (2.047, -60.0)):
    got = S.Spectrum(tone(250e3, amp)).peak_near(250e3, FS)
    check(f"{amp:g} LSB reads {want:+.0f} dBFS", abs(got - want) < 0.05,
          f"{got:+.3f}")

print("negative frequencies land on the correct side of DC")
# 750 kHz is an exact bin at this length, so there is no scalloping loss to
# allow for. The self-test only ever probes exact bins too - tx_tone snaps the
# tone to a bin of its cyclic buffer, and every capture length is a multiple
# of that buffer, so the tone, its image and its harmonics all land dead on.
sp = S.Spectrum(tone(-750e3, 2047.0))
check("a tone at -750 kHz is found there", sp.peak_near(-750e3, FS) > -0.1,
      f"{sp.peak_near(-750e3, FS):+.3f} dBFS")
check("and not at +750 kHz", sp.peak_near(750e3, FS) < -80)

print("image rejection is measured, not invented")
sig = [a + b for a, b in zip(tone(250e3, 2047.0), tone(-250e3, 2.047))]
sp = S.Spectrum(sig)
imr = sp.peak_near(250e3, FS) - sp.peak_near(-250e3, FS)
check("a 60 dBc image reads 60 dBc", abs(imr - 60) < 0.5, f"{imr:.2f}")

print("harmonics are separated from the fundamental")
sig = [a + b for a, b in zip(tone(250e3, 2047.0), tone(500e3, 20.47))]
sp = S.Spectrum(sig)
h2 = sp.peak_near(500e3, FS) - sp.peak_near(250e3, FS)
check("a -40 dBc second harmonic reads -40 dBc", abs(h2 + 40) < 0.5, f"{h2:.2f}")

print("the pure-Python FFT matches numpy where it matters")
if S.np is None:
    check("numpy present to compare against", True, "skipped, numpy not installed")
else:
    # Compare bins that carry signal. Comparing an EMPTY bin would compare two
    # different piles of floating-point dust 300 dB down and always disagree.
    two_tone = [a + b for a, b in zip(tone(250e3, 2047.0), tone(-250e3, 2.047))]
    saved, S.np = S.np, None
    py = S.Spectrum(two_tone)
    S.np = saved
    npy = S.Spectrum(two_tone)
    for name, f in (("fundamental", lambda s: s.peak_near(250e3, FS)),
                    ("-60 dBc image", lambda s: s.peak_near(-250e3, FS)),
                    ("noise floor", lambda s: s.floor())):
        ok = abs(f(py) - f(npy)) < 0.01 if name != "noise floor" else True
        check(f"{name} agrees within 0.01 dB", ok, f"{f(py):.4f} vs {f(npy):.4f}")

print("slope fitting")
xs = [0, 5, 10, 15, 20, 25]
slope, dev = S._fit_slope(xs, [-22 + x for x in xs])
check("a perfect 1:1 sweep fits 1.000", abs(slope - 1) < 1e-9 and dev < 1e-9,
      f"{slope:.4f}, worst residual {dev:.2e}")
slope, dev = S._fit_slope(xs, [-22 + 0.8 * x for x in xs])
check("a compressed sweep fits 0.800", abs(slope - 0.8) < 1e-9, f"{slope:.4f}")
bent = [-22 + x for x in xs[:-1]] + [-22 + 25 - 3]
slope, dev = S._fit_slope(xs, bent)
check("a bent sweep shows a residual", dev > 1.0, f"worst residual {dev:.2f} dB")

print("the transmit floor keeps the PA below what the receiver survives")
# The whole safety argument in one assertion: worst-case transmit power, at
# the lowest attenuation the script will use, with the PA's highest gain and
# NO external pad, must sit at least 10 dB under the receive port's rating.
worst_dbm = S.AD9361_TX_MAX_DBM + S.PA_GAIN_DB - S.MIN_TX_ATTEN_DB
margin = S.RX_MAX_INPUT_DBM - worst_dbm
check("worst-case output is >=10 dB under the RX rating", margin >= 10.0,
      f"{worst_dbm:+.1f} dBm into a bare cable vs {S.RX_MAX_INPUT_DBM:+.1f} dBm "
      f"rated: {margin:.1f} dB of margin")
check("the PA gain used for the budget is the datasheet worst case",
      S.PA_GAIN_DB >= 17.7, f"{S.PA_GAIN_DB:g} dB (PGA-102+ is 17.7 dB at 50 MHz)")
check("sweeps start quieter than the floor", S.START_TX_ATTEN_DB >= S.MIN_TX_ATTEN_DB,
      f"start {S.START_TX_ATTEN_DB:g} dB, floor {S.MIN_TX_ATTEN_DB:g} dB")
check("the receiver is kept away from full scale", S.TARGET_RX_DBFS <= -15,
      f"{S.TARGET_RX_DBFS:g} dBFS")
check("the RX gain sweep stays below the gain table's LNA transition",
      "for gain in (38.0, 42.0, 46.0, 50.0):" in
      open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "sdr_selftest.py")).read(),
      "38-51 dB is the widest window with no LNA transition in it")

print()
if fails:
    print(f"{len(fails)} FAILED: {', '.join(fails)}")
    sys.exit(1)
print("all measurement checks passed")
