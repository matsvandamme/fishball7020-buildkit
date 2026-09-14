#!/usr/bin/env bash
#
# Simulate the repo's custom HDL and check it against a golden model.
#
#     ./sim/run_sim.sh            # run from firmware/
#     ./sim/run_sim.sh --mutate   # also prove the testbench can fail
#
# Needs Icarus Verilog only:  sudo apt install iverilog
#
# Why this exists: without it, the only way to find out whether an HDL change
# is correct is a ~20 minute Vivado build followed by a flash and a reboot.
# This takes about a second, and it catches the class of mistake that a
# synthesis run cannot - logic that builds and meets timing but computes the
# wrong thing.
#
set -uo pipefail

MUTATE=0
FW=""
for arg in "$@"; do
    case "$arg" in
        --mutate) MUTATE=1 ;;
        -h|--help) sed -n '2,16p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) FW=$arg ;;
    esac
done
[ -n "$FW" ] || FW=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
SIM=$FW/sim
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

if ! command -v iverilog >/dev/null; then
    echo "iverilog is not installed.  sudo apt install iverilog" >&2
    exit 2
fi

# The design under test ships inside an OPTIONAL patch, so it is only in src/
# if that patch has been applied. Take it from there when it is, and otherwise
# lift it straight out of the patch - the simulation should not require you to
# have opted into the channelizer to check that the channelizer is correct.
DUT=$FW/src/hdl/projects/pluto/ad_fs4_ddc.v
PATCHFILE=$FW/patches/optional/0003-wbfm-channelizer.patch
if [ -r "$DUT" ]; then
    cp "$DUT" "$WORK/ad_fs4_ddc.v"
    origin="src/ (the channelizer patch is applied)"
elif [ -r "$PATCHFILE" ]; then
    awk '
        /^\+\+\+ b\/hdl\/projects\/pluto\/ad_fs4_ddc\.v$/ { grab = 1; next }
        grab && /^diff --git/                             { grab = 0 }
        grab && /^\+/                                     { print substr($0, 2) }
    ' "$PATCHFILE" > "$WORK/ad_fs4_ddc.v"
    [ -s "$WORK/ad_fs4_ddc.v" ] || { echo "could not extract the module from $PATCHFILE" >&2; exit 2; }
    origin="patches/optional/0003 (extracted; the patch is not applied)"
else
    echo "cannot find ad_fs4_ddc.v in src/ or in patches/optional/" >&2
    exit 2
fi

fail=0
echo "== ad_fs4_ddc =="
echo "   source: $origin"
# -Wall catches width mismatches and implicit nets, which is most of what goes
# wrong in Verilog that nobody simulates.
if ! iverilog -g2005 -Wall -o "$WORK/tb" "$SIM/tb_ad_fs4_ddc.v" "$WORK/ad_fs4_ddc.v" 2>&1 \
     | sed 's/^/   /' ; then
    fail=1
fi
[ -x "$WORK/tb" ] || { echo "   compilation failed"; exit 1; }
# vvp exits 0 even when the testbench reports mismatches, so judge on what the
# testbench actually said rather than on its exit status.
vvp "$WORK/tb" | tee "$WORK/out.txt" | sed 's/^/   /'
grep -q "^  PASS" "$WORK/out.txt" || fail=1
grep -q "FAIL"    "$WORK/out.txt" && fail=1

# --mutate: prove the testbench can actually fail.
#
# A green test suite means nothing until you have watched it go red. Each
# mutant below is a plausible mistake - the phase counter moved out of the
# valid guard is the one that costs a rebuild and a flash to find on hardware -
# and the testbench must reject every one of them. If a mutant survives, the
# corresponding check is decorative and should be fixed.
if [ $MUTATE -eq 1 ]; then
    echo
    echo "== mutation check: the testbench must reject each of these =="
    survived=0
    mutate() {
        local name=$1 sedexpr=$2
        sed "$sedexpr" "$WORK/ad_fs4_ddc.v" > "$WORK/mutant.v"
        if cmp -s "$WORK/ad_fs4_ddc.v" "$WORK/mutant.v"; then
            echo "   SKIP  $name (mutation did not apply)"; survived=$((survived+1)); return
        fi
        iverilog -g2005 -o "$WORK/mtb" "$SIM/tb_ad_fs4_ddc.v" "$WORK/mutant.v" 2>/dev/null
        if vvp "$WORK/mtb" 2>/dev/null | grep -q "^  PASS"; then
            echo "   SURVIVED  $name  <- the testbench does not catch this"
            survived=$((survived+1))
        else
            echo "   caught    $name"
        fi
    }
    mutate "phase advances every clock, not every sample" \
           's/^      phase <= phase + 2.d1;/      \/\/removed/'
    mutate "sign error in the -j quadrant"      's/q_out <= -i_in/q_out <= i_in/'
    mutate "I and Q swapped in the +j quadrant" 's/i_out <= -q_in; q_out <=  i_in/i_out <= i_in; q_out <= -q_in/'
    mutate "valid_out not registered"           's/valid_out <= valid_in;/valid_out <= 1'"'"'b1;/'
    if [ $survived -ne 0 ]; then
        echo "   $survived mutant(s) survived - the testbench is weaker than it looks"
        fail=1
    else
        echo "   all mutants caught"
    fi
fi

echo
if [ $fail -eq 0 ]; then echo "SIMULATION OK"; else echo "SIMULATION FAILED"; fi
exit $fail
