#!/usr/bin/env python3
"""gen_fir_coe.py - generate a Xilinx FIR Compiler .coe file for this board's RX path.

The Python sibling of gen_fir_coe.m. Same output format, same scaling
convention, but standard library only - no MATLAB, no Signal Processing
Toolbox, no numpy. Run it anywhere.

It designs a Kaiser-windowed sinc rather than the equiripple (firpm) filter
the MATLAB script produces. Equiripple hits a given stopband with ~30% fewer
taps, but taps are close to free in a DECIMATING filter: the IP gets D input
sample periods' worth of clock cycles to compute each output, so it folds the
work onto few multipliers. Spending taps to avoid depending on a toolbox is a
good trade here. If you need the tap count minimised, use gen_fir_coe.m.

WHAT THIS DEFAULT CONFIGURATION IS FOR
--------------------------------------
Isolating ONE WBFM broadcast channel in the FPGA, so only that channel ever
crosses USB. See docs/wbfm-channelizer.md for the full picture. The short
version, because it determines every number below:

The AD9361 is tuned 1.056 MHz BELOW the wanted station, so its LO leakage and
DC offset do not land in the middle of the audio. That puts the wanted channel
at +1.056 MHz in complex baseband, not at DC - and a real-coefficient FIR
cannot select it there, because a real filter's magnitude response is
symmetric about DC and would pass the mirror image at -1.056 MHz just as well.

So ad_fs4_ddc.v shifts the channel down to DC first, by exactly Fs/4, which
costs no multipliers at all. THIS filter then runs at DC where a real lowpass
is exactly the right tool.

    LO 101.044 MHz, Fs 4.224 MSPS
        wanted channel at +1.056 MHz = Fs/4,  DC spur at 0
    ad_fs4_ddc  (x e^-j*pi*n/2)
        wanted channel at DC,  DC spur now at -1.056 MHz
    rx_fir_decimator  <- THIS FILTER, decimating by 8
        528 kSPS out, nothing left but the channel

SCALING - the part that silently breaks things if you get it wrong.
The stock coefile_int.coe has sum(h) = 131072 (2^17) with 16-bit signed
INTEGER coefficients (the IP is configured Quantization =
Integer_Coefficients, Coefficient_Fractional_Bits = 0, so these are literal
integers). That DC gain is what the rest of the datapath is built around.
Keep TARGET_DC_GAIN at 2^17 and the filter drops in at the stock output
level. If you see clipping, halve it - do NOT rescale to "max coefficient =
32767", which changes the passband gain arbitrarily.
"""

import math
import sys

# ---------------------------------------------------------------------------
# Configuration - edit this block
# ---------------------------------------------------------------------------

FS_IN = 4_224_000       # sample rate at the FILTER INPUT [Hz] (= AD9361 rate)
D = 8                   # decimation rate of the IP this file feeds
FPASS = 100_000         # passband edge [Hz]: half the WBFM channel width
FSTOP = 175_000         # stopband edge [Hz]: below the adjacent channel
NTAPS = 321             # odd. See the tap-count note below.
ASTOP = 75              # REQUIRED stopband attenuation [dB] - the pass/fail gate
ASTOP_DESIGN = 85       # attenuation asked of the Kaiser window - see below

# WHY TWO ATTENUATION NUMBERS, AND WHY 321 TAPS IS THE SWEET SPOT
# ---------------------------------------------------------------
# The Kaiser formula's attenuation argument is a design target, not a result,
# so gating on the same number you designed with is circular and lands you a
# fraction of a dB inside the requirement. ASTOP_DESIGN aims high; ASTOP is
# the honest gate. The margin between them is the point.
#
# Do not raise NTAPS hoping for a deeper stopband: past ~321 taps this filter
# is limited by 16-BIT COEFFICIENT QUANTIZATION, not by tap count, and more
# taps make it slightly WORSE (more coefficients, more rounding error). At
# Fpass 100 kHz / Fstop 175 kHz / Fs 4.224 MHz, worst-case stopband measured
# ideal (float) vs quantized to 16-bit integers:
#
#     taps   ideal      quantized
#      257   -50.3 dB   -50.3 dB    tap-limited, transition not settled
#      321   -84.5 dB   -78.5 dB    <- best quantized result, chosen
#      385   -86.9 dB   -75.3 dB    ideal better, quantized worse
#      449   -88.9 dB   -75.0 dB    ditto
#
# ~-78 dB is the floor for 16-bit taps here. To go deeper you would have to
# widen Coefficient_Width in adi_fir_filter_bd.tcl, which changes the IP for
# the TX interpolator too. -78 dB is far more than WBFM demodulation needs.

COEF_WIDTH = 16         # CONFIG.Coefficient_Width
TARGET_DC_GAIN = 2 ** 17  # matches stock coefile_int.coe - see docstring
OUTFILE = "coefile_wbfm_102100.coe"

# Frequency of the spur the offset tuning was meant to escape, as it appears
# at THIS filter's input (i.e. after the Fs/4 shift). Set to None if you are
# not using ad_fs4_ddc.
DC_SPUR_AT = FS_IN / 4


# ---------------------------------------------------------------------------
# Kaiser-windowed sinc
# ---------------------------------------------------------------------------

def bessel_i0(x):
    """Modified Bessel function of the first kind, order 0, by series."""
    total, term, k = 1.0, 1.0, 1
    while True:
        term *= (x / 2.0 / k) ** 2
        total += term
        if term < 1e-16 * total:
            return total
        k += 1


def kaiser_beta(atten_db):
    if atten_db > 50:
        return 0.1102 * (atten_db - 8.7)
    if atten_db >= 21:
        return 0.5842 * (atten_db - 21) ** 0.4 + 0.07886 * (atten_db - 21)
    return 0.0


def design_lowpass(ntaps, fcut, fs, atten_db):
    """Kaiser-windowed sinc lowpass. fcut is the -6 dB point."""
    if ntaps % 2 == 0:
        raise ValueError("NTAPS must be odd so the filter has an integer group delay")
    beta = kaiser_beta(atten_db)
    m = ntaps - 1
    taps = []
    for n in range(ntaps):
        k = n - m / 2.0
        sinc = 2 * fcut / fs if k == 0 else math.sin(2 * math.pi * fcut * k / fs) / (math.pi * k)
        window = bessel_i0(beta * math.sqrt(max(0.0, 1 - (2.0 * n / m - 1) ** 2))) / bessel_i0(beta)
        taps.append(sinc * window)
    return taps


def quantize(taps, gain, width):
    """Scale to integers with an exact DC gain, then check the width fits."""
    scaled = [int(round(t * gain / sum(taps))) for t in taps]
    # Correct rounding drift by nudging the centre (largest) tap, where a
    # +/-1 change is least significant.
    biggest = max(range(len(scaled)), key=lambda i: abs(scaled[i]))
    scaled[biggest] += gain - sum(scaled)
    limit = 2 ** (width - 1) - 1
    peak = max(abs(t) for t in scaled)
    if peak > limit:
        raise SystemExit(
            f"ERROR: coefficients overflow {width}-bit signed (peak {peak} > {limit}).\n"
            f"       Widen the transition band, or halve TARGET_DC_GAIN.")
    return scaled, peak, limit


def response_db(taps, freq, fs):
    """Magnitude response at one frequency, in dB relative to DC gain."""
    acc = complex(0.0, 0.0)
    for n, c in enumerate(taps):
        acc += c * complex(math.cos(-2 * math.pi * freq * n / fs),
                           math.sin(-2 * math.pi * freq * n / fs))
    return 20 * math.log10(abs(acc) / sum(taps) + 1e-300)


# ---------------------------------------------------------------------------
# Design
# ---------------------------------------------------------------------------

fs_out = FS_IN / D
print(f"Fs_in  = {FS_IN} Hz")
print(f"Fs_out = {fs_out:.0f} Hz (decimate by {D})")
print(f"Fpass  = {FPASS/1e3:.1f} kHz")
print(f"Fstop  = {FSTOP/1e3:.1f} kHz")
print(f"taps   = {NTAPS}\n")

if FSTOP <= FPASS:
    raise SystemExit("ERROR: FSTOP must exceed FPASS.")
if FSTOP >= FS_IN / 2:
    raise SystemExit("ERROR: FSTOP exceeds the input Nyquist frequency.")

# Anti-alias sanity check. Decimating by D folds everything near k*Fs_out onto
# the output baseband. The passband survives only if the stopband already
# starts below the first band that folds onto it.
first_fold = fs_out - FPASS
if FSTOP > first_fold:
    print(f"WARNING: Fstop ({FSTOP/1e3:.1f} kHz) is above the first aliasing band "
          f"({first_fold/1e3:.1f} kHz).\n"
          f"         Content between them will fold onto the passband.\n")

taps = design_lowpass(NTAPS, (FPASS + FSTOP) / 2, FS_IN, ASTOP_DESIGN)
hq, peak, limit = quantize(taps, TARGET_DC_GAIN, COEF_WIDTH)

print(f"sum(h) = {sum(hq)}  (target {TARGET_DC_GAIN})")
print(f"max|h| = {peak}  (limit {limit})\n")

# ---------------------------------------------------------------------------
# Verify BEFORE spending an hour on a rebuild
# ---------------------------------------------------------------------------

probes = [
    (FPASS, "passband edge (channel edge)"),
    (FSTOP, "stopband edge"),
    (2 * FPASS, "adjacent channel carrier"),
    (first_fold, "first band that folds onto the passband"),
]
if DC_SPUR_AT is not None:
    # After the Fs/4 shift the LO-leakage spur sits here. Decimation folds it
    # straight back onto DC when it is an exact multiple of Fs_out, so the
    # ONLY thing keeping it out of the audio is this filter's stopband.
    probes.append((DC_SPUR_AT, "LO-leakage spur (folds onto DC after decimation)"))

print("response:")
for freq, label in probes:
    print(f"  {freq/1e3:9.1f} kHz  {response_db(hq, freq, FS_IN):8.2f} dB   {label}")

# Passband ripple across the whole channel, and the true worst-case stopband.
STEPS = 4000
pass_pts = [response_db(hq, FPASS * i / 200, FS_IN) for i in range(201)]
ripple = max(pass_pts) - min(pass_pts)
worst_stop = max(response_db(hq, FS_IN / 2 * i / STEPS, FS_IN)
                 for i in range(int(STEPS * 2 * FSTOP / FS_IN), STEPS + 1))
print(f"\npassband ripple (0..{FPASS/1e3:.0f} kHz) = {ripple:.4f} dB")
print(f"worst stopband (>= {FSTOP/1e3:.0f} kHz)   = {worst_stop:.2f} dB  (need <= -{ASTOP})")

failed = False
if worst_stop > -ASTOP:
    print(f"\nFAIL: stopband is only {worst_stop:.1f} dB. Increase NTAPS, or widen "
          f"the transition by raising FSTOP.")
    failed = True
if ripple > 0.1:
    print(f"\nFAIL: passband ripple {ripple:.4f} dB is too high for clean audio.")
    failed = True
if failed:
    sys.exit(1)

# ---------------------------------------------------------------------------
# Write the .coe
# ---------------------------------------------------------------------------

with open(OUTFILE, "w") as f:
    f.write("; Xilinx FIR Compiler coefficient file\n")
    f.write("; Generated by gen_fir_coe.py - do not edit by hand\n")
    f.write(f"; WBFM channel filter, {NTAPS} taps, {COEF_WIDTH}-bit signed integer\n")
    f.write(f"; Fs_in = {FS_IN} Hz, decimate by {D} -> {fs_out:.0f} Hz\n")
    f.write(f"; Fpass = {FPASS} Hz, Fstop = {FSTOP} Hz, stopband {worst_stop:.1f} dB\n")
    f.write(f"; sum(h) = {sum(hq)} (= 2^17, the stock coefile_int.coe DC gain)\n")
    f.write("Radix = 10;\n")
    f.write(f"Coefficient_Width = {COEF_WIDTH};\n")
    f.write("CoefData = " + ",\n".join(str(c) for c in hq) + ";\n")

print(f"\nWrote {OUTFILE}")
