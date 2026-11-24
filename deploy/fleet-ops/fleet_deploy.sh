#!/usr/bin/env bash
# Declarative 2-Pi Fleet Deploy & Health-Gated Rollback (bob + ethan)
# Usage:
#   ./deploy/fleet-ops/fleet_deploy.sh [--dry-run] [--node bob|ethan|all]
set -euo pipefail

DRY_RUN=0
TARGET="all"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    --node)    TARGET="${2:?missing node}"; shift 2 ;;
    *) echo "Unknown arg: $1" >&2; exit 2 ;;
  esac
done

run_cmd() {
  if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "[DRY-RUN] $*"
  else
    eval "$@"
  fi
}

deploy_local_bob() {
  echo "=== Deploying local node: bob ==="
  local prev_sha
  prev_sha="$(git -C /home/craigm26/projects/opencastor-runtime rev-parse HEAD)"
  echo "Snapshot SHA: $prev_sha"
  run_cmd "git -C /home/craigm26/projects/opencastor-runtime pull --ff-only"
  run_cmd "sudo cp /home/craigm26/projects/opencastor-runtime/deploy/fleet-ops/robot-md-gateway@.service /etc/systemd/system/"
  run_cmd "sudo systemctl daemon-reload"
  run_cmd "sudo systemctl restart robot-md-gateway@bob"
  if [[ "$DRY_RUN" -eq 0 ]]; then
    if ! curl -fsS --max-time 5 http://127.0.0.1:8001/health >/dev/null 2>&1; then
      echo "ERROR: bob health check failed! Rolling back to $prev_sha..." >&2
      git -C /home/craigm26/projects/opencastor-runtime checkout "$prev_sha"
      sudo systemctl restart robot-md-gateway@bob
      exit 1
    fi
  fi
  echo "bob deploy healthy."
}

deploy_remote_ethan() {
  echo "=== Deploying remote node: ethan ==="
  run_cmd "ssh -o ConnectTimeout=5 craigm26@ethan 'set -e; prev=\$(git -C ~/OpenCastor rev-parse HEAD); git -C ~/OpenCastor pull --ff-only; sudo systemctl restart robot-md-gateway@ethan || (git -C ~/OpenCastor checkout \$prev; sudo systemctl restart robot-md-gateway@ethan; exit 1)'"
  echo "ethan deploy step complete."
}

[[ "$TARGET" == "all" || "$TARGET" == "bob" ]] && deploy_local_bob
[[ "$TARGET" == "all" || "$TARGET" == "ethan" ]] && deploy_remote_ethan
