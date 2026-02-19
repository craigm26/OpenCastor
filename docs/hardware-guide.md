# Hardware Guide — OpenCastor

> **Who this is for:** Students, educators, hobbyists, and hackers who have
> robot hardware they want to connect to OpenCastor — especially second-hand,
> thrift-store, or school-surplus finds.

---

## 🔍 Peripheral Auto-Detection

Before reading further, try the automatic peripheral scanner:

```bash
castor scan
```

OpenCastor will detect USB cameras, depth cameras (OAK-D, RealSense), NPU accelerators
(Hailo-8, Coral), LiDAR sensors, I²C devices (PCA9685, MPU-6050), serial devices
(Arduino, STM32), and more — then suggest the right RCAN config snippets for each.

See the **[Peripheral Guide](peripherals.md)** for full documentation on every supported
hardware category, wiring tips, driver installation, and how to add support for new
peripherals.

---

## Table of Contents

1. [I Found This at a Thrift Store — Now What?](#-i-found-this-at-a-thrift-store--now-what)
2. [Identifying Your Hardware's Driver Protocol](#-identifying-your-hardwares-driver-protocol)
3. [Wiring Cheatsheet](#-wiring-cheatsheet)
   - [PCA9685 (I2C PWM Controller)](#pca9685-i2c-pwm-controller)
   - [L298N Dual H-Bridge](#l298n-dual-h-bridge)
   - [GPIO Direct Drive](#gpio-direct-drive)
4. [Testing with `castor test-hardware`](#-testing-with-castor-test-hardware)
5. [Common Issues & Fixes](#-common-issues--fixes)

---

## 🛒 I Found This at a Thrift Store — Now What?

Use this decision tree to figure out which OpenCastor preset to use (or how to
create your own).

```
Did you find a robot kit?
│
├─► Is it LEGO?
│   ├─► Yellow/black brick + large EV3 brick? → lego_mindstorms_ev3.rcan.yaml
│   └─► White/teal hub with "SPIKE Prime" label? → lego_spike_prime.rcan.yaml
│
├─► Is it VEX?
│   ├─► Plastic snap-together parts, brain says "IQ"? → vex_iq.rcan.yaml
│   └─► Metal parts with V5 brain? → Generate with castor wizard (VEX V5 serial)
│
├─► Is it Makeblock?
│   └─► Blue board labeled "mCore" or "Orion"? → makeblock_mbot.rcan.yaml
│
├─► Is it a Raspberry Pi-based kit?
│   ├─► 4-wheel car, PCA9685 board visible? → freenove_4wd.rcan.yaml or
│   │                                          yahboom_rosmaster.rcan.yaml
│   ├─► Has a SunFounder label? → presets/sunfounder_picar.rcan.yaml
│   └─► Has a Waveshare label? → presets/waveshare_alpha.rcan.yaml
│
├─► Is it an Arduino-based car kit?
│   ├─► Green/blue PCB with "Elegoo" + CH340? → elegoo_tumbller.rcan.yaml
│   └─► Generic chassis + L298N red/blue board? → arduino_l298n.rcan.yaml
│
├─► Is it an ESP32 board + motors?
│   └─► Any ESP32 dev board? → esp32_generic.rcan.yaml
│
├─► Is it a small purple/blue board with "Cytron"?
│   └─► Maker Pi RP2040 label? → cytron_maker_pi.rcan.yaml
│
└─► None of the above?
    └─► See "Identifying Your Hardware's Driver Protocol" below, then
        run `castor wizard` to generate a custom preset.
```

### Quick Identification Tips

| Clue | Likely Platform |
|---|---|
| Brick-shaped plastic connectors, LEGO studs | LEGO Mindstorms / SPIKE |
| Green snap-together plastic, IQ Brain box | VEX IQ |
| Blue PCB labeled mCore, Orion, or Makeblock | Makeblock mBot/Ranger |
| Red or blue rectangular board with 4 screw terminals + 2 big ICs | L298N motor driver |
| Small green/blue board with "L298N" silkscreen | L298N module |
| Tiny board with "ESP32" + antenna trace | ESP32 |
| Raspberry Pi GPIO header + hat PCB | RPi-based kit |
| Purple board, RP2040 chip, Grove connectors | Cytron Maker Pi RP2040 |
| ROBOTIS sticker, round horn servos | Dynamixel (see dynamixel_arm preset) |

---

## 🔍 Identifying Your Hardware's Driver Protocol

Different hardware uses different communication protocols. Match your hardware
to the right OpenCastor driver.

### Protocol Identification Guide

#### 1. PCA9685 (I2C PWM Controller)
**What it looks like:** A small green/blue breakout board with 16 servo headers,
labeled "PCA9685" or "Adafruit 16-Channel PWM."

**How to verify:**
```bash
# On Raspberry Pi with I2C enabled:
i2cdetect -y 1
# Look for 0x40 (or 0x41-0x7F for shifted addresses)
```

**Used by:** Waveshare AlphaBot, Adeept, Freenove, Yahboom ROSMASTER, SunFounder PiCar

**Preset setting:**
```yaml
drivers:
  - id: "motor_driver"
    protocol: "pca9685_i2c"
    port: "/dev/i2c-1"
    address: "0x40"
    frequency: 50
```

#### 2. Arduino Serial (USB CDC / UART)
**What it looks like:** Arduino Uno (blue, USB-B port), Nano (mini, micro-USB),
or clone board with CH340 USB chip (look for small 8-pin chip near USB port).

**How to verify:**
```bash
ls /dev/ttyACM*    # Genuine Arduino (ATmega16U2 USB chip)
ls /dev/ttyUSB*    # Clone Arduino (CH340 chip)
# After plugging in: dmesg | tail -20
```

**Protocol variants:**
- `arduino_serial_json` — OpenCastor custom firmware (JSON commands)
- `makeblock_serial` — Makeblock proprietary protocol
- `elegoo_serial` — Elegoo firmware protocol

#### 3. EV3dev (LEGO Mindstorms EV3)
**What it looks like:** The large LEGO EV3 brick running ev3dev Linux OS.

**How to verify:**
```bash
ssh robot@ev3dev.local   # Default password: maker
ls /sys/class/tacho-motor/  # Should list motor0, motor1...
ls /sys/class/lego-sensor/  # Should list sensor0, sensor1...
```

#### 4. VEX IQ Serial
**What it looks like:** The VEX IQ Brain box (grey/white) with smart ports.

**How to verify:** Connect via USB; appears as `/dev/ttyACM0`. Run VEXcode to
check firmware version before switching to OpenCastor serial mode.

#### 5. WebSocket / REST (ESP32 WiFi)
**What it looks like:** An ESP32 dev board connected to motor driver(s) with
a WiFi antenna.

**How to verify:** Flash the ESP32 firmware and check its serial output for an IP
address, then: `curl http://<ip>/status`

#### 6. SPIKE Hub Serial (LEGO SPIKE Prime)
**What it looks like:** The LEGO SPIKE Prime hub (white/teal, USB-C port).

**How to verify:** Connect via USB-C; appears as `/dev/ttyACM0`. Run `screen
/dev/ttyACM0 115200` and press Enter — you should see a Python REPL prompt.

---

## 🔌 Wiring Cheatsheet

### PCA9685 (I2C PWM Controller)

The PCA9685 is the most common motor/servo controller in RPi robot kits.

```
Raspberry Pi → PCA9685 Board
─────────────────────────────
Pin 1  (3.3V)   → VCC
Pin 3  (SDA)    → SDA
Pin 5  (SCL)    → SCL
Pin 6  (GND)    → GND

PCA9685 V+ → External 5-6V power supply (for servos)
           → Or 12V for DC motors via motor driver hat

⚠️  NEVER power motors from the Pi's 5V pin — it will crash the Pi.
    Always use an external supply for motors.
```

**Channel assignment (typical):**
```
Channel 0  → Left front motor (or M1)
Channel 1  → Left rear motor  (or M2)
Channel 2  → Right front motor (or M3)
Channel 3  → Right rear motor  (or M4)
Channel 8  → Camera pan servo
Channel 9  → Camera tilt servo
```

**Frequency:** Use 50 Hz for servos, 200-1000 Hz for DC motors (check your driver IC specs).

**I2C Address:** Default 0x40. If multiple PCA9685 boards, use A0-A5 solder jumpers to change address.

---

### L298N Dual H-Bridge

The L298N is the classic DC motor driver. Red or blue module, two large screw-terminal blocks.

```
Arduino (Uno/Nano)  →  L298N Module
────────────────────────────────────
Pin 5  (PWM)  → ENA    (left motor speed)
Pin 6  (D)    → IN1    (left motor dir A)
Pin 7  (D)    → IN2    (left motor dir B)
Pin 8  (D)    → IN3    (right motor dir A)
Pin 9  (D)    → IN4    (right motor dir B)
Pin 10 (PWM)  → ENB    (right motor speed)
GND           → GND

External Power → L298N
───────────────────────
7-12V Battery+  → L298N VCC (+12V screw terminal)
Battery-        → L298N GND
                  L298N 5V output → Arduino 5V (ONLY if jumper is present on L298N)

Motors → L298N
──────────────
Motor A: Left  → OUT1 and OUT2
Motor B: Right → OUT3 and OUT4

⚠️  Remove the 5V jumper on L298N if supplying >12V — the onboard regulator
    will overheat. Power Arduino from USB instead.
```

**Logic table:**
```
Forward:   IN1=HIGH, IN2=LOW,  IN3=HIGH, IN4=LOW,  ENA=PWM, ENB=PWM
Backward:  IN1=LOW,  IN2=HIGH, IN3=LOW,  IN4=HIGH, ENA=PWM, ENB=PWM
Left turn: IN1=LOW,  IN2=HIGH, IN3=HIGH, IN4=LOW,  ENA=PWM, ENB=PWM
Right turn:IN1=HIGH, IN2=LOW,  IN3=LOW,  IN4=HIGH, ENA=PWM, ENB=PWM
Stop:      ENA=LOW,  ENB=LOW
```

---

### GPIO Direct Drive

For small motors driven directly from GPIO (only possible with DRV8833/TB6612 breakouts
wired to GPIO pins, NOT raw GPIO — GPIO can't source enough current for motors).

```
Raspberry Pi GPIO → TB6612FNG Breakout
───────────────────────────────────────
GPIO 17 → AIN1   (left dir)
GPIO 27 → AIN2   (left dir)
GPIO 18 → PWMA   (left speed — must be PWM-capable pin)
GPIO 22 → BIN1   (right dir)
GPIO 23 → BIN2   (right dir)
GPIO 13 → PWMB   (right speed — must be PWM-capable pin)
3.3V    → VCC    (logic power)
GND     → GND, STBY tied HIGH

External 5V → VM  (motor power — separate from logic)
```

**RPi BCM PWM pins:** GPIO 12, 13, 18, 19 are hardware PWM. Others are software PWM
(less precise, more CPU use, OK for most robots).

**DRV8833 vs TB6612FNG vs L298N:**
```
Driver    | Max Current | Voltage | Efficiency | Notes
──────────┼─────────────┼─────────┼────────────┼──────────────────────────
L298N     | 2A (3A peak)| 5-46V   | ~70%       | Hot, simple, ubiquitous
TB6612FNG | 1.2A (3A pk)| 2.5-13V | ~95%       | Compact, cool, preferred
DRV8833   | 1.5A (2A pk)| 2.7-10V | ~95%       | Tiny, cheap, great for small bots
L293D     | 600mA       | 4.5-36V | ~60%       | Old DIP IC, very common at makerspaces
```

---

## 🧪 Testing with `castor test-hardware`

Before running autonomy, always verify your hardware manually.

### Basic Test Sequence

```bash
# 1. List detected hardware
castor test-hardware --config my_robot.rcan.yaml --list

# 2. Test each motor individually (2 seconds each direction)
castor test-hardware --config my_robot.rcan.yaml --motor left_motor
castor test-hardware --config my_robot.rcan.yaml --motor right_motor

# 3. Test all motors together
castor test-hardware --config my_robot.rcan.yaml --motors-all

# 4. Test sensors
castor test-hardware --config my_robot.rcan.yaml --sensor ultrasonic_sensor
castor test-hardware --config my_robot.rcan.yaml --sensors-all

# 5. Full diagnostic
castor test-hardware --config my_robot.rcan.yaml --full
```

### What to Look For

**Motors:**
- ✅ Both wheels spin in the correct direction
- ✅ Speed varies proportionally with PWM value
- ❌ Motor doesn't move → Check wiring, PWM pins, power supply voltage
- ❌ Motor spins wrong direction → Set `polarity: -1` in preset or swap motor wires

**Sensors:**
- ✅ Ultrasonic returns plausible distances (5-300 cm)
- ✅ Touch/bumper registers when pressed
- ❌ Sensor returns garbage → Check baud rate, pin assignments
- ❌ I2C sensor not found → Run `i2cdetect -y 1` to find actual address

**Serial connection:**
```bash
# Check device is visible
ls /dev/ttyACM* /dev/ttyUSB*

# Check permissions (Linux)
sudo usermod -a -G dialout $USER  # Then log out and back in
# Or: sudo chmod 666 /dev/ttyACM0  (temporary)

# Monitor raw serial
screen /dev/ttyACM0 115200   # Ctrl+A then K to quit
# Or:
python3 -m serial.tools.miniterm /dev/ttyACM0 115200
```

### I2C Debugging

```bash
# Install i2c-tools
sudo apt install i2c-tools

# Scan for I2C devices
i2cdetect -y 1     # Raspberry Pi I2C bus 1

# Common addresses:
# 0x40 → PCA9685
# 0x68 → MPU6050 IMU
# 0x3C → SSD1306 OLED
# 0x48 → ADS1115 ADC
```

---

## 🔧 Common Issues & Fixes

| Symptom | Likely Cause | Fix |
|---|---|---|
| Robot drives in circles | One motor reversed | Set `polarity: -1` for that motor in YAML |
| Motors barely move | Deadband too low | Increase `deadband_pwm` value |
| Robot lurches then stops | Battery voltage drop | Charge battery; add capacitor across motor terminals |
| Serial port not found | Permission denied | `sudo usermod -a -G dialout $USER` |
| Serial port not found | Wrong port | `ls /dev/tty*` before and after plugging in |
| Arduino upload fails | Wrong board selected | Try "ATmega328P (Old Bootloader)" for clones |
| I2C nothing at 0x40 | I2C not enabled | `sudo raspi-config` → Interfaces → I2C |
| EV3 not reachable | ev3dev not flashed | Flash ev3dev to microSD; USB RNDIS auto-IP |
| ESP32 won't connect | Wrong IP | Check ESP32 serial output for assigned IP |
| LEGO motor not detected | Loose port | Push cable in until it clicks |
| VEX IQ motor not detected | Dead smart port | Try another port; ports are interchangeable |

---

## 📚 Additional Resources

- **OpenCastor Discord:** [discord.gg/jMjA8B26Bq](https://discord.gg/jMjA8B26Bq) — `#second-hand-hardware` channel
- **ev3dev docs:** [ev3dev.org](https://www.ev3dev.org/)
- **SPIKE Prime Python API:** [education.lego.com/api/spike-prime](https://education.lego.com/en-us/lessons/spike-prime)
- **VEX IQ Python SDK:** [api.vex.com](https://api.vex.com/)
- **Makeblock mBot library:** [github.com/Makeblock-official/mBot](https://github.com/Makeblock-official/mBot)
- **Adafruit PCA9685 guide:** [learn.adafruit.com/16-channel-pwm-servo-driver](https://learn.adafruit.com/16-channel-pwm-servo-driver)
- **RPi GPIO pinout:** [pinout.xyz](https://pinout.xyz/)

---

*Part of the [OpenCastor](https://github.com/craigm26/OpenCastor) project — Apache 2.0*
