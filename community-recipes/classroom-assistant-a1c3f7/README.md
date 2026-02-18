# Classroom Q&A Assistant

> Wheeled robot that answers student questions via WhatsApp using Claude Haiku

## Overview

| | |
|---|---|
| **Category** | Education |
| **Difficulty** | Beginner — no soldering, kit-based |
| **AI Provider** | Anthropic Claude Haiku 4 |
| **Budget** | ~$140 |
| **OpenCastor Version** | 2026.2.17.7 |

## Use Case

I teach 7th grade science and wanted a physical "class helper" that students could ask questions to. They send questions via a shared WhatsApp group and the robot responds. Having it physically present in the room (vs. just a chatbot) makes students way more engaged — they actually walk up to it, wave, and ask questions out loud too.

## Hardware

- SunFounder PiCar-X v2.0 (~$60)
- Raspberry Pi 4B 4GB (~$45)
- Pi Camera Module v3 (~$15)
- Small USB speaker for audio feedback (~$10)
- 32GB microSD card (~$10)

Total: ~$140

## What Works Well

- **Claude Haiku** is perfect here — fast responses (under 1 second), cheap (~$0.01/class period), and the answers are consistently age-appropriate.
- **WhatsApp group mode** means the whole class can see Q&A. Students learn from each other's questions.
- **Rate limiting (10 per student)** prevents spam and teaches students to think before asking.
- **Very low speed (0.15)** is essential in a classroom. It creeps between desks without knocking anything over.

## What I'd Change

- Add text-to-speech so the robot can read answers aloud. Some students prefer listening.
- The PiCar-X is a bit noisy on tile floors. Foam wheels or a rubber mat help.
- Would like to add a simple LCD screen on top to display the current topic.

## Setup

```bash
curl -sL opencastor.com/install | bash
castor hub install classroom-assistant-a1c3f7
# Edit config to set your WhatsApp group
castor run --config config.rcan.yaml
```

## Tips for Teachers

1. Set the `TOPIC` environment variable each morning: `export TOPIC="photosynthesis"`
2. Review the chat log after class — great for understanding what students struggle with
3. Start with the robot stationary on a desk, then enable movement once students are used to it
4. The content filter blocks inappropriate questions automatically

---

*Shared via [OpenCastor Community Hub](https://opencastor.com/hub) — February 2026*
