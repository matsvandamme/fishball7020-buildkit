#!/usr/bin/env python3
"""Redraw docs/img/loop-gain-{light,dark}.svg from measured sweep data.

Input is a JSON file (default /tmp/chartdata.json) of the form

    {"ch0": [[freq_hz, median_db, min_db, max_db], ...], "ch1": [...]}

holding TX->RX loop gain with the external attenuator ADDED BACK, so the curve
describes the board rather than the cable. Build it from one or more runs:

    ./sdr_selftest.py --loopback --pad 20 --channel 0 \\
        --sweep-points 60 --sweep-start 70e6 --sweep-stop 6e9 --json run1.json

then take the median/min/max of each frequency's `chN_path_loss_curve` value
across runs and add the pad. Two or more runs give the band; one gives a line.

Standard library only, matching the rest of this repo. The two series colours
are slots 1 and 2 of the validated reference palette at agentskills.io - they
pass the colour-vision and contrast checks as a pair in both light and dark.
Do not substitute them by eye.

Usage:  python3 make_loop_gain_svg.py [output_dir]
"""
import json, math, pathlib, sys

data = json.load(open("/tmp/chartdata.json"))       # {"ch0":[[f,med,lo,hi],...], ...}
W, H = 760, 400
L, R, T, B = 62, 118, 40, 52
PW, PH = W - L - R, H - T - B
FMIN, FMAX = 63e6, 6800e6
YMIN, YMAX = 0.0, 26.0
def x(f): return L + PW*(math.log10(f)-math.log10(FMIN))/(math.log10(FMAX)-math.log10(FMIN))
def y(v): return T + PH*(1-(v-YMIN)/(YMAX-YMIN))
TH = {"light": dict(surface="#fcfcfb", primary="#0b0b0b", secondary="#52514e",
                    muted="#8a8985", grid="#e6e5e1", s1="#2a78d6", s2="#eb6834"),
      "dark":  dict(surface="#1a1a19", primary="#ffffff", secondary="#c3c2b7",
                    muted="#8a8985", grid="#33322f", s1="#3987e5", s2="#d95926")}
NAMES = {"ch0": ("Channel 0", "s1"), "ch1": ("Channel 1", "s2")}
keys = [k for k in ("ch0", "ch1") if k in data]

def build(t):
    c = TH[t]; o = []
    o.append(f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
             f'font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,Arial,sans-serif" role="img" '
             f'aria-label="Transmit to receive loop gain against frequency, 70 MHz to 6 GHz">')
    o.append(f'<rect width="{W}" height="{H}" fill="{c["surface"]}"/>')
    o.append(f'<rect x="{x(2000e6):.1f}" y="{T}" width="{x(FMAX)-x(2000e6):.1f}" height="{PH}" fill="{c["muted"]}" opacity="0.07"/>')
    for v in range(0, 27, 5):
        o.append(f'<line x1="{L}" y1="{y(v):.1f}" x2="{L+PW}" y2="{y(v):.1f}" stroke="{c["grid"]}" stroke-width="1"/>')
        o.append(f'<text x="{L-10}" y="{y(v)+4:.1f}" text-anchor="end" font-size="12" fill="{c["secondary"]}">{v}</text>')
    for f, lab in ((70e6,"70"),(100e6,"100"),(200e6,"200"),(500e6,"500"),(1000e6,"1 GHz"),(2000e6,"2"),(5000e6,"5")):
        o.append(f'<line x1="{x(f):.1f}" y1="{T+PH}" x2="{x(f):.1f}" y2="{T+PH+5}" stroke="{c["grid"]}" stroke-width="1"/>')
        o.append(f'<text x="{x(f):.1f}" y="{T+PH+21}" text-anchor="middle" font-size="12" fill="{c["secondary"]}">{lab}</text>')
    o.append(f'<text x="{L+PW/2:.0f}" y="{H-12}" text-anchor="middle" font-size="12" fill="{c["secondary"]}">Frequency (MHz, log scale)</text>')
    o.append(f'<text x="16" y="{T+PH/2:.0f}" text-anchor="middle" font-size="12" fill="{c["secondary"]}" '
             f'transform="rotate(-90 16 {T+PH/2:.0f})">TX&#8594;RX loop gain (dB)</text>')
    for k in keys:
        rows = data[k]; _n, slot = NAMES[k]
        up = " ".join(f"{x(f):.1f},{y(hi):.1f}" for f,_m,_l,hi in rows)
        dn = " ".join(f"{x(f):.1f},{y(lo):.1f}" for f,_m,lo,_h in reversed(rows))
        o.append(f'<polygon points="{up} {dn}" fill="{c[slot]}" opacity="0.35"/>')
    for k in keys:
        rows = data[k]; name, slot = NAMES[k]
        o.append(f'<polyline points="{" ".join(f"{x(f):.1f},{y(m):.1f}" for f,m,_l,_h in rows)}" '
                 f'fill="none" stroke="{c[slot]}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>')
    # Direct labels, pushed apart if the curves end close together. Two series
    # finishing within a decibel of each other would otherwise overprint.
    ends = sorted(((y(data[k][-1][1]) + 4, x(data[k][-1][0]) + 10, NAMES[k][0]) for k in keys),
                  key=lambda e: e[0])
    MIN_GAP = 16
    placed = []
    for ly, lx, name in ends:
        if placed and ly - placed[-1][0] < MIN_GAP:
            ly = placed[-1][0] + MIN_GAP
        placed.append((ly, lx, name))
    for ly, lx, name in placed:
        o.append(f'<text x="{lx:.1f}" y="{ly:.1f}" font-size="13" fill="{c["primary"]}">{name}</text>')
    if len(keys) > 1:
        lx, ly = L+14, T+16
        for i, k in enumerate(keys):
            name, slot = NAMES[k]; yy = ly + i*17
            o.append(f'<line x1="{lx}" y1="{yy-4}" x2="{lx+18}" y2="{yy-4}" stroke="{c[slot]}" stroke-width="2" stroke-linecap="round"/>')
            o.append(f'<text x="{lx+25}" y="{yy}" font-size="12" fill="{c["primary"]}">{name}</text>')
    # Caveats live bottom-left, where the plot is empty at every frequency.
    o.append(f'<text x="{L+14}" y="{y(3.0):.1f}" font-size="11.5" fill="{c["muted"]}">'
             f'105 points per channel; band = spread over repeated passes, median under 0.1 dB</text>')
    o.append(f'<text x="{L+14}" y="{y(1.3):.1f}" font-size="11.5" fill="{c["muted"]}">'
             f'above 2 GHz, recabling shifts the whole curve by 6&#8211;8 dB</text>')
    # The AD9361 swaps RX gain table at 4 GHz. The step in the curve is that,
    # not the hardware, so mark it rather than leave it looking like a fault.
    # Label goes above the data - the curve never exceeds 21.2 dB - so it
    # cannot collide whatever the trace does.
    gx = x(3986e6)
    o.append(f'<line x1="{gx:.1f}" y1="{y(22.4):.1f}" x2="{gx:.1f}" y2="{y(3.8):.1f}" '
             f'stroke="{c["muted"]}" stroke-width="1" stroke-dasharray="3 3"/>')
    o.append(f'<text x="{gx-8:.1f}" y="{y(23.4):.1f}" text-anchor="end" font-size="11.5" '
             f'fill="{c["muted"]}">AD9361 swaps RX gain table at 4 GHz &#8212; hence the step</text>')
    o.append('</svg>')
    return "\n".join(o)

out = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "/home/matthieu/fishball7020-fpga-devkit/docs/img")
for t in TH:
    (out / f"loop-gain-{t}.svg").write_text(build(t))
print(f"wrote {len(keys)} series to {out}/loop-gain-{{light,dark}}.svg")
