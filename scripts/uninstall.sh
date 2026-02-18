#!/usr/bin/env bash
# OpenCastor Uninstaller
set -uo pipefail

INSTALL_DIR="${OPENCASTOR_DIR:-$HOME/opencastor}"
CONFIG_DIR="$HOME/.opencastor"

# Colors
if [ -t 1 ] && command -v tput &>/dev/null && [ "$(tput colors 2>/dev/null || echo 0)" -ge 8 ]; then
  RED=$(tput setaf 1); GREEN=$(tput setaf 2); YELLOW=$(tput setaf 3)
  BOLD=$(tput bold); RESET=$(tput sgr0)
else
  RED=""; GREEN=""; YELLOW=""; BOLD=""; RESET=""
fi

echo ""
echo "${BOLD}OpenCastor Uninstaller${RESET}"
echo "======================"
echo ""

if [ ! -d "$INSTALL_DIR" ] && [ ! -d "$CONFIG_DIR" ]; then
  echo "${YELLOW}OpenCastor not found at $INSTALL_DIR${RESET}"
  echo "Nothing to uninstall."
  exit 0
fi

echo "This will remove:"
if [ -d "$INSTALL_DIR" ]; then
  echo "  ${BOLD}$INSTALL_DIR${RESET}  (source code, venv, build artifacts)"
fi

# Check for user-created configs in install dir
CONFIG_COUNT=$(find "$INSTALL_DIR" -maxdepth 1 -name "*.rcan.yaml" 2>/dev/null | wc -l)
if [ "$CONFIG_COUNT" -gt 0 ]; then
  echo ""
  echo "  ${YELLOW}⚠  Found $CONFIG_COUNT robot config file(s):${RESET}"
  find "$INSTALL_DIR" -maxdepth 1 -name "*.rcan.yaml" -exec echo "     {}" \;
fi

# Show what's in ~/.opencastor
if [ -d "$CONFIG_DIR" ]; then
  echo ""
  echo "  Your credentials and settings are stored separately at:"
  echo "  ${BOLD}$CONFIG_DIR/${RESET}"
  if [ -f "$CONFIG_DIR/anthropic-token" ]; then
    echo "    • anthropic-token  (Claude subscription auth)"
  fi
  if [ -f "$CONFIG_DIR/env" ]; then
    echo "    • env              (API keys, config vars)"
  fi
  if [ -f "$CONFIG_DIR/wizard-state.yaml" ]; then
    echo "    • wizard-state     (saved wizard preferences)"
  fi
fi

# Legacy .env in install dir
if [ -f "$INSTALL_DIR/.env" ]; then
  echo ""
  echo "  ${YELLOW}⚠  Legacy .env found in $INSTALL_DIR — will migrate to $CONFIG_DIR/env${RESET}"
fi

# Check for systemd service
SERVICE_ACTIVE=false
if systemctl is-active opencastor.service &>/dev/null; then
  SERVICE_ACTIVE=true
  echo ""
  echo "  ${YELLOW}⚠  opencastor.service is running and will be stopped${RESET}"
fi

echo ""
echo "${RED}This cannot be undone (except credentials — see below).${RESET}"
read -rp "Type 'yes' to confirm uninstall: " CONFIRM </dev/tty

if [ "$CONFIRM" != "yes" ]; then
  echo "Cancelled."
  exit 0
fi

echo ""

# Stop and disable service if running
if [ "$SERVICE_ACTIVE" = true ]; then
  echo "Stopping opencastor.service..."
  sudo systemctl stop opencastor.service 2>/dev/null || true
  sudo systemctl disable opencastor.service 2>/dev/null || true
  sudo rm -f /etc/systemd/system/opencastor.service 2>/dev/null || true
  sudo systemctl daemon-reload 2>/dev/null || true
  echo "${GREEN}[OK]${RESET} Service removed"
fi

# Migrate legacy .env to ~/.opencastor/env if it doesn't exist there yet
if [ -f "$INSTALL_DIR/.env" ] && [ ! -f "$CONFIG_DIR/env" ]; then
  mkdir -p "$CONFIG_DIR" && chmod 700 "$CONFIG_DIR"
  cp "$INSTALL_DIR/.env" "$CONFIG_DIR/env"
  chmod 600 "$CONFIG_DIR/env"
  echo "${GREEN}[OK]${RESET} Migrated .env → $CONFIG_DIR/env"
fi

# Remove the install directory
if [ -d "$INSTALL_DIR" ]; then
  echo "Removing $INSTALL_DIR..."
  rm -rf "$INSTALL_DIR"
  echo "${GREEN}[OK]${RESET} Installation removed"
fi

# Remove audit log if in home dir
if [ -f "$HOME/.opencastor-audit.log" ]; then
  rm -f "$HOME/.opencastor-audit.log"
  echo "${GREEN}[OK]${RESET} Audit log removed"
fi

# Ask about credentials
echo ""
if [ -d "$CONFIG_DIR" ]; then
  echo "${BOLD}Your credentials are safe at $CONFIG_DIR/${RESET}"
  echo ""
  echo "  [1] Keep credentials (recommended — reuse on reinstall)"
  echo "  [2] Delete everything (removes all tokens and settings)"
  echo ""
  read -rp "Selection [1]: " CRED_CHOICE </dev/tty
  CRED_CHOICE="${CRED_CHOICE:-1}"

  if [ "$CRED_CHOICE" = "2" ]; then
    rm -rf "$CONFIG_DIR"
    echo "${GREEN}[OK]${RESET} Credentials removed"
  else
    echo "${GREEN}[OK]${RESET} Credentials kept at $CONFIG_DIR/"
  fi
fi

echo ""
echo "${GREEN}OpenCastor uninstalled.${RESET}"
echo ""
echo "To reinstall:  ${BOLD}curl -sL opencastor.com/install | bash${RESET}"
if [ -d "$CONFIG_DIR" ]; then
  echo "               (your saved credentials will be reused automatically)"
fi
echo ""
