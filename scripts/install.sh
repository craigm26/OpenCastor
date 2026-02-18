#!/usr/bin/env bash
# OpenCastor Cross-Platform Installer
# Supports: macOS, Debian/Ubuntu, Fedora/RHEL, Arch, Alpine, Raspberry Pi
set -euo pipefail

VERSION="2026.2.17.15"
REPO_URL="https://github.com/craigm26/OpenCastor.git"
INSTALL_DIR="${OPENCASTOR_DIR:-$HOME/opencastor}"

# ── Flags ──────────────────────────────────────────────
DRY_RUN=false
NO_RPI=false
SKIP_WIZARD=false

for arg in "$@"; do
  case "$arg" in
    --dry-run)   DRY_RUN=true ;;
    --no-rpi)    NO_RPI=true ;;
    --skip-wizard) SKIP_WIZARD=true ;;
    --help|-h)
      echo "Usage: install.sh [--dry-run] [--no-rpi] [--skip-wizard]"
      exit 0 ;;
  esac
done

# ── Colors ─────────────────────────────────────────────
if [ -t 1 ] && command -v tput &>/dev/null && [ "$(tput colors 2>/dev/null || echo 0)" -ge 8 ]; then
  RED=$(tput setaf 1); GREEN=$(tput setaf 2); YELLOW=$(tput setaf 3)
  BLUE=$(tput setaf 4); BOLD=$(tput bold); RESET=$(tput sgr0)
else
  RED=""; GREEN=""; YELLOW=""; BLUE=""; BOLD=""; RESET=""
fi

info()  { echo "${BLUE}[INFO]${RESET} $*"; }
ok()    { echo "${GREEN}[OK]${RESET} $*"; }
warn()  { echo "${YELLOW}[WARN]${RESET} $*"; }
err()   { echo "${RED}[ERROR]${RESET} $*" >&2; }
step()  { echo ""; echo "${BOLD}$*${RESET}"; }

run() {
  if [ "$DRY_RUN" = true ]; then
    echo "${YELLOW}[DRY-RUN]${RESET} $*"
  else
    "$@"
  fi
}

# ── Banner ─────────────────────────────────────────────
echo ""
echo "   ___                   ___         _"
echo "  / _ \\ _ __   ___ _ __ / __|__ _ __| |_ ___ _ _"
echo " | (_) | '_ \\ / -_) '_ \\ (__/ _\` (_-<  _/ _ \\ '_|"
echo "  \\___/| .__/ \\___|_| |_|\\___\\__,_/__/\\__\\___/_|"
echo "       |_|"
echo ""
echo "  Installer v${VERSION}  |  Cross-Platform"
echo ""

# ── Detect OS ──────────────────────────────────────────
OS="unknown"
DISTRO="unknown"
IS_RPI=false

detect_os() {
  case "$(uname -s)" in
    Darwin) OS="macos" ;;
    Linux)  OS="linux" ;;
    *)      err "Unsupported OS: $(uname -s). Use install.ps1 for Windows."; exit 1 ;;
  esac

  if [ "$OS" = "linux" ]; then
    if [ -f /etc/os-release ]; then
      # shellcheck disable=SC1091
      . /etc/os-release
      case "${ID:-}" in
        debian|ubuntu|linuxmint|pop) DISTRO="debian" ;;
        fedora|rhel|centos|rocky|alma) DISTRO="fedora" ;;
        arch|manjaro|endeavouros) DISTRO="arch" ;;
        alpine) DISTRO="alpine" ;;
        raspbian) DISTRO="debian"; IS_RPI=true ;;
        *) warn "Unknown distro '${ID:-}', trying debian-style..."; DISTRO="debian" ;;
      esac
    fi
    # RPi detection
    if [ "$NO_RPI" = false ] && [ "$IS_RPI" = false ]; then
      if grep -qi "raspberry pi" /proc/cpuinfo 2>/dev/null || \
         grep -qi "raspberry" /etc/os-release 2>/dev/null; then
        IS_RPI=true
      fi
    fi
  fi
}

detect_os
info "OS: ${OS}, Distro: ${DISTRO}, RPi: ${IS_RPI}"

# ── Helpers ────────────────────────────────────────────
has_cmd() { command -v "$1" &>/dev/null; }

need_sudo() {
  if [ "$OS" = "macos" ]; then return 1; fi
  if [ "$(id -u)" -eq 0 ]; then return 1; fi
  return 0
}

SUDO=""
if need_sudo; then
  if has_cmd sudo; then
    SUDO="sudo"
  else
    warn "No sudo available. Install may fail if not running as root."
  fi
fi

check_python_version() {
  local py="$1"
  if ! has_cmd "$py"; then return 1; fi
  local ver
  ver=$("$py" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null) || return 1
  local major minor
  major="${ver%%.*}"
  minor="${ver#*.}"
  [ "$major" -ge 3 ] && [ "$minor" -ge 10 ]
}

find_python() {
  for py in python3.13 python3.12 python3.11 python3.10 python3; do
    if check_python_version "$py"; then
      echo "$py"
      return 0
    fi
  done
  return 1
}

# ── Step 1: System Dependencies ───────────────────────
step "[1/6] Installing system dependencies..."

install_deps_macos() {
  if ! has_cmd brew; then
    info "Homebrew not found. Installing..."
    run /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    # Add brew to PATH for Apple Silicon
    if [ -f /opt/homebrew/bin/brew ]; then
      eval "$(/opt/homebrew/bin/brew shellenv)"
    fi
  fi
  run brew install python@3.12 portaudio git 2>/dev/null || true
  ok "macOS dependencies installed"
}

install_deps_debian() {
  export DEBIAN_FRONTEND=noninteractive
  export NEEDRESTART_MODE=a
  run $SUDO apt-get update -qq
  run $SUDO apt-get install -y -qq \
    python3 python3-pip python3-venv python3-dev \
    portaudio19-dev \
    libglib2.0-0 libsdl2-mixer-2.0-0 libsdl2-2.0-0 \
    i2c-tools git

  # libgl1 (handles renamed packages across versions)
  run $SUDO apt-get install -y -qq libgl1 2>/dev/null || \
    run $SUDO apt-get install -y -qq libgl1-mesa-glx 2>/dev/null || true

  # Optional: libatlas for older ARM numpy (not available on Bookworm/RPi5 — safe to skip)
  if apt-cache policy libatlas-base-dev 2>/dev/null | grep -q 'Candidate:' \
     && ! apt-cache policy libatlas-base-dev 2>/dev/null | grep -q 'Candidate: (none)'; then
    run $SUDO apt-get install -y -qq libatlas-base-dev 2>/dev/null || true
  else
    info "libatlas-base-dev not available (not needed on Bookworm/RPi5, skipping)"
  fi

  if [ "$IS_RPI" = true ]; then
    info "Raspberry Pi detected: installing camera packages..."
    run $SUDO apt-get install -y -qq python3-libcamera python3-picamera2 2>/dev/null || \
      warn "Pi camera packages unavailable; skipping."
  fi
}

install_deps_fedora() {
  run $SUDO dnf install -y -q \
    python3 python3-pip python3-devel \
    portaudio-devel \
    mesa-libGL glib2 SDL2 SDL2_mixer \
    i2c-tools git
}

install_deps_arch() {
  run $SUDO pacman -Syu --noconfirm --needed \
    python python-pip \
    portaudio \
    mesa glib2 sdl2 sdl2_mixer \
    i2c-tools git
}

install_deps_alpine() {
  run $SUDO apk add --no-cache \
    python3 py3-pip python3-dev \
    portaudio-dev \
    mesa-gl glib sdl2 sdl2_mixer \
    i2c-tools git
}

case "$OS" in
  macos) install_deps_macos ;;
  linux)
    case "$DISTRO" in
      debian)  install_deps_debian ;;
      fedora)  install_deps_fedora ;;
      arch)    install_deps_arch ;;
      alpine)  install_deps_alpine ;;
      *)       err "Unsupported distro: $DISTRO"; exit 1 ;;
    esac ;;
esac

# ── Verify Python ─────────────────────────────────────
PYTHON=$(find_python) || {
  err "Python 3.10+ is required but not found."
  err "Install Python 3.10+ and re-run this script."
  exit 1
}
ok "Using $PYTHON ($($PYTHON --version 2>&1))"

# ── Step 2: RPi Hardware Config ───────────────────────
step "[2/6] Configuring hardware interfaces..."

if [ "$IS_RPI" = true ]; then
  # Enable I2C
  if ! grep -q "^dtparam=i2c_arm=on" /boot/config.txt 2>/dev/null && \
     ! grep -q "^dtparam=i2c_arm=on" /boot/firmware/config.txt 2>/dev/null; then
    run $SUDO raspi-config nonint do_i2c 0 2>/dev/null || warn "Enable I2C manually: sudo raspi-config"
  fi
  # Enable Camera
  if ! grep -q "^start_x=1" /boot/config.txt 2>/dev/null && \
     ! grep -q "camera_auto_detect=1" /boot/firmware/config.txt 2>/dev/null; then
    run $SUDO raspi-config nonint do_camera 0 2>/dev/null || warn "Enable Camera manually: sudo raspi-config"
  fi
  ok "RPi I2C and Camera configured"
else
  info "Non-RPi system: skipping hardware config"
fi

# ── Step 3: Clone ─────────────────────────────────────
step "[3/6] Cloning OpenCastor..."

if [ -d "$INSTALL_DIR" ]; then
  info "Directory exists. Pulling latest..."
  run git -C "$INSTALL_DIR" checkout main 2>/dev/null || true
  run git -C "$INSTALL_DIR" pull origin main 2>/dev/null || warn "git pull failed (offline or detached HEAD); continuing with existing files"
else
  run git clone "$REPO_URL" "$INSTALL_DIR"
fi
cd "$INSTALL_DIR"

# ── Step 4: Virtual Environment ───────────────────────
step "[4/6] Setting up Python environment..."

VENV_ARGS=""
if [ "$IS_RPI" = true ]; then
  VENV_ARGS="--system-site-packages"
fi

if [ "$DRY_RUN" = false ]; then
  $PYTHON -m venv $VENV_ARGS venv
  # shellcheck disable=SC1091
  source venv/bin/activate
else
  echo "${YELLOW}[DRY-RUN]${RESET} $PYTHON -m venv $VENV_ARGS venv"
fi

# ── Step 5: Python Packages ──────────────────────────
step "[5/6] Installing Python packages..."

if [ "$DRY_RUN" = false ]; then
  pip install --quiet --upgrade pip
  if [ "$IS_RPI" = true ]; then
    pip install --quiet -e ".[rpi]" || {
      warn "Some RPi extras failed to install. Falling back to core..."
      pip install --quiet -e "."
    }
  else
    pip install --quiet -e ".[core]" 2>/dev/null || pip install --quiet -e "."
  fi
  ok "Python packages installed"
else
  echo "${YELLOW}[DRY-RUN]${RESET} pip install -e '.[core]'"
fi

# ── Step 6: Setup ─────────────────────────────────────
step "[6/6] Setting up your robot..."

if [ ! -f .env ] && [ -f .env.example ]; then
  run cp .env.example .env
  info "Created .env from template"
fi

if [ "$SKIP_WIZARD" = false ] && [ "$DRY_RUN" = false ]; then
  # Run wizard with --accept-risk (skip safety prompt, go straight to config)
  set +e
  $PYTHON -m castor.wizard --accept-risk </dev/tty
  WIZARD_EXIT=$?
  set -e
  if [ "$WIZARD_EXIT" -ne 0 ]; then
    warn "Wizard exited (code $WIZARD_EXIT)."
    info "No worries! Run ${BOLD}castor wizard${RESET} anytime to configure your robot."
    info "For all commands: ${BOLD}castor --help${RESET}"
  fi
fi

# Ensure a default config exists even if wizard was skipped/failed
if ! ls *.rcan.yaml &>/dev/null; then
  DEFAULT_PRESET="config/presets/rpi_rc_car.rcan.yaml"
  if [ "$IS_RPI" = true ] && [ -f "$DEFAULT_PRESET" ]; then
    run cp "$DEFAULT_PRESET" robot.rcan.yaml
    info "Copied default RPi RC Car preset → robot.rcan.yaml"
  elif [ -f "config/presets/sunfounder_picar.rcan.yaml" ]; then
    run cp "config/presets/sunfounder_picar.rcan.yaml" robot.rcan.yaml
    info "Copied default preset → robot.rcan.yaml"
  fi
  info "Edit robot.rcan.yaml to customize, or run 'castor wizard' to generate a new one."
fi

# ── Done ──────────────────────────────────────────────
echo ""
echo "${GREEN}================================================${RESET}"
echo "  ${BOLD}OpenCastor installed successfully!${RESET}"
echo ""
echo "  ${BOLD}Quick Start:${RESET}"
echo "    1. cd $INSTALL_DIR && source venv/bin/activate"
echo "    2. Edit .env and add your ANTHROPIC_API_KEY"
echo "    3. castor run --config robot.rcan.yaml"
echo ""
echo "  ${BOLD}Useful Commands:${RESET}"
echo "    castor wizard          Re-run the setup wizard anytime"
echo "    castor --help          See all available commands"
echo "    castor status          Check robot & system status"
echo "    castor doctor          Diagnose common issues"
echo "    castor dashboard       Open the web dashboard"
echo ""
echo "  ${BOLD}Verify Install:${RESET}  bash scripts/install-check.sh"
echo ""
echo "  ${YELLOW}Tip:${RESET} You can re-run ${BOLD}castor wizard${RESET} anytime to"
echo "  reconfigure your robot, change AI provider, or add channels."
echo "${GREEN}================================================${RESET}"
