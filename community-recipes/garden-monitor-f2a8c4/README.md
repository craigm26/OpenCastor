# Garden Health Monitor

> Stationary garden robot that monitors plant health and soil conditions, with daily Telegram reports powered by GPT-4o

## Overview

| | |
|---|---|
| **Category** | Agriculture |
| **Difficulty** | Beginner — no motors, just sensors and camera |
| **AI Provider** | OpenAI GPT-4o |
| **Budget** | ~$95 |
| **OpenCastor Version** | 2026.2.17.7 |

## Use Case

I travel for work 2-3 days a week and my raised bed garden was suffering. This setup sits between two beds, takes photos 3 times a day, reads soil moisture and temperature, and sends me a Telegram digest every morning. GPT-4o is surprisingly good at spotting early aphid infestations and nutrient deficiency from leaf photos.

## Hardware

- Raspberry Pi 4B 4GB (~$45)
- Pi Camera Module v3 (~$15)
- Capacitive soil moisture sensor (~$4)
- DHT22 temperature/humidity sensor (~$5)
- Waterproof enclosure (~$12) — I used a food container with holes drilled for the camera and sensors
- 5V 3A solar panel + battery pack (~$25)
- 32GB microSD (~$8)

Total: ~$95, no soldering if you use jumper wires

## What Works Well

- **GPT-4o vision** is excellent at plant health analysis. It correctly identified early blight on my tomatoes before I could see it myself.
- **Solar power** keeps it running indefinitely in summer. Spring/fall it sometimes sleeps overnight but wakes reliably.
- **Telegram alerts** for critical moisture levels saved my peppers during a heat wave — I got an alert at 2 PM and asked a neighbor to water.
- **3 photos/day** is the sweet spot. More wastes API credits; fewer misses fast changes.

## What I'd Change

- Add a pan/tilt servo to cover more area. Currently points at one 4-foot section.
- Rain sensor would prevent false "needs water" alerts after rain.
- The DHT22 reads high in direct sunlight. Mount it in shade.

## Setup

```bash
curl -sL opencastor.com/install | bash
castor hub install garden-monitor-f2a8c4

# Calibrate soil sensor — stick in dry soil, then wet soil
castor sensor calibrate --type soil_moisture

# Test with a single capture
castor run --config config.rcan.yaml --once
```

## Wiring

- Soil moisture sensor: VCC→3.3V, GND→GND, signal→GPIO17
- DHT22: VCC→3.3V, GND→GND, data→GPIO4 (add 10kΩ pull-up resistor)

---

*Shared via [OpenCastor Community Hub](https://opencastor.com/hub) — February 2026*
