# Warehouse Inventory Scanner

> Camera-equipped robot that patrols warehouse aisles scanning shelves and QR codes using Gemini vision

## Overview

| | |
|---|---|
| **Category** | Industrial |
| **Difficulty** | Intermediate — requires LiDAR wiring and route calibration |
| **AI Provider** | Google Gemini 2.5 Flash |
| **Budget** | ~$280 |
| **OpenCastor Version** | 2026.2.17.7 |

## Use Case

We run a small warehouse (4 aisles, ~2000 SKUs) and cycle counts were eating 8 hours/week of staff time. This robot does a nightly scan at 2 AM, photographing every shelf section. Gemini reads the QR codes on shelf labels and flags anything out of place. Our morning report now catches misplaced items before the pick team starts.

## Hardware

- Freenove 4WD Car Kit (~$90)
- Raspberry Pi 5 8GB (~$80)
- Pi Camera Module v3 wide-angle (~$20)
- TF-Luna LiDAR (~$20) — essential for obstacle avoidance in tight aisles
- 64GB microSD (~$15)
- USB power bank 20000mAh (~$40) — lasts 2+ hours
- Optional: USB barcode scanner for close-range reads

Total: ~$280

## What Works Well

- **Gemini 2.5 Flash** reads QR codes from photos reliably at 1080p. Below 720p accuracy drops significantly.
- **1920x1080 resolution** is key — don't skimp on this for label reading.
- **TF-Luna LiDAR** prevents collisions in narrow aisles. Camera-only obstacle detection wasn't reliable enough with shelving.
- **Structured JSON output** from the AI makes it easy to pipe into our inventory management system.
- **Weekend full scan** catches overflow area issues that weeknight scans miss.

## What I'd Change

- Add a second camera angled upward for high shelves. Currently only scans eye-level and below.
- The Freenove wheels struggle on smooth concrete. Upgraded to silicone tires (~$8).
- Would add line-following tape on the floor for more precise routing.

## Setup

```bash
curl -sL opencastor.com/install | bash
castor hub install warehouse-inventory-d5e9b2

# Calibrate your routes — drive manually first
castor route record --name aisle_A
castor route record --name aisle_B
# ... etc

# Test with a single aisle
castor run --config config.rcan.yaml --route "dock -> aisle_A -> dock"
```

## Important Notes

- **Run at night** when the warehouse is empty. This robot isn't fast enough to avoid forklifts.
- **Floor must be clear** — dropped pallets or debris will block it.
- **Label your QR codes consistently** — the AI needs a predictable format to compare against inventory.

---

*Shared via [OpenCastor Community Hub](https://opencastor.com/hub) — February 2026*
