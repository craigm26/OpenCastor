# PiCar-X Home Patrol Bot

> Autonomous home patrol using SunFounder PiCar-X with camera pan/tilt and Gemini vision

## Overview

| | |
|---|---|
| **Category** | Home & Indoor |
| **Difficulty** | Beginner — off-the-shelf kit, no soldering |
| **AI Provider** | Google Gemini 2.5 Flash |
| **Budget** | ~$120 |
| **OpenCastor Version** | 2026.2.17.7 |

## Use Case

My apartment has had some water leak issues, so I wanted a robot that could do a nightly check — look under sinks, check window sills for condensation, spot anything on the floor that shouldn't be there. The PiCar-X is perfect because it's small enough to navigate between furniture and the camera pan/tilt lets it look around corners.

## Hardware

- SunFounder PiCar-X v2.0 (~$60 on Amazon)
- Raspberry Pi 4B 4GB (~$45)
- Pi Camera Module v3 (~$15)
- 32GB microSD card

Total: ~$120, no soldering required.

## What Works Well

- **Gemini 2.5 Flash** is great for this — fast enough for real-time vision at 15fps, and the image understanding is solid. It reliably spots water puddles, open cabinets, and items on the floor.
- **Low speed (0.3 max)** is key indoors. Anything faster and it bumps into chair legs.
- **Camera pan/tilt** really helps — the robot sweeps left/right at each waypoint to get a wider view.
- **WhatsApp alerts** work perfectly. I get a photo + description when it spots something unusual.

## What I'd Change

- The PiCar-X wheels slip on hardwood. I added rubber bands around the wheels — instant traction.
- `obstacle_distance_m: 0.25` is good for open areas but too aggressive near furniture clusters. I'm experimenting with 0.35.
- Would love ultrasonic sensors for better obstacle detection. The camera-only approach misses transparent objects (glass doors).

## Lessons Learned

1. **Start with `castor demo --simulate` first** to test your system prompt before putting it on hardware.
2. **Gemini is cheaper than Claude for vision patrol** — this runs for ~$0.02/night.
3. **The system prompt matters a lot.** "Move cautiously" in the prompt actually makes it drive slower and more carefully. Without it, the robot tries to speed through rooms.
4. **Nightly patrol at 11 PM works great** — house is quiet, less interference from pets and people.

## Quick Start

```bash
curl -sL opencastor.com/install | bash
castor wizard  # Select Google Gemini + SunFounder PiCar-X
# Or just copy this config:
cp config.rcan.yaml my_patrol.rcan.yaml
castor run --config my_patrol.rcan.yaml
```

---

*Shared via [OpenCastor Community Hub](https://opencastor.com/hub) — February 2026*
