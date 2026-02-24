#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APPARMOR_DIR="$ROOT_DIR/apparmor"
SECCOMP_DIR="$ROOT_DIR/seccomp"
TARGET_ETC="/etc/opencastor/security"

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "[WARN] MAC/seccomp profiles are only installed on Linux hosts."
  exit 0
fi

SUDO=""
if [[ "${EUID}" -ne 0 ]]; then
  SUDO="sudo"
fi

$SUDO mkdir -p "$TARGET_ETC/apparmor" "$TARGET_ETC/seccomp"
$SUDO cp "$APPARMOR_DIR/opencastor-gateway" "$TARGET_ETC/apparmor/opencastor-gateway"
$SUDO cp "$APPARMOR_DIR/opencastor-driver" "$TARGET_ETC/apparmor/opencastor-driver"
$SUDO cp "$SECCOMP_DIR/gateway-seccomp.json" "$TARGET_ETC/seccomp/gateway-seccomp.json"
$SUDO cp "$SECCOMP_DIR/driver-strict-seccomp.json" "$TARGET_ETC/seccomp/driver-strict-seccomp.json"

if command -v apparmor_parser >/dev/null 2>&1; then
  $SUDO apparmor_parser -r "$TARGET_ETC/apparmor/opencastor-gateway" || true
  $SUDO apparmor_parser -r "$TARGET_ETC/apparmor/opencastor-driver" || true
fi

echo "Installed MAC/seccomp artifacts to $TARGET_ETC"
