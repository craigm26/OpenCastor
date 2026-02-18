# Pet Companion Bot

> Interactive robot that plays with cats and dogs using camera detection and treat dispensing

## Overview

| | |
|---|---|
| **Category** | Home & Entertainment |
| **Difficulty** | Beginner — kit + one extra servo |
| **AI Provider** | Anthropic Claude Sonnet 4 |
| **Budget** | ~$85 |
| **OpenCastor Version** | 2026.2.17.7 |

## Use Case

My two cats (indoor only) get bored while I'm at work. This robot wanders around the living room on a schedule, and when a cat approaches, it pauses and drops a treat. Claude Sonnet's vision is good enough to distinguish between my cats approaching vs. just walking past. I get WhatsApp photos of playtime and an evening summary.

## Hardware

- SunFounder PiCar-X v2.0 (~$60)
- Raspberry Pi 4B 4GB (~$45 — I had this already)
- Pi Camera Module v3 (~$15)
- SG90 micro servo (~$3)
- 3D printed treat hopper or small cardboard funnel
- 32GB microSD (~$8)

Total: ~$85 (less if you already have a Pi)

## Treat Dispenser Build

The treat dispenser is dead simple: mount an SG90 servo on top of the PiCar-X with a small hopper (tube or funnel) above it. The servo arm acts as a gate. When it rotates 90°, one treat falls through. I used a toilet paper tube cut in half as the hopper.

## What Works Well

- **Claude Sonnet vision** reliably detects cats and distinguishes "approaching to play" from "just walking by."
- **Random movement pattern** keeps cats interested. Predictable paths = boring after day one.
- **Treat cooldown (15 min)** prevents overfeeding. The robot tracks treats dispensed per day.
- **Photo on interaction** — I get adorable photos of my cats batting at the robot.
- **Low speed (0.20)** is important. Faster movement startles cats.

## What I'd Change

- Add a small speaker for chirping sounds — cats respond to bird-like noises.
- The PiCar-X is a bit too big for my smaller cat. A mini chassis would be better.
- Battery life is only ~2 hours of active play. A bigger battery or charging dock would help.

## Setup

```bash
curl -sL opencastor.com/install | bash
castor hub install pet-companion-b7d1e6

# Wire the SG90 servo signal to GPIO 18
# Load treats into the hopper
castor run --config config.rcan.yaml
```

## Safety Notes

- **Always supervise first few sessions** to make sure your pet isn't stressed.
- The robot stops if it detects fearful behavior (backing away, flat ears).
- Treats should be small and dry — wet food jams the hopper.
- Keep the robot in open areas; avoid running near stairs.

---

*Shared via [OpenCastor Community Hub](https://opencastor.com/hub) — February 2026*
