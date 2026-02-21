# Advanced CLI Recipes

Copy-paste recipes for OpenCastor's power-user commands. All commands assume
you have run `pip install -e ".[all]"` and have a valid `.env` file.

## Fleet Management

### View all robots on your local network

```bash
castor fleet status

# Extend scan window for slower networks
castor fleet status --timeout 15
```

**When to use:** Check which robots are online before issuing commands.

### Health-check a specific robot via API

```bash
curl http://192.168.1.10:8000/health
# {"status": "ok", "uptime_seconds": 3421.2, "version": "2026.2.21.3"}
```

## Self-Improvement

### Record a session then trigger improvement

```bash
# Step 1: Record a 60-second session
castor record --config config/presets/rpi_rc_car.rcan.yaml --duration 60

# Step 2: Run improvement from the latest episode
castor improve --from-latest-episode

# Step 3: Review the generated patch (optional)
castor improve --show-patch

# Step 4: If happy, apply (if auto_apply is set to manual)
castor improve --apply
```

**When to use:** After noticing the robot made the same mistake several times.

### Dry-run improvement (no changes written)

```bash
castor improve --episodes 5 --dry-run
```

**When to use:** Verify what Sisyphus would change before trusting it with
`behavior` mode.

### Roll back last auto-patch

```bash
castor improve --rollback

# Roll back everything Sisyphus has ever applied
castor improve --rollback --all
```

## Session Recording and Replay

### Record a full session to disk

```bash
castor record --config robot.rcan.yaml --output my_session.rcan.episode
```

Saves: video frames, motor commands, LLM responses, latency data.

### Replay for debugging

```bash
# Replay at half speed
castor replay --episode my_session.rcan.episode --speed 0.5

# Replay with a different provider (compare behaviours)
castor replay --episode my_session.rcan.episode --provider ollama

# Replay without executing motor commands (visual only)
castor replay --episode my_session.rcan.episode --dry-run
```

**When to use:** Debug why the robot behaved unexpectedly, or benchmark
a new provider against recorded real-world data.

## Interactive Development

### Python REPL with live robot context

```bash
castor repl --config robot.rcan.yaml

# Inside the REPL:
>>> robot.move({"direction": "forward", "speed": 0.3})
>>> robot.think("what do you see?")
>>> robot.stop()
```

**When to use:** Experiment with motor commands or provider responses
without writing code.

### Interactive command shell

```bash
castor shell --config robot.rcan.yaml

# Shell commands:
> move forward 0.5
> think "describe the scene"
> stop
> status
> quit
```

**When to use:** Quick manual control and testing without a messaging channel.

### Live telemetry dashboard in terminal

```bash
castor watch --config robot.rcan.yaml

# Shows: FPS, latency, last action, provider, battery, loop count
# Updates every 0.5s — Ctrl+C to exit
```

**When to use:** Monitor the robot in real time from a terminal (SSH session,
tmux pane, etc.) without opening the Streamlit dashboard.

## Diagnostics

### Full system health check

```bash
castor doctor

# Example output:
# ✅  Python 3.12.0
# ✅  castor package v2026.2.21.3
# ✅  ANTHROPIC_API_KEY set
# ⚠️  GOOGLE_API_KEY not set (optional)
# ✅  Camera index 0 accessible
# ✅  PCA9685 detected on I2C bus (0x40)
# ❌  zeroconf not installed — mDNS fleet discovery unavailable
#     Fix: pip install "opencastor[rcan]"
```

### Hardware-only test (no LLM calls)

```bash
castor test-hardware --config robot.rcan.yaml

# Runs: motor sweep, camera capture, I2C scan, servo calibration
# Safe to run without network access
```

### Performance benchmark

```bash
# Benchmark a provider (measures think() latency over 10 calls)
castor benchmark --config robot.rcan.yaml --provider anthropic
castor benchmark --config robot.rcan.yaml --provider ollama

# Example output:
# Provider: anthropic (claude-opus-4-6)
# Calls:    10
# Mean:     11.8s  P50: 11.3s  P95: 14.2s  P99: 15.1s
# Images:   640×480 JPEG, mean 48KB
```

**When to use:** Choose the right provider for your latency budget, or
verify that a local model meets the reactive layer requirements.

## Config Management

### Validate a config file

```bash
castor lint --config robot.rcan.yaml

# Checks: RCAN schema, required keys, value ranges, provider key presence
```

### Diff two config files

```bash
castor diff config/presets/rpi_rc_car.rcan.yaml my_custom.rcan.yaml

# Shows: added keys, removed keys, changed values
```

### Migrate config to a newer RCAN version

```bash
castor migrate --config old_robot.rcan.yaml --target-version 2.0

# Writes: old_robot.rcan.yaml.bak (backup) + updated old_robot.rcan.yaml
```

### Export a portable config bundle

```bash
castor export --config robot.rcan.yaml --output robot_bundle.zip

# Bundle contains: RCAN config + referenced files + stripped .env template
# Useful for sharing a working config without secrets
```

## Infrastructure

### Install as a systemd service (auto-start on boot)

```bash
castor install-service --config /home/pi/robot.rcan.yaml

# Creates: /etc/systemd/system/opencastor.service
# Enables: auto-start + auto-restart on crash
# Usage after install:
sudo systemctl status opencastor
sudo journalctl -u opencastor -f
```

### Backup and restore configs

```bash
# Backup all configs + .env (secrets stripped) to ~/.opencastor/backups/
castor backup

# List available backups
castor backup --list

# Restore from a backup
castor restore --backup 2026-02-21T14:30:00
```

### Check for newer versions

```bash
castor update-check

# Output:
# Current: v2026.2.21.3
# Latest:  v2026.2.21.3  ✅  Up to date
```

## Community Hub

### Browse available recipes

```bash
castor hub list

# Filter by hardware
castor hub list --filter hardware=rpi

# Filter by provider
castor hub list --filter provider=ollama
```

### Install a recipe

```bash
castor hub install picar-home-patrol

# Downloads + validates RCAN config
# Asks: merge provider key from your .env? [Y/n]
# Writes: config/recipes/picar-home-patrol.rcan.yaml
```

### Share your config as a recipe

```bash
castor hub share --config robot.rcan.yaml

# Strips API keys + personal paths
# Generates: recipes/my-robot-XXXXXX/
# Next step: open a PR to community-recipes/
```
