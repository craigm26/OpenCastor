# LeRobot Kit Integration

OpenCastor provides native RCAN profiles and drivers for [Hugging Face LeRobot](https://github.com/huggingface/lerobot) hardware kits, letting you deploy trained policies to physical robots via a single YAML config.

## What is LeRobot?

LeRobot is Hugging Face's open-source robotics library for imitation learning and reinforcement learning. It provides:
- Dataset recording tools (teleop → LEROBOT dataset format)
- Policy training pipelines (ACT, Diffusion Policy, Pi0)
- Pretrained models on the Hub

OpenCastor handles the **deployment side**: connect your trained policy (or any LLM brain) to the hardware via a standardized RCAN config.

## Supported Kits

| Kit | Servos | Interface | RCAN Profile |
|-----|--------|-----------|-------------|
| SO-ARM101 (follower) | 6× Feetech STS3215 | USB serial (CH340) | `lerobot/so-arm101-follower` |
| SO-ARM101 (leader/teleop) | 6× Feetech STS3215 | USB serial (CH340) | `lerobot/so-arm101-leader` |
| SO-ARM101 bimanual | 12× Feetech STS3215 | 2× USB serial | `lerobot/so-arm101-bimanual` |
| Koch arm | 4× Dynamixel XL430 + 2× XL330 | U2D2 (USB) | `lerobot/koch-arm` |
| ALOHA bimanual | 14× Dynamixel XM430/XL430 | 2× U2D2 | `lerobot/aloha` |

## FeetechDriver — SO-ARM101

The `FeetechDriver` communicates with Feetech SCS/STS serial bus servos over USB serial.

### Minimal RCAN config

```yaml
rcan_version: "1.1.0"
metadata:
  robot_name: so-arm101
agent:
  provider: google
  model: gemini-2.5-flash
drivers:
- id: arm
  protocol: feetech
  port: auto          # auto-detects CH340 adapter; or set e.g. /dev/ttyUSB0
  baudrate: 1000000
  servo_ids: [1, 2, 3, 4, 5, 6]
```

### Wiring

```
SO-ARM101 servo chain → Feetech USB adapter (CH340) → Raspberry Pi / Linux PC USB
```

Power the servo bus from a 5V/3A supply. The CH340 USB adapter handles TTL-level half-duplex serial.

### Install

```bash
pip install opencastor[lerobot]
```

This installs the `feetech-servo-sdk` and `dynamixel-sdk` packages.

## Dynamixel U2D2 — Koch Arm

The Koch arm uses Dynamixel XL430-W250-T (4×) and XL330-M288-T (2×) servos, controlled via the U2D2 USB-to-RS485 adapter.

### Minimal RCAN config

```yaml
rcan_version: "1.1.0"
metadata:
  robot_name: koch-arm
agent:
  provider: google
  model: gemini-2.5-flash
drivers:
- id: arm
  protocol: dynamixel
  port: auto            # detects U2D2 by VID 0x0403 PID 0x6014
  baudrate: 57600
  servo_ids: [1, 2, 3, 4, 5, 6]
```

### Wiring

```
Koch arm servo chain → U2D2 (USB-RS485) → Raspberry Pi / Linux PC USB
```

## RCAN Profiles

Pre-built profiles are in `castor/profiles/lerobot/`. Use them with:

```bash
castor hub install lerobot/so-arm101-follower
castor run --config ~/.castor/profiles/lerobot/so-arm101-follower.yaml
```

Or reference in your own config:

```yaml
# Extend a preset profile
extends: lerobot/so-arm101-follower
metadata:
  robot_name: my-arm
agent:
  provider: anthropic
  model: claude-sonnet-4-6
```

## LeRobot Compatibility

The recommended workflow:

1. **Record** datasets with LeRobot's teleop tools → upload to Hugging Face Hub
2. **Train** a policy (ACT, Diffusion Policy) using LeRobot
3. **Deploy** via OpenCastor — use the ONNX provider to run the exported policy, or connect any LLM brain for language-driven control

LeRobot and OpenCastor can run side-by-side on the same machine; they don't conflict.
