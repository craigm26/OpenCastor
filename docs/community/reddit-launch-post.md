# Reddit Launch Post Draft

**Subreddit:** r/robotics (also consider: r/raspberry_pi, r/MachineLearning, r/DIY, r/homeautomation)  
**Post type:** Text post  
**Tone:** Builder sharing something cool, not marketing  

---

## Title Options

1. `I built an open-source framework that gives any robot an AI brain — tiered architecture, 8 providers, self-improving loop`
2. `Built OpenCastor: an open-source runtime for robot AI — runs on Pi 5 + Hailo-8 + OAK-D, 8 providers, self-improving`
3. `Show HN-style: OpenCastor — give any robot a tiered AI brain, hot-swap providers, 2K+ tests`

**Recommended title:**
> I built an open-source framework to give any robot a tiered AI brain — runs on Pi 5 + Hailo-8 + OAK-D, 8 AI providers, self-improving

---

## Post Body

---

Hey r/robotics,

I've been working on a project for the past year and finally feel like it's ready to share. It's called **OpenCastor** — an open-source runtime that gives any robot an AI brain.

**The problem I was trying to solve:**

Every time I built a new robot, I was rewriting the same glue code: camera capture → AI call → motor command → repeat. And every time I swapped to a new AI model, I had to rewrite the entire pipeline. I wanted a framework where I could describe my robot in a config file and swap brains without touching code.

**What I built:**

OpenCastor is a Python framework with a tiered cognitive architecture:

- **Reactive layer** — pure code, no AI latency, <1ms. Emergency stops, collision avoidance, physical bounds enforcement.
- **Fast brain** — open-source models via HuggingFace, Ollama, or llama.cpp. ~100ms decisions. This is your robot's default mode.
- **Planner** — cloud AI (Claude, Gemini, GPT-4.1) for complex multi-step reasoning. Only invoked when the fast brain escalates.

The whole thing is described in a YAML config (RCAN format):

```yaml
robot:
  name: "farm-scout"
  driver: "freenove_4wd"

brain:
  fast:
    provider: "huggingface"
    model: "meta-llama/Llama-3.3-70B-Instruct"
  planner:
    provider: "anthropic"
    model: "claude-haiku-4"

vision:
  source: "picamera"
  fps: 1
```

To swap the entire AI brain, you change one line. No code changes.

**Hardware it runs on:**

My main dev setup is:
- Raspberry Pi 5 (8GB)
- Hailo-8 NPU — hardware-accelerated YOLOv8 at 30+ FPS, no GPU required
- OAK-D depth camera — stereo depth + on-device neural inference

But it also runs on Pi 4, Pi Zero (slow but works), Jetson, and x86 Linux/macOS. ARM64 native.

**The self-improving loop:**

This is the part I'm most excited about. The robot runs ALMA (Autonomous Lifecycle Management Agent) — it watches its own performance, identifies failure patterns, and proposes code patches. Patches go through QA and PM stages before applying. It's like a robot that writes its own bug fixes.

**Community recipes:**

I built a hub system for sharing working configs. Instead of starting from scratch, you can do:

```bash
castor hub install picar-home-patrol-e7f3a1
```

And you get a tested config for a PiCar-X home patrol bot. I've seeded 7 recipes — home patrol, farm scout, classroom assistant, warehouse scanner, etc. The goal is a community library of working robot configs.

**Numbers:**
- 49K+ lines of code
- 2,009 tests
- 8 AI providers (Anthropic, Google, OpenAI, HuggingFace, Ollama, llama.cpp, MLX, Claude OAuth)
- Primary brain is free forever (HuggingFace free inference API or local models)

**Quick start:**

```bash
pip install opencastor
castor wizard   # interactive setup
castor run --config my_robot.rcan.yaml
```

**Links:**
- Site: https://opencastor.com
- GitHub: https://github.com/craigm26/OpenCastor
- Community hub: https://opencastor.com/hub

Happy to answer questions about the architecture, hardware choices, or anything else. I know the ALMA self-improving stuff sounds wild — I can explain how the safety checks work if people are curious.

---

## Notes for Posting

- Post on a weekday, 9-11am Eastern or 2-4pm Eastern for max visibility
- Cross-post to r/raspberry_pi with title focusing on Pi 5 + Hailo-8 setup
- Cross-post to r/MachineLearning focusing on the tiered brain architecture
- Don't cross-post all at once — stagger by a few days
- Reply to every early comment — momentum matters in the first 2 hours
- Prepare to share: architecture diagram, demo video clip, code snippets

## Key talking points to prepare for comments

- "Why not just use ROS?" — OpenCastor is application-level, not low-level robotics middleware. Think of it as the "brain" layer that sits on top of whatever motor control you have. ROS handles the bottom layers; OpenCastor handles AI decisions.
- "Is this production-ready?" — Tested on real hardware, 2K+ tests, used in my personal robots daily. Not battle-hardened for industrial deployment yet. Great for makers and researchers.
- "What's the Hailo-8 setup like?" — Hailo provides the HailoRT runtime. OpenCastor wraps it with a clean vision pipeline. The main gotcha is that you need the right .hef model file for Hailo — we ship one for YOLOv8.
- "Self-improving loop safety?" — Every patch requires explicit work authorization, goes through automated QA, and can be rolled back. There's a tamper-evident audit log. Nothing applies without human-readable approval.
- "Does it work offline?" — Yes. Ollama + llama.cpp = fully local, no internet required. The fast brain can run entirely on-device.
