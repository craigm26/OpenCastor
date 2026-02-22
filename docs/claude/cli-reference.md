# OpenCastor CLI Reference

The `castor` command is the unified entry point for all operations. Implemented in `castor/cli.py`.

## Quick Reference

```bash
castor <command> [options]
# Also available as:
python -m castor.main --config robot.rcan.yaml
python -m castor.api --config robot.rcan.yaml
python -m castor.wizard
```

---

## Core Operations

### castor run
Start the perception-action loop.

```bash
castor run --config robot.rcan.yaml             # With hardware
castor run --config robot.rcan.yaml --simulate  # Without hardware (mock driver)
```

### castor gateway
Start the API gateway server and messaging channels.

```bash
castor gateway --config robot.rcan.yaml
castor gateway --config robot.rcan.yaml --wizard  # Run wizard first
```

### castor wizard
Interactive setup wizard. Configures API keys, hardware, and channels.

```bash
castor wizard              # Full interactive wizard
castor wizard --simple     # Minimal wizard
castor wizard --web        # Browser-based wizard
castor wizard --web --port 8080  # Custom port
```

### castor dashboard
Launch the tmux-based terminal dashboard (preferred over Streamlit).

```bash
castor dashboard
```

### castor demo
Cinematic terminal demo — no hardware required.

```bash
castor demo
```

### castor status
Show provider and channel readiness.

```bash
castor status              # Local status
castor status --swarm      # Include swarm nodes
```

### castor doctor
Run system health diagnostics.

```bash
castor doctor
```

---

## Hardware

### castor test-hardware
Test individual motors and peripherals interactively.

```bash
castor test-hardware
```

### castor calibrate
Interactive hardware calibration wizard.

```bash
castor calibrate
```

### castor benchmark
Performance profiling of providers and hardware.

```bash
castor benchmark
castor benchmark --providers google,openai,anthropic
```

---

## Configuration

### castor configure
Configuration CLI helpers and interactive config editor.

```bash
castor configure
```

### castor validate
RCAN conformance check against the schema.

```bash
castor validate
castor validate --config my-robot.rcan.yaml
```

### castor lint
Deep config validation (beyond schema — logic checks).

```bash
castor lint
```

### castor migrate
Migrate a RCAN config to a newer spec version.

```bash
castor migrate --config old.rcan.yaml
```

### castor diff
Show diff between two RCAN config files.

```bash
castor diff old.rcan.yaml new.rcan.yaml
```

### castor backup / castor restore
Backup and restore configs.

```bash
castor backup
castor restore --file backup-2026-02-22.tar.gz
```

### castor export
Export a config bundle (config + .env template).

```bash
castor export --output bundle.zip
```

---

## Development & Debugging

### castor shell
Interactive command shell with robot context.

```bash
castor shell
```

### castor repl
Python REPL with live robot objects pre-loaded.

```bash
castor repl
```

### castor watch
Live Rich TUI telemetry with episode memory panel.

```bash
castor watch
```

### castor logs
View and filter logs.

```bash
castor logs
castor logs --tail 100
castor logs --level ERROR
```

### castor fix
Auto-fix common issues (permissions, config errors, etc.).

```bash
castor fix
```

### castor test
Run the test suite.

```bash
castor test
castor test --module providers
```

### castor learn
Interactive tutorial for new users.

```bash
castor learn
```

### castor quickstart
Quick start guide (prints setup steps).

```bash
castor quickstart
```

### castor record / castor replay
Record and replay robot sessions.

```bash
castor record --output session.json
castor replay --file session.json
```

---

## Advanced

### castor improve
Manage the Sisyphus self-improvement loop.

```bash
castor improve --enable
castor improve --disable
castor improve --episodes     # Show recent episodes
castor improve --status       # Show loop status
```

### castor agents
Manage multi-agent framework.

```bash
castor agents list
castor agents status
castor agents spawn <agent-type>
```

### castor fleet
Multi-robot fleet management.

```bash
castor fleet status
castor fleet status <ruri>
castor fleet command <ruri> "go forward"
castor fleet --watch          # Live status refresh
```

### castor token
JWT token management.

```bash
castor token --create --role operator
castor token --verify <token>
```

### castor discover
Auto-discover local robots via mDNS.

```bash
castor discover
```

### castor safety
Safety controls and e-stop management.

```bash
castor safety
castor safety estop
castor safety clear
```

### castor install-service
Generate and install a systemd unit file.

```bash
castor install-service
```

### castor upgrade
Self-update OpenCastor and run doctor.

```bash
castor upgrade
```

### castor plugin(s)
Plugin management.

```bash
castor plugins list
castor plugins install <name>
castor plugin remove <name>
```

### castor login
Authenticate with the gateway.

```bash
castor login
castor login --url http://192.168.68.91:8000
```

### castor privacy
Privacy and data deletion utilities.

```bash
castor privacy                 # Show data inventory
castor privacy --delete-all    # Delete all stored data
```

### castor schedule
Task scheduling management.

```bash
castor schedule
```

### castor network
Network diagnostics and configuration.

```bash
castor network
```

### castor approvals
Work approval workflow management.

```bash
castor approvals list
castor approvals approve <id>
castor approvals deny <id>
```

### castor profile
User profile management.

```bash
castor profile
castor profile --set name="My Robot"
```

### castor update-check
Check for available version updates.

```bash
castor update-check
```

### castor deploy
SSH-push config to a remote node and restart its service.

```bash
castor deploy <host> --config robot.rcan.yaml
castor deploy alex.local --config robot.rcan.yaml --full
castor deploy alex.local --status
castor deploy alex.local --dry-run
```

---

## Swarm Management

Swarm node registry lives in `config/swarm.yaml`.

### castor swarm status
Query all registered swarm nodes concurrently.

```bash
castor swarm status
castor swarm status --swarm config/swarm.yaml
castor swarm status --json          # Machine-readable output
```

### castor swarm command
Broadcast a command to all nodes, or target one.

```bash
castor swarm command --instruction "go forward"
castor swarm command --instruction "stop" --node alex
```

### castor swarm stop
Emergency stop for all nodes or a specific node.

```bash
castor swarm stop
castor swarm stop --node alex
```

### castor swarm sync
Sync RCAN config to all nodes or a specific node.

```bash
castor swarm sync
castor swarm sync --node alex
```

---

## Hub (Preset Registry)

Preset index at `config/hub_index.json`. Override URL with `CASTOR_HUB_URL`.

### castor hub list
List all available presets.

```bash
castor hub list
```

### castor hub search
Search preset names and descriptions.

```bash
castor hub search "rc car"
castor hub search dynamixel
```

### castor hub install
Download a preset to `config/presets/`.

```bash
castor hub install amazon_kit_generic
castor hub install sunfounder_picar
```

### castor hub publish
Submit a preset to the hub (opens a GitHub PR).

```bash
castor hub publish config/presets/my_robot.rcan.yaml
```

---

## Self-Update

### castor update
Git pull or pip upgrade for local install. Can SSH into swarm nodes.

```bash
castor update                   # Update local install
castor update --node alex       # Update remote swarm node via SSH
```

---

## Available Hardware Presets

| Preset | Hardware |
|--------|---------|
| `amazon_kit_generic` | Amazon RC car kits (PCA9685) |
| `adeept_generic` | Adeept robot kits |
| `waveshare_alpha` | Waveshare AlphaBot |
| `sunfounder_picar` | SunFounder PiCar |
| `dynamixel_arm` | Dynamixel servo arm |
| `rpi_rc_car` | Generic RPi RC car |
| `arduino_l298n` | Arduino + L298N motor driver |
| `esp32_generic` | ESP32 microcontroller |
| `cytron_maker_pi` | Cytron Maker Pi |
| `elegoo_tumbller` | Elegoo Tumbller |
| `freenove_4wd` | Freenove 4WD kit |
| `lego_mindstorms_ev3` | LEGO Mindstorms EV3 |
| `lego_spike_prime` | LEGO Spike Prime |
| `makeblock_mbot` | Makeblock mBot |
| `vex_iq` | VEX IQ |
| `yahboom_rosmaster` | Yahboom ROSMASTER |
