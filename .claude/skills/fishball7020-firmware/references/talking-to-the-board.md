# Talking to the board

## Three routes in

| Route | Reaches | Notes |
|---|---|---|
| libiio network protocol, port 30431 | IIO attributes, sample buffers | no library needed; `tools/selftest/iiod_min.py` speaks it with the standard library alone |
| ssh `root@192.168.2.1` (password `analog`) | sysfs, debugfs, the filesystem | busybox — see limits below |
| USB mass storage / serial console | firmware images, boot messages | the debug port's UART shows the whole boot; the OTG port's only appears after Linux is up |

## IIOD protocol gotchas

All confirmed against a live board running IIOD 0.25.

- **The channel mask is fixed-width**: exactly 8 hex characters per 32 scan
  channels. `00000003` enables channels 0 and 1. Both `3` and
  `0000000000000003` fail with `-22 EINVAL` and no hint.
- **`WRITEBUF` is acknowledged twice**, before and after the payload. Skip the
  first status and the stream desyncs, with your samples arriving as the next
  "response line".
- **`VERSION` answers with a bare line**, not a length-prefixed payload — the
  one command that breaks the general framing rule.
- **Large transfers time out on the board, not the client.** 1,048,576 samples
  succeeds; 4,194,304 fails with `-110 ETIMEDOUT`. Raising the client timeout
  does not help. Chunk at 262,144 and loop `READBUF` on one open buffer.
- **Receive is 12-bit sign-extended into int16** (full scale ±2047). **Transmit
  is the full 16 bits.** Scaling transmit samples to ±2047 emits 24 dB low.

## Two applications cannot hold the board at once

Opening it in SDRangel or anything else that claims the USB device reconfigures
the composite gadget, the Ethernet gadget disappears, and `ip:192.168.2.1` stops
answering until that application closes. Not a fault; just exclusive.

## busybox limits

- **No `pkill`.** Use `ps` and `kill` with a PID. (And beware: `pkill -f
  <pattern>` run from your own shell can match your own command line and kill
  the shell — this has happened here more than once.)
- **No ftrace**, so no kprobes. `dump_stack()` in a driver plus `dmesg` is the
  available substitute.
- `dmesg` being empty is information: it means the kernel is not doing what you
  suspect.

## Useful sysfs and debugfs

```
/sys/bus/iio/devices/iio:device0        ad9361-phy
/sys/bus/iio/devices/iio:device1        xadc  (supply rails, die temperature)
/sys/bus/iio/devices/iio:device2        cf-ad9361-dds-core-lpc  (TX)
/sys/bus/iio/devices/iio:device3        cf-ad9361-lpc  (RX)
/sys/kernel/debug/iio/iio:device0/      bist_prbs, bist_tone, bist_timing_analysis,
                                        loopback, calib_mode, gaininfo_rx1/2,
                                        digital_tune, and every adi,* device-tree value
```

`bist_timing_analysis` needs a write to trigger, then a read: it walks all 16×16
clock/data delay combinations with a PRBS running and prints the eye. `loopback`
= 1 routes DAC data back into the ADC path inside the chip, which exercises both
DMAs and the LVDS link with no RF at all — remember to set it back to 0.

## Mounting the SD card from the board

```bash
mkdir -p /mnt/sd && mount -t vfat /dev/mmcblk0p1 /mnt/sd
# ... copy files in ...
sync; umount /mnt/sd; sync; reboot
```

Forgetting `mkdir -p` after a reboot is a good way to have `scp` write nothing
and then reboot into the old image believing you flashed. Always compare md5sums
before rebooting.
