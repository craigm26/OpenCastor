# Farm Scout — Crop Row Inspector

> 4WD rover that drives between crop rows checking for pests, disease, and irrigation issues using Llama 3.3 on Hugging Face

## Overview

| | |
|---|---|
| **Category** | Agriculture & Farming |
| **Difficulty** | Intermediate — some wiring + 3D-printed boom |
| **AI Provider** | Hugging Face (Llama 3.3 70B) |
| **Budget** | ~$200 |
| **OpenCastor Version** | 2026.2.17.7 |

## Use Case

I run a small market garden (12 raised beds) and checking every plant for pests and disease was eating 2 hours of my morning. Now the robot does a 30-minute scan at 6:30 AM before I even wake up. By 7 AM I have a Telegram report telling me exactly which rows need attention and what to look for.

## Why Hugging Face + Llama 3.3?

- **Cost**: Inference API is way cheaper than commercial vision APIs for batch processing. I run ~200 inference calls per morning scan for about $0.15.
- **No vendor lock-in**: If Meta releases Llama 4, I change one line. If I want to try Qwen or Mistral, same thing.
- **Privacy**: My garden photos don't go to a big-company API. Hugging Face Inference API has better data policies, or I can run it on my own endpoint.
- **`castor login hf`** made setup trivial. One command to authenticate.

## What Works Well

- The **Freenove 4WD** handles dirt and gravel paths between beds without issues. The 4-wheel drive is essential for outdoor terrain.
- **Llama 3.3 70B** is surprisingly good at agricultural diagnosis from text descriptions. I preprocess camera frames into detailed text descriptions, and it catches things I'd miss.
- **Solar charging** between scans means the robot is always ready. The Waveshare Solar Power Manager keeps it topped up.
- The **3D-printed camera boom** lets the camera look down at plants from above, which is way better than the default forward-facing position.

## What I'd Change

- Want to switch to a vision-language model (LLaVA or Qwen-VL) to skip the text preprocessing step. Waiting for Llama 4 Scout to stabilize on the Inference API.
- The geofence is GPS-based which isn't accurate enough between 4-foot-wide raised beds. Looking into RTK-GPS or visual lane following.
- Rain is still a problem. Need a better waterproof enclosure. Currently I just don't run it when it's raining (cron skips based on weather API check).

## Photos

_TODO: Add photos of the rover in the garden_

---

*Shared via [OpenCastor Community Hub](https://opencastor.com/hub) — February 2026*
