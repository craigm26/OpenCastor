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

# 1. System Prep
echo "[1/5] Installing system dependencies..."
sudo apt-get update -qq
sudo apt-get install -y -qq \
    python3-pip python3-venv \
    portaudio19-dev libatlas-base-dev \
    libgl1-mesa-glx libglib2.0-0 \
    git

# 2. Clone the Brain
echo "[2/5] Cloning OpenCastor..."
if [ -d "$HOME/opencastor" ]; then
    echo "  -> OpenCastor directory already exists. Pulling latest..."
    cd "$HOME/opencastor"
    git pull
else
    git clone https://github.com/continuonai/OpenCastor.git "$HOME/opencastor"
    cd "$HOME/opencastor"
fi

# 3. Create Virtual Environment
echo "[3/5] Setting up Python environment..."
python3 -m venv venv
source venv/bin/activate

# 4. Install Dependencies
echo "[4/5] Installing Python packages..."
pip install --quiet -r requirements.txt

# 5. Run the Hardware Wizard
echo "[5/5] Configuring your robot..."
echo ""
python3 -m castor.wizard --simple

echo ""
echo "================================================"
echo "  OpenCastor installed successfully!"
echo ""
echo "  To start your robot:"
echo "    cd ~/opencastor"
echo "    source venv/bin/activate"
echo "    python -m castor.main --config <your_config>.rcan.yaml"
echo ""
echo "  To launch the dashboard:"
echo "    streamlit run castor/dashboard.py"
echo "================================================"
