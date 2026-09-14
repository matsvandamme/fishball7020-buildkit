#!/usr/bin/env bash
#
# Check that a build produced a complete, sane set of SD-card files, and
# report what is actually in the bitstream. Run it after build_all.sh and
# before you spend ten minutes flashing:
#
#     ./scripts/verify_output.sh            # run from firmware/
#
# Exits non-zero if anything is wrong, so CI can call it too.
#
set -uo pipefail

FW=${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}
OUT=$FW/output
PRJ=$FW/src/hdl/projects/pluto

fail=0
ck() { if eval "$2" >/dev/null 2>&1; then printf '  \033[32mPASS\033[0m  %s\n' "$1"
       else printf '  \033[31mFAIL\033[0m  %s\n' "$1"; fail=1; fi; }
note() { printf '        %s\n' "$1"; }

echo "== SD-card files =="
for f in BOOT.bin devicetree.dtb uEnv.txt uImage uramdisk.image.gz; do
    ck "$f present and non-trivial" "[ -s '$OUT/$f' ] && [ \$(stat -c%s '$OUT/$f') -gt 1000 ]"
done
ck "no stray files in output/" "[ \$(ls -A '$OUT' | grep -cv '^\.gitkeep$') -eq 5 ]"

echo
echo "== FPGA design =="
if [ -r "$PRJ/utilization.rpt" ]; then
    dsp=$(sed -n '/^4\. DSP/,/^5\./p' "$PRJ/utilization.rpt" | awk '/^\| DSPs/{print $4}')
    lut=$(awk -F'|' '/^\| Slice LUTs /{gsub(/ /,"",$3); print $3; exit}' "$PRJ/utilization.rpt")
    ck "utilization report present" "true"
    note "DSP48s ${dsp:-?} / 220   Slice LUTs ${lut:-?} / 53200"
    # 72 is the stock filter (129 taps); the channelizer patch takes it to 96.
    case "$dsp" in
        72) note "-> stock filter" ;;
        96) note "-> channelizer filter (321 taps)" ;;
        *)  note "-> custom design" ;;
    esac
else
    ck "utilization report present ($PRJ/utilization.rpt)" "false"
fi

if grep -q 'rx_ddc' "$PRJ/system_bd.tcl" 2>/dev/null; then
    note "block design: rx_ddc (Fs/4 shifter) is wired in"
    ck "ad_fs4_ddc.v present alongside it" "[ -e '$PRJ/ad_fs4_ddc.v' ]"
else
    note "block design: stock RX path, no rx_ddc"
fi
coe=$(grep -o 'coefile[A-Za-z_0-9]*\.coe' "$PRJ/system_bd.tcl" 2>/dev/null | sort -u | tr '\n' ' ')
note "FIR coefficients: ${coe:-<none found>}"

echo
echo "== bitstream =="
bit=$(ls -S "$PRJ"/pluto.runs/impl_1/*.bit "$PRJ"/*.bit 2>/dev/null | tail -1)
if [ -n "$bit" ]; then
    sz=$(stat -c%s "$bit")
    # An uncompressed XC7Z020 bitstream is ~4.05 MB. Anything well under that
    # means BITSTREAM.GENERAL.COMPRESS took effect - which BOOT.bin needs, or
    # the FSBL runs out of OCM loading it.
    ck "compressed (${sz} B < 3.9 MB uncompressed)" "[ $sz -lt 3900000 ]"
else
    ck "bitstream found" "false"
fi

echo
echo "== timing =="
if [ -r "$PRJ/timing.rpt" ]; then
    read -r wns _ tnsfail total < <(grep -A6 'Design Timing Summary' "$PRJ/timing.rpt" \
        | awk 'NF>=8 && $1 ~ /^-?[0-9.]+$/ {print $1, $2, $3, $4; exit}')
    ck "no failing setup endpoints" "[ '${tnsfail:-x}' = 0 ]"
    note "WNS ${wns:-?} ns over ${total:-?} endpoints"
else
    ck "timing report present ($PRJ/timing.rpt)" "false"
fi

echo
if [ $fail -eq 0 ]; then echo "OK - output/ is ready to flash."; else
    echo "PROBLEMS FOUND - do not flash this build."; fi
exit $fail
