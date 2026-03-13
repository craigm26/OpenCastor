#!/bin/bash
set -e

CONFIG_PATH="${CASTOR_CONFIG:-/app/config/arm.rcan.yaml}"

# ── Print LeRobot tool status on startup ──────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║        OpenCastor ARM  —  SO-ARM101 + LeRobot               ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""

# Check lerobot tools
for tool in lerobot-find-port lerobot-setup-motors lerobot-calibrate; do
    if command -v "$tool" &>/dev/null; then
        echo "  ✓  $tool"
    else
        echo "  ✗  $tool (not found — image may need rebuild)"
    fi
done
echo ""

# List available USB serial devices
USB_DEVS=$(ls /dev/ttyACM* /dev/ttyUSB* 2>/dev/null || true)
if [ -n "$USB_DEVS" ]; then
    echo "  USB serial devices:"
    for d in $USB_DEVS; do echo "    $d"; done
else
    echo "  ⚠  No USB serial devices found."
    echo "     Ensure controller boards are plugged in and --device flags are set."
fi
echo ""

# Generate arm config if missing
if [ ! -f "$CONFIG_PATH" ]; then
    echo "  No arm config at $CONFIG_PATH"
    echo "  Run the arm setup wizard:"
    echo ""
    echo "    docker exec -it opencastor-arm castor arm assemble"
    echo "    docker exec -it opencastor-arm castor arm detect"
    echo "    docker exec -it opencastor-arm castor arm setup --arm follower"
    echo "    docker exec -it opencastor-arm castor arm calibrate --arm follower"
    echo "    docker exec -it opencastor-arm castor arm config --name my_arm"
    echo ""
    echo "  Or use the web wizard:"
    echo "    http://alex.local:8765  (castor wizard --web)"
    echo ""
fi

exec "$@"
