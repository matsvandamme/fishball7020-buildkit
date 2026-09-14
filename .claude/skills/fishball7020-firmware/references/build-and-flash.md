# Exact command sequences

## Build

```bash
source tools/env-vivado.sh          # always, before any vivado/xsct/bootgen
cd firmware
./scripts/setup.sh                  # once: clones upstream into src/, applies patches/*.patch
./scripts/build_all.sh              # full: ~70 min
./scripts/build_all.sh --hdl-only   # reuses kernel/u-boot/rootfs: ~20 min
```

`setup.sh` applies `patches/*.patch` in sorted order and deliberately skips
`patches/optional/`. To use a worked example:

```bash
(cd src && git apply ../patches/optional/0003-wbfm-channelizer.patch)
```

**Then delete the Vivado project**, or the change is silently ignored:

```bash
rm -rf src/hdl/projects/pluto/pluto.{xpr,cache,gen,hw,ip_user_files,runs,sim,srcs,sdk}
```

## Kernel only

Much faster than `build_all.sh` when only the driver changed:

```bash
cd firmware
SRC=$PWD/src
PATH="$SRC/buildroot/output/host/bin:$SRC/buildroot/output/host/sbin:$PATH" \
  make -C "$SRC/linux" -j"$(nproc)" ARCH=arm \
  CROSS_COMPILE=arm-linux-gnueabihf- uImage UIMAGE_LOADADDR=0x8000
cp src/linux/arch/arm/boot/uImage output/uImage
```

Device tree only: same, with target `zynq-pluto-sdr-fishball.dtb` and
`DTC_FLAGS=-@`, then copy to `output/devicetree.dtb`.

## Check before flashing

```bash
./sim/run_sim.sh                # HDL against a golden model, ~1 s
./scripts/verify_output.sh      # the five files, compression, timing, DSP count
```

## Flash — SD partition only, never DFU

The board mounts its own SD card and you copy over ssh. Nothing needs to be
unplugged.

```bash
B=root@192.168.2.1                       # password: analog
sshpass -p analog ssh $B 'mkdir -p /mnt/sd && mount -t vfat /dev/mmcblk0p1 /mnt/sd'
for f in BOOT.bin devicetree.dtb uEnv.txt uImage uramdisk.image.gz; do
    sshpass -p analog scp output/$f $B:/mnt/sd/$f
done
sshpass -p analog ssh $B 'sync; md5sum /mnt/sd/*'      # compare against the host
md5sum output/*
sshpass -p analog ssh $B 'umount /mnt/sd; sync; (sleep 1; reboot) &'
```

Copy only what changed — `uImage` alone for a kernel change, `BOOT.bin` alone
for an HDL change. The board is back in about 15 seconds.

Two easy mistakes: forgetting `mkdir -p /mnt/sd` after a reboot (the mount
fails, `scp` writes nothing, and you reboot into the old image believing you
flashed), and not comparing md5sums.

## After flashing

```bash
python3 ../tools/selftest/sdr_selftest.py --ssh              # never transmits
python3 ../tools/selftest/sdr_selftest.py --ssh --loopback --pad 50
```

## Recovering

Keep a copy of a known-good `output/` before experimenting. The distributor's
prebuilt factory firmware is at `OpenSourceSDRLab/PlutoSky_7020_AD936X_SDR`,
confirmed by checksum against a real unit; copying those files onto the SD card
returns the board to its shipped state.
