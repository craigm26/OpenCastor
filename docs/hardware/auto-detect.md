# Hardware Auto-Detection

OpenCastor can automatically identify connected hardware using `castor scan`. No manual port configuration required for supported devices.

## What `castor scan` Does

`castor scan` probes multiple discovery channels simultaneously:

1. **USB VID/PID** — reads all USB descriptors via `lsusb` (memoized; called once per scan)
2. **I2C address scan** — queries `/dev/i2c-*` buses for known device addresses
3. **PCIe / sysfs** — checks `lspci` output and `/dev/hailo*` for NPUs
4. **Network / mDNS** — resolves known robot hostnames (e.g. `reachy.local`) concurrently

Results are printed as a categorized table and can be consumed programmatically:

```python
from castor.hardware_detect import detect_hardware

hw = detect_hardware()
print(hw)
# {
#   "realsense": [{"vid": "0x8086", "product": "RealSense D435"}],
#   "oak": [{"vid": "0x03E7", "product": "OAK-D-Pro"}],
#   "arduino": [{"vid": "0x2341", "port": "/dev/ttyACM0"}],
#   ...
# }
```

## Detected Device Types

| Device | VID/PID | Detection Method |
|--------|---------|-----------------|
| Intel RealSense D4xx / L515 | VID `0x8086` | USB descriptor |
| Luxonis OAK-D / OAK-D-Lite / OAK-D-Pro | VID `0x03E7` | USB descriptor |
| ODrive v3 / Pro / S1 | VID `0x1209` | USB descriptor |
| VESC motor controller | VID `0x0483` (product string filter) | USB descriptor |
| Hailo-8 NPU | — | `lspci` + `/dev/hailo0` + Python import |
| Google Coral USB Accelerator | VID `0x1A6E` / `0x18D1` | USB descriptor |
| Google Coral M.2 TPU | — | `lspci` |
| Arduino (official) | VID `0x2341` | USB descriptor |
| Arduino clones (CH340/FTDI) | VID `0x1A86` / `0x0403` | USB descriptor + product string |
| Adafruit CircuitPython boards | VID `0x239A` | USB descriptor |
| Dynamixel U2D2 | VID `0x0403` PID `0x6014` | USB descriptor (exact match) |
| RPLidar / YDLIDAR | VID `0x10C4` / `0x0483` (product string) | USB descriptor |
| Raspberry Pi AI Camera (IMX500) | — | `picamera2` probe |
| Pollen Robotics Reachy 2 | — | mDNS (`reachy.local`) |
| Pollen Robotics Reachy Mini | — | mDNS (`reachy-mini.local`) |

## I2C Address Lookup Table

When I2C devices are found, their addresses are matched against a built-in table:

| I2C Address | Device |
|-------------|--------|
| `0x28` / `0x29` | BNO055 IMU |
| `0x29` | VL53L1X ToF distance sensor |
| `0x3C` / `0x3D` | SSD1306 OLED display |
| `0x48` | ADS1115 ADC |
| `0x76` / `0x77` | BME280 environmental sensor |
| `0x6A` / `0x6B` | LSM6DSO IMU |
| `0x1E` | HMC5883L magnetometer |
| `0x40` | INA219 current sensor |
| `0x60` | MPR121 capacitive touch |
| `0x68` / `0x69` | MPU6050 / MPU9250 IMU |

## How `suggest_preset()` Works

After scanning, `suggest_preset()` recommends an RCAN profile:

1. **Exact VID/PID match** — highest confidence (e.g. Dynamixel U2D2 → `lerobot/koch-arm`)
2. **Vendor + product string** — medium confidence (e.g. Hailo-8 → `hailo/hailo8-inference`)
3. **Network discovery** — hostname-based (e.g. `reachy-mini.local` → `pollen/reachy-mini`, `reachy.local` → `pollen/reachy2`)
4. **Generic fallback** — e.g. any Arduino → `arduino/uno`

Reachy Mini is distinguished from Reachy 2 by checking which hostname resolves first.

## `port: auto` in Driver Configs

Supported drivers accept `port: auto` in their RCAN config block:

```yaml
drivers:
- id: arm
  protocol: feetech
  port: auto        # detects CH340 serial adapter automatically
  baudrate: 1000000

- id: wheels
  protocol: dynamixel
  port: auto        # detects U2D2 by VID/PID 0x0403/0x6014
  baudrate: 57600

- id: lidar
  protocol: rplidar
  port: auto        # detects RPLidar by USB product string
```

When `port: auto` is set, the driver calls the corresponding `detect_*_usb()` function from `castor.hardware_detect` at startup. If no device is found, the driver falls back to mock mode.

## Network Discovery (Reachy mDNS)

Reachy robots advertise themselves on the local network. `detect_reachy_network()` probes `reachy.local` and `reachy-mini.local` in parallel daemon threads so discovery never blocks startup:

```python
from castor.hardware_detect import detect_reachy_network

result = detect_reachy_network()
# {"reachy2": "192.168.1.42", "reachy_mini": None}
```

Set `host: auto` in the Reachy driver config to use this automatically.

## Cache Invalidation for Hot-Plug

USB descriptors are memoized for the lifetime of a scan. If you plug in a device after startup, call:

```python
from castor.hardware_detect import invalidate_usb_descriptors_cache

invalidate_usb_descriptors_cache()
# Next call to detect_hardware() or any detect_*() function will re-run lsusb
```

## Install Extras

```bash
pip install opencastor[lerobot]   # Feetech SCS/STS SDK + Dynamixel SDK (SO-ARM101, Koch arm, ALOHA)
pip install opencastor[reachy]    # reachy2-sdk + zeroconf (Pollen Robotics Reachy 2 / Mini)
pip install opencastor[hailo]     # hailo-platform (Hailo-8 NPU inference)
pip install opencastor[coral]     # pycoral + tflite-runtime (Google Coral TPU)
```
