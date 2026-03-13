#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# OpenCastor — alex.local bootstrap script
# Nukes old installs and sets up OpenCastor ARM in Docker
#
# Usage (run on alex.local):
#   curl -sL https://raw.githubusercontent.com/craigm26/OpenCastor/main/docker/alex/setup.sh | bash
#   # or clone first:
#   git clone https://github.com/craigm26/OpenCastor.git && cd OpenCastor
#   bash docker/alex/setup.sh
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

REPO_DIR="${HOME}/OpenCastor"
COMPOSE_FILE="docker-compose.arm.yml"
IMAGE="opencastor-arm:latest"
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'

log()  { echo -e "${GREEN}[setup]${NC} $*"; }
warn() { echo -e "${YELLOW}[warn]${NC}  $*"; }
die()  { echo -e "${RED}[error]${NC} $*" >&2; exit 1; }

echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║        OpenCastor ARM — alex.local bootstrap                ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""

# ── 1. Nuke old installs ──────────────────────────────────────────────────────
log "Cleaning up old installs..."

# Remove LeRobot venv if present
if [ -d "${HOME}/lerobot" ]; then
    warn "Removing ~/lerobot (was: $(du -sh "${HOME}/lerobot" 2>/dev/null | cut -f1) on disk)"
    rm -rf "${HOME}/lerobot"
    log "Removed ~/lerobot"
fi

# Remove any stray pip user installs of lerobot/opencastor
pip3 uninstall -y lerobot opencastor 2>/dev/null || true
pip3 uninstall -y feetech-servo-sdk feetech_servo_sdk 2>/dev/null || true

log "Old installs cleaned."

# ── 2. Install Docker if missing ─────────────────────────────────────────────
if ! command -v docker &>/dev/null; then
    log "Docker not found — installing..."
    curl -fsSL https://get.docker.com | sh
    sudo usermod -aG docker "${USER}"
    warn "Docker installed. You may need to log out and back in for group changes."
    warn "If the next steps fail with 'permission denied', run: newgrp docker"
else
    log "Docker already installed: $(docker --version)"
fi

# Ensure docker compose plugin is available
if ! docker compose version &>/dev/null; then
    log "Installing docker-compose-plugin..."
    sudo apt-get install -y docker-compose-plugin 2>/dev/null || \
        sudo apt-get install -y docker-compose 2>/dev/null || \
        pip3 install docker-compose
fi

# ── 3. Clone / update OpenCastor ─────────────────────────────────────────────
if [ ! -d "${REPO_DIR}/.git" ]; then
    log "Cloning OpenCastor..."
    git clone https://github.com/craigm26/OpenCastor.git "${REPO_DIR}"
else
    log "Updating OpenCastor..."
    git -C "${REPO_DIR}" pull --ff-only origin main
fi

cd "${REPO_DIR}"

# ── 4. Create .env if missing ─────────────────────────────────────────────────
if [ ! -f ".env" ]; then
    log "Creating .env from template..."
    if [ -f ".env.example" ]; then
        cp .env.example .env
    else
        cat > .env << 'ENVEOF'
# OpenCastor ARM environment
# Add your AI provider key below — Ollama works offline with no key needed.
# ANTHROPIC_API_KEY=sk-ant-...
# GOOGLE_API_KEY=...
# OPENAI_API_KEY=sk-...
ENVEOF
    fi
    warn ".env created. Edit it to add your AI provider API key if needed."
fi

# Create config dir
mkdir -p config

# ── 5. Build Docker image ─────────────────────────────────────────────────────
log "Building opencastor-arm Docker image (this takes a few minutes on first run)..."
docker build -f Dockerfile.arm -t "${IMAGE}" . 2>&1 | \
    grep -E '(Step|RUN|COPY|FROM|Successfully|error|ERROR|warning)' || true

log "Image built: ${IMAGE}"

# ── 6. Start the container ────────────────────────────────────────────────────
log "Starting opencastor-arm container..."
docker compose -f "${COMPOSE_FILE}" up -d

log "Container started. Checking health..."
sleep 3
docker compose -f "${COMPOSE_FILE}" ps

# ── 7. Print next steps ───────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║  ✅  OpenCastor ARM is running on alex.local                ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""
echo "  Now assemble and configure your SO-ARM101:"
echo ""
echo "  1. Guided assembly walkthrough:"
echo "     docker exec -it opencastor-arm castor arm assemble --arm follower"
echo ""
echo "  2. Detect USB ports (plug in controller boards first):"
echo "     docker exec -it opencastor-arm castor arm detect"
echo ""
echo "  3. Configure motor IDs 1–6 (one motor at a time):"
echo "     docker exec -it opencastor-arm castor arm setup --arm follower"
echo ""
echo "  4. Verify all 6 motors in the daisy chain:"
echo "     docker exec -it opencastor-arm castor arm verify --port /dev/ttyACM0"
echo ""
echo "  5. Calibrate joints:"
echo "     docker exec -it opencastor-arm castor arm calibrate --arm follower --port /dev/ttyACM0"
echo ""
echo "  6. Generate RCAN config:"
echo "     docker exec -it opencastor-arm castor arm config --name alex_arm --follower-port /dev/ttyACM0"
echo ""
echo "  7. Web wizard (browser-based setup):"
echo "     http://alex.local:8765"
echo ""
echo "  Logs:    docker compose -f docker-compose.arm.yml logs -f"
echo "  Shell:   docker exec -it opencastor-arm bash"
echo "  Stop:    docker compose -f docker-compose.arm.yml down"
echo ""
