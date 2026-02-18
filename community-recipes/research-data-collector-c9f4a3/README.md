# Research Data Collection Platform

> Multi-sensor fleet platform for environmental field research with GPS-tagged structured data logging

## Overview

| | |
|---|---|
| **Category** | Research |
| **Difficulty** | Advanced — fleet coordination, multiple sensors, GPS |
| **AI Provider** | Google Gemini 2.5 Flash + Gemini Robotics ER 1.5 |
| **Budget** | ~$220 per unit ($660 for fleet of 3) |
| **OpenCastor Version** | 2026.2.17.7 |

## Use Case

Our ecology lab studies ground cover changes in a 2-hectare university meadow. Manual surveying took 3 researchers a full day per week. Now, 3 robots run morning and afternoon surveys autonomously. Each covers a grid section, collecting GPS-tagged photos, air quality data (temperature, humidity, pressure, VOC levels), and ambient sound levels. All data syncs to a lab server as structured JSONL.

## Hardware (per robot)

- Freenove 4WD Car Kit (~$90)
- Raspberry Pi 5 8GB (~$80)
- Pi Camera Module v3 wide-angle (~$20)
- BME680 environmental sensor (~$12)
- NEO-6M GPS module (~$8)
- USB microphone (~$6)
- 64GB microSD (~$15)
- 20000mAh USB-C power bank (~$40)

Total: ~$220 per unit

## Fleet Architecture

The 3 robots use OpenCastor's fleet mode with `grid_partition` coordination:
- A central sync server (any laptop or Raspberry Pi on the local network) assigns grid sections
- Each robot reports heartbeats every 30 seconds
- Data batches flush to the server every 60 seconds
- If one robot goes offline, its section is flagged for manual review (not reassigned)

## What Works Well

- **Gemini 2.5 Flash** for data analysis is fast and cheap (~$0.15/survey run for all 3 robots).
- **Gemini Robotics ER** for waypoint navigation handles uneven terrain surprisingly well.
- **BME680 IAQ score** correlates well with our lab's reference air quality monitors.
- **JSONL logging** makes post-processing trivial — `jq` and Python pandas handle it natively.
- **GPS waypoint tolerance of 0.5m** is realistic for consumer GPS. Tighter tolerances cause looping.

## What I'd Change

- RTK GPS for centimeter-level accuracy (adds ~$100/unit).
- The NEO-6M takes 30-60 seconds for a cold fix. Add `hot_start: true` to keep almanac cached.
- Bigger wheels for tall grass — stock Freenove wheels bog down in vegetation over 15cm.
- Add a rain sensor to abort surveys in bad weather.

## Setup

```bash
# On each robot:
curl -sL opencastor.com/install | bash
castor hub install research-data-collector-c9f4a3

# Edit robot_id in config: unit_1, unit_2, or unit_3
# Set GPS coordinates for base and geofence bounds

# On the sync server:
castor fleet server --port 8080

# Start the fleet:
castor fleet start --config config.rcan.yaml
```

## Data Output

Each data point is a JSONL line:
```json
{"timestamp":"2026-02-17T07:15:32Z","gps_lat":37.8749,"gps_lon":-122.2585,"temperature_c":12.3,"humidity_percent":78,"pressure_hpa":1013.2,"iaq_score":42,"db_level":38,"ground_cover_class":"wildflower","photo_path":"captures/20260217_071532.jpg","notes":""}
```

---

*Shared via [OpenCastor Community Hub](https://opencastor.com/hub) — February 2026*
