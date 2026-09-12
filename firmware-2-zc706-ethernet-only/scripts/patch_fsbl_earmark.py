#!/usr/bin/env python3
"""Patch a freshly-scaffolded fsbl_hooks.c to print a unique build tag over
UART right after the PL bitstream loads (FsblHookAfterBitstreamDload) - this
runs unconditionally on every boot, before handoff to u-boot, so it's the
most reliable "did my new build actually land on the device" signal
available: independent of Linux/rootfs, visible on the serial console alone.

Usage: patch_fsbl_earmark.py <path-to-fsbl_hooks.c> <build-tag>
"""
import sys

def main():
    if len(sys.argv) != 3:
        print("usage: patch_fsbl_earmark.py <fsbl_hooks.c> <build-tag>", file=sys.stderr)
        sys.exit(1)
    path, tag = sys.argv[1], sys.argv[2]

    with open(path) as f:
        content = f.read()

    marker = 'fsbl_printf(DEBUG_INFO, "In FsblHookAfterBitstreamDload function \\r\\n");'
    if marker not in content:
        print("ERROR: expected FsblHookAfterBitstreamDload body not found - "
              "the Vitis FSBL template may have changed.", file=sys.stderr)
        sys.exit(1)

    # Use xil_printf directly, not the fsbl_printf macro: fsbl_printf is
    # gated by a debug-level bitmask that is 0 unless FSBL_DEBUG(_INFO) is
    # defined, so the compiler dead-code-eliminates the whole call (string
    # included) by default - confirmed missing from `strings` output on a
    # test build before this fix.
    replacement = (
        marker
        + f'\n\txil_printf("\\r\\n*** BUILD EARMARK: {tag} ***\\r\\n");'
    )
    content = content.replace(marker, replacement, 1)

    with open(path, "w") as f:
        f.write(content)

    print(f"Patched {path} with build tag: {tag}")

if __name__ == "__main__":
    main()
