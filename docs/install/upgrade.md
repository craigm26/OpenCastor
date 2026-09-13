# Upgrading OpenCastor

This guide covers upgrading OpenCastor on all supported platforms, with special attention to Raspberry Pi and embedded Linux setups where Python packaging has constraints.

---

## Quick Upgrade (git install)

If you cloned OpenCastor from GitHub:

```bash
castor upgrade
```

This runs `git pull origin main` and reinstalls the package in place. Services are restarted automatically.

To preview pending updates without installing:

```bash
castor upgrade --check
```

---

## Manual Upgrade Steps

```bash
cd ~/OpenCastor
git pull origin main
pip install -e .
systemctl --user restart castor-gateway.service
systemctl --user restart castor-dashboard.service
castor doctor
```

---

## Fresh Install on Raspberry Pi OS (PEP 668)

Raspberry Pi OS Bookworm (and later) enforces **PEP 668** — `pip install` into the system Python is blocked to protect OS packages. Always use a virtual environment.

### Recommended: venv with --system-site-packages

```bash
python3 -m venv ~/opencastor-env --system-site-packages
source ~/opencastor-env/bin/activate
pip install -e ~/OpenCastor
```

**Why `--system-site-packages`?**

Several hardware libraries on Raspberry Pi are only available as system packages (installed by `apt`), not on PyPI:

| Package | Install method | Notes |
|---------|---------------|-------|
| `picamera2` | `sudo apt install python3-picamera2` | CSI/IMX camera support |
| `libcamera` | installed with picamera2 | Camera backend |
| `RPi.GPIO` | `sudo apt install python3-rpi.gpio` | GPIO pin control |
| `lgpio` | `sudo apt install python3-lgpio` | Modern GPIO for Pi 5 |

Without `--system-site-packages`, your venv cannot see these packages and hardware features will silently fall back to mock mode.

### Alternative: --break-system-packages (not recommended)

```bash
pip install -e ~/OpenCastor --break-system-packages
```

This installs into the system Python directly. It works but may conflict with OS package manager updates.

---

## Systemd Service Migration

After changing venv paths or upgrading Python, update the service to use the new Python binary.

The generated service files use `python -m castor.cli gateway` (not a hardcoded `castor` binary path), so they work with any Python in `OPENCASTOR_VENV`:

```bash
# Regenerate service files
castor install-service --config ~/OpenCastor/robot.rcan.yaml

# Or update manually
systemctl --user daemon-reload
systemctl --user restart castor-gateway.service
```

To point the service at a specific venv:

```ini
# In /etc/systemd/system/castor-gateway.service
Environment=OPENCASTOR_VENV=/home/pi/opencastor-env
ExecStart=/home/pi/opencastor-env/bin/python -m castor.cli gateway --config /home/pi/OpenCastor/robot.rcan.yaml
```

---

## castor upgrade Command Reference

```
castor upgrade [--check] [--venv PATH] [--verbose]

Options:
  --check        Show pending commits without upgrading
  --venv PATH    Use this venv's Python for pip install (default: current Python)
  --verbose      Show full git and pip output
```

**What it does:**
1. Detects whether you're on a git install (`~/.git` present)
2. Runs `git pull origin main` if so
3. Runs `pip install -e <repo>` with the active Python
4. Restarts `castor-gateway.service` and `castor-dashboard.service` (best-effort)
5. Prints the new version and runs `castor doctor`

---

## Troubleshooting

### Port already in use

```
RuntimeError: Port 8000 already in use. Stop the existing gateway with: castor stop
```

```bash
castor stop          # graceful SIGTERM via PID file
# or
fuser -k 8000/tcp    # force-kill whatever owns port 8000
```

### Old process not cleaned up after upgrade

```bash
# Check what's running
ps aux | grep castor

# Kill by PID or use
castor stop
```

### Service restart fails

```bash
# Check logs
journalctl --user -u castor-gateway.service -n 50

# Reload service definition after file changes
systemctl --user daemon-reload
systemctl --user restart castor-gateway.service
```

### Wrong Python after changing venv

```bash
# Verify which Python the service uses
systemctl --user cat castor-gateway.service | grep ExecStart

# Regenerate with correct venv
castor install-service --config robot.rcan.yaml
```

### picamera2 / GPIO not found in venv

Re-create the venv with `--system-site-packages`:

```bash
deactivate
rm -rf ~/opencastor-env
python3 -m venv ~/opencastor-env --system-site-packages
source ~/opencastor-env/bin/activate
pip install -e ~/OpenCastor
```

---

## Health Check After Upgrade

```bash
castor doctor
```

This checks CPU/memory/disk, RCAN compliance, and — after installing hardware deps — reports any missing optional packages detected by `suggest_extras()`.

---

## Upgrading to RCAN v2.1

v2.1 is a **clean break** from v1.x. After upgrading OpenCastor, run the migration tool before restarting:

```bash
git pull origin main
pip install -e .
castor migrate --config robot.rcan.yaml   # upgrades any version → 2.1
```

### New required steps after migration

**1. Generate and sign your firmware manifest**

```bash
castor attest generate
castor attest sign --key /path/to/robot-private.pem
```

**2. Generate and publish your SBOM**

```bash
castor sbom generate
castor sbom publish --token <your-rrf-token>
```

**3. Enable the authority handler in your config**

```yaml
# robot.rcan.yaml
authority_handler_enabled: true
authority:
  # The flag on its own is not enough. The AUTHORITY_ACCESS (41) handler
  # fails closed, so with no ids listed here it refuses every requester,
  # `castor iso-check` and the conformance row grade it as not satisfied,
  # and the robot discovers eu_ai_act as false. List the registry ids of
  # the authorities this robot should answer.
  trusted_authority_ids:
    - eu.aiact.notified-body.001
audit_retention_days: 3650   # 10yr, required for EU AI Act Art. 12
```

**4. Verify L5 compliance**

```bash
castor doctor
castor conformance --json | python3 -m json.tool
```

Firmware and SBOM artifacts live at `/run/opencastor/` (runtime-generated). They are `warn` not `fail` if absent at startup — generate them at least once before the EU AI Act deadline: **August 2, 2026**.

### Breaking changes from v1.x

| Before (v1.x) | After (v2.1) |
|---|---|
| `rcan_version: "1.10"` | `rcan_version: "2.1"` |
| `LevelOfAssurance.HARDWARE_TOKEN` | `Role.ADMIN` or `Role.CREATOR` |
| `MessageType.ALERT` | `MessageType.FAULT_REPORT` |
| `MessageType.AUDIT` | `MessageType.TRANSPARENCY` |
| `MessageType.FEDERATION_SYNC` | `MessageType.FLEET_COMMAND` |
| `signature: "pending"` in wire format | **Rejected at parse time** |
