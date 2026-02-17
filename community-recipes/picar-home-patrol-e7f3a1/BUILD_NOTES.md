# Build Notes — PiCar-X Home Patrol

## Assembly Tips

The PiCar-X kit comes with great instructions, but a few things tripped me up:

1. **Camera mount orientation** — mount the camera module with the ribbon cable going DOWN, not up. Otherwise the pan servo hits the cable at full rotation.

2. **I2C address** — the PCA9685 on the PiCar-X defaults to `0x40`. Verify with `i2cdetect -y 1` before running.

3. **Power** — the included battery pack works but dies after ~45 min of patrol. I switched to a 10000mAh USB-C power bank zip-tied to the chassis. Gets 3+ hours now.

## Software Setup

```bash
# On a fresh Raspberry Pi OS (64-bit Lite):
sudo apt update && sudo apt install -y python3-pip i2c-tools

# Enable I2C and camera
sudo raspi-config
# → Interface Options → I2C → Enable
# → Interface Options → Camera → Enable

# Install OpenCastor
curl -sL opencastor.com/install | bash

# Test hardware
castor test-hardware --config config.rcan.yaml
```

## Calibration

The steering on mine pulled slightly left. Fixed with:

```bash
castor calibrate --config config.rcan.yaml
# Steering offset: -3 (varies per unit)
```

## Network

The robot connects to WiFi and the WhatsApp channel runs on the Pi itself (via neonize). No cloud server needed. Just scan the QR code once during `castor wizard`.
