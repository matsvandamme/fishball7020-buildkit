# Controlling the USER LED

The board has three LEDs between the `USB2.0` and `DEBUG` ports:

| LED | Driven by | Can you control it? |
|---|---|---|
| `PWR` | Power rail | No — hardwired |
| `DONE` | The FPGA's own configuration logic | No — it goes high when the bitstream loads |
| `USER` | Linux, through a PS GPIO pin | **Yes** — this page |

## How it's wired (and why that matters)

From the board's device tree:

```dts
leds {
    compatible = "gpio-leds";
    led0 {
        label = "led0:green";
        gpios = <0x09 0x00 0x00>;          /* controller, pin 0, active high */
        linux,default-trigger = "heartbeat";
    };
};
```

Phandle `0x09` resolves to `gpio@e000a000` — `xlnx,zynq-gpio-1.0`, the
**PS** GPIO controller. So the LED hangs off **MIO pin 0 on the ARM side**.

That has one important consequence:

> **You cannot drive this LED from your HDL.** MIO pins belong to the
> Processing System and are not routed into the Programmable Logic. It does
> not appear in `system_top.v` or `system_constr.xdc`, and no amount of
> block-design work will connect it. Driving it is a *software* job.

That `linux,default-trigger = "heartbeat"` line is also why the LED pulses
on a healthy board — it's the kernel's heartbeat trigger, not your firmware.

## Taking control from Linux

Everything happens under sysfs. On the board (serial console or SSH):

```sh
# on the BOARD
ls /sys/class/leds/
cd /sys/class/leds/led0:green
```

*(If the directory name differs, use whatever `ls` shows — it comes from the
`label` property above.)*

**Turn off the heartbeat and drive it yourself.** The trigger must be set to
`none` first, or the kernel keeps overwriting your value:

```sh
echo none > trigger
echo 1 > brightness        # on
echo 0 > brightness        # off
```

**See what else it can do automatically:**

```sh
cat trigger
```

The current trigger is shown in `[brackets]`. Useful ones include `none`,
`heartbeat`, `timer`, `oneshot`, plus activity triggers such as `mmc0` (SD
card access) and CPU triggers.

**Blink at your own rate**, with no code at all:

```sh
echo timer > trigger
echo 100 > delay_on        # milliseconds lit
echo 900 > delay_off       # milliseconds dark
```

**Flash it on SD-card activity:**

```sh
echo mmc0 > trigger
```

## Using it as a status light in your own program

From a shell script:

```sh
#!/bin/sh
LED=/sys/class/leds/led0:green
echo none > $LED/trigger
while true; do
    if my_application_is_healthy; then
        echo 1 > $LED/brightness
    else
        echo 0 > $LED/brightness; sleep 0.2; echo 1 > $LED/brightness
    fi
    sleep 1
done
```

From C, it's just a file write:

```c
int fd = open("/sys/class/leds/led0:green/brightness", O_WRONLY);
write(fd, "1", 1);
```

Remember the root filesystem is a **ramdisk** — a script you write on the
board vanishes at reboot unless you either put it in `/mnt/jffs2` or, better,
add it to `firmware/patches/` so it becomes part of every build.

## Making your setting the default at boot

Rather than reconfiguring after every boot, change the device tree so the LED
comes up the way you want. Edit
`firmware/src/linux/arch/arm/boot/dts/zynq-pluto-sdr-fishball.dts`:

```dts
linux,default-trigger = "none";     /* was "heartbeat" */
```

Then rebuild, and capture it as a patch so it survives a clean `setup.sh`:

```bash
# run from: firmware/src
git diff linux/arch/arm/boot/dts/zynq-pluto-sdr-fishball.dts \
    > ../patches/0003-led-default-off.patch
```

Other useful values are `timer`, `mmc0`, or `default-on`.

## If you want an LED your FPGA logic drives directly

The `USER` LED can't do this, so you need a pin that actually reaches the PL.
Every PL pin currently constrained in `system_constr.xdc` is already spoken
for — the AD9361 LVDS interface, its control/status GPIOs, I²C and the two
SPI buses. The expansion header on the board is the place to look for free
ones.

Mapping a header pin to an FPGA package pin needs the **board schematic** —
don't guess, because driving a pin that turns out to be an input, or is tied
to something else, can damage the board. Once you know the package pin, the
pattern is the same as every other line in the file:

```tcl
set_property -dict {PACKAGE_PIN <pin> IOSTANDARD LVCMOS25} [get_ports my_led]
```

Add a matching `output my_led` to `system_top.v`, drive it from your logic,
and rebuild. A counter off `axi_ad9361/l_clk` makes a good first test — see
[step 4](../README.md#4-add-your-own-hdl).

**The pragmatic middle ground:** if you just want the `USER` LED to reflect
something happening inside the PL, expose that state in an AXI register your
logic already writes, and have a small userspace loop read it and set
`brightness`. The PS does the driving; your HDL decides when.
