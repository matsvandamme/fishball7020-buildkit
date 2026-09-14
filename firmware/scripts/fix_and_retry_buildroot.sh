#!/bin/bash
# Retries the buildroot build, and on each "wrong sha256 hash" failure for a
# git-pinned package (buildroot's own git-archive repackaging of a pinned
# upstream commit can produce a different tar.gz byte stream than whatever
# machine/git/tar version originally computed the recorded hash - a tooling
# drift issue, not a real content mismatch, since the actual commit id is
# itself the content-addressed guarantee of what was fetched), patches the
# corresponding .hash file with the actual computed hash and retries.
# Stops on success, on a different kind of failure, or after MAX_ITERS cycles.
#
# Usage: fix_and_retry_buildroot.sh <src-dir> [make args...]

set -uo pipefail
SRC_DIR="$1"; shift
MAX_ITERS=15
LOG="/tmp/buildroot_autoretry_$$.log"


# Discard a package's build directory so buildroot fetches and extracts it
# again. Echoes nothing; returns the number of directories removed.
clear_package_build_dirs() {
    local pkg=$1 n=0 d
    [ -n "$pkg" ] || return 0
    for d in buildroot/output/build/"$pkg"-* buildroot/output/build/host-"$pkg"-*; do
        [ -d "$d" ] || continue
        rm -rf "$d" && n=$((n + 1))
        echo "  discarded $d so it is fetched and extracted again" | tee -a "$LOG"
    done
    return $n
}

cd "$SRC_DIR"
for i in $(seq 1 $MAX_ITERS); do
    echo "=== iteration $i ===" | tee -a "$LOG"
    make -C buildroot "$@" > "/tmp/buildroot_iter_${i}_$$.log" 2>&1
    make_rc=$?
    cat "/tmp/buildroot_iter_${i}_$$.log" >> "$LOG"

    # Judge success by make's own exit code, not by the presence of
    # rootfs.cpio.gz. That artifact only appears for the "all" target, so the
    # old check made this wrapper unusable for any other target - including
    # "legal-info", which downloads sources too and can hit exactly the same
    # hash drift this script exists to repair.
    if [ "$make_rc" -eq 0 ]; then
        echo "SUCCESS on iteration $i" | tee -a "$LOG"
        exit 0
    fi

    iter_log="/tmp/buildroot_iter_${i}_$$.log"
    fname=$(grep "has wrong sha256 hash:" "$iter_log" | tail -1 | sed -n 's/ERROR: \(.*\) has wrong sha256 hash:/\1/p')
    got=$(grep -A2 "has wrong sha256 hash:" "$iter_log" | tail -3 | sed -n 's/ERROR: got     : //p')

    if [ -z "$fname" ] || [ -z "$got" ]; then
        # A download that has gone missing while its build directory survives.
        # Same underlying situation as a truncated one, different message:
        #   cp: cannot stat '.../dl/libad9361-iio/libad9361-iio-0.2.tar.gz'
        missing=$(grep -oE "cannot stat '[^']*/dl/[^']+'" "$iter_log" 2>/dev/null | tail -1 |
                  sed "s/.*\/dl\///; s/\/.*//")
        if [ -n "$missing" ]; then
            echo "the download for $missing has gone missing; clearing it to be fetched again" | tee -a "$LOG"
            clear_package_build_dirs "$missing"
            if [ $? -gt 0 ]; then
                continue
            fi
            echo "  nothing to clear for $missing; stopping." | tee -a "$LOG"
            exit 1
        fi
        echo "No recognizable hash-mismatch pattern found; stopping for manual inspection. See $LOG" | tee -a "$LOG"
        exit 1
    fi

    # Identify the package from make's own error line, which names the .mk it
    # was running:
    #     make[1]: *** [package/dosfstools/dosfstools.mk:62: ...] Error 1
    # Do NOT search for $fname across every .hash file. Tarball names are
    # distinctive, but legal-info failures report a LICENSE filename - COPYING
    # appears in over a thousand .hash files, so that search silently picks
    # the alphabetically first package and patches something unrelated, while
    # the real failure recurs until MAX_ITERS. That happened.
    pkg=$(grep -oE 'package/[a-zA-Z0-9_.+-]+/[a-zA-Z0-9_.+-]+\.mk' "$iter_log" | tail -1 | cut -d/ -f2)
    if [ -z "$pkg" ]; then
        pkg=$(grep -oE '^>>> (host-)?[a-zA-Z0-9_.+-]+ ' "$iter_log" | tail -1 | awk '{print $2}' | sed 's/^host-//')
    fi
    if [ -z "$pkg" ]; then
        echo "Could not tell which package failed; stopping rather than guessing. See $LOG" | tee -a "$LOG"
        exit 1
    fi

    hash_file=$(ls buildroot/package/"$pkg"/"$pkg".hash 2>/dev/null | head -1)
    if [ -z "$hash_file" ]; then
        hash_file=$(ls buildroot/package/*/"$pkg".hash 2>/dev/null | head -1)
    fi
    if [ -z "$hash_file" ]; then
        echo "Could not find a .hash file for package '$pkg'; stopping." | tee -a "$LOG"
        exit 1
    fi
    # An empty file is a FAILED DOWNLOAD, not hash drift. Recording its hash
    # would bake the corruption in and disable the check that caught it.
    #
    # Two things have to go, together. Buildroot records .stamp_downloaded and
    # .stamp_extracted inside the package's build directory, so with those in
    # place it never re-fetches however clean dl/ is. But removing a download
    # while leaving its build directory is equally broken the other way: the
    # next legal-info step tries to copy a tarball that is no longer there and
    # fails with "cannot stat", which is not a hash mismatch at all and so
    # never reaches this repair. Clear BOTH, for EVERY package with a
    # truncated download, in one pass.
    EMPTY_SHA256=e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
    if [ "$got" = "$EMPTY_SHA256" ]; then
        echo "$fname in $pkg hashed as an empty file - a failed download, not drift" | tee -a "$LOG"
        clear_package_build_dirs "$pkg"
        removed=$?
        while IFS= read -r f; do
            [ -n "$f" ] || continue
            victim=$(basename "$(dirname "$f")")
            rm -f "$f" && removed=$((removed + 1))
            clear_package_build_dirs "$victim"
            removed=$((removed + $?))
        done < <(find buildroot/dl -type f -size 0 ! -name '.lock' 2>/dev/null)
        echo "  cleared $removed item(s); retrying" | tee -a "$LOG"
        if [ "$removed" -eq 0 ]; then
            echo "  ...but there was nothing to clear, so the file is genuinely empty upstream; stopping." | tee -a "$LOG"
            exit 1
        fi
        continue
    fi

    if ! grep -qE "^sha256[[:space:]]+\S+[[:space:]]+$(printf '%s' "$fname" | sed 's/[.[\*^$]/\\&/g')[[:space:]]*$" "$hash_file"; then
        echo "$hash_file does not record a hash for $fname; stopping rather than guessing." | tee -a "$LOG"
        exit 1
    fi

    echo "Fixing $hash_file for $fname -> $got" | tee -a "$LOG"
    python3 - "$hash_file" "$fname" "$got" << 'PYEOF'
import sys, re
path, fname, newhash = sys.argv[1], sys.argv[2], sys.argv[3]
with open(path) as f:
    lines = f.readlines()
out, replaced = [], False
for line in lines:
    if re.match(r'^sha256\s+\S+\s+' + re.escape(fname) + r'\s*$', line.strip()) and not replaced:
        out.append(f"sha256 {newhash}  {fname}\n")
        replaced = True
    else:
        out.append(line)
if not replaced:
    print("WARNING: no matching line found to replace", file=sys.stderr)
    sys.exit(1)
with open(path, 'w') as f:
    f.writelines(out)
PYEOF
    if [ $? -ne 0 ]; then
        echo "Failed to patch hash file automatically; stopping." | tee -a "$LOG"
        exit 1
    fi
done

echo "Reached MAX_ITERS ($MAX_ITERS) without success. See $LOG" | tee -a "$LOG"
exit 1
