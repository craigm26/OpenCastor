#!/bin/bash
set -e

echo ""
echo "   ___                   ___         _"
echo "  / _ \\ _ __   ___ _ __ / __|__ _ __| |_ ___ _ _"
echo " | (_) | '_ \\ / -_) '_ \\ (__/ _\` (_-<  _/ _ \\ '_|"
echo "  \\___/| .__/ \\___|_| |_|\\___\\__,_/__/\\__\\___/_|"
echo "       |_|"
echo ""
echo "OpenCastor Installer v0.1.0"
echo "The Universal Runtime for Embodied AI"
echo ""

# 1. System Prep (includes CSI camera, audio, and I2C dependencies)
echo "[1/6] Installing system dependencies..."
sudo apt-get update -qq
sudo apt-get install -y -qq \
    python3-pip python3-venv python3-dev \
    portaudio19-dev libatlas-base-dev \
    libgl1-mesa-glx libglib2.0-0 \
    libsdl2-mixer-2.0-0 libsdl2-2.0-0 \
    python3-libcamera python3-picamera2 \
    i2c-tools \
    git

# 2. Enable I2C + Camera if not already enabled
echo "[2/6] Enabling I2C and Camera interfaces..."
if ! grep -q "^dtparam=i2c_arm=on" /boot/config.txt 2>/dev/null && \
   ! grep -q "^dtparam=i2c_arm=on" /boot/firmware/config.txt 2>/dev/null; then
    sudo raspi-config nonint do_i2c 0 2>/dev/null || {
        echo ""
        echo -e "\033[31m  [WARNING] Failed to automatically enable I2C via raspi-config.\033[0m"
        echo "           Your RC car hardware may not work until I2C is enabled."
        echo ""
        echo "           To enable I2C manually, run:"
        echo "             sudo raspi-config -> Interface Options -> I2C -> Enable"
        echo ""
        read -p "  Press ENTER to continue installation anyway, or Ctrl+C to abort... " _
    }
fi
if ! grep -q "^start_x=1" /boot/config.txt 2>/dev/null && \
   ! grep -q "camera_auto_detect=1" /boot/firmware/config.txt 2>/dev/null; then
    sudo raspi-config nonint do_camera 0 2>/dev/null || {
        echo ""
        echo -e "\033[31m  [WARNING] Failed to automatically enable Camera via raspi-config.\033[0m"
        echo "           Your CSI camera may not work until the camera interface is enabled."
        echo ""
        echo "           To enable the Camera interface manually, run:"
        echo "             sudo raspi-config -> Interface Options -> Legacy Camera -> Enable"
        echo ""
        read -p "  Press ENTER to continue installation anyway, or Ctrl+C to abort... " _
    }
fi

# 3. Clone the Brain
echo "[3/6] Cloning OpenCastor..."
if [ -d "$HOME/opencastor" ]; then
    echo "  -> OpenCastor directory already exists. Pulling latest..."
    cd "$HOME/opencastor"
    git pull
else
    git clone https://github.com/continuonai/OpenCastor.git "$HOME/opencastor"
    cd "$HOME/opencastor"
fi

# 4. Create Virtual Environment
# NOTE: --system-site-packages is required so that picamera2 (installed as a system
# package via apt) is accessible inside the venv. picamera2 depends on libcamera
# Python bindings which cannot be installed via pip.
echo "[4/6] Setting up Python environment..."
python3 -m venv --system-site-packages venv
source venv/bin/activate

# 5. Install Dependencies
echo "[5/6] Installing Python packages..."
pip install --quiet -e ".[rpi]"

# Note for Dynamixel users: dynamixel-sdk is no longer installed by default.
# If your robot uses Dynamixel servos, install support with:
#   pip install dynamixel-sdk
# or:
#   pip install -e ".[dynamixel]"

# 6. Setup
echo "[6/6] Setting up your robot..."
echo ""

# Copy .env template if not present
if [ ! -f .env ]; then
    cp .env.example .env
    echo "  -> Created .env from template"
fi

# Run the wizard
python3 -m castor.wizard

echo ""
echo "================================================"
echo "  OpenCastor installed successfully!"
echo ""
echo "  Quick Start:"
echo "    1. Edit .env and add your AI provider API key"
echo "       (and messaging credentials if desired)"
echo ""
echo "    2. Start the gateway:"
echo "       castor gateway --config <your_config>.rcan.yaml"
echo ""
echo "  Common commands:"
echo "    castor run --config <config>.rcan.yaml"
echo "    castor gateway --config <config>.rcan.yaml"
echo "    castor status"
echo "    castor dashboard"
echo "    castor wizard     (re-run setup)"
echo ""
echo "  Dynamixel users: pip install dynamixel-sdk"
echo "================================================"
