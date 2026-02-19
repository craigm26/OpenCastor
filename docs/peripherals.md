# Peripheral Guide — OpenCastor

> **OpenCastor's hardware philosophy:** Your robot, your hardware. OpenCastor is not tied
> to any specific bill-of-materials. It works with OAK-D cameras, Hailo-8 NPUs, RPLiDAR
> scanners, Logitech webcams, Arduino boards, PCA9685 servo controllers, IMUs, and virtually
> any peripheral that has a Linux driver.

---

## Table of Contents

1. [Overview](#1-overview)
2. [Quick Start — castor scan](#2-quick-start--castor-scan)
3. [Supported Peripheral Categories](#3-supported-peripheral-categories)
4. [USB Cameras](#4-usb-cameras)
5. [Depth & Stereo Cameras](#5-depth--stereo-cameras)
6. [NPU Accelerators](#6-npu-accelerators)
7. [LiDAR](#7-lidar)
8. [IMU & Sensors](#8-imu--sensors)
9. [Motor Controllers & Servo Drivers](#9-motor-controllers--servo-drivers)
10. [Adding an Unsupported Peripheral](#10-adding-an-unsupported-peripheral)
11. [Hot-plug / Runtime Detection](#11-hot-plug--runtime-detection)

---

## 1. Overview

OpenCastor separates the **robot runtime** (perception → reasoning → action loop) from
**hardware specifics** via the RCAN config system. A single `robot.rcan.yaml` file tells
OpenCastor what camera to use, which motor driver to load, and what NPU to accelerate with.

**What this means for you:**

- You are **not** required to have an OAK-D camera.
- You are **not** required to have a Hailo-8 NPU.
- Any V4L2-compatible USB camera, any serial-based motor controller, and most common
  I²C sensors are supported out of the box.
- OpenCastor will tell you what it found when you run `castor scan`.

---

## 2. Quick Start — `castor scan`

Run the peripheral scanner to automatically detect what hardware is connected:

```bash
castor scan
```

Example output:

```
  OpenCastor Peripheral Scanner

   ✓  OAK-D / OAK-D Lite / OAK-D Pro     depth      usb       03e7:2485
   ✓  Hailo-8 / Hailo-8L NPU              npu        pcie      /dev/hailo0
   ✓  PCA9685 PWM servo controller         motor      i2c       0x40
   ~  Serial device /dev/ttyUSB0           serial     serial    /dev/ttyUSB0

  Suggested rcan.yaml additions:

  # OAK-D / OAK-D Lite / OAK-D Pro
  camera:
    type: "oakd"
    depth_enabled: true
    fps: 30

  # Hailo-8 / Hailo-8L NPU
  npu:
    type: "hailo"
    device: "/dev/hailo0"

  # PCA9685 PWM servo controller
  driver:
    type: "pca9685"
    i2c_bus: 1
    address: 0x40
```

### Flags

| Flag | Description |
|------|-------------|
| `--json` | Output as machine-readable JSON |
| `--no-color` | Plain text output (no ANSI colors) |
| `--i2c-bus N` | Scan I2C bus N instead of default (bus 1) |

### JSON output

```bash
castor scan --json | jq '.[].name'
```

### Integrate into scripts

```python
from castor.peripherals import scan_all

peripherals = scan_all()
cameras = [p for p in peripherals if p.category == "camera"]
print(f"Found {len(cameras)} camera(s)")
```

---

## 3. Supported Peripheral Categories

| Category | Examples | Interface | RCAN key |
|----------|----------|-----------|----------|
| `depth` | OAK-D, RealSense D435, ZED 2 | USB | `camera:` |
| `camera` | Logitech C920, Pi Camera, BRIO | USB / CSI | `camera:` |
| `npu` | Hailo-8, Google Coral, Jetson | PCIe / USB | `npu:` |
| `lidar` | RPLiDAR A1/A2/C1, Hokuyo UST | USB-serial | `lidar:` |
| `imu` | MPU-6050, BNO055, ICM-42688 | I²C | `imu:` |
| `motor` | PCA9685, L298N, Pololu | I²C / serial | `driver:` |
| `serial` | Arduino, ESP32, STM32 | USB-serial | `driver:` |
| `sensor` | BMP280, ADS1115, VL53L0X | I²C | `sensor:` |
| `display` | SSD1306 OLED | I²C | `display:` |

---

## 4. USB Cameras

Any camera that presents as a V4L2 device works with OpenCastor.

### Known tested cameras

| Camera | VID:PID | Notes |
|--------|---------|-------|
| Logitech C920 | `046d:082d` | Excellent low-light performance |
| Logitech BRIO | `046d:085e` | 4K capable |
| Microsoft Modern Webcam | `045e:097d` | Wide field of view |
| Microdia generic USB cam | `0c45:636b` | Budget-friendly |
| Any V4L2 device | — | Works via generic driver |

### RCAN config

```yaml
camera:
  type: "usb"
  device: "/dev/video0"
  width: 640
  height: 480
  fps: 30
```

### Find your camera's device node

```bash
ls /dev/video*
v4l2-ctl --list-devices
```

### Troubleshooting USB cameras

**Camera not detected:**
```bash
lsusb                        # Is the device listed?
dmesg | tail -20             # Did the kernel recognize it?
ls -la /dev/video*           # Does a device node exist?
```

**Permission denied:**
```bash
sudo usermod -aG video $USER
# Log out and back in
```

**Wrong resolution:**
```bash
v4l2-ctl --device=/dev/video0 --list-formats-ext
# Use a supported resolution in rcan.yaml
```

---

## 5. Depth & Stereo Cameras

### OAK-D (Primary recommended depth camera)

OAK-D cameras (OAK-D Lite, OAK-D Pro, OAK-D W, OAK-1) from Luxonis use the
DepthAI SDK. They provide RGB + stereo depth + optional neural inference on-device.

**Install driver:**
```bash
pip install depthai
```

**RCAN config:**
```yaml
camera:
  type: "oakd"
  depth_enabled: true
  fps: 30
  # Optional: enable on-device inference
  # nn_model: "face-detection-retail-0004"
```

**USB IDs:** `03e7:2485`, `03e7:f63b`, `03e7:2150`

**Verify connection:**
```bash
python3 -c "import depthai as dai; print(dai.Device.getAllAvailableDevices())"
```

**USB permissions (Linux):**
```bash
echo 'SUBSYSTEM=="usb", ATTRS{idVendor}=="03e7", MODE="0666"' | \
  sudo tee /etc/udev/rules.d/80-movidius.rules
sudo udevadm control --reload-rules && sudo udevadm trigger
```

---

### Intel RealSense D415 / D435 / D435i

**Install driver:**
```bash
pip install pyrealsense2
```

**RCAN config:**
```yaml
camera:
  type: "realsense"
  depth_enabled: true
  fps: 30
  serial: ""    # Leave empty for first device, or specify S/N
```

**USB IDs:** `8086:0b07` (D415), `8086:0b3a` (D435), `8086:0b64` (D435i)

---

### Stereolabs ZED / ZED 2 / ZED Mini

**Install driver:**
```bash
# Install ZED SDK from https://www.stereolabs.com/developers/release
pip install pyzed
```

**RCAN config:**
```yaml
camera:
  type: "zed"
  depth_enabled: true
  fps: 30
```

---

### Generic stereo / any depth camera

For cameras without a named driver, use the USB camera type and handle depth
in your custom specialist:

```yaml
camera:
  type: "usb"
  device: "/dev/video0"
  fps: 30
```

---

## 6. NPU Accelerators

NPUs dramatically accelerate vision inference. OpenCastor supports multiple NPU backends.

### Hailo-8 / Hailo-8L (Primary recommended NPU)

The Hailo-8 M.2 module is the primary tested NPU for OpenCastor on Raspberry Pi 5.
It provides ~26 TOPS at very low power draw.

**Install:**
```bash
# Follow Hailo's installation guide for your platform
# https://developer.hailo.ai/
pip install hailo-platform
```

**RCAN config:**
```yaml
npu:
  type: "hailo"
  device: "/dev/hailo0"
```

**Detect:**
```bash
castor scan
ls /dev/hailo*
hailortcli fw-control identify
```

---

### Google Coral USB / PCIe Accelerator

Coral USB Accelerator provides ~4 TOPS via USB 3.0.
Coral M.2 PCIe provides ~4 TOPS with lower latency.

**Install:**
```bash
pip install pycoral tflite-runtime
```

**RCAN config (USB):**
```yaml
npu:
  type: "coral"
  device: "usb"
```

**RCAN config (PCIe):**
```yaml
npu:
  type: "coral"
  device: "/dev/apex_0"
```

**USB IDs:** `18d1:9302`, `18d1:9303`

---

### NVIDIA Jetson (Orin / Xavier / Nano)

Jetson devices expose GPU + DLA (Deep Learning Accelerator) via CUDA and TensorRT.

**RCAN config:**
```yaml
npu:
  type: "tensorrt"
  device: "cuda:0"
```

**Detect:**
```bash
nvidia-smi
tegrastats
```

---

## 7. LiDAR

LiDAR scanners give the robot 360° distance awareness.

### RPLiDAR A1 / A2 / C1 (Primary recommended LiDAR)

SLAMTEC RPLiDAR is the most common budget LiDAR for hobbyist robots.

**Install driver:**
```bash
pip install rplidar-roboticia
```

**RCAN config:**
```yaml
lidar:
  type: "rplidar"
  port: "/dev/ttyUSB0"
  baudrate: 115200
```

**USB IDs:** `0403:6015` (FTDI FT231X), `10c4:ea60` (CP2102)

**Permissions:**
```bash
sudo usermod -aG dialout $USER
```

**Verify:**
```bash
ls -la /dev/ttyUSB*
python3 -c "from rplidar import RPLidar; l=RPLidar('/dev/ttyUSB0'); print(l.get_info())"
```

---

### Hokuyo UST / URG series

Hokuyo sensors connect via USB (shows as FTDI serial) or Ethernet.

**RCAN config:**
```yaml
lidar:
  type: "hokuyo"
  port: "/dev/ttyACM0"    # or IP address for network models
  baudrate: 115200
```

---

### SICK TiM series

SICK TiM5xx/TiM7xx are industrial-grade LiDARs with Ethernet.

**RCAN config:**
```yaml
lidar:
  type: "sick"
  host: "192.168.0.1"
  port: 2112
```

---

## 8. IMU & Sensors

### MPU-6050 (6-DoF IMU — gyro + accelerometer)

The MPU-6050 is the most common hobby IMU. Connects via I²C.

**Default I²C address:** `0x68` (or `0x69` when AD0 pin is HIGH)

**Install driver:**
```bash
pip install mpu6050-raspberrypi
```

**RCAN config:**
```yaml
imu:
  type: "mpu6050"
  i2c_bus: 1
  address: 0x68
```

**Enable I²C on Raspberry Pi:**
```bash
sudo raspi-config
# Interface Options → I2C → Enable
```

**Verify:**
```bash
i2cdetect -y 1
# Should show '68' in the grid
```

---

### BNO055 (9-DoF IMU with sensor fusion)

The BNO055 includes an onboard processor for Euler angles and quaternions.

**Default I²C address:** `0x28` (or `0x29` when COM3 pin is HIGH)

**RCAN config:**
```yaml
imu:
  type: "bno055"
  i2c_bus: 1
```

---

### VL53L0X (ToF distance sensor)

Single-point laser distance sensor, up to 2m range.

**Default I²C address:** `0x29`

**RCAN config:**
```yaml
sensor:
  type: "vl53l0x"
  i2c_bus: 1
```

---

### BME280 / BMP280 (Temperature, pressure, humidity)

**I²C addresses:** `0x76` or `0x77`

**RCAN config:**
```yaml
sensor:
  type: "bme280"
  i2c_bus: 1
  address: 0x77
```

---

### ADS1115 (16-bit ADC for analog sensors)

**I²C address:** `0x48`–`0x4B`

**RCAN config:**
```yaml
sensor:
  type: "ads1115"
  i2c_bus: 1
  address: 0x48
  gain: 1
```

---

## 9. Motor Controllers & Servo Drivers

### PCA9685 (I²C PWM controller — recommended)

The PCA9685 drives up to 16 servos or PWM devices via I²C. It is the most common
servo controller in Raspberry Pi robot kits.

**I²C address:** `0x40` (default) up to `0x7F`

**Install:**
```bash
pip install adafruit-circuitpython-pca9685
```

**RCAN config:**
```yaml
driver:
  type: "pca9685"
  i2c_bus: 1
  address: 0x40
  frequency: 50    # 50 Hz for servos, up to 1600 Hz for LEDs
  channels:
    left_motor: 0
    right_motor: 1
    pan_servo: 4
    tilt_servo: 5
```

---

### L298N Dual H-Bridge (GPIO direct drive)

The L298N drives two DC motors via GPIO pins. No I²C, no library required.

**RCAN config:**
```yaml
driver:
  type: "gpio"
  pins:
    left_forward: 17
    left_backward: 18
    right_forward: 22
    right_backward: 23
    left_pwm: 12
    right_pwm: 13
```

---

### Arduino (via USB serial)

An Arduino running a serial command protocol acts as a motor controller bridge.

**USB IDs:** `2341:0043` (Uno), `2341:0001` (older Uno), `2341:8036` (Leonardo), `1a86:7523` (CH340 clone)

**RCAN config:**
```yaml
driver:
  type: "arduino"
  port: "/dev/ttyACM0"
  baud: 115200
  protocol: "simple"    # or "firmata", "ros_serial"
```

**Find Arduino port:**
```bash
ls /dev/ttyACM* /dev/ttyUSB*
dmesg | grep tty | tail -5
```

---

### STM32 / Nucleo boards

STM32 boards appear as `/dev/ttyACM*` via Virtual COM Port.

**USB IDs:** `0483:5740` (VCP), `0483:df11` (DFU mode)

**RCAN config:**
```yaml
driver:
  type: "serial"
  port: "/dev/ttyACM0"
  baud: 115200
```

---

### Pololu USB Servo / Motor Controllers

Pololu controllers (Maestro, qik, TReX) connect via USB serial.

**USB ID:** `1ffb:0089`

**RCAN config:**
```yaml
driver:
  type: "pololu"
  port: "/dev/ttyACM0"
  baud: 9600
```

---

## 10. Adding an Unsupported Peripheral

### Option A: Generic serial driver

Any microcontroller that communicates via serial (UART) can be used with the
generic serial driver:

```yaml
driver:
  type: "serial"
  port: "/dev/ttyUSB0"
  baud: 115200
  # Define your command protocol in a custom specialist
```

### Option B: Generic I²C device

For unknown I²C devices detected by `castor scan`:

```yaml
sensor:
  type: "i2c_raw"
  i2c_bus: 1
  address: 0x5A    # your device address
```

### Option C: Contribute a new VID:PID to the scanner

1. Find your device's VID:PID:
   ```bash
   lsusb
   # Look for: ID 1234:5678 Manufacturer Device Name
   ```

2. Add it to `castor/peripherals.py` in the `_USB_DEVICES` dict:
   ```python
   "1234:5678": {
       "name": "My Cool Robot Controller",
       "category": "motor",          # or serial, camera, lidar, etc.
       "driver_hint": "serial",
       "rcan_snippet": (
           "driver:\n"
           '  type: "serial"\n'
           '  port: "/dev/ttyUSB0"\n'
           "  baud: 115200"
       ),
   },
   ```

3. Submit a PR — see [CONTRIBUTING.md](../CONTRIBUTING.md).

### Option D: Write a custom driver

Implement a driver class in `castor/drivers/` following the base driver interface.
See existing drivers for examples.

---

## 11. Hot-plug / Runtime Detection

### Scan at runtime

You can call `castor scan` anytime — even while the robot is running in another terminal:

```bash
castor scan
```

Hot-plug is supported for USB devices. I²C and serial devices require a rescan after
connecting.

### Python API

```python
from castor.peripherals import scan_all, scan_usb, scan_i2c, print_scan_table

# Full scan
peripherals = scan_all()

# Targeted scans
usb_devices = scan_usb()
i2c_devices = scan_i2c(bus=1)

# Print results
print_scan_table(peripherals)

# Filter by category
cameras = [p for p in peripherals if p.category in ("camera", "depth")]
motors = [p for p in peripherals if p.category == "motor"]
npus = [p for p in peripherals if p.category == "npu"]

# Get RCAN snippet for a peripheral
from castor.peripherals import to_rcan_snippet
for cam in cameras:
    print(to_rcan_snippet(cam))
```

### JSON output for scripting

```bash
# Get all camera device paths
castor scan --json | jq '.[] | select(.category == "camera") | .device_path'

# Check if any NPU is present
castor scan --json | jq 'any(.[]; .category == "npu")'

# Export scan to file
castor scan --json > peripheral_inventory.json
```

### Auto-populate rcan.yaml from scan

Use the scan output to bootstrap a new config:

```bash
castor scan
# Copy the "Suggested rcan.yaml additions" into your config:
castor wizard                        # or manually edit robot.rcan.yaml
```

### Scan in castor doctor

`castor doctor` automatically runs `castor scan` at the end of its health report,
so you always see a peripheral summary alongside the software health checks.

---

## See Also

- [Hardware Guide](hardware-guide.md) — Identifying thrift-store hardware, wiring cheatsheet
- [RCAN Config Schema](../config/rcan.schema.json) — Full config reference
- [Configuration Presets](../config/presets/) — Ready-made configs for common robot kits
- [CONTRIBUTING.md](../CONTRIBUTING.md) — How to add new VID:PID entries or drivers
