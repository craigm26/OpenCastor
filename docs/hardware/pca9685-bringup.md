# PCA9685 bring-up — the ordered checklist

The ten steps between a generated OpenCastor RC-car robot on simulated wheels
and one that drives. Do them in order. `castor up` generates everything above
this line; nothing below it can be done from software alone, which is why it is
a checklist and not a command.

**The rule that outranks everything below: the wheels stay off the ground until
step 6, and the e-stop is fitted before step 7.** Every stop in this system —
the deadman, the envelope, the app's STOP button, the phone's camera governor —
is upstream of a chip that holds its last pulse width in hardware, forever, and
none of them survive a hung process. A mechanical switch does.

## Turning the wheels on, once the checklist is done

Real PWM is one deliberate act, and `castor up` is where it happens:

```sh
castor up --real-wheels        # wheels OFF the ground when you run this
```

`castor up` also asks, once, if it finds a PCA9685 at 0x40 and has a terminal to
ask at. Either way it writes five variables into `<robot home>/gateway-policy.env`
(step 8 lists them) and restarting the gateway picks them up. `castor up
--simulated-wheels` comments them back out. On a host with no terminal — an image
firstboot, CI, `ssh -T` — the answer is always simulated wheels.

---

## 0. What is already true before you start

- `rc_car_actuator.pca9685.PCA9685Drive` is the I2C backend, tested against a
  fake bus.
- `rc_car_actuator.backend.drive_from_env` selects it from the environment. The
  default is `SimulatedDrive`, and **an explicit request that cannot be honoured
  raises rather than falling back** — so "I asked for the PCA9685 and the wheels
  did not move" can never be a silent simulation.
- The iOS app's camera governor clamps throttle against what the phone can see.
  That is a competence layer, not a safety layer. Do not count it as a stop.

## 1. 🔴 BLOCKER — enable the ARM I2C bus (needs sudo and a reboot)

Raspberry Pi OS ships with the I2C bus **off**. On a freshly flashed card
`/dev/i2c-1` does not exist, and any i2c buses you do see (commonly `i2c-13` and
`i2c-14` on a Pi 5) are the HDMI DDC buses, not the GPIO header.
`/boot/firmware/config.txt` carries the line commented out:

```
#dtparam=i2c_arm=on
```

```sh
sudo raspi-config nointeractive do_i2c 0     # or uncomment the line by hand
sudo reboot
```

**Before rebooting, know what comes back.** Anything running bare — started by
hand rather than by a systemd unit — does not return. Check `systemctl --user
is-enabled` on every unit you rely on, and have the start script for anything
else ready before you reboot, not after.

After the reboot:

```sh
ls /dev/i2c-1                 # must exist
i2cdetect -y 1                # the board should answer at 0x40
```

A board that also answers at 0x70 is normal: that is the PCA9685's all-call
address. Your user must be in the `i2c` group (`castor up` and the OpenCastor
image both arrange this) and `smbus2` must be installed.

## 1b. If you would rather not use I2C at all — the Maestro option

`OPENCASTOR_DRIVE=maestro` is implemented and tested, so switching controllers is
one environment variable and the same trim numbers. The per-vehicle trims are
shared between both backends deliberately: they describe the vehicle, not the
chip.

```
OPENCASTOR_DRIVE=maestro
OPENCASTOR_DRIVE_SERIAL_PORT=/dev/ttyACM0
```

**Why you might actually want it, and it is not wiring convenience.** A PCA9685
holds its last pulse width in hardware forever — through an exception, a
`kill -9`, a kernel panic. Every stop in this system is therefore software. A
Pololu Maestro has a **serial timeout** plus a per-channel **"on startup or
error"** position: set both and the controller returns the wheels to neutral on
its own when commands stop, with no Linux in the path. That is a real second
layer between the software deadman and the e-stop.

🔴 **That failsafe is a setting stored on the device, written with Pololu's
Maestro Control Center, and it is NOT readable over the serial protocol.** The
code cannot confirm it, cannot enable it, and does not claim it. Verify it by
pulling the USB cable on a stand and watching the wheels.

Two ways a Maestro looks dead when it is fine: it presents **two** USB serial
devices and only the lower-numbered one is the command port, and it must be in
**USB Dual Port** mode (in USB Chained mode your bytes go out the TTL pin
instead). Neither produces an error.

Watch the USB power budget either way. A Pi 5 at `usb_max_current_enable=0` caps
all USB at 600 mA until you declare a 5 A supply; a Maestro's ~30 mA is fine, but
a camera and a powered speaker on the same host are not, and the symptom is every
USB device resetting at once for no visible reason.

## 2. Wiring, before power

| PCA9685 | Goes to |
|---|---|
| VCC | Pi 3V3 — **logic only** |
| GND | Pi GND **and** the ESC/battery ground (one common ground, or nothing works) |
| SDA / SCL | Pi GPIO2 / GPIO3 |
| V+ | **Not from the Pi.** Servo power from the BEC or a separate 5–6 V supply |
| Ch 0 | Steering servo signal |
| Ch 1 | ESC signal |

Channels 0 and 1 are the OpenCastor default (`THROTTLE_CHANNEL=1`,
`STEERING_CHANNEL=0`) because that is what the vehicles this template was cut
from use. **Trace your own two servo leads to the board and believe the wire, not
this table.** A cross-plugged harness is the one fault no bench test can catch:
"the steering command produced a pulse on the steering channel" is true no matter
what is plugged into that pin. It has cost a week of register-level testing
before; if the stick turns the wheels and the throttle steers, swap the two
numbers in step 8 rather than rewiring.

Powering servos from the Pi's 5 V rail is the classic way to brown out a Pi 5
mid-drive, which drops the gateway, which stops the commands — which is at least
a stop, but it also corrupts SD cards.

## 3. First power-on, wheels OFF the ground, ESC UNPLUGGED from the motor

```sh
export OPENCASTOR_DRIVE=pca9685
python3 -c "
from rc_car_actuator.backend import drive_from_env
d = drive_from_env()
print('constructed and centred')
"
```

Constructing the object writes neutral to both channels before it returns. If
this raises, read the message: it names the actual cause (no bus, no `smbus2`, no
device at the address) and it does not fall back.

## 4. Measure the oscillator, or accept a creep at rest

🔴 **The calibration most likely to waste a day.** The PCA9685's "25 MHz" is a
cheap on-die RC oscillator with percent-level tolerance, and every pulse it emits
scales with it. A part running 4% fast turns a commanded 1500 µs neutral into
roughly 1440 µs — which many ESCs read as a slow crawl in reverse. The car moves
while commanded to stop, and nothing in the software is wrong.

Put a scope or logic analyser on the throttle channel, command neutral, and
measure the real frame period:

```sh
export OPENCASTOR_DRIVE_OSCILLATOR_HZ=$(( 25000000 * requested_period / measured_period ))
```

If there is no scope: skip it, and treat any motion at commanded-neutral in step
5 as this, not as a broken ESC.

## 5. Trim neutral and span, still on the stand

Two numbers per channel, both per-vehicle, neither knowable from code:

```sh
export OPENCASTOR_DRIVE_THROTTLE_NEUTRAL_US=1500   # where the ESC actually sits still
export OPENCASTOR_DRIVE_STEERING_NEUTRAL_US=1500   # where the wheels actually point straight
export OPENCASTOR_DRIVE_STEERING_SPAN_US=300       # start here and open it up
export OPENCASTOR_DRIVE_THROTTLE_INVERT=false      # if forward is backwards
```

A steering servo driven into a mechanical bind stalls, heats, and dies quietly.
Start the steering span at 300 and **reduce** it the moment the linkage binds.

## 6. ESC signal-loss behaviour — verify, do not assume

On the stand, with the motor connected: command a small throttle, then kill the
commanding process outright. Watch the wheels.

- Wheels stop → the ESC fails safe on signal loss. Good.
- Wheels keep turning → **this ESC holds its last command.** The deadman still
  covers the normal case (it writes neutral every tick), but a killed process
  leaves the chip emitting. Fit the e-stop before anything else, and consider a
  GPIO-relay heartbeat layer.

## 7. Fit the e-stop, then the ground

Normally-closed latching mushroom switch, inline on the traction battery to the
ESC positive lead, rated ≥20–30 A. Test it under load before the car has anywhere
to go. Only then does the car come off the stand.

## 8. Point the runtime at real wheels

`castor up --real-wheels` writes exactly this into
`<robot home>/gateway-policy.env` (the systemd unit's `EnvironmentFile`); to do it
by hand, uncomment the same five lines and restart `<name>-gateway`:

```
OPENCASTOR_DRIVE=pca9685
OPENCASTOR_DRIVE_I2C_BUS=1
OPENCASTOR_DRIVE_I2C_ADDRESS=0x40
OPENCASTOR_DRIVE_THROTTLE_CHANNEL=1
OPENCASTOR_DRIVE_STEERING_CHANNEL=0
# plus whatever steps 4 and 5 measured
```

```sh
systemctl --user restart <name>-gateway
```

`status.report` telemetry reports `hardware`, which will read `PCA9685Drive`
instead of `SimulatedDrive`. **Check that field before believing anything moved
for the reason you think.** If the board is not answering, the gateway refuses to
start and says so in `journalctl --user -u <name>-gateway` rather than quietly
pretending to drive; the runtime and its `/api/stop` are a separate service and
stay up regardless.

## 9. First envelope, and what to ask for

Open a deliberately tiny approval from the phone: **5 seconds of motion, a
60-second window, speed cap 0.10.** At the 2.0 s deadman ceiling, one un-renewed
full-lease command at 0.35 is about two metres of travel; at 0.10 it is under a
metre. The first real drive should not be able to reach a wall.

## 10. Only then, the phone

Mount the phone, open the drive screen, and use **"The phone is mounted on the
car"**. Watch for two things before pressing Creep:

- **"Floor measured"**, not "Floor assumed". If it says assumed, the lens-height
  guess is doing the work, and a few centimetres of error makes the floor itself
  read as a wall — the car will report "Blocked 0.5 m ahead" down an empty
  hallway. Nudge the height until the readout stops claiming an obstacle that is
  not there.
- **"Depth camera"**, not "No depth sensor". The autopilot will not engage
  without LiDAR, by design; manual driving still works, with you as the sensor.
