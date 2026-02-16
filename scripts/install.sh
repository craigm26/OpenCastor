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
echo "RPi RC Car + Claude Opus + WhatsApp Stack"
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
    sudo raspi-config nonint do_i2c 0 2>/dev/null || echo "  -> Enable I2C manually: sudo raspi-config"
fi
if ! grep -q "^start_x=1" /boot/config.txt 2>/dev/null && \
   ! grep -q "camera_auto_detect=1" /boot/firmware/config.txt 2>/dev/null; then
    sudo raspi-config nonint do_camera 0 2>/dev/null || echo "  -> Enable Camera manually: sudo raspi-config"
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

# 4. Create Virtual Environment (--system-site-packages for picamera2)
echo "[4/6] Setting up Python environment..."
python3 -m venv --system-site-packages venv
source venv/bin/activate

# 5. Install Dependencies
echo "[5/6] Installing Python packages (core + RPi + WhatsApp)..."
pip install --quiet -e ".[rpi]"

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
echo "  Quick Start (3 steps):"
echo "    1. Edit .env and add your ANTHROPIC_API_KEY"
echo "       and TWILIO_* credentials"
echo ""
echo "    2. Start the gateway:"
echo "       castor gateway --config config/presets/rpi_rc_car.rcan.yaml"
echo ""
echo "    3. Send a WhatsApp message to your robot!"
echo ""
echo "  Other commands:"
echo "    castor run --config config/presets/rpi_rc_car.rcan.yaml"
echo "    castor status"
echo "    castor dashboard"
echo "================================================"
