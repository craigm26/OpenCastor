# Community Recipes — OpenCastor

Community recipes are shared robot configurations — tested RCAN configs plus optional scripts and documentation contributed by real builders. Browse, install, and remix them to skip the hard parts.

**Browse online:** [opencastor.com/hub](https://opencastor.com/hub)  
**Browse CLI:** `castor hub browse`  
**Submit a recipe:** `castor hub share --submit`

---

## Table of Contents

1. [What is a Recipe?](#what-is-a-recipe)
2. [Directory Structure](#directory-structure)
3. [The Manifest (recipe.json)](#the-manifest-recipejson)
4. [The Config (config.rcan.yaml)](#the-config-configrcanyaml)
5. [Optional Files](#optional-files)
6. [How to Create a Recipe](#how-to-create-a-recipe)
7. [How to Submit a Recipe](#how-to-submit-a-recipe)
8. [The Review Process](#the-review-process)
9. [Recipe Quality Standards](#recipe-quality-standards)
10. [Example Recipes](#example-recipes)

---

## What is a Recipe?

A recipe is a shareable, self-contained package that lets another builder reproduce your robot setup. Think of it as a Dockerfile — but for a robot's brain.

A recipe contains:
- **A working RCAN config** (`config.rcan.yaml`) — the robot's brain, validated and PII-scrubbed
- **A manifest** (`recipe.json`) — metadata: who made it, what hardware, what AI, how much it costs
- **Documentation** (`README.md`, optionally `BUILD_NOTES.md`, `LESSONS.md`) — what you learned

Recipes live in [`community-recipes/`](../community-recipes/) in the repo and are indexed in [`community-recipes/index.json`](../community-recipes/index.json).

### What a recipe is NOT

- A tutorial (though you can link to one)
- A product pitch
- Theoretical — every recipe must be tested on real hardware

---

## Directory Structure

Each recipe is a directory inside `community-recipes/`:

```
community-recipes/
├── index.json                          # Master index of all recipes
├── README.md                           # Overview of the recipe system
│
├── picar-home-patrol-e7f3a1/           # <slug>-<6char-hash>/
│   ├── recipe.json                     # Manifest (required)
│   ├── config.rcan.yaml                # Robot config (required)
│   ├── README.md                       # Overview + quick-start (required)
│   └── BUILD_NOTES.md                  # Optional: detailed build notes
│
├── llama-farm-scout-b4d2e8/
│   ├── recipe.json
│   ├── config.rcan.yaml
│   ├── README.md
│   └── scripts/                        # Optional: helper scripts
│       ├── daily_report.py
│       └── calibrate_camera.sh
│
└── classroom-assistant-a1c3f7/
    ├── recipe.json
    ├── config.rcan.yaml
    └── README.md
```

### Naming Convention

```
<short-name>-<6char-random-hex>/
```

Examples:
- `picar-home-patrol-e7f3a1`
- `llama-farm-scout-b4d2e8`
- `hailo-warehouse-scan-c3d9a2`

The 6-char suffix makes recipe IDs globally unique and stable (used in `castor hub install <id>`).

---

## The Manifest (recipe.json)

The manifest is the metadata file for your recipe. It's used by `castor hub browse`, `castor hub show`, and the community website.

### Required Fields

```json
{
  "id": "picar-home-patrol-e7f3a1",
  "name": "PiCar-X Home Patrol Bot",
  "description": "Autonomous home patrol using SunFounder PiCar-X with camera pan/tilt and Gemini vision",
  "author": "your_github_username",
  "category": "home",
  "difficulty": "beginner",
  "hardware": [
    "SunFounder PiCar-X v2.0",
    "Raspberry Pi 4B (4GB)",
    "Pi Camera Module v3",
    "32GB microSD"
  ],
  "ai": {
    "provider": "google",
    "model": "gemini-2.5-flash"
  },
  "tags": ["patrol", "home", "camera", "autonomous"],
  "budget": "$120",
  "use_case": "One sentence: what does this robot actually do day-to-day?",
  "created": "2026-02-15T10:00:00Z",
  "version": "1.0.0",
  "opencastor_version": "2026.2.17.7",
  "files": {
    "config": "config.rcan.yaml",
    "readme": "README.md",
    "docs": ["BUILD_NOTES.md"]
  }
}
```

### Field Reference

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `id` | string | ✅ | Unique ID — must match directory name |
| `name` | string | ✅ | Human-readable recipe name (max 60 chars) |
| `description` | string | ✅ | One-sentence description (max 120 chars) |
| `author` | string | ✅ | Your GitHub username or handle |
| `category` | string | ✅ | One of: `home`, `agriculture`, `outdoor`, `industrial`, `education`, `research`, `service` |
| `difficulty` | string | ✅ | One of: `beginner`, `intermediate`, `advanced` |
| `hardware` | array | ✅ | Exact model names of all hardware components |
| `ai.provider` | string | ✅ | One of: `anthropic`, `google`, `openai`, `huggingface`, `ollama`, `llamacpp`, `mlx` |
| `ai.model` | string | ✅ | Exact model ID (e.g. `gemini-2.5-flash`, `claude-haiku-4`) |
| `tags` | array | ✅ | 2–8 lowercase tags for search |
| `budget` | string | ✅ | Approximate total hardware cost (e.g. `"$120"`) |
| `use_case` | string | ✅ | What it actually does in plain English |
| `created` | ISO 8601 | ✅ | Creation date |
| `version` | semver | ✅ | Recipe version |
| `opencastor_version` | string | ✅ | OpenCastor version it was tested with |
| `files.config` | string | ✅ | Filename of the RCAN config |
| `files.readme` | string | ✅ | Filename of the README |
| `files.docs` | array | — | Additional doc filenames |

---

## The Config (config.rcan.yaml)

The RCAN config is the heart of the recipe. It tells OpenCastor exactly how to run your robot.

### Minimal example (local brain, no API key needed)

```yaml
# config.rcan.yaml — minimal PiCar-X with Ollama
version: "1"
robot:
  name: "home-patrol"
  driver: "picar_x"

brain:
  provider: "ollama"
  model: "llama3.2-vision"
  system_prompt: |
    You are a home security robot. Patrol the house and report anything unusual.
    Respond with JSON: {"action": "...", "report": "...", "alert": true/false}

vision:
  source: "picamera"
  resolution: [640, 480]
  fps: 2

channels:
  - type: "whatsapp"
    phone: "${WHATSAPP_PHONE}"
    allowed_users: ["${OWNER_PHONE}"]

safety:
  max_speed: 0.4
  emergency_stop: true
  work_authorization: true
```

### Production example (tiered brain with planner)

```yaml
version: "1"
robot:
  name: "farm-scout"
  driver: "freenove_4wd"

brain:
  fast:
    provider: "huggingface"
    model: "meta-llama/Llama-3.3-70B-Instruct"
    system_prompt: |
      You are a farm scouting robot. Inspect crops for pests, disease, dry spots.
      Respond with JSON: {"action": "move_forward|stop|turn_left|turn_right",
                          "observation": "...", "severity": "none|low|high"}
  planner:
    provider: "anthropic"
    model: "claude-haiku-4"
    system_prompt: |
      You are the planner for a farm robot. Given observations from the fast brain,
      decide if an alert should be sent and draft the daily report.

vision:
  source: "picamera"
  resolution: [1280, 720]
  fps: 1

schedule:
  patrol: "0 7 * * *"    # 7am daily
  report: "0 19 * * *"   # 7pm daily

channels:
  - type: "telegram"
    token: "${TELEGRAM_BOT_TOKEN}"
    chat_id: "${TELEGRAM_CHAT_ID}"

safety:
  max_speed: 0.3
  bounds:
    area_m2: 500
  emergency_stop: true
```

### PII Scrubbing

Before sharing, run `castor hub share --submit` to auto-scrub:
- API keys and tokens → `[REDACTED]`
- Email addresses → `[EMAIL_REDACTED]`  
- Phone numbers → `[PHONE_REDACTED]`
- Hostnames → `[HOSTNAME_REDACTED]`
- Public IP addresses → `[IP_REDACTED]`
- Home directory paths → `/home/user/`

The tool preserves private/local IPs (192.168.x.x, 10.x.x.x) since they're not unique.

---

## Optional Files

### README.md (required)

Your README should cover:
- **What it does** — a clear 2–3 sentence description
- **Hardware list** — every component with model numbers and approximate prices
- **Quick Start** — minimal steps to get it running
- **How it works** — brief explanation of the brain/vision/schedule
- **Limitations** — what it can't do, known issues
- **Links** — where to buy parts, any related projects

### BUILD_NOTES.md (recommended)

Build notes are where you share the messy truth:
- What didn't work and why
- Which hardware combinations cause problems
- Calibration steps you had to figure out manually
- Gotchas and edge cases

Builders often find BUILD_NOTES.md more useful than the README.

### LESSONS.md (optional)

A brief retrospective:
- "If I were starting over, I'd..."
- "The biggest time sink was..."
- "What surprised me most..."

### scripts/ (optional)

Helper scripts that support the recipe:
- `calibrate_camera.sh` — camera calibration routine
- `daily_report.py` — custom report formatter
- `setup_hardware.sh` — hardware setup automation

Keep scripts minimal and document what they do.

---

## How to Create a Recipe

### Step 1: Build and test your robot

Get your robot working reliably. Test it over multiple sessions. Document what version of OpenCastor you're using (`castor --version`).

### Step 2: Package your config

```bash
# This scrubs PII, validates the config, and generates a submission bundle
castor hub share --submit

# Or specify files explicitly
castor hub share --submit \
  --config my_robot.rcan.yaml \
  --docs BUILD_NOTES.md LESSONS.md
```

The command outputs a directory like `my-robot-a3b2c1/` ready for submission.

### Step 3: Write your README.md

Use this template:

```markdown
# [Recipe Name]

[2-3 sentence description of what the robot does and why you built it.]

## Hardware

| Component | Model | Price |
|-----------|-------|-------|
| Robot chassis | SunFounder PiCar-X v2.0 | ~$60 |
| Compute | Raspberry Pi 4B 4GB | ~$45 |
| Camera | Pi Camera Module v3 | ~$25 |
| Storage | 32GB microSD | ~$10 |
| **Total** | | **~$140** |

## Quick Start

\```bash
castor hub install classroom-assistant-a1c3f7
# Set env vars in .env
castor run --config config.rcan.yaml
\```

## How It Works

[Brief explanation: what the robot sees, what the AI does with it, how results are delivered.]

## Limitations

- [Known issue 1]
- [Known issue 2]

## Links

- [PiCar-X on SunFounder](https://www.sunfounder.com/products/picar-x)
```

### Step 4: Fill in recipe.json

Copy the template from the manifest section above. Make sure:
- `id` matches your directory name exactly
- `opencastor_version` matches your installed version
- `hardware` lists every component by exact model name

---

## How to Submit a Recipe

Recipes are submitted as GitHub Pull Requests.

```bash
# 1. Fork the repo
gh repo fork craigm26/OpenCastor --clone

# 2. Create a branch
cd OpenCastor
git checkout -b add-my-recipe

# 3. Copy your packaged recipe
cp -r /path/to/my-recipe-a3b2c1/ community-recipes/

# 4. Add to the index
# Edit community-recipes/index.json to add your recipe metadata

# 5. Commit and push
git add community-recipes/
git commit -m "Add my-recipe-a3b2c1: [one-line description]"
git push origin add-my-recipe

# 6. Open a PR
gh pr create --title "Add [recipe name]" --body "$(cat community-recipes/my-recipe-a3b2c1/README.md | head -20)"
```

### Updating index.json

Add an entry to `community-recipes/index.json`:

```json
{
  "id": "my-recipe-a3b2c1",
  "name": "My Robot Recipe",
  "category": "home",
  "difficulty": "beginner",
  "provider": "google",
  "model": "gemini-2.5-flash",
  "budget": "$120"
}
```

---

## The Review Process

When you open a PR, maintainers will:

1. **PII scan** — check that no API keys, emails, or personal data leaked through
2. **Schema validation** — validate `recipe.json` and `config.rcan.yaml` against the RCAN spec
3. **Content review** — verify the recipe is real (tested on hardware, not theoretical)
4. **README check** — confirm the README has hardware list, quick-start, and limitations
5. **Merge** — once approved, your recipe appears in `castor hub browse`

**Typical review time:** 2–5 days. We're a small team — thank you for your patience.

### Common rejection reasons

- API keys or tokens not redacted
- No hardware list with real model names
- No tested version of OpenCastor listed
- Config doesn't validate against RCAN schema
- Recipe is theoretical / not tested on real hardware

---

## Recipe Quality Standards

### ✅ Good

- Tested on the hardware listed, at the OpenCastor version listed
- Honest about limitations ("This doesn't work well in low light")
- Specific hardware: "Raspberry Pi 4B 4GB" not "Raspberry Pi"
- Real budget numbers (not "varies")
- BUILD_NOTES.md with what you'd do differently

### ❌ Avoid

- Generic configs that work "theoretically" on any hardware
- Configs with hardcoded absolute paths (`/home/yourname/...`)
- Configs that require paid API access with no free alternative
- Recipes without a clear use case

---

## Example Recipes

### Beginner: PiCar-X Home Patrol Bot

**Hardware:** SunFounder PiCar-X v2.0 + Raspberry Pi 4B (4GB) + Pi Camera v3  
**AI:** Google Gemini 2.5 Flash (free tier)  
**Budget:** ~$120  
**Use case:** Patrols apartment at night, sends WhatsApp alerts for anything unusual

```bash
castor hub install picar-home-patrol-e7f3a1
```

Key config decisions:
- Gemini 2.5 Flash chosen for vision quality on free tier
- 2 FPS capture (battery-friendly, fast enough for stationary detection)
- WhatsApp for alerts (no app install needed for the recipient)
- `max_speed: 0.4` — slow enough to not startle pets or tip over

---

### Intermediate: Farm Scout

**Hardware:** Freenove 4WD + Raspberry Pi 5 (8GB) + Pi Camera v3 wide-angle  
**AI:** Llama 3.3 70B on Hugging Face (free inference API)  
**Budget:** ~$200  
**Use case:** Daily crop row inspection, Telegram report with pest/disease findings

```bash
castor hub install llama-farm-scout-b4d2e8
```

Key config decisions:
- Pi 5 for improved inference speed when using local fallback
- Wide-angle camera (160°) to see entire crop row from center path
- 1 FPS — crop inspection doesn't require real-time reaction
- Scheduled patrol at 7am, report at 7pm
- Solar power manager: the robot runs autonomously all day

---

### Advanced: Research Data Collector

**Hardware:** Freenove 4WD + Raspberry Pi 5 (8GB) + GPS module + environmental sensors  
**AI:** Google Gemini 2.5 Flash  
**Budget:** ~$220  
**Use case:** GPS-tagged multi-sensor field data collection, structured JSONL logging, fleet coordination

```bash
castor hub install research-data-collector-c9f4a3
```

Key config decisions:
- Fleet coordination via shared state (multiple robots, one report)
- GPS tagging on every observation
- JSONL logging for downstream data pipeline
- Gemini for structured JSON output (important for data pipelines)

---

## Questions?

- **Discord:** [discord.gg/jMjA8B26Bq](https://discord.gg/jMjA8B26Bq)
- **GitHub Issues:** [github.com/craigm26/OpenCastor/issues](https://github.com/craigm26/OpenCastor/issues)
- **Hub:** [opencastor.com/hub](https://opencastor.com/hub)
