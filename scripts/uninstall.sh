#!/usr/bin/env bash
# OpenCastor Uninstaller
set -uo pipefail

INSTALL_DIR="${OPENCASTOR_DIR:-$HOME/opencastor}"

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

if [ ! -d "$INSTALL_DIR" ]; then
  echo "${YELLOW}OpenCastor not found at $INSTALL_DIR${RESET}"
  echo "Nothing to uninstall."
  exit 0
fi

echo "This will remove:"
echo "  ${BOLD}$INSTALL_DIR${RESET}  (source code, venv, configs)"

# Check for user-created configs
CONFIG_COUNT=$(find "$INSTALL_DIR" -maxdepth 1 -name "*.rcan.yaml" 2>/dev/null | wc -l)
if [ "$CONFIG_COUNT" -gt 0 ]; then
  echo ""
  echo "  ${YELLOW}⚠  Found $CONFIG_COUNT config file(s) that will be deleted:${RESET}"
  find "$INSTALL_DIR" -maxdepth 1 -name "*.rcan.yaml" -exec echo "     {}" \;
fi

# Check for .env
if [ -f "$INSTALL_DIR/.env" ]; then
  echo "  ${YELLOW}⚠  .env file with API keys will be deleted${RESET}"
fi

# Check for systemd service
SERVICE_ACTIVE=false
if systemctl is-active opencastor.service &>/dev/null; then
  SERVICE_ACTIVE=true
  echo "  ${YELLOW}⚠  opencastor.service is running and will be stopped${RESET}"
fi

echo ""
echo "${RED}This cannot be undone.${RESET}"
read -p "Type 'yes' to confirm uninstall: " CONFIRM

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

# Remove the directory
echo "Removing $INSTALL_DIR..."
rm -rf "$INSTALL_DIR"
echo "${GREEN}[OK]${RESET} Directory removed"

# Remove audit log if in home dir
if [ -f "$HOME/.opencastor-audit.log" ]; then
  rm -f "$HOME/.opencastor-audit.log"
  echo "${GREEN}[OK]${RESET} Audit log removed"
fi

echo ""
echo "${GREEN}OpenCastor uninstalled.${RESET}"
echo ""
echo "To reinstall:  ${BOLD}curl -sL opencastor.com/install | bash${RESET}"
echo ""
